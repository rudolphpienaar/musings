"""Tests for health and termination routes."""

from __future__ import annotations

from app.services import termination


def test_health_is_public(test_client) -> None:
    """Health needs no token."""
    response = test_client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["service"] == "musings"
    assert response.json()["status"] == "ok"


def test_terminate_writes_eot_without_exiting(
    test_client, auth_headers, tmp_path, monkeypatch
) -> None:
    """Terminate writes the EOT marker and schedules the hook.

    The shutdown hook is replaced with a recorder so the test process
    survives the termination request.
    """
    calls: list[None] = []
    monkeypatch.setattr(termination, "shutdown_hook", lambda: calls.append(None))

    test_client.post(
        "/api/v1/save",
        json={"dir": "", "file": "a.txt", "content": "hi"},
        headers=auth_headers,
    )
    response = test_client.post(
        "/api/v1/terminate", json={"reason": "test done"}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["eotPath"] == "EOT"
    assert calls == [None]

    eotPath = tmp_path / "EOT"
    assert eotPath.exists()
    assert "test done" in eotPath.read_text()
    assert "a.txt" in eotPath.read_text()


def test_terminate_requires_auth(test_client) -> None:
    """Unauthenticated termination is rejected with 401."""
    response = test_client.post("/api/v1/terminate", json={})
    assert response.status_code == 401
