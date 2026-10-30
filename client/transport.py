"""One HTTP client factory for every musings client.

The CLI, the poller, mush, rush, and the rush executor all talk to the
same server with the same bearer token, and since the server can
terminate TLS they all need the same trust decisions too. This module
holds those decisions in one place, so a change to how a client
verifies the server or proves itself cannot be made in four tools and
forgotten in a fifth.

Three environment variables beyond ``MUSINGS_URL`` and
``MUSINGS_TOKEN`` shape the connection. ``MUSINGS_CA_CERT`` is the
PEM authority the server certificate must chain to; with a private
CA this is the pin, and without it the system trust store is used.
``MUSINGS_CLIENT_CERT`` and ``MUSINGS_CLIENT_KEY`` are the certificate
and key this client presents when the server runs mutual TLS. None of
them is read for a plain ``http`` URL, where there is nothing to
verify.

Key components:
    - ClientTlsOptions: the verify and cert values resolved from the
      environment, in the shape httpx takes them.
    - tlsOptions_build: resolve those values, with a clear error when
      they are inconsistent.
    - client_build: the authenticated httpx client every tool uses.

Proxy environment variables are deliberately bypassed
(``trust_env=False``): httpx's proxy parsing breaks in some sandboxed
environments, and musings servers are reached directly. Proxy support
is a roadmap item.
"""

from __future__ import annotations

import os
import ssl
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from client.env import env_load


@dataclass
class ClientTlsOptions:
    """How a client verifies the server and proves itself.

    Attributes:
        caFile: PEM authority to pin the server certificate to, or None
            for the system trust store. Verification is never turned
            off: an unverified TLS connection would hand the token to
            whoever answers.
        clientCert: ``(certFile, keyFile)`` presented to the server under
            mutual TLS, or None when the server asks for none.

    Example:
        >>> options = ClientTlsOptions(caFile="/home/me/musings/pki/ca.pem", clientCert=None)
        >>> context = options.sslContext_build()
    """

    caFile: str | None
    clientCert: tuple[str, str] | None

    def sslContext_build(self) -> ssl.SSLContext:
        """Build the SSL context httpx uses for the connection.

        Returns:
            A default client context (hostname checking and certificate
            verification on) trusting ``caFile`` when set, otherwise the
            system store, with the client certificate chain loaded when
            one is configured.
        """
        context = ssl.create_default_context(cafile=self.caFile)
        if self.clientCert is not None:
            certFile, keyFile = self.clientCert
            context.load_cert_chain(certFile, keyFile)
        return context


def tlsOptions_build(baseUrl: str, environ: dict[str, str] | None = None) -> ClientTlsOptions | None:
    """Resolve the client's TLS options from the environment.

    Args:
        baseUrl: Service URL; its scheme decides whether TLS applies.
        environ: Variables to read; None means the process environment.

    Returns:
        Options for an ``https`` URL, or None for plain ``http``, where
        the TLS variables are not consulted at all.

    Raises:
        SystemExit: When exactly one of ``MUSINGS_CLIENT_CERT`` and
            ``MUSINGS_CLIENT_KEY`` is set, or a named file is missing.
    """
    env = os.environ if environ is None else environ
    if urlparse(baseUrl).scheme != "https":
        return None
    caCert = env.get("MUSINGS_CA_CERT", "")
    clientCert = env.get("MUSINGS_CLIENT_CERT", "")
    clientKey = env.get("MUSINGS_CLIENT_KEY", "")
    if bool(clientCert) != bool(clientKey):
        print(
            "MUSINGS_CLIENT_CERT and MUSINGS_CLIENT_KEY must be set together.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    for label, path in (
        ("MUSINGS_CA_CERT", caCert),
        ("MUSINGS_CLIENT_CERT", clientCert),
        ("MUSINGS_CLIENT_KEY", clientKey),
    ):
        if path and not os.path.isfile(path):
            print(f"{label} names {path}, which is not a readable file.", file=sys.stderr)
            raise SystemExit(2)
    return ClientTlsOptions(
        caFile=caCert if caCert else None,
        clientCert=(clientCert, clientKey) if clientCert else None,
    )


def client_build(timeout: httpx.Timeout | float = 30.0) -> httpx.Client:
    """Build the authenticated HTTP client from the environment.

    Loads ``.env`` from the current directory first (never overriding
    real environment variables), then requires ``MUSINGS_URL`` and
    ``MUSINGS_TOKEN``.

    Args:
        timeout: httpx timeout for the client; a tool that long-polls or
            streams passes one with a read timeout to match.

    Returns:
        A client pointed at the service URL with the bearer header set
        and, for ``https``, the SSL context from ``tlsOptions_build``.

    Raises:
        SystemExit: When ``MUSINGS_URL`` or ``MUSINGS_TOKEN`` is unset,
            or the TLS variables are inconsistent.
    """
    env_load()
    baseUrl = os.environ.get("MUSINGS_URL", "")
    token = os.environ.get("MUSINGS_TOKEN", "")
    if not baseUrl or not token:
        print("MUSINGS_URL and MUSINGS_TOKEN must both be set.", file=sys.stderr)
        raise SystemExit(2)
    tlsOptions = tlsOptions_build(baseUrl)
    verify: ssl.SSLContext | bool = (
        tlsOptions.sslContext_build() if tlsOptions is not None else True
    )
    return httpx.Client(
        base_url=baseUrl,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
        trust_env=False,
        verify=verify,
    )
