"""Aggregate router for API v1."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import files, jobs, messages, rcli, system

api_router = APIRouter()
api_router.include_router(jobs.router)
api_router.include_router(files.router)
api_router.include_router(messages.router)
api_router.include_router(system.router)
api_router.include_router(rcli.router)
