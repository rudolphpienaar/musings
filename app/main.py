"""Musings application entry point.

Run with uvicorn, for example::

    MUSINGS_TOKEN=<token> uvicorn app.main:app --host 127.0.0.1 --port 8000

Or with the installed console script, which listens on the port of
``MUSINGS_URL`` (default 8000) and, by default, on loopback only::

    MUSINGS_TOKEN=<token> musings-server

The token is required: the settings object raises at import time when
``MUSINGS_TOKEN`` is unset, so the service cannot start unprotected.

``--public`` listens on every interface instead of loopback, which is
what a router port forward or a LAN client needs to reach the server.
It is a flag rather than a setting on purpose: exposing the service is
a decision to make visibly, at startup, not one to inherit from a file.
``--host`` and ``--port`` remain as explicit overrides for odd cases.

``--with-poller`` additionally runs a job poller inside this process,
for single-machine use where the rendezvous and the execution host are
the same box. It changes the security posture: the server host becomes
the execution target, and whoever holds the token can run commands
here. Without the flag the server only ever relays; execution requires
someone to deliberately start ``musings-poller`` elsewhere.

Transport follows the scheme of ``MUSINGS_URL``: ``https`` makes the
server terminate TLS with ``MUSINGS_TLS_CERT`` and ``MUSINGS_TLS_KEY``,
and ``MUSINGS_TLS_CLIENT_CA`` additionally demands a client certificate
on every connection. Listening beyond loopback over plain ``http`` is
refused unless ``--insecure`` is also given, so a bearer token crossing
a network in the clear is, like execution, something typed on purpose.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
from urllib.parse import urlparse

import httpx
import uvicorn
from fastapi import FastAPI

from app import __version__
from app.api.v1.router import api_router
from app.api.v1.routes.system import health_get
from app.core.config import LOOPBACK_HOSTS, TlsConfigError, settings_get
from client.transport import client_build

logging.basicConfig(level=logging.INFO)


def app_create() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        The configured application.
    """
    app = FastAPI(title="musings", version=__version__)
    app.include_router(api_router, prefix="/api/v1")
    app.get("/")(health_get)
    return app


app = app_create()


def host_isLoopback(host: str) -> bool:
    """Report whether a host name resolves only to loopback addresses.

    Args:
        host: Host name or address as written in a URL.

    Returns:
        True for the literal loopback names and for any name whose
        every resolved address is loopback (an ``/etc/hosts`` entry
        pointing a public name at this machine, for instance); False
        otherwise, and False when the name does not resolve at all.
    """
    if host in LOOPBACK_HOSTS:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    addresses = {info[4][0] for info in infos}
    return bool(addresses) and all(
        address.startswith("127.") or address == "::1" for address in addresses
    )


def pollerUrl_derive(musingsUrl: str | None, port: int, scheme: str) -> str:
    """Pick the address the embedded poller dials.

    The poller shares the server's process, so it should always reach
    the server over loopback and never through the network in front
    of it, which fails on routers without NAT loopback. Under a public
    certificate, though, the hostname matters: a certificate issued
    for ``musings.example.org`` does not verify against ``127.0.0.1``.
    So when ``MUSINGS_URL`` names a host that resolves to loopback on
    this machine (typically via an ``/etc/hosts`` entry), the poller
    keeps that hostname; otherwise it dials ``127.0.0.1``.

    Args:
        musingsUrl: The configured ``MUSINGS_URL``, or None.
        port: Port the server listens on.
        scheme: ``"http"`` or ``"https"``.

    Returns:
        The base URL for the embedded poller, ending in ``/api/v1``.
    """
    host = "127.0.0.1"
    if musingsUrl is not None:
        urlHost = urlparse(musingsUrl).hostname
        if urlHost and host_isLoopback(urlHost):
            host = urlHost
    return f"{scheme}://{host}:{port}/api/v1"


