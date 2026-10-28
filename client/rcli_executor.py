"""Rcli executor: claim ``/rcli`` submissions and run them.

The executor is the agent side of the synchronous rendezvous. It
long-polls ``GET /rcli/next`` (204 means nothing pending), runs the
claimed command through hostlib, streams output chunks back as they
arrive, and closes with the result envelope. It only needs outbound
connections, like the job poller.

Run with::

    export MUSINGS_URL=http://127.0.0.1:8000/api/v1
    export MUSINGS_TOKEN=<token>
    export MUSINGS_RCLI_BASE_DIR=/work   # commands are confined here
    musings-rcli-executor
"""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

import httpx
from hostlib import JobSpec, job_run
from hostlib.sink import SinkBase

from client.poller import output_truncate


class ChunkForwardSink(SinkBase):
    """Forward hostlib output chunks to the server as they arrive.

    Attributes:
        client: Authenticated client for chunk posts.
        submissionId: Submission receiving the chunks.
        maxChars: Total forwarded characters before truncating.
    """

    def __init__(self, client: httpx.Client, submissionId: str, maxChars: int) -> None:
        """Initialize the sink.

        Args:
            client: Authenticated client.
            submissionId: Submission receiving the chunks.
            maxChars: Total forwarded characters before truncating.
        """
        super().__init__()
        self._client = client
        self._submissionId = submissionId
        self._maxChars = maxChars
        self._forwardedChars = 0
        self.truncated = False

    def _chunk_post(self, stream: str, data: str) -> None:
        """Forward one chunk, respecting the character cap.

        Args:
            stream: ``stdout`` or ``stderr``.
            data: Raw output text for this chunk.
        """
        if self._forwardedChars >= self._maxChars:
            self.truncated = True
            return
        kept = data[: self._maxChars - self._forwardedChars]
        if len(kept) < len(data):
            self.truncated = True
        self._forwardedChars += len(kept)
        try:
            self._client.post(
                f"/rcli/{self._submissionId}/chunk",
                json={"stream": stream, "data": kept},
            ).raise_for_status()
        except httpx.HTTPError as error:
            print(f"Chunk post failed: {error}", file=sys.stderr)

    def stdout_write(self, data: str) -> None:
        """Forward a stdout chunk.

        Args:
            data: Raw stdout text.
        """
        self._chunk_post("stdout", data)

    def stderr_write(self, data: str) -> None:
        """Forward a stderr chunk.

        Args:
            data: Raw stderr text.
        """
        self._chunk_post("stderr", data)


def cwd_resolve(baseDir: str, cwd: str) -> str | None:
    """Resolve a submission's working directory under the base.

    Args:
        baseDir: Executor base directory; commands are confined here.
        cwd: Working directory relative to base ("" is the base).

    Returns:
        The resolved absolute path, or None when it escapes the base.
    """
    base = Path(baseDir).resolve()
    target = (base / cwd).resolve() if cwd else base
    if not target.is_relative_to(base):
        return None
    return str(target)


def submission_execute(
    client: httpx.Client, submission: dict, baseDir: str, timeoutSeconds: int, maxChars: int
) -> None:
    """Run one submission and report its envelope.

    Args:
        client: Authenticated client.
        submission: Claimed submission (id, command, cwd).
        baseDir: Executor base directory.
        timeoutSeconds: Execution timeout.
        maxChars: Output character cap.
    """
    submissionId = submission["submissionId"]
    resolved = cwd_resolve(baseDir, submission["cwd"])
    if resolved is None:
        client.post(
            f"/rcli/{submissionId}/result",
            json={
                "status": False,
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "errors": ["Working directory escapes base."],
                "truncated": False,
                "durationSeconds": 0.0,
            },
        ).raise_for_status()
        return
    sink = ChunkForwardSink(client, submissionId, maxChars)
    # Explicit shell, per hostlib's rule: the shell's involvement is
    # visible in the argument list rather than implicit.
    result = job_run(
        JobSpec(
            argv=["sh", "-c", submission["command"]],
            cwd=resolved,
            timeout=float(timeoutSeconds),
        ),
        sink=sink,
    )
    exitCode = 124 if result.timedOut else (result.returncode if result.returncode is not None else 1)
    stdoutTruncation = output_truncate(result.stdout, maxChars)
    stderrTruncation = output_truncate(result.stderr, maxChars)
    client.post(
        f"/rcli/{submissionId}/result",
        json={
            "status": result.status,
            "returncode": exitCode,
            "stdout": stdoutTruncation.text,
            "stderr": stderrTruncation.text,
            "errors": list(result.errors),
            "truncated": sink.truncated or stdoutTruncation.truncated or stderrTruncation.truncated,
            "durationSeconds": result.duration,
        },
    ).raise_for_status()
    print(f"Submission {submissionId} finished with exit code {exitCode}.", flush=True)


def executor_run(argv: list[str] | None = None) -> None:
    """Run the executor loop until interrupted.

    Args:
        argv: Unused; accepted for console-script symmetry.
    """
    baseUrl = os.environ.get("MUSINGS_URL", "")
    token = os.environ.get("MUSINGS_TOKEN", "")
    if not baseUrl or not token:
        print("MUSINGS_URL and MUSINGS_TOKEN must both be set.", file=sys.stderr)
        raise SystemExit(2)
    baseDir = os.environ.get("MUSINGS_RCLI_BASE_DIR", ".")
    executorId = os.environ.get(
        "MUSINGS_RCLI_EXECUTOR_ID", f"{socket.gethostname()}-{os.getpid()}"
    )
    timeoutSeconds = int(os.environ.get("JOB_TIMEOUT_SECONDS", "300"))
    maxChars = int(os.environ.get("MAX_OUTPUT_CHARS", "100000"))

    # trust_env=False: see client.cli for why proxy env is bypassed.
    # The next-poll timeout exceeds the server's 25s long-poll wait.
    client = httpx.Client(
        base_url=baseUrl,
        headers={"Authorization": f"Bearer {token}"},
        timeout=35.0,
        trust_env=False,
    )
    print(f"Executor {executorId} attached, base {baseDir}.", flush=True)
    try:
        while True:
            try:
                response = client.get("/rcli/next", params={"executorId": executorId})
                if response.status_code == 204:
                    continue
                response.raise_for_status()
                submission = response.json()
                print(f"Claimed {submission['submissionId']}: {submission['command']}", flush=True)
                submission_execute(client, submission, baseDir, timeoutSeconds, maxChars)
            except httpx.HTTPError as error:
                print(f"Executor error: {error}", file=sys.stderr)
                time.sleep(5)
    except KeyboardInterrupt:
        print("Executor stopped.", flush=True)


if __name__ == "__main__":
    executor_run()
