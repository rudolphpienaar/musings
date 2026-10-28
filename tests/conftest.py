"""Shared fixtures: a fresh app per test with isolated stores."""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

os.environ["MUSINGS_TOKEN"] = "test-token-123"

from app.core.config import settings_get
from app.main import app_create
from app.services.file_store import FileStore
from app.services.file_store import file_store_get
from app.services.job_store import JobStore, store_get
from app.services.message_store import MessageStore, message_store_get
from app.services.rcli_store import RcliStore, rcli_store_get


@pytest.fixture
def test_client(tmp_path) -> Generator[TestClient, None, None]:
    """Yield a TestClient bound to fresh, isolated stores.

    Args:
        tmp_path: Pytest temporary directory, used as the file base.

    Yields:
        A test client for a freshly built app.
    """
    jobStore = JobStore()
    messageStore = MessageStore()
    fileStore = FileStore(tmp_path)
    rcliStore = RcliStore(
        presenceWindowSeconds=60.0,
        claimTimeoutSeconds=2.0,
        nextWaitSeconds=0.5,
    )
    app = app_create()
    app.dependency_overrides[store_get] = lambda: jobStore
    app.dependency_overrides[message_store_get] = lambda: messageStore
    app.dependency_overrides[file_store_get] = lambda: fileStore
    app.dependency_overrides[rcli_store_get] = lambda: rcliStore
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Return the bearer auth header for the test token.

    Returns:
        Authorization header dict.
    """
    return {"Authorization": "Bearer test-token-123"}


@pytest.fixture
def settings() -> object:
    """Return the cached test settings.

    Returns:
        The settings instance.
    """
    return settings_get()
