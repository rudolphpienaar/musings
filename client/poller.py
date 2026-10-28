"""Job poller: claim jobs from musings, execute them, post results.

The poller is the agent side of the bidirectional loop. It polls
``GET /jobs/pending`` (204 means the queue is empty), runs the claimed
command as ``bash -c <command>``, and posts the structured result back
to ``POST /jobs/{jobId}/result``. Claiming is atomic on the server,
so two pollers never execute the same job.

Run with::

    export MUSINGS_URL=http://127.0.0.1:8000/api/v1
    export MUSINGS_TOKEN=<token>
    musings-poller

Pass ``--once`` to claim and execute at most one job, then exit; this
is useful for testing and for cron-driven polling.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import httpx


def output_truncate(text: str, maxChars: int) -> tuple[str, bool]:
    """Truncate captured output at a character cap.

    Args:
        text: Captured output.
        maxChars: Maximum characters to keep.

    Returns:
        The kept text and whether truncation happened.
    """
    if len(text) <= maxChars:
        return text, False
    return text[:maxChars], True


def job_execute(command: str, timeoutSeconds: int, maxChars: int) -> dict:
    """Execute one command and build its result payload.

    Args:
        command: Shell command, run as ``bash -c``.
        timeoutSeconds: Execution timeout.
        maxChars: Output character cap.

    Returns:
        A result payload matching the server JobResult model.
    """
    startedAt = datetime.now(timezone.utc)
    try:
        completed = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=timeoutSeconds,
        )
        exitCode = completed.returncode
        stdoutText = completed.stdout
        stderrText = completed.stderr
    except subprocess.TimeoutExpired as error:
        exitCode = 124
        stdoutText = error.stdout.decode() if isinstance(error.stdout, bytes) else (error.stdout or "")
        stderrText = (error.stderr.decode() if isinstance(error.stderr, bytes) else (error.stderr or "")) + "\nCommand timed out."
    finishedAt = datetime.now(timezone.utc)
    stdoutKept, stdoutTruncated = output_truncate(stdoutText, maxChars)
    stderrKept, stderrTruncated = output_truncate(stderrText, maxChars)
    return {
        "exitCode": exitCode,
        "stdout": stdoutKept,
        "stderr": stderrKept,
        "truncated": stdoutTruncated or stderrTruncated,
        "startedAt": startedAt.isoformat(),
        "finishedAt": finishedAt.isoformat(),
    }


def poll_once(client: httpx.Client, timeoutSeconds: int, maxChars: int) -> bool:
    """Claim and execute at most one job.

    Args:
        client: Authenticated client.
        timeoutSeconds: Execution timeout for the claimed job.
        maxChars: Output character cap.

    Returns:
        True when a job was executed, False when the queue was empty.
    """
    response = client.get("/jobs/pending")
    if response.status_code == 204:
        return False
    response.raise_for_status()
    job = response.json()
    print(f"Claimed job {job['jobId']}: {job['command']}", flush=True)
    result = job_execute(job["command"], timeoutSeconds, maxChars)
    resultResponse = client.post(f"/jobs/{job['jobId']}/result", json=result)
    resultResponse.raise_for_status()
    print(f"Job {job['jobId']} finished with exit code {result['exitCode']}.", flush=True)
    return True


def poller_run(argv: list[str] | None = None) -> None:
    """Run the poll loop until interrupted.

    Args:
        argv: Argument list; None means sys.argv.
    """
    parser = argparse.ArgumentParser(description="Poll musings for jobs and execute them.")
    parser.add_argument("--once", action="store_true", help="Execute at most one job, then exit.")
    args = parser.parse_args(argv)

    baseUrl = os.environ.get("MUSINGS_URL", "")
    token = os.environ.get("MUSINGS_TOKEN", "")
    if not baseUrl or not token:
        print("MUSINGS_URL and MUSINGS_TOKEN must both be set.", file=sys.stderr)
        raise SystemExit(2)
    timeoutSeconds = int(os.environ.get("JOB_TIMEOUT_SECONDS", "300"))
    maxChars = int(os.environ.get("MAX_OUTPUT_CHARS", "100000"))
    pollInterval = float(os.environ.get("POLL_INTERVAL_SECONDS", "10"))

    # trust_env=False: see client.cli for why proxy env is bypassed.
    client = httpx.Client(
        base_url=baseUrl,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
        trust_env=False,
    )
    try:
        while True:
            try:
                didWork = poll_once(client, timeoutSeconds, maxChars)
            except httpx.HTTPError as error:
                print(f"Poll error: {error}", file=sys.stderr)
                didWork = False
            if args.once:
                break
            time.sleep(0 if didWork else pollInterval)
    except KeyboardInterrupt:
        print("Poller stopped.", flush=True)


if __name__ == "__main__":
    poller_run()
