"""Rcli rendezvous store: pair submitters with executors.

The store holds submissions, the pending queue, and executor
presence. A submitter's request stays open on a per-submission
asyncio queue; the executor claims through the pending queue and
posts chunks and the final envelope into that queue. The store
never executes anything itself. See ``docs/comms.adoc``.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from app.models.rcli import (
    RcliChunk,
    RcliError,
    RcliResult,
    RcliSubmission,
)


class NoExecutorError(Exception):
    """Raised when a submission arrives with no executor attached."""


class UnknownSubmissionError(Exception):
    """Raised when a chunk or result names an unknown submission."""


class UnclaimedSubmissionError(Exception):
    """Raised when a chunk or result names an unclaimed submission."""


def cwd_validate(cwd: str) -> str:
    """Validate and normalize a client-supplied working directory.

    Args:
        cwd: Directory relative to the executor base; empty or ``.``
            means the base itself.

    Returns:
        The normalized relative path, or ``""`` for the base itself.

    Raises:
        ValueError: When the path is absolute or escapes the base.
    """
    if not cwd or cwd == ".":
        return ""
    path = Path(cwd)
    if path.is_absolute():
        raise ValueError(f"Absolute working directory rejected: {cwd!r}.")
    parts: list[str] = []
    for part in path.parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError(f"Parent escape rejected: {cwd!r}.")
        parts.append(part)
    return "/".join(parts)


@dataclass
class RcliRecord:
    """Server-side state for one submission.

    Attributes:
        submission: The command handed to an executor.
        streamQueue: Chunks and the final envelope, in order.
        claimed: True once an executor has claimed the submission.
        executorId: Claiming executor, if any.
        createdAt: Monotonic timestamp of submission.
    """

    submission: RcliSubmission
    streamQueue: asyncio.Queue = field(default_factory=asyncio.Queue)
    claimed: bool = False
    executorId: str | None = None
    createdAt: float = field(default_factory=time.monotonic)


class RcliStore:
    """In-memory rendezvous between submitters and executors."""

    def __init__(
        self,
        presenceWindowSeconds: float = 60.0,
        claimTimeoutSeconds: float = 60.0,
        nextWaitSeconds: float = 25.0,
    ) -> None:
        """Initialize an empty store.

        Args:
            presenceWindowSeconds: How recently an executor must have
                polled to count as attached.
            claimTimeoutSeconds: How long an unclaimed submission waits
                before its stream ends with an error.
            nextWaitSeconds: How long a ``/next`` long-poll waits.
        """
        self.presenceWindowSeconds = presenceWindowSeconds
        self.claimTimeoutSeconds = claimTimeoutSeconds
        self.nextWaitSeconds = nextWaitSeconds
        self._records: dict[str, RcliRecord] = {}
        self._pending: deque[str] = deque()
        self._executors: dict[str, float] = {}

    def executor_touch(self, executorId: str) -> None:
        """Record a poll from an executor.

        Args:
            executorId: Executor identifier from the poll request.
        """
        self._executors[executorId] = time.monotonic()

    def executor_present(self) -> bool:
        """Report whether any executor has polled recently.

        Returns:
            True when at least one executor is within the window.
        """
        now = time.monotonic()
        return any(now - seen <= self.presenceWindowSeconds for seen in self._executors.values())

    def sweep_stale(self) -> None:
        """End streams whose executor disappeared or never claimed.

        Claimed submissions whose executor has not polled within the
        presence window, and unclaimed submissions older than the
        claim timeout, receive an error line. Their records are
        dropped; a stale id still sitting in the pending queue is
        skipped when claimed.
        """
        now = time.monotonic()
        for submissionId, record in list(self._records.items()):
            if record.claimed:
                lastSeen = self._executors.get(record.executorId or "", 0.0)
                if now - lastSeen > self.presenceWindowSeconds:
                    record.streamQueue.put_nowait(
                        RcliError(errors="Executor lost").model_dump()
                    )
                    del self._records[submissionId]
            elif now - record.createdAt > self.claimTimeoutSeconds:
                record.streamQueue.put_nowait(
                    RcliError(errors="No executor claimed in time").model_dump()
                )
                del self._records[submissionId]

    def submission_create(self, command: str, cwd: str) -> RcliRecord:
        """Create a submission, or fail fast with no executor.

        Args:
            command: Command string for the executor to run.
            cwd: Normalized working directory, relative to base.

        Returns:
            The new record, already queued for claiming.

        Raises:
            NoExecutorError: When no executor has polled recently.
        """
        self.sweep_stale()
        if not self.executor_present():
            raise NoExecutorError("No executor attached.")
        submission = RcliSubmission(
            submissionId=uuid.uuid4().hex,
            command=command,
            cwd=cwd,
        )
        record = RcliRecord(submission=submission)
        self._records[submission.submissionId] = record
        self._pending.append(submission.submissionId)
        return record

    async def submission_claim(self, executorId: str) -> RcliSubmission | None:
        """Claim the next pending submission, waiting if necessary.

        Args:
            executorId: Claiming executor, recorded for presence.

        Returns:
            The claimed submission, or None when the wait expires.
        """
        self.executor_touch(executorId)
        self.sweep_stale()
        deadline = time.monotonic() + self.nextWaitSeconds
        while True:
            while self._pending:
                submissionId = self._pending.popleft()
                record = self._records.get(submissionId)
                if record is None:
                    continue
                if time.monotonic() - record.createdAt > self.claimTimeoutSeconds:
                    record.streamQueue.put_nowait(
                        RcliError(errors="No executor claimed in time").model_dump()
                    )
                    del self._records[submissionId]
                    continue
                record.claimed = True
                record.executorId = executorId
                return record.submission
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(remaining, 0.5))

    def chunk_post(self, submissionId: str, chunk: RcliChunk) -> None:
        """Append an output chunk to a submission's stream.

        Args:
            submissionId: Target submission.
            chunk: Output chunk to deliver.

        Raises:
            UnknownSubmissionError: When the submission is unknown.
            UnclaimedSubmissionError: When it was never claimed.
        """
        record = self._records.get(submissionId)
        if record is None:
            raise UnknownSubmissionError(submissionId)
        if not record.claimed:
            raise UnclaimedSubmissionError(submissionId)
        record.streamQueue.put_nowait(chunk.model_dump())

    def result_post(self, submissionId: str, result: RcliResult) -> None:
        """Close a submission's stream with the result envelope.

        Args:
            submissionId: Target submission.
            result: Final execution envelope.

        Raises:
            UnknownSubmissionError: When the submission is unknown.
            UnclaimedSubmissionError: When it was never claimed.
        """
        record = self._records.get(submissionId)
        if record is None:
            raise UnknownSubmissionError(submissionId)
        if not record.claimed:
            raise UnclaimedSubmissionError(submissionId)
        record.streamQueue.put_nowait(result.model_dump())

    async def stream_lines(self, submissionId: str):
        """Yield a submission's stream as JSON lines.

        The generator ends the stream itself when nothing claims
        the submission in time, or when a claimed executor stops
        polling: both cases yield an error line, since a stream
        ending without a result envelope means unknown outcome.

        Args:
            submissionId: Submission whose stream to drain.

        Yields:
            One JSON line per chunk, ending with the result or error
            envelope, each newline-terminated.
        """
        record = self._records.get(submissionId)
        if record is None:
            yield json.dumps(RcliError(errors="Unknown submission").model_dump()) + "\n"
            return
        while not record.claimed:
            if time.monotonic() - record.createdAt > self.claimTimeoutSeconds:
                yield json.dumps(
                    RcliError(errors="No executor claimed in time").model_dump()
                ) + "\n"
                self._records.pop(submissionId, None)
                return
            await asyncio.sleep(0.1)
        while True:
            try:
                item = await asyncio.wait_for(
                    record.streamQueue.get(), timeout=self.presenceWindowSeconds
                )
            except asyncio.TimeoutError:
                lastSeen = self._executors.get(record.executorId or "", 0.0)
                if time.monotonic() - lastSeen > self.presenceWindowSeconds:
                    yield json.dumps(RcliError(errors="Executor lost").model_dump()) + "\n"
                    self._records.pop(submissionId, None)
                    return
                continue
            yield json.dumps(item) + "\n"
            if item.get("type") in ("result", "error"):
                break
        self._records.pop(submissionId, None)


_RCLI_STORE: RcliStore | None = None


def rcli_store_get() -> RcliStore:
    """Return the process-wide rendezvous store.

    Returns:
        The shared store.
    """
    global _RCLI_STORE
    if _RCLI_STORE is None:
        _RCLI_STORE = RcliStore()
    return _RCLI_STORE
