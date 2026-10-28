"""Musings application entry point.

Run with uvicorn, for example::

    MUSINGS_TOKEN=<token> uvicorn app.main:app --host 127.0.0.1 --port 8000

The token is required: the settings object raises at import time when
``MUSINGS_TOKEN`` is unset, so the service cannot start unprotected.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app import __version__
from app.api.v1.router import api_router
from app.api.v1.routes.system import health_get

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
