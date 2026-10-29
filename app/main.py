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
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
import time

import httpx
import uvicorn
from fastapi import FastAPI

from app import __version__
from app.api.v1.router import api_router
from app.api.v1.routes.system import health_get
from app.core.config import settings_get

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


def poller_thread_start(port: int) -> threading.Thread:
    """Start an in-process job poller against this server.

    The poller runs in a daemon thread, so it dies with the server
    process, and it dials the server over HTTP exactly as an external
    ``musings-poller`` would: same claim endpoint, same presence
    stamp, same atomic claiming. It always dials loopback on the bound
    port, never ``MUSINGS_URL``: it shares the server's process, so
    loopback is always reachable, whereas a URL naming a public
    address would route through the network in front of the server
    and fail on routers without NAT loopback. It waits for the server
    to answer its health route before entering the poll loop, so
    startup order does not matter.

    Args:
        port: Port the server listens on.

    Returns:
        The started daemon thread.
    """
    settings = settings_get()
    # The poller reads its target and token from the environment; the
    # token comes from settings so a .env-configured server needs no
    # exports, and the URL is forced to loopback for the reason above.
    os.environ.setdefault("MUSINGS_TOKEN", settings.musingsToken)
    os.environ["MUSINGS_URL"] = f"http://127.0.0.1:{port}/api/v1"
    healthUrl = os.environ["MUSINGS_URL"] + "/health"

    def poller_delayed_run() -> None:
        """Wait for the server to come up, then run the poll loop."""
        # Imported here so the server does not pull hostlib unless the
        # flag is used.
        from client.poller import poller_run

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            try:
                httpx.get(healthUrl, timeout=2.0, trust_env=False)
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
    args = parser.parse_args(argv)
    settings = settings_get()
    host = args.host or ("0.0.0.0" if args.public else "127.0.0.1")
    port = args.port or settings.port_derive()
    if args.with_poller:
        poller_thread_start(port)
    uvicorn.run(app, host=host, port=port)
