"""Rush executor: claim ``/rush`` submissions and run them.

The executor is the agent side of the synchronous rendezvous. It
long-polls ``GET /rush/next`` (204 means nothing pending), runs the
claimed command through hostlib, streams output chunks back as they
arrive, and closes with the result envelope. It only needs outbound
connections, like the job poller.

Attaching an executor is the act of exposing this machine to whoever
holds the token, and the executor is the one party entitled to decide
how much of it. By default it is unconfined: a submission names any
directory on this machine and the command runs there, exactly like a
shell session would. ``--clamp`` confines every command to
``MUSINGS_RUSH_BASE_DIR`` instead, refusing a working directory that
escapes it. The server never judges directories; this is where that
decision lives, on the disk it concerns.

Run with::

    export MUSINGS_URL=http://127.0.0.1:8000/api/v1
    export MUSINGS_TOKEN=<token>
    musings-rush-executor                 # whole machine, starting here
    MUSINGS_RUSH_BASE_DIR=/work musings-rush-executor --clamp
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from pathlib import Path

import httpx
from hostlib import JobSpec, job_run
from hostlib.sink import SinkBase

from client.env import env_load
from client.transport import client_build
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
                f"/rush/{self._submissionId}/chunk",
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


def cwd_resolve(baseDir: str, cwd: str, clamp: bool) -> str | None:
    """Resolve a submission's working directory on this machine.

    Args:
        baseDir: Executor base directory: where an empty ``cwd`` lands,
            and the boundary when clamped.
        cwd: Submitted working directory. Absolute paths stand on their
            own; relative ones are joined to the base.
        clamp: When True, a resolved path outside the base is refused.

    Returns:
        The resolved absolute path, or None when clamped and the path
        escapes the base.
    """
    base = Path(baseDir).resolve()
    target = (base / cwd).resolve() if cwd else base
    if clamp and not target.is_relative_to(base):
        return None
    return str(target)


def submission_execute(
    client: httpx.Client,
    submission: dict,
    baseDir: str,
    timeoutSeconds: int,
    maxChars: int,
    clamp: bool = False,
) -> None:
    """Run one submission and report its envelope.

    Args:
        client: Authenticated client.
        submission: Claimed submission (id, command, cwd).
        baseDir: Executor base directory.
        timeoutSeconds: Execution timeout.
        maxChars: Output character cap.
        clamp: Refuse working directories outside the base.
    """
    submissionId = submission["submissionId"]
    resolved = cwd_resolve(baseDir, submission["cwd"], clamp)
    refusal: str | None = None
    if resolved is None:
        refusal = "Working directory escapes base."
    elif not Path(resolved).is_dir():
        refusal = f"No such directory: {resolved}"
    if refusal is not None:
        client.post(
            f"/rush/{submissionId}/result",
            json={
                "status": False,
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "errors": [refusal],
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
        f"/rush/{submissionId}/result",
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
        argv: Argument list; None means sys.argv.
    """
    parser = argparse.ArgumentParser(description="Attach a rush executor to musings.")
    parser.add_argument(
        "--clamp",
        action="store_true",
        help="Confine every command to MUSINGS_RUSH_BASE_DIR; by default any "
        "directory on this machine may be named.",
    )
    args = parser.parse_args(argv)
    env_load()
    baseDir = os.environ.get("MUSINGS_RUSH_BASE_DIR", ".")
    executorId = os.environ.get(
        "MUSINGS_RUSH_EXECUTOR_ID", f"{socket.gethostname()}-{os.getpid()}"
    )
    timeoutSeconds = int(os.environ.get("JOB_TIMEOUT_SECONDS", "300"))
    maxChars = int(os.environ.get("MAX_OUTPUT_CHARS", "100000"))

    # The next-poll timeout exceeds the server's 25s long-poll wait.
    client = client_build(timeout=35.0)
    baseShown = str(Path(baseDir).resolve())
    if args.clamp:
        print(f"Executor {executorId} attached, confined to {baseShown}.", flush=True)
    else:
        print(
            f"Executor {executorId} attached, unconfined; commands may run anywhere on "
            f"this machine as this user, starting in {baseShown}.",
            flush=True,
        )
    try:
        while True:
            try:
                response = client.get("/rush/next", params={"executorId": executorId})
                if response.status_code == 204:
                    continue
                response.raise_for_status()
                submission = response.json()
                print(f"Claimed {submission['submissionId']}: {submission['command']}", flush=True)
                submission_execute(
                    client, submission, baseDir, timeoutSeconds, maxChars, clamp=args.clamp
                )
            except httpx.HTTPError as error:
                print(f"Executor error: {error}", file=sys.stderr)
                time.sleep(5)
    except KeyboardInterrupt:
        print("Executor stopped.", flush=True)


if __name__ == "__main__":
    executor_run()
