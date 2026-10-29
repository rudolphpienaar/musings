"""In-memory job store.

Jobs live only in process memory in v1: the service is mortal by
design, and its job queue dies with it. Persistent job history is a
roadmap item. All operations are guarded by an asyncio condition
because routes run concurrently; the condition doubles as the wake-up
signal for long-polling claims and result waits, so a poller parked on
``job_claim_next`` wakes the moment a job is created, and a client
parked on ``job_wait`` wakes the moment its result is posted.

The store also tracks poller presence the way the rush store tracks
executor presence: every claim poll stamps a last-seen time, and
``poller_status`` reports whether that stamp falls within the presence
window. The jobs API stays fire-and-forget (a submission without a
poller still queues), so presence is advisory, surfaced to clients
like mush that want to warn a human instead of waiting silently.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.models.job import Job, JobCreate, JobResult, JobState, PollerStatus


@dataclass
class JobRecord:
    """Internal mutable record for one job.

    Attributes:
        jobId: Unique job identifier.
        command: Shell command to execute.
        description: Human-readable note about the job.
        state: Current lifecycle state.
        createdAt: When the job was submitted.
        claimedAt: When the poller claimed the job, if ever.
        result: Execution result, once finished.
    """

    jobId: str
    command: str
    description: str
    state: JobState = JobState.QUEUED
    createdAt: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    claimedAt: datetime | None = None
    result: JobResult | None = None


def record_to_job(record: JobRecord) -> Job:
    """Convert an internal record to its API model.

    Args:
        record: Internal job record.

    Returns:
        The public job model.
    """
    return Job(
        jobId=record.jobId,
        command=record.command,
        description=record.description,
        state=record.state,
        createdAt=record.createdAt,
        claimedAt=record.claimedAt,
        result=record.result,
    )


class JobStore:
    """Thread of record for jobs: creation, claiming, and results."""

    def __init__(self, presenceWindowSeconds: float = 60.0) -> None:
        """Initialize an empty store.

        Args:
            presenceWindowSeconds: How recently a poller must have
                polled ``/jobs/pending`` to count as attached.
        """
        self.jobs: dict[str, JobRecord] = {}
        self.cond = asyncio.Condition()
        self.presenceWindowSeconds = presenceWindowSeconds
        self._pollerSeenAt: float | None = None

    def poller_touch(self) -> None:
        """Record a claim poll, marking a poller as present."""
        self._pollerSeenAt = time.monotonic()

    def poller_status(self) -> PollerStatus:
        """Report whether a poller has polled recently.

        Returns:
            Attachment status with seconds since the last claim poll;
            ``lastSeenSecondsAgo`` is None when no poller ever polled.
        """
        if self._pollerSeenAt is None:
            return PollerStatus(attached=False, lastSeenSecondsAgo=None)
        elapsed = time.monotonic() - self._pollerSeenAt
        return PollerStatus(
            attached=elapsed <= self.presenceWindowSeconds,
            lastSeenSecondsAgo=elapsed,
        )

    async def job_create(self, payload: JobCreate) -> Job:
        """Create a queued job and wake any waiting claim.

        Args:
            payload: Submission payload.

        Returns:
            The created job.
        """
        async with self.cond:
            record = JobRecord(
                jobId=uuid.uuid4().hex,
                command=payload.command,
                description=payload.description,
            )
            self.jobs[record.jobId] = record
            self.cond.notify_all()
            return record_to_job(record)

    async def job_claim_next(self, waitSeconds: float = 0.0) -> Job | None:
        """Claim the oldest queued job, waiting for one if asked.

        Claiming is atomic: the job moves to ``claimed`` before it is
        returned, so two pollers can never execute the same job. With
        a positive ``waitSeconds`` the call parks on the store
        condition and wakes the moment a job is created, so a
        long-polling poller claims new work immediately instead of on
        its next scheduled visit.

        Args:
            waitSeconds: How long to wait for a job when the queue is
                empty; 0 preserves the original immediate behavior.

        Returns:
            The claimed job, or None when the queue stayed empty for
            the whole wait.
        """
        deadline = time.monotonic() + waitSeconds
        async with self.cond:
            while True:
                for record in self.jobs.values():
                    if record.state == JobState.QUEUED:
                        record.state = JobState.CLAIMED
                        record.claimedAt = datetime.now(timezone.utc)
                        return record_to_job(record)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    await asyncio.wait_for(self.cond.wait(), timeout=remaining)
                except TimeoutError:
                    continue

    async def job_get(self, jobId: str) -> Job | None:
        """Fetch one job by id.

        Args:
            jobId: Job identifier.

        Returns:
            The job, or None when unknown.
        """
        async with self.cond:
            record = self.jobs.get(jobId)
            return record_to_job(record) if record is not None else None

    async def job_wait(self, jobId: str, waitSeconds: float) -> Job | None:
        """Fetch one job, waiting for it to reach a terminal state.

        The call parks on the store condition and wakes when a result
        is posted, so a client waiting on output sees it the moment it
        lands instead of on its next poll. When the wait expires first
        the job is returned in its current state, so the caller can
        tell "still queued" from "unknown".

        Args:
            jobId: Job identifier.
            waitSeconds: Longest time to wait for a terminal state.

        Returns:
            The job (terminal or not), or None when unknown.
        """
        deadline = time.monotonic() + waitSeconds
        async with self.cond:
            while True:
                record = self.jobs.get(jobId)
                if record is None:
                    return None
                if record.state in (JobState.SUCCEEDED, JobState.FAILED):
                    return record_to_job(record)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return record_to_job(record)
                try:
                    await asyncio.wait_for(self.cond.wait(), timeout=remaining)
                except TimeoutError:
                    continue

    async def job_list(self) -> list[Job]:
        """List all jobs in submission order.

        Returns:
            Every job known to this store.
        """
        async with self.cond:
            return [record_to_job(record) for record in self.jobs.values()]

    async def job_count_finished(self) -> int:
        """Count jobs that reached a terminal state.

        Returns:
            Number of succeeded plus failed jobs.
        """
        async with self.cond:
            return sum(
                1
                for record in self.jobs.values()
                if record.state in (JobState.SUCCEEDED, JobState.FAILED)
            )

    async def result_submit(self, jobId: str, result: JobResult) -> Job | None:
        """Attach a result to a claimed job, close it, and wake waiters.

        Args:
            jobId: Job identifier.
            result: Execution result posted by the poller.

        Returns:
            The finished job, or None when the job is unknown or is
            not in the claimed state.
        """
        async with self.cond:
            record = self.jobs.get(jobId)
            if record is None or record.state != JobState.CLAIMED:
                return None
            record.result = result
            record.state = (
                JobState.SUCCEEDED if result.exitCode == 0 else JobState.FAILED
            )
            self.cond.notify_all()
            return record_to_job(record)


JOB_STORE = JobStore()


def store_get() -> JobStore:
    """Return the process-wide job store (FastAPI dependency).

    Returns:
        The shared job store.
    """
    return JOB_STORE
