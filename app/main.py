"""Musings application entry point.

Run with uvicorn, for example::

    MUSINGS_TOKEN=<token> uvicorn app.main:app --host 127.0.0.1 --port 8000

Or with the installed console script, which reads the same
environment (``MUSINGS_HOST`` and ``MUSINGS_PORT`` override the
defaults)::

    MUSINGS_TOKEN=<token> musings-server

The token is required: the settings object raises at import time when
``MUSINGS_TOKEN`` is unset, so the service cannot start unprotected.
"""

from __future__ import annotations

import argparse
import logging

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


def server_run(argv: list[str] | None = None) -> None:
    """Run the musings server with uvicorn.

    Args:
        argv: Argument list; None means sys.argv.
    """
    parser = argparse.ArgumentParser(description="Run the musings server.")
    parser.add_argument("--host", default=None, help="Interface to bind.")
    parser.add_argument("--port", type=int, default=None, help="Port to bind.")
    args = parser.parse_args(argv)
    settings = settings_get()
    host = args.host or settings.musingsHost
    port = args.port or settings.musingsPort
    uvicorn.run(app, host=host, port=port)
