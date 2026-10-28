"""Tests for the file-save route."""

from __future__ import annotations


def test_save_and_traversal(test_client, auth_headers, tmp_path) -> None:
    """Nested saves succeed; escapes are rejected with 400."""
    saved = test_client.post(
        "/api/v1/save",
        json={"dir": "job-1/output", "file": "report.txt", "content": "hello"},
        headers=auth_headers,
    )
    assert saved.status_code == 201
    assert saved.json()["path"] == "job-1/output/report.txt"
    assert saved.json()["bytesWritten"] == 5

    escape = test_client.post(
        "/api/v1/save",
        json={"dir": "../escape", "file": "evil.txt", "content": "x"},
        headers=auth_headers,
    )
    assert escape.status_code == 400

    absolute = test_client.post(
        "/api/v1/save",
        json={"dir": "/tmp", "file": "evil.txt", "content": "x"},
        headers=auth_headers,
    )
    assert absolute.status_code == 400

    separator = test_client.post(
        "/api/v1/save",
        json={"dir": "", "file": "sub/evil.txt", "content": "x"},
        headers=auth_headers,
    )
    assert separator.status_code == 400


def test_save_requires_auth(test_client) -> None:
    """Unauthenticated saves are rejected with 401."""
    response = test_client.post(
        "/api/v1/save", json={"dir": "", "file": "a.txt", "content": "x"}
    )
    assert response.status_code == 401
