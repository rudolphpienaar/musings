"""Tests for health and EOT routes."""

from __future__ import annotations

from app.services import termination


def test_health_is_public(test_client) -> None:
    """Health needs no token."""
    response = test_client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["service"] == "musings"
    assert response.json()["status"] == "ok"


def test_eot_writes_bare_marker_without_exiting(
    test_client, auth_headers, tmp_path, monkeypatch
) -> None:
    """EOT writes a bare marker and schedules the hook.

    The shutdown hook is replaced with a recorder so the test process
    survives the termination request. The marker carries no job or
    file accounting: its presence is the signal.
    """
    calls: list[None] = []
    monkeypatch.setattr(termination, "shutdown_hook", lambda: calls.append(None))

    test_client.post(
        "/api/v1/save",
        json={"dir": "", "file": "a.txt", "content": "hi"},
        headers=auth_headers,
    )
    response = test_client.post(
        "/api/v1/eot",
        json={"confirm": "EOT", "reason": "test done"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["eotPath"] == "EOT"
    assert calls == [None]

    eotPath = tmp_path / "EOT"
    assert eotPath.exists()
    content = eotPath.read_text()
    assert "test done" in content
    assert "a.txt" not in content


def test_eot_rejects_generic_posts(test_client, auth_headers) -> None:
    """A generic POST must not terminate: the payload model guards."""
    assert test_client.post("/api/v1/eot", json={}, headers=auth_headers).status_code == 422
    assert (
        test_client.post("/api/v1/eot", json={"reason": "x"}, headers=auth_headers).status_code
        == 422
    )
    assert (
        test_client.post(
            "/api/v1/eot", json={"confirm": "yes"}, headers=auth_headers
        ).status_code
        == 422
    )


def test_eot_requires_auth(test_client) -> None:
    """Unauthenticated termination is rejected with 401."""
    response = test_client.post("/api/v1/eot", json={"confirm": "EOT"})
    assert response.status_code == 401


def test_terminate_route_is_gone(test_client, auth_headers) -> None:
    """The old manifest-writing terminate endpoint no longer exists."""
    response = test_client.post("/api/v1/terminate", json={}, headers=auth_headers)
    assert response.status_code == 404
