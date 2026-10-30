"""Tests for the rush rendezvous store."""

from __future__ import annotations

import asyncio
import json

from app.models.rush import RushChunk, RushResult
from app.services.rush_store import (
    NoExecutorError,
    RushStore,
    UnclaimedSubmissionError,
    UnknownSubmissionError,
)
import pytest


def test_submission_without_executor_fails_fast() -> None:
    """Submission creation fails fast when nobody is attached."""
    store = RushStore()
    with pytest.raises(NoExecutorError):
        store.submission_create("ls", "")


def test_claim_empty_returns_none() -> None:
    """Claiming with nothing pending returns None after the wait."""
    store = RushStore(nextWaitSeconds=0.1)
    assert asyncio.run(store.submission_claim("e1")) is None


def test_full_flow_streams_chunks_then_envelope() -> None:
    """Claim, chunk, result, and the stream yields them in order."""
    store = RushStore()

    async def flow():
        store.executor_touch("e1")
        record = store.submission_create("echo hi", "")
        submission = await store.submission_claim("e1")
        assert submission.submissionId == record.submission.submissionId
        streamTask = asyncio.create_task(
            collect_lines(store, record.submission.submissionId)
        )
        await asyncio.sleep(0)
        store.chunk_post(
            record.submission.submissionId, RushChunk(stream="stdout", data="hi\n")
        )
        store.result_post(
            record.submission.submissionId,
            RushResult(status=True, returncode=0, stdout="hi\n", stderr=""),
        )
        return await streamTask

    lines = asyncio.run(flow())
    assert len(lines) == 2
    assert lines[0]["stream"] == "stdout"
    assert lines[0]["data"] == "hi\n"
    assert lines[1]["type"] == "result"
    assert lines[1]["returncode"] == 0


async def collect_lines(store: RushStore, submissionId: str) -> list[dict]:
    """Drain a stream into parsed JSON lines.

    Args:
        store: Store holding the stream.
        submissionId: Submission to drain.

    Returns:
        Parsed lines in arrival order.
    """
    lines = []
    async for line in store.stream_lines(submissionId):
        lines.append(json.loads(line))
    return lines


def test_chunk_and_result_require_claim() -> None:
    """Chunks and results for unclaimed submissions are rejected."""
    store = RushStore()
    store.executor_touch("e1")
    record = store.submission_create("ls", "")
    with pytest.raises(UnclaimedSubmissionError):
        store.chunk_post(
            record.submission.submissionId, RushChunk(stream="stdout", data="x")
        )
    with pytest.raises(UnclaimedSubmissionError):
        store.result_post(
            record.submission.submissionId,
            RushResult(status=True, returncode=0, stdout="", stderr=""),
        )


def test_chunk_unknown_raises() -> None:
    """Chunks for unknown submissions are rejected."""
    store = RushStore()
    with pytest.raises(UnknownSubmissionError):
        store.chunk_post("nope", RushChunk(stream="stdout", data="x"))


def test_disconnected_long_poll_does_not_claim() -> None:
    """A long-poll whose executor dropped leaves the submission for a live one."""
    store = RushStore(presenceWindowSeconds=60.0, claimTimeoutSeconds=5.0, nextWaitSeconds=1.0)
    store.executor_touch("dead")
    record = store.submission_create("pwd", "")

    async def gone() -> bool:
        return True

    async def alive() -> bool:
        return False

    async def scenario() -> tuple[object, object]:
        first = await store.submission_claim("dead", gone)
        second = await store.submission_claim("live", alive)
        return first, second

    first, second = asyncio.run(scenario())
    assert first is None
    assert second is not None
    assert second.submissionId == record.submission.submissionId
    assert record.executorId == "live"
