"""Job poller: claim jobs from musings, execute them, post results.

The poller is the agent side of the bidirectional loop. It long-polls
``GET /jobs/pending`` with a ``wait`` of ``POLL_WAIT_SECONDS`` (default
20): the server parks the request until a job is queued, so a fresh
submission is claimed within a network round-trip rather than a poll
interval, and 204 means only that the wait expired empty, answered by
polling again immediately. It runs the claimed command through hostlib
as an explicit ``sh -c`` invocation, and posts the structured result
back to ``POST /jobs/{jobId}/result``. Claiming is atomic on the
server, so two pollers never execute the same job.

``POLL_INTERVAL_SECONDS`` (default 10) survives as the error backoff:
the delay before retrying after a failed poll, and the guard spacing
against a server that answers a long-poll instantly (an old musings
that ignores ``wait``), so the loop can never hammer.

Starting a poller is the act of granting shell access: commands claimed
here run as this user, on this machine, in this directory. The server
alone never executes anything.

Run with::

    export MUSINGS_URL=http://127.0.0.1:8000/api/v1
    export MUSINGS_TOKEN=<token>
    musings-poller

Pass ``--once`` to claim and execute at most one job, then exit; this
is useful for testing and for cron-driven polling. ``--once`` checks
the queue immediately (no long-poll wait) so cron runs return fast.
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
from client.transport import client_build


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


def poll_once(
    client: httpx.Client, timeoutSeconds: int, maxChars: int, waitSeconds: float = 0.0
) -> bool:
    """Claim and execute at most one job.

    Args:
        client: Authenticated client.
        timeoutSeconds: Execution timeout for the claimed job.
        maxChars: Output character cap.
        waitSeconds: Long-poll wait passed to the server; 0 checks the
            queue and returns immediately.

    Returns:
        True when a job was executed, False when the queue was empty.
    """
    response = client.get("/jobs/pending", params={"wait": waitSeconds})
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
    timeoutSeconds = int(os.environ.get("JOB_TIMEOUT_SECONDS", "300"))
    maxChars = int(os.environ.get("MAX_OUTPUT_CHARS", "100000"))
    pollInterval = float(os.environ.get("POLL_INTERVAL_SECONDS", "10"))
    waitSeconds = 0.0 if args.once else float(os.environ.get("POLL_WAIT_SECONDS", "20"))

    # The read timeout must outlive the server-side long-poll wait.
    client = client_build(timeout=httpx.Timeout(30.0, read=waitSeconds + 30.0))
    baseUrl = str(client.base_url)
    print(
        f"Polling {baseUrl} for jobs; commands will execute as this user, here.",
        flush=True,
    )
    try:
        while True:
            started = time.monotonic()
            try:
                didWork = poll_once(client, timeoutSeconds, maxChars, waitSeconds)
            except httpx.HTTPError as error:
                print(f"Poll error: {error}", file=sys.stderr)
                didWork = False
                if not args.once:
                    time.sleep(pollInterval)
            if args.once:
                break
            # Guard against a server that ignores ``wait`` and answers
            # instantly: an empty poll that returned too fast is spaced
            # by the poll interval instead of hammering the server.
            elapsed = time.monotonic() - started
            if not didWork and elapsed < 1.0:
                time.sleep(pollInterval)
    except KeyboardInterrupt:
        print("Poller stopped.", flush=True)


if __name__ == "__main__":
    poller_run()
