"""Musings application entry point.

Run with uvicorn, for example::

    MUSINGS_TOKEN=<token> uvicorn app.main:app --host 127.0.0.1 --port 8000

Or with the installed console script, which reads the same
environment (``MUSINGS_HOST`` and ``MUSINGS_PORT`` override the
defaults)::

    MUSINGS_TOKEN=<token> musings-server

The token is required: the settings object raises at import time when
``MUSINGS_TOKEN`` is unset, so the service cannot start unprotected.

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


def poller_thread_start(host: str, port: int) -> threading.Thread:
    """Start an in-process job poller against this server.

    The poller runs in a daemon thread, so it dies with the server
    process, and it dials the server over HTTP exactly as an external
    ``musings-poller`` would: same claim endpoint, same presence
    stamp, same atomic claiming. It waits for the server to answer
    its health route before entering the poll loop, so startup order
    does not matter.

    Args:
        host: Interface the server binds, used to derive the poller's
            target URL when ``MUSINGS_URL`` is not already set.
        port: Port the server binds.

    Returns:
        The started daemon thread.
    """
    settings = settings_get()
    # The poller reads its target and token from the environment; fill
    # them from settings so a .env-configured server needs no exports.
    os.environ.setdefault("MUSINGS_TOKEN", settings.musingsToken)
    connectHost = "127.0.0.1" if host == "0.0.0.0" else host
    os.environ.setdefault("MUSINGS_URL", f"http://{connectHost}:{port}/api/v1")
    healthUrl = os.environ["MUSINGS_URL"].rstrip("/") + "/health"

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
    parser.add_argument("--host", default=None, help="Interface to bind.")
    parser.add_argument("--port", type=int, default=None, help="Port to bind.")
    parser.add_argument(
        "--with-poller",
        action="store_true",
        help="Also run a job poller in this process: commands submitted "
        "to this server will execute on this host, as this user.",
    )
    args = parser.parse_args(argv)
    settings = settings_get()
    host = args.host or settings.musingsHost
    port = args.port or settings.musingsPort
    if args.with_poller:
        poller_thread_start(host, port)
    uvicorn.run(app, host=host, port=port)
