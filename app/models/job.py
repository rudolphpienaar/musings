"""Job and job-result models.

A job is a shell command submitted by an external entity, claimed by
the poller, executed in the agent VM, and answered with a structured
result. Job state is a small state machine: ``queued`` -> ``claimed`` ->
``succeeded`` or ``failed``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class JobState(str, Enum):
    """Lifecycle state of a job."""

    QUEUED = "queued"
    CLAIMED = "claimed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobCreate(BaseModel):
    """Payload for submitting a new job.

    Attributes:
        command: Shell command to execute, run as ``sh -c <command>``.
        description: Human-readable note about what the job is for.
        timeoutSeconds: Execution timeout; null means the server default.
    """

    command: str = Field(min_length=1)
    description: str = ""
    timeoutSeconds: int | None = None


class JobResult(BaseModel):
    """Result of an executed job, as posted by the poller.

    Attributes:
        exitCode: Process exit code; 0 means success.
        stdout: Captured standard output, possibly truncated.
        stderr: Captured standard error, possibly truncated.
        truncated: True when stdout or stderr was cut at the cap.
        startedAt: When execution started.
        finishedAt: When execution finished.
    """

    exitCode: int
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    startedAt: datetime
    finishedAt: datetime


class PollerStatus(BaseModel):
    """Whether a job poller is attached to this service.

    A poller counts as attached when it has hit ``GET /jobs/pending``
    within the server's presence window. The jobs API works without
    one (submissions queue regardless), so this is advisory: clients
    like mush read it to warn a human that nothing will execute their
    commands until a poller starts.

    Attributes:
        attached: True when a poller polled within the window.
        lastSeenSecondsAgo: Seconds since the last claim poll, or
            None when no poller has ever polled this run.
    """

    attached: bool
    lastSeenSecondsAgo: float | None = None


class Job(BaseModel):
    """A job with its current state and, once finished, its result.

    Attributes:
        jobId: Unique job identifier.
        command: Shell command to execute.
        description: Human-readable note about the job.
        state: Current lifecycle state.
        createdAt: When the job was submitted.
        claimedAt: When the poller claimed the job, if ever.
        result: Execution result, once the job has finished.
    """

    jobId: str
    command: str
    description: str
    state: JobState
    createdAt: datetime
    claimedAt: datetime | None = None
    result: JobResult | None = None
