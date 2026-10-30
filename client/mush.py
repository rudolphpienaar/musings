"""Mush: a shell-like REPL over the ``/jobs`` queue.

Each line typed at the prompt becomes one job: it is submitted with
``POST /jobs`` and, by default, mush waits for the poller to run it,
long-polling ``GET /jobs/{jobId}?wait=10`` until the job succeeds or
fails, then prints the captured output. The server parks each wait
and answers the moment the result is posted, so a prompt backed by a
long-polling poller feels like a shell: round-trips plus execution
time, not poll intervals. This is the asynchronous counterpart of
``rush``: where rush speaks the synchronous ``/rush``
rendezvous, mush speaks the job queue, so it works with any running
``musings-poller`` and needs no attached ``rush`` executor.

At startup mush asks ``GET /jobs/poller`` whether a poller is
attached and warns immediately when none is, because a submission
without a poller queues forever: the server is only the rendezvous,
and nothing executes until someone deliberately starts a poller.

The prompt shows the poller's real filesystem, honestly. At startup,
when a poller is attached, mush runs ``pwd`` through the queue to
learn the poller's actual working directory, and every ``cd`` is
validated by running ``cd <arg> && pwd`` there: the prompt moves only
where the poller's filesystem actually has a place to move to, and it
always displays the real absolute path. The jobs API carries no
working-directory field, so each command is submitted with a ``cd``
prefix for that absolute path. When the directory cannot be known (no
poller attached yet, or ``--no-wait``) the prompt shows ``?`` instead
of pretending, and with ``--no-wait`` directories are tracked
client-side without validation.

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
import posixpath
import shlex
import sys
import time

import httpx

from client import transport


def client_build() -> httpx.Client:
    """Build an authenticated HTTP client from the environment.

    Returns:
        A client pointed at the configured service URL, with TLS trust
        resolved by ``client.transport``.

    Raises:
        SystemExit: When MUSINGS_URL or MUSINGS_TOKEN is unset.
    """
    return transport.client_build(timeout=30.0)


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


RESULT_WAIT_SECONDS = 10.0


def poller_check(client: httpx.Client) -> bool:
    """Warn when the service reports no attached poller.

    Advisory only: the submission queues either way, and a server too
    old to know ``GET /jobs/poller`` is silently tolerated.

    Args:
        client: Authenticated client.

    Returns:
        True only when the server affirms an attached poller; False on
        a detached report and on any error.
    """
    try:
        response = client.get("/jobs/poller")
        response.raise_for_status()
    except httpx.HTTPError:
        return False
    attached = bool(response.json().get("attached"))
    if not attached:
        print(
            "No poller is attached: commands will queue but nothing will "
            "execute them. Start musings-poller in another terminal.",
            file=sys.stderr,
        )
    return attached


def job_wait(client: httpx.Client, jobId: str, pollInterval: float) -> dict:
    """Long-poll one job until it succeeds or fails.

    Each request parks on the server for up to ``RESULT_WAIT_SECONDS``
    and returns the moment the result lands, so the result costs a
    round-trip, not a poll interval.

    Args:
        client: Authenticated client.
        jobId: Job identifier to wait for.
        pollInterval: Fallback seconds between polls, for a server
            that answers without parking; 0 with a current server.

    Returns:
        The finished job record as decoded JSON.
    """
    notified = False
    start = time.monotonic()
    while True:
        response = client.get(f"/jobs/{jobId}", params={"wait": RESULT_WAIT_SECONDS})
        response.raise_for_status()
        job = response.json()
        if job["state"] in ("succeeded", "failed"):
            return job
        if not notified and time.monotonic() - start >= 10.0:
            print(
                "Still queued after 10s: is a poller running? "
                "Start musings-poller in another terminal.",
                file=sys.stderr,
            )
            notified = True
        time.sleep(pollInterval)


def cwd_discover(client: httpx.Client, pollInterval: float) -> str:
    """Learn the poller's real working directory by running ``pwd``.

    Call only when a poller is known to be attached: the probe is an
    ordinary job and would otherwise queue indefinitely.

    Args:
        client: Authenticated client.
        pollInterval: Fallback seconds between result polls.

    Returns:
        The poller's absolute working directory, or "" when it could
        not be learned (error or interrupt).
    """
    try:
        job = job_submit(client, "pwd")
        job = job_wait(client, job["jobId"], pollInterval)
    except (httpx.HTTPError, KeyboardInterrupt):
        return ""
    result = job.get("result") or {}
    if result.get("exitCode", 1) != 0:
        return ""
    return result.get("stdout", "").strip()


def cd_remote(client: httpx.Client, cwd: str, arg: str | None, pollInterval: float) -> str:
    """Change directory by probing the poller's real filesystem.

    Submits ``cd <arg> && pwd`` (prefixed with the current directory)
    as a job, so the prompt moves only where the poller can actually
    go, and always to the real absolute path the probe prints. A bare
    ``cd`` resolves to the poller's home directory, as in a shell.

    Args:
        client: Authenticated client.
        cwd: Current absolute directory, or "" for the poller's own.
        arg: ``cd`` argument, or None for a bare ``cd``.
        pollInterval: Fallback seconds between result polls.

    Returns:
        The new absolute directory on success, the old one on failure.
    """
    probe = "cd && pwd" if arg is None else f"cd {shlex.quote(arg)} && pwd"
    try:
        job = job_submit(client, command_wrap(probe, cwd))
        job = job_wait(client, job["jobId"], pollInterval)
    except httpx.HTTPError as error:
        print(f"cd failed: {error}", file=sys.stderr)
        return cwd
    except KeyboardInterrupt:
        print("cd abandoned.", file=sys.stderr)
        return cwd
    result = job.get("result") or {}
    if result.get("exitCode", 1) != 0:
        print(result.get("stderr", "").strip() or "cd failed.", file=sys.stderr)
        return cwd
    return result.get("stdout", "").strip() or cwd


def cwd_join(current: str, arg: str | None) -> str:
    """Resolve a ``cd`` argument client-side, without validation.

    Used only with ``--no-wait``, where results never come back and so
    nothing can be probed: absolute paths are taken as given, relative
    ones joined onto the current directory, and nothing is checked
    against the poller's filesystem.

    Args:
        current: Current absolute directory, or "" when unknown.
        arg: ``cd`` argument, or None for a bare ``cd``.

    Returns:
        The resolved directory, or "" for unknown.
    """
    if arg is None:
        return ""
    if arg.startswith("/"):
        return posixpath.normpath(arg)
    if not current:
        print("Current directory unknown: cd to an absolute path first.", file=sys.stderr)
        return current
    return posixpath.normpath(posixpath.join(current, arg))


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
        default=float(os.environ.get("MUSH_POLL_INTERVAL_SECONDS", "0.0")),
        help="Fallback seconds between result polls; the server parks "
        "each poll, so 0 is right against a current musings.",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Submit each line and print its job record without waiting.",
    )
    args = parser.parse_args(argv)

    client = client_build()
    pollerAttached = poller_check(client)
    cwd = ""
    if pollerAttached and not args.no_wait:
        cwd = cwd_discover(client, args.poll_interval)
    while True:
        display = cwd if cwd else "?"
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
            if args.no_wait:
                cwd = cwd_join(cwd, arg)
            else:
                cwd = cd_remote(client, cwd, arg, args.poll_interval)
            continue
        try:
            job = job_submit(client, command_wrap(stripped, cwd))
        except httpx.HTTPError as error:
            print(f"Submit failed: {error}", file=sys.stderr)
            continue
        if args.no_wait:
            print(f"Job {job['jobId']} queued.")
            continue
        print(f"Job {job['jobId']} submitted, waiting for a poller.", flush=True)
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
