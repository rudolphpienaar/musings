"""Tests for rush routes: auth, validation, fail-fast, long-poll."""

from __future__ import annotations


def test_rush_requires_auth(test_client) -> None:
    """Unauthenticated submission is rejected with 401."""
    response = test_client.post("/api/v1/rush", json={"command": "ls"})
    assert response.status_code == 401


def test_rush_rejects_bad_cwd(test_client, auth_headers) -> None:
    """Absolute or escaping working directories are rejected."""
    response = test_client.post(
        "/api/v1/rush", json={"command": "ls", "cwd": "/etc"}, headers=auth_headers
    )
    assert response.status_code == 400
    response = test_client.post(
        "/api/v1/rush", json={"command": "ls", "cwd": "../x"}, headers=auth_headers
    )
    assert response.status_code == 400


def test_rush_rejects_empty_command(test_client, auth_headers) -> None:
    """An empty command string fails validation."""
    response = test_client.post("/api/v1/rush", json={"command": ""}, headers=auth_headers)
    assert response.status_code == 422


def test_rush_fails_fast_without_executor(test_client, auth_headers) -> None:
    """A submission with no executor attached fails fast, not hangs."""
    response = test_client.post(
        "/api/v1/rush", json={"command": "ls"}, headers=auth_headers
    )
    assert response.status_code == 503


def test_next_empty_returns_204(test_client, auth_headers) -> None:
    """Long-poll with nothing pending returns 204 when it expires."""
    response = test_client.get("/api/v1/rush/next", headers=auth_headers)
    assert response.status_code == 204


def test_chunk_and_result_unknown_are_404(test_client, auth_headers) -> None:
    """Chunks and results for unknown submissions are rejected."""
    response = test_client.post(
        "/api/v1/rush/nope/chunk",
        json={"stream": "stdout", "data": "hi"},
        headers=auth_headers,
    )
    assert response.status_code == 404
    response = test_client.post(
        "/api/v1/rush/nope/result",
        json={
            "status": True,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        },
        headers=auth_headers,
    )
    assert response.status_code == 404
