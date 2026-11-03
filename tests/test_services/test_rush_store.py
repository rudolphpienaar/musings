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


def test_executor_status_tracks_last_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Status reports the newest executor and whether it is within the window."""
    store = RushStore(presenceWindowSeconds=10.0)
    assert store.executor_status().attached is False
    assert store.executor_status().lastSeenSecondsAgo is None
    clock = [1000.0]
    monkeypatch.setattr("app.services.rush_store.time.monotonic", lambda: clock[0])
    store.executor_touch("muse-1")
    status = store.executor_status()
    assert status.attached is True and status.executorId == "muse-1"
    clock[0] += 11.0
    status = store.executor_status()
    assert status.attached is False
    assert status.lastSeenSecondsAgo == pytest.approx(11.0)
    assert "last seen 11s ago" in store.absence_describe()


def test_executor_attach_and_loss_are_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The server log carries the executor timeline, once per transition."""
    store = RushStore(presenceWindowSeconds=10.0)
    clock = [1000.0]
    monkeypatch.setattr("app.services.rush_store.time.monotonic", lambda: clock[0])
    with caplog.at_level("INFO", logger="musings.rush"):
        store.executor_touch("muse-1")
        store.executor_touch("muse-1")
        clock[0] += 11.0
        store.presence_sweep()
        store.presence_sweep()
        clock[0] += 1.0
        store.executor_touch("muse-1")
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "rush executor muse-1 attached",
        "rush executor muse-1 lost; last poll 11s ago",
        "rush executor muse-1 attached",
    ]


def test_seconds_describe_is_coarse() -> None:
    """Durations render in the unit a human would use."""
    from app.services.rush_store import seconds_describe

    assert seconds_describe(42.7) == "42s"
    assert seconds_describe(420.0) == "7m"
    assert seconds_describe(3 * 3600 + 12 * 60 + 5) == "3h12m"
