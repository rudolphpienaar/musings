"""Mush: a shell-like REPL over the ``/jobs`` queue.

Each line typed at the prompt becomes one job: it is submitted with
``POST /jobs`` and, by default, mush waits for the poller to run it,
polling ``GET /jobs/{jobId}`` until the job succeeds or fails, then
prints the captured output. This is the asynchronous counterpart of
``musings-repl``: where the REPL speaks the synchronous ``/rcli``
rendezvous, mush speaks the job queue, so it works with any running
``musings-poller`` and needs no attached ``rcli`` executor.

The working directory is held client-side, like the REPL: ``cd``
lines update it, and every other line is submitted with a ``cd``
prefix for that directory. Unlike ``/rcli``, the jobs API carries
no working-directory field and the server does not clamp it, so
the directory is relative to the poller's working directory, not
to the service base directory.

Run with::

    export MUSINGS_URL=http://127.0.0.1:8000/api/v1
    export MUSINGS_TOKEN=<token>
    mush

Pass ``--no-wait`` to submit each line and print its job record
without waiting for the result.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import time

import httpx

from client.repl import cwd_resolve


def client_build() -> httpx.Client:
    """Build an authenticated HTTP client from the environment.

    Returns:
        A client pointed at the configured service URL.

    Raises:
        SystemExit: When MUSINGS_URL or MUSINGS_TOKEN is unset.
    """
    baseUrl = os.environ.get("MUSINGS_URL", "")
    token = os.environ.get("MUSINGS_TOKEN", "")
    if not baseUrl or not token:
        print("MUSINGS_URL and MUSINGS_TOKEN must both be set.", file=sys.stderr)
        raise SystemExit(2)
    # trust_env=False: see client.cli for why proxy env is bypassed.
    return httpx.Client(
        base_url=baseUrl,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
        trust_env=False,
    )


def command_wrap(command: str, cwd: str) -> str:
    """Prefix a command with a directory change, when one is set.

    Args:
        command: Command line to submit.
        cwd: Working directory relative to the poller's working
            directory, or "" for no prefix.

    Returns:
        The command to submit, with a ``cd`` prefix when cwd is set.
    """
    if not cwd:
        return command
    return f"cd {shlex.quote(cwd)} && {command}"


def job_submit(client: httpx.Client, command: str) -> dict:
    """Submit one command as a job and return its record.

    Args:
        client: Authenticated client.
        command: Shell command to execute.

    Returns:
        The submitted job record as decoded JSON.
    """
    response = client.post("/jobs", json={"command": command, "description": ""})
    response.raise_for_status()
    return response.json()


def job_wait(client: httpx.Client, jobId: str, pollInterval: float) -> dict:
    """Poll one job until it succeeds or fails.

    Args:
        client: Authenticated client.
        jobId: Job identifier to wait for.
        pollInterval: Seconds between polls.

    Returns:
        The finished job record as decoded JSON.
    """
    while True:
        response = client.get(f"/jobs/{jobId}")
        response.raise_for_status()
        job = response.json()
        if job["state"] in ("succeeded", "failed"):
            return job
        time.sleep(pollInterval)


def result_print(job: dict) -> None:
    """Print a finished job's output and exit status.

    Args:
        job: Finished job record with a result payload.
    """
    result = job.get("result") or {}
    stdoutText = result.get("stdout", "")
    stderrText = result.get("stderr", "")
    if stdoutText:
        print(stdoutText, end="" if stdoutText.endswith("\n") else "\n")
    if stderrText:
        print(stderrText, end="" if stderrText.endswith("\n") else "\n", file=sys.stderr)
    if result.get("truncated"):
        print("[output truncated]", file=sys.stderr)
    exitCode = result.get("exitCode", 1)
    if exitCode != 0:
        print(f"[exit {exitCode}]", file=sys.stderr)


def mush_run(argv: list[str] | None = None) -> None:
    """Run the mush read-eval-post loop until quit or EOF.

    Args:
        argv: Argument list; None means sys.argv.
    """
    parser = argparse.ArgumentParser(description="Shell REPL over the musings job queue.")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.environ.get("MUSH_POLL_INTERVAL_SECONDS", "1.0")),
        help="Seconds between result polls.",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Submit each line and print its job record without waiting.",
    )
    args = parser.parse_args(argv)

    client = client_build()
    cwd = ""
    while True:
        display = "/" + cwd if cwd else "/"
        try:
            line = input(f"mush:{display}$ ")
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in ("quit", "exit"):
            break
        if stripped == "cd" or stripped.startswith("cd "):
            arg = stripped[2:].strip() or None
            cwd = cwd_resolve(cwd, arg)
            continue
        try:
            job = job_submit(client, command_wrap(stripped, cwd))
        except httpx.HTTPError as error:
            print(f"Submit failed: {error}", file=sys.stderr)
            continue
        if args.no_wait:
            print(f"Job {job['jobId']} queued.")
            continue
        try:
            job = job_wait(client, job["jobId"], args.poll_interval)
        except KeyboardInterrupt:
            print(f"Still waiting on job {job['jobId']}; fetch it with musings get.")
            continue
        except httpx.HTTPError as error:
            print(f"Wait failed: {error}", file=sys.stderr)
            continue
        result_print(job)


if __name__ == "__main__":
    mush_run()
