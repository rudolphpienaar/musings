"""Tests for the job routes."""

from __future__ import annotations


def test_submit_requires_auth(test_client) -> None:
    """Unauthenticated submission is rejected with 401."""
    response = test_client.post("/api/v1/jobs", json={"command": "echo hi"})
    assert response.status_code == 401


def test_submit_and_claim_flow(test_client, auth_headers) -> None:
    """Submit, claim, finish, and re-read a job end to end."""
    created = test_client.post(
        "/api/v1/jobs",
        json={"command": "echo hi", "description": "smoke"},
        headers=auth_headers,
    )
    assert created.status_code == 201
    jobId = created.json()["jobId"]
    assert created.json()["state"] == "queued"

    claimed = test_client.get("/api/v1/jobs/pending", headers=auth_headers)
    assert claimed.status_code == 200
    assert claimed.json()["jobId"] == jobId
    assert claimed.json()["state"] == "claimed"

    empty = test_client.get("/api/v1/jobs/pending", headers=auth_headers)
    assert empty.status_code == 204

    finished = test_client.post(
        f"/api/v1/jobs/{jobId}/result",
        json={
            "exitCode": 0,
            "stdout": "hi\n",
            "stderr": "",
            "truncated": False,
            "startedAt": "2026-10-28T12:00:00+00:00",
            "finishedAt": "2026-10-28T12:00:01+00:00",
        },
        headers=auth_headers,
    )
    assert finished.status_code == 200
    assert finished.json()["state"] == "succeeded"
    assert finished.json()["result"]["stdout"] == "hi\n"

    fetched = test_client.get(f"/api/v1/jobs/{jobId}", headers=auth_headers)
    assert fetched.json()["state"] == "succeeded"


def test_result_for_unknown_job_is_404(test_client, auth_headers) -> None:
    """Posting a result for an unknown job is a 404."""
    response = test_client.post(
        "/api/v1/jobs/nope/result",
        json={
            "exitCode": 0,
            "stdout": "",
            "stderr": "",
            "truncated": False,
            "startedAt": "2026-10-28T12:00:00+00:00",
            "finishedAt": "2026-10-28T12:00:01+00:00",
        },
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_result_for_unclaimed_job_is_409(test_client, auth_headers) -> None:
    """Posting a result for a merely queued job is a 409."""
    created = test_client.post(
        "/api/v1/jobs", json={"command": "echo hi"}, headers=auth_headers
    )
    jobId = created.json()["jobId"]
    response = test_client.post(
        f"/api/v1/jobs/{jobId}/result",
        json={
            "exitCode": 0,
            "stdout": "",
            "stderr": "",
            "truncated": False,
            "startedAt": "2026-10-28T12:00:00+00:00",
            "finishedAt": "2026-10-28T12:00:01+00:00",
        },
        headers=auth_headers,
    )
    assert response.status_code == 409
