"""In-memory job store.

Jobs live only in process memory in v1: the service is mortal by
design, and its job queue dies with it. Persistent job history is a
roadmap item. All operations are guarded by an asyncio lock because
routes run concurrently.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.models.job import Job, JobCreate, JobResult, JobState


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

    def __init__(self) -> None:
        """Initialize an empty store."""
        self.jobs: dict[str, JobRecord] = {}
        self.lock = asyncio.Lock()

    async def job_create(self, payload: JobCreate) -> Job:
        """Create a queued job.

        Args:
            payload: Submission payload.

        Returns:
            The created job.
        """
        async with self.lock:
            record = JobRecord(
                jobId=uuid.uuid4().hex,
                command=payload.command,
                description=payload.description,
            )
            self.jobs[record.jobId] = record
            return record_to_job(record)

    async def job_claim_next(self) -> Job | None:
        """Claim the oldest queued job.

        Claiming is atomic: the job moves to ``claimed`` before it is
        returned, so two pollers can never execute the same job.

        Returns:
            The claimed job, or None when the queue is empty.
        """
        async with self.lock:
            for record in self.jobs.values():
                if record.state == JobState.QUEUED:
                    record.state = JobState.CLAIMED
                    record.claimedAt = datetime.now(timezone.utc)
                    return record_to_job(record)
            return None

    async def job_get(self, jobId: str) -> Job | None:
        """Fetch one job by id.

        Args:
            jobId: Job identifier.

        Returns:
            The job, or None when unknown.
        """
        async with self.lock:
            record = self.jobs.get(jobId)
            return record_to_job(record) if record is not None else None

    async def job_list(self) -> list[Job]:
        """List all jobs in submission order.

        Returns:
            Every job known to this store.
        """
        async with self.lock:
            return [record_to_job(record) for record in self.jobs.values()]

    async def job_count_finished(self) -> int:
        """Count jobs that reached a terminal state.

        Returns:
            Number of succeeded plus failed jobs.
        """
        async with self.lock:
            return sum(
                1
                for record in self.jobs.values()
                if record.state in (JobState.SUCCEEDED, JobState.FAILED)
            )

    async def result_submit(self, jobId: str, result: JobResult) -> Job | None:
        """Attach a result to a claimed job and close it.

        Args:
            jobId: Job identifier.
            result: Execution result posted by the poller.

        Returns:
            The finished job, or None when the job is unknown or is
            not in the claimed state.
        """
        async with self.lock:
            record = self.jobs.get(jobId)
            if record is None or record.state != JobState.CLAIMED:
                return None
            record.result = result
            record.state = (
                JobState.SUCCEEDED if result.exitCode == 0 else JobState.FAILED
            )
            return record_to_job(record)


JOB_STORE = JobStore()


def store_get() -> JobStore:
    """Return the process-wide job store (FastAPI dependency).

    Returns:
        The shared job store.
    """
    return JOB_STORE
