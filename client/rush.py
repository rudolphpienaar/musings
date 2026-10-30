"""rush client: a shell-like prompt over the ``/rush`` rendezvous.

The user believes they are on a shell inside the executor's machine,
but in reality they are in this local read-eval-print loop: each
line becomes an ``/rush`` post, and the streamed output is printed
as it arrives. The client holds the shell state -- the working
directory -- and intercepts ``cd`` itself; every other line is sent
with the current directory.

The prompt shows the executor's real filesystem, honestly. At startup
rush runs ``pwd`` through the rendezvous to learn where the executor
starts, and every ``cd`` is a probe: the candidate directory is sent
as the working directory of a ``pwd``, so a directory that does not
exist, cannot be entered, or that a clamped executor refuses, is
reported and the prompt stays where it was. The executor, not this
client and not the server, decides what may be entered; rush only
reflects the answer. When nothing is known yet the prompt shows ``?``.

Run with::

    export MUSINGS_URL=http://127.0.0.1:8000/api/v1
    export MUSINGS_TOKEN=<token>
    rush
"""

from __future__ import annotations

import json
import posixpath
import sys
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

from client import transport


@dataclass
class CaptureResult:
    """What one submission produced, collected rather than printed.

    Attributes:
        ok: True when the executor ran the command and it exited 0.
        stdout: Collected standard output.
        stderr: Collected standard error.
        errors: Executor or rendezvous errors, in words.
    """

    ok: bool
    stdout: str
    stderr: str
    errors: list[str]


def cwd_join(current: str, arg: str) -> str:
    """Compute the directory a ``cd`` argument names, as a path string.

    Args:
        current: Current absolute directory on the executor, or "" when
            unknown.
        arg: ``cd`` argument: absolute, or relative to ``current``.

    Returns:
        The normalized candidate path. It is only a candidate: whether
        it exists, or may be entered, is for the executor to say.
    """
    if arg.startswith("/"):
        return posixpath.normpath(arg)
    return posixpath.normpath(posixpath.join(current, arg)) if current else arg


def client_build() -> httpx.Client:
    """Build an authenticated HTTP client from the environment.

    Returns:
        A client pointed at the configured service URL, with TLS trust
        resolved by ``client.transport``. It has no read timeout: a
        stream stays open for the whole command.

    Raises:
        SystemExit: When MUSINGS_URL or MUSINGS_TOKEN is unset.
    """
    return transport.client_build(
        timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    )


def items_stream(client: httpx.Client, command: str, cwd: str) -> Iterator[dict]:
    """Post one command and yield the stream's JSON items as they arrive.

    Rendezvous failures are turned into ``error`` items so callers see
    one shape: output chunks (``stream``/``data``), the ``result``
    envelope, or an ``error``.

    Args:
        client: Authenticated client.
        command: Command string for the executor to run.
        cwd: Working directory, as the executor should interpret it.

    Yields:
        Decoded JSON items from the response stream.
    """
    try:
        with client.stream("POST", "/rush", json={"command": command, "cwd": cwd}) as response:
            if response.status_code == 503:
                yield {"type": "error", "errors": "No executor attached."}
                return
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    yield json.loads(line)
    except httpx.HTTPStatusError as error:
        yield {"type": "error", "errors": f"Request failed: {error.response.status_code}"}
    except httpx.HTTPError as error:
        yield {"type": "error", "errors": f"Connection failed: {error}"}


def command_run(client: httpx.Client, command: str, cwd: str) -> None:
    """Post one command and print its streamed output live.

    Args:
        client: Authenticated client.
        command: Command string for the executor to run.
        cwd: Working directory, as the executor should interpret it.
    """
    for item in items_stream(client, command, cwd):
        if "stream" in item:
            target = sys.stdout if item["stream"] == "stdout" else sys.stderr
            print(item["data"], end="", file=target, flush=True)
        elif item.get("type") == "result":
            if not item["status"]:
                print(f"[exit {item['returncode']}]", file=sys.stderr)
                for message in item.get("errors", []):
                    print(message, file=sys.stderr)
        elif item.get("type") == "error":
            print(f"Error: {item['errors']}", file=sys.stderr)


def command_capture(client: httpx.Client, command: str, cwd: str) -> CaptureResult:
    """Post one command and collect its output instead of printing it.

    Args:
        client: Authenticated client.
        command: Command string for the executor to run.
        cwd: Working directory, as the executor should interpret it.

    Returns:
        The collected output and outcome.
    """
    stdoutParts: list[str] = []
    stderrParts: list[str] = []
    errors: list[str] = []
    ok = False
    for item in items_stream(client, command, cwd):
        if "stream" in item:
            (stdoutParts if item["stream"] == "stdout" else stderrParts).append(item["data"])
        elif item.get("type") == "result":
            ok = bool(item["status"])
            errors.extend(item.get("errors", []))
        elif item.get("type") == "error":
            errors.append(str(item["errors"]))
    return CaptureResult(ok=ok, stdout="".join(stdoutParts), stderr="".join(stderrParts), errors=errors)


def cwd_discover(client: httpx.Client) -> str:
    """Learn where the executor starts, by running ``pwd`` there.

    Args:
        client: Authenticated client.

    Returns:
        The executor's starting directory, or "" when it could not be
        learned (no executor attached, or the probe failed), in which
        case the problem is printed.
    """
    probe = command_capture(client, "pwd", "")
    if probe.ok and probe.stdout.strip():
        return probe.stdout.strip()
    for message in probe.errors or [probe.stderr.strip() or "pwd probe failed"]:
        print(message, file=sys.stderr)
    return ""


def cd_remote(client: httpx.Client, cwd: str, arg: str | None) -> str:
    """Change directory by asking the executor whether the target works.

    The candidate path is sent as the working directory of a ``pwd``,
    so the executor applies its own rules: the directory must exist
    and be enterable, and a clamped executor refuses an escape. On
    success the prompt moves to the path the executor reports, which
    resolves symlinks; on failure the reason is printed and the
    current directory is kept.

    Args:
        client: Authenticated client.
        cwd: Current directory, or "" when unknown.
        arg: ``cd`` argument, or None for a bare ``cd`` (the executor's
            base directory).

    Returns:
        The new current directory.
    """
    candidate = "" if arg is None else cwd_join(cwd, arg)
    probe = command_capture(client, "pwd", candidate)
    if probe.ok and probe.stdout.strip():
        return probe.stdout.strip()
    shown = arg if arg is not None else "~"
    for message in probe.errors or [probe.stderr.strip() or "cannot enter directory"]:
        print(f"cd: {shown}: {message}", file=sys.stderr)
    return cwd


def rush_run(argv: list[str] | None = None) -> None:
    """Run the read-eval-print loop until quit or EOF.

    Args:
        argv: Unused; accepted for console-script symmetry.
    """
    client = client_build()
    cwd = cwd_discover(client)
    while True:
        display = cwd if cwd else "?"
        try:
            line = input(f"rush:{display}$ ")
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
            cwd = cd_remote(client, cwd, arg)
            continue
        command_run(client, stripped, cwd)


if __name__ == "__main__":
    rush_run()
