"""Tests for the job store's long-poll claiming, result waits, and
poller presence."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.models.job import JobCreate, JobResult, JobState
from app.services.job_store import JobStore


def result_build(exitCode: int = 0) -> JobResult:
    """Build a minimal job result for tests.

    Args:
        exitCode: Exit code to report.

    Returns:
        A result payload with fixed timestamps.
    """
    now = datetime.now(timezone.utc)
    return JobResult(exitCode=exitCode, startedAt=now, finishedAt=now)


def test_claim_immediate_empty_returns_none() -> None:
    """A zero-wait claim on an empty queue returns None at once."""
    store = JobStore()
    assert asyncio.run(store.job_claim_next()) is None


def test_claim_wait_expires_empty() -> None:
    """A waiting claim on a queue that stays empty returns None."""
    store = JobStore()
    assert asyncio.run(store.job_claim_next(waitSeconds=0.05)) is None


def test_claim_wait_wakes_on_submission() -> None:
    """A parked claim returns the job created during the wait."""
    store = JobStore()

    async def flow() -> tuple[str, str]:
        claimTask = asyncio.create_task(store.job_claim_next(waitSeconds=5.0))
        await asyncio.sleep(0.01)
        created = await store.job_create(JobCreate(command="echo hi"))
        claimed = await asyncio.wait_for(claimTask, timeout=1.0)
        assert claimed is not None
        return created.jobId, claimed.jobId

    createdId, claimedId = asyncio.run(flow())
    assert createdId == claimedId


def test_job_wait_wakes_on_result() -> None:
    """A parked result wait returns the moment the result is posted."""
    store = JobStore()

    async def flow() -> JobState:
        created = await store.job_create(JobCreate(command="echo hi"))
        claimed = await store.job_claim_next()
        assert claimed is not None
        waitTask = asyncio.create_task(store.job_wait(created.jobId, 5.0))
        await asyncio.sleep(0.01)
        await store.result_submit(created.jobId, result_build())
        finished = await asyncio.wait_for(waitTask, timeout=1.0)
        assert finished is not None
        return finished.state

    assert asyncio.run(flow()) == JobState.SUCCEEDED


def test_job_wait_timeout_returns_current_state() -> None:
    """A result wait that expires returns the job as it stands."""
    store = JobStore()

    async def flow() -> JobState:
        created = await store.job_create(JobCreate(command="echo hi"))
        job = await store.job_wait(created.jobId, 0.05)
        assert job is not None
        return job.state

    assert asyncio.run(flow()) == JobState.QUEUED


def test_job_wait_unknown_returns_none() -> None:
    """A result wait on an unknown job returns None."""
    store = JobStore()
    assert asyncio.run(store.job_wait("nope", 0.05)) is None


def test_poller_presence_window() -> None:
    """Presence is off until touched, then on within the window."""
    store = JobStore(presenceWindowSeconds=60.0)
    before = store.poller_status()
    assert before.attached is False
    assert before.lastSeenSecondsAgo is None
    store.poller_touch()
    after = store.poller_status()
    assert after.attached is True
    assert after.lastSeenSecondsAgo is not None
    assert after.lastSeenSecondsAgo < 1.0


def test_poller_presence_expires() -> None:
    """A stale touch outside the window reports detached."""
    store = JobStore(presenceWindowSeconds=0.0)
    store.poller_touch()
    status = store.poller_status()
    assert status.attached is False
    assert status.lastSeenSecondsAgo is not None
