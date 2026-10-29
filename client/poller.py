"""Job poller: claim jobs from musings, execute them, post results.

The poller is the agent side of the bidirectional loop. It polls
``GET /jobs/pending`` (204 means the queue is empty), runs the claimed
command through hostlib as an explicit ``sh -c`` invocation, and posts
the structured result back to ``POST /jobs/{jobId}/result``. Claiming
is atomic on the server, so two pollers never execute the same job.

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
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from hostlib import JobSpec, job_run

from client.env import env_load


@dataclass
class TruncatedText:
    """Text kept after truncation, with whether truncation happened.

    Attributes:
        text: Kept text.
        truncated: True when text was cut at the cap.
    """

    text: str
    truncated: bool


def output_truncate(text: str, maxChars: int) -> TruncatedText:
    """Truncate captured output at a character cap.

    Args:
        text: Captured output.
        maxChars: Maximum characters to keep.

    Returns:
        The kept text as a modeled value.
    """
    if len(text) <= maxChars:
        return TruncatedText(text=text, truncated=False)
    return TruncatedText(text=text[:maxChars], truncated=True)


def timestamp_format(value) -> str:
    """Format a hostlib timestamp as an ISO string.

    Args:
        value: Hostlib timestamp (already an ISO string) or a
            datetime, or None.

    Returns:
        ISO string, or now when the value is missing.
    """
    if isinstance(value, str) and value:
        return value
    if value is not None:
        return value.isoformat()
    return datetime.now(timezone.utc).isoformat()


def job_execute(command: str, timeoutSeconds: int, maxChars: int) -> dict:
    """Execute one command and build its result payload.

    Args:
        command: Shell command, run through hostlib as ``sh -c``.
        timeoutSeconds: Execution timeout.
        maxChars: Output character cap.

    Returns:
        A result payload matching the server JobResult model.
    """
    result = job_run(
        JobSpec(argv=["sh", "-c", command], timeout=float(timeoutSeconds))
    )
    exitCode = 124 if result.timedOut else (result.returncode if result.returncode is not None else 1)
    stdoutText = result.stdout
    stderrText = result.stderr
    if result.timedOut:
        stderrText += "\nCommand timed out."
    startedAt = timestamp_format(result.startedAt)
    finishedAt = timestamp_format(result.endedAt)
    stdoutTruncation = output_truncate(stdoutText, maxChars)
    stderrTruncation = output_truncate(stderrText, maxChars)
    return {
        "exitCode": exitCode,
        "stdout": stdoutTruncation.text,
        "stderr": stderrTruncation.text,
        "truncated": stdoutTruncation.truncated or stderrTruncation.truncated,
        "startedAt": startedAt,
        "finishedAt": finishedAt,
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

    env_load()
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
