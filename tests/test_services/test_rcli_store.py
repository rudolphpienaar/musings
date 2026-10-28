"""Tests for the rcli rendezvous store."""

from __future__ import annotations

import asyncio
import json

from app.models.rcli import RcliChunk, RcliResult
from app.services.rcli_store import (
    NoExecutorError,
    RcliStore,
    UnclaimedSubmissionError,
    UnknownSubmissionError,
    cwd_validate,
)
import pytest


def test_cwd_validate() -> None:
    """Working directories normalize, and escapes are rejected."""
    assert cwd_validate("") == ""
    assert cwd_validate(".") == ""
    assert cwd_validate("a/b") == "a/b"
    with pytest.raises(ValueError):
        cwd_validate("/etc")
    with pytest.raises(ValueError):
        cwd_validate("../x")
    with pytest.raises(ValueError):
        cwd_validate("a/../b")


def test_submission_without_executor_fails_fast() -> None:
    """Submission creation fails fast when nobody is attached."""
    store = RcliStore()
    with pytest.raises(NoExecutorError):
        store.submission_create("ls", "")


def test_claim_empty_returns_none() -> None:
    """Claiming with nothing pending returns None after the wait."""
    store = RcliStore(nextWaitSeconds=0.1)
    assert asyncio.run(store.submission_claim("e1")) is None


def test_full_flow_streams_chunks_then_envelope() -> None:
    """Claim, chunk, result, and the stream yields them in order."""
    store = RcliStore()

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
            record.submission.submissionId, RcliChunk(stream="stdout", data="hi\n")
        )
        store.result_post(
            record.submission.submissionId,
            RcliResult(status=True, returncode=0, stdout="hi\n", stderr=""),
        )
        return await streamTask

    lines = asyncio.run(flow())
    assert len(lines) == 2
    assert lines[0]["stream"] == "stdout"
    assert lines[0]["data"] == "hi\n"
    assert lines[1]["type"] == "result"
    assert lines[1]["returncode"] == 0


async def collect_lines(store: RcliStore, submissionId: str) -> list[dict]:
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
    store = RcliStore()
    store.executor_touch("e1")
    record = store.submission_create("ls", "")
    with pytest.raises(UnclaimedSubmissionError):
        store.chunk_post(
            record.submission.submissionId, RcliChunk(stream="stdout", data="x")
        )
    with pytest.raises(UnclaimedSubmissionError):
        store.result_post(
            record.submission.submissionId,
            RcliResult(status=True, returncode=0, stdout="", stderr=""),
        )


def test_chunk_unknown_raises() -> None:
    """Chunks for unknown submissions are rejected."""
    store = RcliStore()
    with pytest.raises(UnknownSubmissionError):
        store.chunk_post("nope", RcliChunk(stream="stdout", data="x"))