def poller_thread_start(port: int, scheme: str) -> threading.Thread:
    """Start an in-process job poller against this server.

    The poller runs in a daemon thread, so it dies with the server
    process, and it dials the server over HTTP exactly as an external
    ``musings-poller`` would: same claim endpoint, same presence
    stamp, same atomic claiming. It dials loopback on the bound port
    (see ``pollerUrl_derive`` for how the hostname is chosen), never a
    public address: it shares the server's process, so loopback is
    always reachable, whereas the network in front of the server fails
    on routers without NAT loopback. It waits for the server
    to answer its health route before entering the poll loop, so
    startup order does not matter.

    Under TLS the poller is a client like any other: it verifies the
    server against ``MUSINGS_CA_CERT`` and, when the server demands a
    client certificate, presents ``MUSINGS_CLIENT_CERT``. Those are
    taken from the settings so a ``.env``-configured server needs no
    exports, and the server certificate must therefore name
    ``127.0.0.1`` among its subject alternative names.

    Args:
        port: Port the server listens on.
        scheme: ``"http"`` or ``"https"``, matching how the server listens.

    Returns:
        The started daemon thread.
    """
    settings = settings_get()
    # The poller reads its target, token, and trust files from the
    # environment; the URL is forced to loopback for the reason above.
    os.environ.setdefault("MUSINGS_TOKEN", settings.musingsToken)
    for name, path in (
        ("MUSINGS_CA_CERT", settings.caCertFile),
        ("MUSINGS_CLIENT_CERT", settings.clientCertFile),
        ("MUSINGS_CLIENT_KEY", settings.clientKeyFile),
    ):
        if path is not None:
            os.environ.setdefault(name, str(path))
    os.environ["MUSINGS_URL"] = pollerUrl_derive(settings.musingsUrl, port, scheme)

    def poller_delayed_run() -> None:
        """Wait for the server to come up, then run the poll loop."""
        # Imported here so the server does not pull hostlib unless the
        # flag is used.
        from client.poller import poller_run

        deadline = time.monotonic() + 15.0
        with client_build(timeout=2.0) as client:
            while time.monotonic() < deadline:
                try:
                    client.get("/health")
                    break
                except httpx.HTTPError:
                    time.sleep(0.25)
        poller_run([])

    thread = threading.Thread(
        target=poller_delayed_run, name="musings-poller", daemon=True
    )
    thread.start()
    return thread


def server_run(argv: list[str] | None = None) -> None:
    """Run the musings server with uvicorn.

    Args:
        argv: Argument list; None means sys.argv.
    """
    parser = argparse.ArgumentParser(description="Run the musings server.")
    parser.add_argument(
        "--public",
        action="store_true",
        help="Listen on every interface (0.0.0.0) instead of loopback only, "
        "so a port forward or LAN client can reach the server.",
    )
    parser.add_argument(
        "--host", default=None, help="Explicit interface to bind; overrides --public."
    )
    parser.add_argument(
        "--port", type=int, default=None, help="Explicit port; overrides MUSINGS_URL."
    )
    parser.add_argument(
        "--with-poller",
        action="store_true",
        help="Also run a job poller in this process: commands submitted "
        "to this server will execute on this host, as this user.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Allow listening beyond loopback over plain http, sending the "
        "bearer token in the clear. Refused otherwise.",
    )
    args = parser.parse_args(argv)
    settings = settings_get()
    host = args.host or ("0.0.0.0" if args.public else "127.0.0.1")
    port = args.port or settings.port_derive()
    try:
        tlsConfig = settings.tlsServer_config()
    except TlsConfigError as error:
        print(f"musings-server: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    if tlsConfig is None and host not in LOOPBACK_HOSTS and not args.insecure:
        print(
            "musings-server: refusing to listen on "
            f"{host} over plain http; the bearer token would cross the network "
            "in the clear. Make MUSINGS_URL https (see scripts/pki_make.sh), or "
            "pass --insecure to accept that on purpose.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    scheme = "https" if tlsConfig is not None else "http"
    if args.with_poller:
        poller_thread_start(port, scheme)
    tlsKwargs = tlsConfig.uvicornKwargs_get() if tlsConfig is not None else {}
    uvicorn.run(app, host=host, port=port, **tlsKwargs)
