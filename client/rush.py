"""rush client: a shell-like prompt over the ``/rush`` rendezvous.

The user believes they are on a shell inside the executor's machine,
but in reality they are in this local read-eval-print loop: each
line becomes an ``/rush`` post, and the streamed output is printed
as it arrives. The client holds the shell state -- the working
directory -- and intercepts ``cd`` itself; every other line is sent
with the current directory, and the server clamps it to the base.

Run with::

    export MUSINGS_URL=http://127.0.0.1:8000/api/v1
    export MUSINGS_TOKEN=<token>
    rush
"""

from __future__ import annotations

import json
import os
import sys

import httpx


def cwd_resolve(current: str, arg: str | None) -> str:
    """Resolve a ``cd`` argument against the current directory.

    Args:
        current: Current directory, relative to base ("" is base).
        arg: ``cd`` argument, or None for a bare ``cd`` (base).

    Returns:
        The new directory, relative to base, clamped at the base:
        ``..`` above the base stays at the base.
    """
    if arg is None or arg == "":
        return ""
    if arg.startswith("/"):
        parts: list[str] = []
        rest = arg.lstrip("/")
    else:
        parts = current.split("/") if current else []
        rest = arg
    for part in rest.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


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
    # No read timeout: a stream stays open for the whole command.
    return httpx.Client(
        base_url=baseUrl,
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0),
        trust_env=False,
    )


def command_run(client: httpx.Client, command: str, cwd: str) -> None:
    """Post one command and print its streamed output live.

    Args:
        client: Authenticated client.
        command: Command string for the executor to run.
        cwd: Working directory, relative to base.
    """
    try:
        with client.stream(
            "POST", "/rush", json={"command": command, "cwd": cwd}
        ) as response:
            if response.status_code == 503:
                print("No executor attached.", file=sys.stderr)
                return
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                item = json.loads(line)
                if "stream" in item:
                    target = sys.stdout if item["stream"] == "stdout" else sys.stderr
                    print(item["data"], end="", file=target, flush=True)
                elif item.get("type") == "result":
                    if not item["status"]:
                        print(f"[exit {item['returncode']}]", file=sys.stderr)
                elif item.get("type") == "error":
                    print(f"Error: {item['errors']}", file=sys.stderr)
    except httpx.HTTPStatusError as error:
        print(f"Request failed: {error.response.status_code}", file=sys.stderr)
    except httpx.HTTPError as error:
        print(f"Connection failed: {error}", file=sys.stderr)


def rush_run(argv: list[str] | None = None) -> None:
    """Run the read-eval-print loop until quit or EOF.

    Args:
        argv: Unused; accepted for console-script symmetry.
    """
    client = client_build()
    cwd = ""
    while True:
        display = "/" + cwd if cwd else "/"
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
            cwd = cwd_resolve(cwd, arg)
            continue
        command_run(client, stripped, cwd)


if __name__ == "__main__":
    rush_run()
