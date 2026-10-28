"""Tests for the poller's hostlib execution backend."""

from __future__ import annotations

from client.poller import job_execute, output_truncate


def test_output_truncate_models_kept_text_and_flag() -> None:
    """Truncate returns a model, not a tuple, in both cases."""
    kept = output_truncate("hi", 100000)
    assert kept.text == "hi"
    assert kept.truncated is False
    cut = output_truncate("hello", 3)
    assert cut.text == "hel"
    assert cut.truncated is True


def test_job_execute_echo() -> None:
    """A trivial command returns its output and exit code 0."""
    result = job_execute("echo hi", 10, 100000)
    assert result["exitCode"] == 0
    assert result["stdout"] == "hi\n"
    assert result["truncated"] is False


def test_job_execute_failure() -> None:
    """A failing command returns its exit code."""
    result = job_execute("false", 10, 100000)
    assert result["exitCode"] == 1


def test_job_execute_timeout_maps_to_124() -> None:
    """A timed-out command maps to exit code 124 with a note."""
    result = job_execute("sleep 5", 1, 100000)
    assert result["exitCode"] == 124
    assert "timed out" in result["stderr"]
