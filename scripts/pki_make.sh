#!/usr/bin/env bash
# pki_make.sh: mint and inspect the private PKI that musings uses for TLS.
#
# musings is reached by one or two parties the operator already knows, on
# an address that usually has no DNS name, so a public certificate
# authority buys nothing. This script makes a private CA, a server
# certificate for musings-server, and client certificates for mTLS, all
# with plain openssl. The CA public certificate (ca.pem) is what a remote
# client pins; the CA private key (ca.key) signs and must never leave the
# box. Everything lands in one directory, PKI_DIR, which defaults to
# ~/musings/pki and must not be inside the repository.
#
# Usage:
#   pki_make.sh ca      [--days 3650] [--name "musings CA"]
#   pki_make.sh server  [--days 397]  [--name HOST] [--san IP:a.b.c.d,DNS:host,...]
#   pki_make.sh client  --name NAME   [--days 90]
#   pki_make.sh show
#
# Files written (keys mode 600, certificates 644):
#   PKI_DIR/ca.pem  ca.key            the authority
#   PKI_DIR/server.pem  server.key    musings-server identity
#   PKI_DIR/clients/NAME.pem  NAME.key   one pair per mTLS client
#
# The server certificate always carries DNS:localhost and IP:127.0.0.1 as
# subject alternative names, because an embedded poller (--with-poller)
# dials loopback; add the LAN and public addresses with --san. Lifetimes
# are deliberate: the CA lives long, the server a year, a client only 90
# days because its key sits on someone else's machine.

set -euo pipefail

PKI_DIR="${PKI_DIR:-$HOME/musings/pki}"
CURVE="P-256"

usage() {
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
}

die() {
    echo "pki_make: $*" >&2
    exit 1
}

key_make() {
    # Write an EC private key at $1, readable by the owner only.
    local keyPath="$1"
    [ -e "$keyPath" ] && die "refusing to overwrite existing key $keyPath"
    (umask 077 && openssl genpkey -algorithm EC -pkeyopt "ec_paramgen_curve:$CURVE" -out "$keyPath")
}

ca_make() {
    local days=3650 name="musings CA"
    while [ $# -gt 0 ]; do
        case "$1" in
            --days) days="$2"; shift 2 ;;
            --name) name="$2"; shift 2 ;;
            *) usage ;;
        esac
    done
    mkdir -p "$PKI_DIR"
    chmod 700 "$PKI_DIR"
    [ -e "$PKI_DIR/ca.pem" ] && die "CA already exists at $PKI_DIR/ca.pem"
    key_make "$PKI_DIR/ca.key"
    openssl req -x509 -new -key "$PKI_DIR/ca.key" -days "$days" \
        -subj "/CN=$name" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -addext "subjectKeyIdentifier=hash" \
        -out "$PKI_DIR/ca.pem"
    chmod 644 "$PKI_DIR/ca.pem"
    echo "CA written: $PKI_DIR/ca.pem (public, share this) and ca.key (private, never share)."
}

cert_sign() {
    # Sign CSR $1 into certificate $2 for $3 days, copying its extensions.
    local csrPath="$1" certPath="$2" days="$3"
    openssl x509 -req -in "$csrPath" -days "$days" \
        -CA "$PKI_DIR/ca.pem" -CAkey "$PKI_DIR/ca.key" -CAcreateserial \
        -copy_extensions copy -out "$certPath"
    chmod 644 "$certPath"
    rm -f "$csrPath"
}

server_make() {
    local days=397 name sans=""
    name="$(hostname)"
    while [ $# -gt 0 ]; do
        case "$1" in
            --days) days="$2"; shift 2 ;;
            --name) name="$2"; shift 2 ;;
            --san) sans="$2"; shift 2 ;;
            *) usage ;;
        esac
    done
    [ -f "$PKI_DIR/ca.key" ] || die "no CA at $PKI_DIR; run: $0 ca"
    local sanList="DNS:localhost,IP:127.0.0.1,DNS:$name"
    [ -n "$sans" ] && sanList="$sanList,$sans"
    key_make "$PKI_DIR/server.key"
    openssl req -new -key "$PKI_DIR/server.key" -subj "/CN=$name" \
        -addext "subjectAltName=$sanList" \
        -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
        -addext "extendedKeyUsage=serverAuth" \
        -out "$PKI_DIR/server.csr"
    cert_sign "$PKI_DIR/server.csr" "$PKI_DIR/server.pem" "$days"
    echo "Server certificate written: $PKI_DIR/server.pem, SANs $sanList, $days days."
}

client_make() {
    local days=90 name=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --days) days="$2"; shift 2 ;;
            --name) name="$2"; shift 2 ;;
            *) usage ;;
        esac
    done
    [ -n "$name" ] || die "client needs --name"
    [ -f "$PKI_DIR/ca.key" ] || die "no CA at $PKI_DIR; run: $0 ca"
    mkdir -p "$PKI_DIR/clients"
    chmod 700 "$PKI_DIR/clients"
    local base="$PKI_DIR/clients/$name"
    key_make "$base.key"
    openssl req -new -key "$base.key" -subj "/CN=$name" \
        -addext "keyUsage=critical,digitalSignature" \
        -addext "extendedKeyUsage=clientAuth" \
        -out "$base.csr"
    cert_sign "$base.csr" "$base.pem" "$days"
    echo "Client certificate written: $base.pem and $base.key, $days days."
    echo "Hand the client ca.pem, $name.pem, and $name.key over a channel you trust."
}

fingerprint_print() {
    local certPath="$1"
    printf '%-40s %s\n' "${certPath#"$PKI_DIR"/}" \
        "$(openssl x509 -noout -fingerprint -sha256 -in "$certPath" | sed 's/^.*=//')"
    printf '%-40s expires %s\n' "" \
        "$(openssl x509 -noout -enddate -in "$certPath" | sed 's/^.*=//')"
}

pki_show() {
    [ -d "$PKI_DIR" ] || die "no PKI at $PKI_DIR"
    echo "PKI at $PKI_DIR (SHA256 fingerprints):"
    local certPath
    for certPath in "$PKI_DIR"/ca.pem "$PKI_DIR"/server.pem "$PKI_DIR"/clients/*.pem; do
        [ -f "$certPath" ] && fingerprint_print "$certPath"
    done
    return 0
}

command -v openssl >/dev/null || die "openssl not found"
[ $# -ge 1 ] || usage
subcommand="$1"
shift
case "$subcommand" in
    ca) ca_make "$@" ;;
    server) server_make "$@" ;;
    client) client_make "$@" ;;
    show) pki_show ;;
    *) usage ;;
esac
