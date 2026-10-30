"""Tests for the rush client's honest working-directory handling."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from client.rush import CaptureResult, cd_remote, command_capture, cwd_discover, cwd_join


def test_cwd_join_absolute_and_relative() -> None:
    """Candidates are normalized paths; nothing is clamped client-side."""
    assert cwd_join("/home/me", "work") == "/home/me/work"
    assert cwd_join("/home/me/work", "..") == "/home/me"
    assert cwd_join("/home/me", "../..") == "/"
    assert cwd_join("/home/me", "/etc") == "/etc"
    assert cwd_join("/home/me", "./a/../b") == "/home/me/b"
    assert cwd_join("", "work") == "work"


@dataclass
class StreamStub:
    """Stand-in for an ``httpx.Client`` whose ``/rush`` stream is scripted.

    Attributes:
        lines: JSON items to stream back, in order.
        status: HTTP status to answer with.
        posted: Every (command, cwd) pair submitted, for assertions.
    """

    lines: list[dict]
    status: int = 200
    posted: list[tuple[str, str]] = field(default_factory=list)

    def stream(self, method: str, path: str, json: dict) -> "StreamStub":
        self.posted.append((json["command"], json["cwd"]))
        return self

    def __enter__(self) -> "StreamStub":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    @property
    def status_code(self) -> int:
        return self.status

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        for item in self.lines:
            yield json.dumps(item)


def result_item(status: bool, errors: list[str] | None = None) -> dict:
    """Build a result envelope item."""
    return {
        "type": "result",
        "status": status,
        "returncode": 0 if status else 1,
        "stdout": "",
        "stderr": "",
        "errors": errors or [],
    }


def test_command_capture_collects_streams_and_result() -> None:
    """Chunks are gathered per stream and the envelope sets the outcome."""
    stub = StreamStub(
        [
            {"stream": "stdout", "data": "/home/muse\n"},
            {"stream": "stderr", "data": "warn"},
            result_item(True),
        ]
    )
    captured = command_capture(stub, "pwd", "")  # type: ignore[arg-type]
    assert captured == CaptureResult(ok=True, stdout="/home/muse\n", stderr="warn", errors=[])


def test_cwd_discover_uses_pwd_in_the_base() -> None:
    """Startup asks the executor where it is, with an empty cwd."""
    stub = StreamStub([{"stream": "stdout", "data": "/home/muse\n"}, result_item(True)])
    assert cwd_discover(stub) == "/home/muse"  # type: ignore[arg-type]
    assert stub.posted == [("pwd", "")]


def test_cwd_discover_without_executor_is_unknown(capsys) -> None:
    """No executor means no directory, and the user is told."""
    stub = StreamStub([], status=503)
    assert cwd_discover(stub) == ""  # type: ignore[arg-type]
    assert "No executor attached" in capsys.readouterr().err


def test_cd_remote_moves_to_the_path_the_executor_reports() -> None:
    """A successful probe sends the candidate as cwd and adopts the real path."""
    stub = StreamStub([{"stream": "stdout", "data": "/srv/data\n"}, result_item(True)])
    assert cd_remote(stub, "/home/muse", "/srv/link") == "/srv/data"  # type: ignore[arg-type]
    assert stub.posted == [("pwd", "/srv/link")]


def test_cd_remote_keeps_cwd_when_executor_refuses(capsys) -> None:
    """A clamped executor's refusal is shown and the prompt does not move."""
    stub = StreamStub([result_item(False, ["Working directory escapes base."])])
    assert cd_remote(stub, "/work", "..") == "/work"  # type: ignore[arg-type]
    assert stub.posted == [("pwd", "/")]
    assert "escapes base" in capsys.readouterr().err


def test_cd_remote_bare_cd_returns_to_base() -> None:
    """A bare ``cd`` probes with an empty cwd, which the executor maps to its base."""
    stub = StreamStub([{"stream": "stdout", "data": "/work\n"}, result_item(True)])
    assert cd_remote(stub, "/work/deep", None) == "/work"  # type: ignore[arg-type]
    assert stub.posted == [("pwd", "")]
