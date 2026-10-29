"""Tests for mush over the job queue."""

from __future__ import annotations

import pytest

from client.mush import (
    cd_remote,
    command_wrap,
    cwd_discover,
    cwd_join,
    job_submit,
    job_wait,
    poller_check,
    result_print,
)


class ResponseStub:
    """Minimal stand-in for an httpx response."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        """No-op: stub responses are always successful."""

    def json(self) -> dict:
        """Return the stub payload."""
        return self.payload


class ClientStub:
    """Minimal stand-in for an httpx client serving canned responses."""

    def __init__(self, posts: list[dict], gets: list[dict]) -> None:
        self.posts = posts
        self.gets = gets
        self.posted: list[dict] = []
        self.getParams: list[dict | None] = []

    def post(self, path: str, json: dict) -> ResponseStub:
        """Record a post and return the next canned response."""
        self.posted.append(json)
        return ResponseStub(self.posts.pop(0))

    def get(self, path: str, params: dict | None = None) -> ResponseStub:
        """Record the params and return the next canned response."""
        self.getParams.append(params)
        return ResponseStub(self.gets.pop(0))


def test_command_wrap() -> None:
    """Wrap prefixes with cd only when a directory is set."""
    assert command_wrap("ls", "") == "ls"
    assert command_wrap("ls", "reports") == "cd reports && ls"
    assert command_wrap("cat x.txt", "my dir") == "cd 'my dir' && cat x.txt"


def test_job_submit_posts_command() -> None:
    """Submit posts the command to /jobs and returns the record."""
    client = ClientStub(
        posts=[{"jobId": "abc", "state": "queued"}],
        gets=[],
    )
    job = job_submit(client, "df -kh")
    assert job["jobId"] == "abc"
    assert client.posted == [{"command": "df -kh", "description": ""}]


def test_job_wait_polls_until_finished() -> None:
    """Wait returns the first succeeded or failed record."""
    client = ClientStub(
        posts=[],
        gets=[
            {"jobId": "abc", "state": "queued"},
            {"jobId": "abc", "state": "claimed"},
            {
                "jobId": "abc",
                "state": "succeeded",
                "result": {"exitCode": 0, "stdout": "ok\n", "stderr": ""},
            },
        ],
    )
    job = job_wait(client, "abc", 0)
    assert job["state"] == "succeeded"
    assert job["result"]["stdout"] == "ok\n"
    assert all(params == {"wait": 10.0} for params in client.getParams)


def test_job_wait_warns_when_still_queued(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A job queued past 10s warns that no poller may be running."""
    calls = {"count": 0}

    def fake_monotonic() -> float:
        """Return 0.0 once, then 11.0 for every later call."""
        calls["count"] += 1
        return 0.0 if calls["count"] == 1 else 11.0

    monkeypatch.setattr("client.mush.time.monotonic", fake_monotonic)
    client = ClientStub(
        posts=[],
        gets=[
            {"jobId": "abc", "state": "queued"},
            {
                "jobId": "abc",
                "state": "succeeded",
                "result": {"exitCode": 0, "stdout": "ok\n", "stderr": ""},
            },
        ],
    )
    job = job_wait(client, "abc", 0)
    assert job["state"] == "succeeded"
    assert "is a poller running?" in capsys.readouterr().err


def test_poller_check_warns_when_detached(capsys) -> None:
    """A detached poller status prints a warning and reports False."""
    client = ClientStub(posts=[], gets=[{"attached": False, "lastSeenSecondsAgo": None}])
    assert poller_check(client) is False
    assert "No poller is attached" in capsys.readouterr().err


def test_poller_check_silent_when_attached(capsys) -> None:
    """An attached poller status prints nothing and reports True."""
    client = ClientStub(posts=[], gets=[{"attached": True, "lastSeenSecondsAgo": 1.0}])
    assert poller_check(client) is True
    assert capsys.readouterr().err == ""


def test_cwd_discover_returns_real_path() -> None:
    """Discovery submits pwd and returns the printed directory."""
    client = ClientStub(
        posts=[{"jobId": "abc", "state": "queued"}],
        gets=[
            {
                "jobId": "abc",
                "state": "succeeded",
                "result": {"exitCode": 0, "stdout": "/real/dir\n", "stderr": ""},
            }
        ],
    )
    assert cwd_discover(client, 0) == "/real/dir"
    assert client.posted == [{"command": "pwd", "description": ""}]


def test_cd_remote_moves_to_probed_path() -> None:
    """A successful cd probe returns the path pwd printed."""
    client = ClientStub(
        posts=[{"jobId": "abc", "state": "queued"}],
        gets=[
            {
                "jobId": "abc",
                "state": "succeeded",
                "result": {"exitCode": 0, "stdout": "/home\n", "stderr": ""},
            }
        ],
    )
    assert cd_remote(client, "/start", "/home", 0) == "/home"
    assert client.posted == [
        {"command": "cd /start && cd /home && pwd", "description": ""}
    ]


def test_cd_remote_keeps_cwd_on_failure(capsys) -> None:
    """A failed cd probe prints the error and keeps the old directory."""
    client = ClientStub(
        posts=[{"jobId": "abc", "state": "queued"}],
        gets=[
            {
                "jobId": "abc",
                "state": "failed",
                "result": {
                    "exitCode": 1,
                    "stdout": "",
                    "stderr": "sh: cd: nope: No such file or directory\n",
                },
            }
        ],
    )
    assert cd_remote(client, "/start", "nope", 0) == "/start"
    assert "No such file or directory" in capsys.readouterr().err


def test_cwd_join_tracks_without_validation(capsys) -> None:
    """No-wait resolution joins paths and refuses blind relative moves."""
    assert cwd_join("/a/b", "..") == "/a"
    assert cwd_join("/a", "c/d") == "/a/c/d"
    assert cwd_join("", "/abs") == "/abs"
    assert cwd_join("/a", None) == ""
    assert cwd_join("", "relative") == ""
    assert "unknown" in capsys.readouterr().err


def test_result_print_reports_exit_code(capsys) -> None:
    """Print writes stdout and an exit marker for failures."""
    job = {
        "jobId": "abc",
        "state": "failed",
        "result": {"exitCode": 1, "stdout": "out\n", "stderr": "err\n"},
    }
    result_print(job)
    captured = capsys.readouterr()
    assert captured.out == "out\n"
    assert captured.err == "err\n[exit 1]\n"
