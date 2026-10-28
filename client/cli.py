"""Command-line client for the musings service.

Usage::

    export MUSINGS_URL=http://127.0.0.1:8000/api/v1
    export MUSINGS_TOKEN=<token>
    musings submit "df -kh"
    musings get <jobId>
    musings save mydir report.txt "hello"
    musings terminate

Every command reads the service URL and token from the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx


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
    # trust_env=False: httpx proxy-environment parsing breaks in sandboxed
    # environments, and musings servers are reached directly (localhost,
    # tailnet, or LAN). Proxy support is a roadmap item.
    return httpx.Client(
        base_url=baseUrl,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
        trust_env=False,
    )


def output_print(payload: object) -> None:
    """Print a response payload as indented JSON.

    Args:
        payload: Decoded JSON payload.
    """
    print(json.dumps(payload, indent=2))


def job_submit(client: httpx.Client, command: str, description: str) -> None:
    """Submit a job and print it.

    Args:
        client: Authenticated client.
        command: Shell command to execute.
        description: Human-readable note about the job.
    """
    response = client.post(
        "/jobs", json={"command": command, "description": description}
    )
    response.raise_for_status()
    output_print(response.json())


def job_get(client: httpx.Client, jobId: str) -> None:
    """Fetch one job and print it.

    Args:
        client: Authenticated client.
        jobId: Job identifier.
    """
    response = client.get(f"/jobs/{jobId}")
    response.raise_for_status()
    output_print(response.json())


def job_list(client: httpx.Client) -> None:
    """List all jobs and print them.

    Args:
        client: Authenticated client.
    """
    response = client.get("/jobs")
    response.raise_for_status()
    output_print(response.json())


def file_save(client: httpx.Client, dirName: str, fileName: str, content: str) -> None:
    """Save content to a file through the service and print the outcome.

    Args:
        client: Authenticated client.
        dirName: Directory under the service base directory.
        fileName: Plain file name.
        content: Text content to write.
    """
    response = client.post(
        "/save", json={"dir": dirName, "file": fileName, "content": content}
    )
    response.raise_for_status()
    output_print(response.json())


def message_post(client: httpx.Client, content: str, author: str) -> None:
    """Post a message to the inbox and print it.

    Args:
        client: Authenticated client.
        content: Message body.
        author: Author label.
    """
    response = client.post("/messages", json={"content": content, "author": author})
    response.raise_for_status()
    output_print(response.json())


def terminate_post(client: httpx.Client, reason: str) -> None:
    """Request server termination and print the outcome.

    Args:
        client: Authenticated client.
        reason: Reason recorded in the EOT manifest.
    """
    response = client.post("/terminate", json={"reason": reason})
    response.raise_for_status()
    output_print(response.json())


def parser_build() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(description="Client for the musings service.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    submitParser = subparsers.add_parser("submit", help="Submit a job.")
    submitParser.add_argument("shellCommand", help="Shell command to execute.")
    submitParser.add_argument("--description", default="", help="Job note.")

    getParser = subparsers.add_parser("get", help="Fetch one job.")
    getParser.add_argument("jobId", help="Job identifier.")

    subparsers.add_parser("list", help="List all jobs.")

    saveParser = subparsers.add_parser("save", help="Save a file.")
    saveParser.add_argument("dirName", help="Directory under the base directory.")
    saveParser.add_argument("fileName", help="Plain file name.")
    saveParser.add_argument("content", help="Text content to write.")

    messageParser = subparsers.add_parser("message", help="Post a message.")
    messageParser.add_argument("content", help="Message body.")
    messageParser.add_argument("--author", default="anonymous", help="Author label.")

    terminateParser = subparsers.add_parser("terminate", help="Terminate the server.")
    terminateParser.add_argument("--reason", default="", help="Reason for termination.")

    return parser


def cli_run(argv: list[str] | None = None) -> None:
    """Run the CLI.

    Args:
        argv: Argument list; None means sys.argv.
    """
    args = parser_build().parse_args(argv)
    client = client_build()
    try:
        if args.command == "submit":
            job_submit(client, args.shellCommand, args.description)
        elif args.command == "get":
            job_get(client, args.jobId)
        elif args.command == "list":
            job_list(client)
        elif args.command == "save":
            file_save(client, args.dirName, args.fileName, args.content)
        elif args.command == "message":
            message_post(client, args.content, args.author)
        elif args.command == "terminate":
            terminate_post(client, args.reason)
    except httpx.HTTPStatusError as error:
        print(f"Request failed: {error.response.status_code} {error.response.text}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    cli_run()
