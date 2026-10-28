"""Tests for the message routes."""

from __future__ import annotations


def test_message_post_and_list(test_client, auth_headers) -> None:
    """Posted messages come back in submission order."""
    first = test_client.post(
        "/api/v1/messages",
        json={"content": "first", "author": "rudolph"},
        headers=auth_headers,
    )
    assert first.status_code == 201
    second = test_client.post(
        "/api/v1/messages", json={"content": "second"}, headers=auth_headers
    )
    assert second.status_code == 201

    listed = test_client.get("/api/v1/messages", headers=auth_headers)
    assert [item["content"] for item in listed.json()] == ["first", "second"]
