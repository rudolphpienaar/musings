"""Tests for mush over the job queue."""

from __future__ import annotations

from client.mush import command_wrap, job_submit, job_wait, result_print


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

    def post(self, path: str, json: dict) -> ResponseStub:
        """Record a post and return the next canned response."""
        self.posted.append(json)
        return ResponseStub(self.posts.pop(0))

    def get(self, path: str) -> ResponseStub:
        """Return the next canned response."""
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
