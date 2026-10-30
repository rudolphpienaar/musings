"""Rush models: synchronous command rendezvous payloads.

The ``/rush`` contract pairs one submitter POST with one attached
executor. The submitter's request stays open while the executor runs
the command; output arrives as JSON lines, and the final line is the
execution envelope. See ``docs/comms.adoc`` for the full contract.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RushSubmitRequest(BaseModel):
    """Payload for submitting a command to ``/rush``.

    Attributes:
        command: Command string for the executor to run.
        cwd: Working directory for the command, as the executor will
            interpret it: an absolute path on the executor's machine,
            or a path relative to the executor's base directory. Empty
            means the base itself. The server relays it unjudged.
    """

    command: str = Field(min_length=1)
    cwd: str = ""


class RushSubmission(BaseModel):
    """A command handed to an executor.

    Attributes:
        submissionId: Server-assigned identifier for this submission.
        command: Command string to run.
        cwd: Working directory, as submitted.
    """

    submissionId: str
    command: str
    cwd: str


class RushChunk(BaseModel):
    """One streamed output chunk.

    Attributes:
        stream: Which stream the chunk came from.
        data: Raw output text for this chunk.
    """

    stream: Literal["stdout", "stderr"]
    data: str


class RushResult(BaseModel):
    """Execution envelope closing an ``/rush`` stream.

    Attributes:
        type: Always ``result``; distinguishes the envelope from
            output chunks on the same stream.
        status: True when the program started and exited 0.
        returncode: Exit status, or None when the program could not
            be started.
        stdout: Complete standard output, as captured by the executor.
        stderr: Complete standard error, as captured by the executor.
        errors: Why a failed run failed, in words.
        truncated: True when output was capped by the executor.
        durationSeconds: How long the command ran.
    """

    type: Literal["result"] = "result"
    status: bool
    returncode: int | None
    stdout: str
    stderr: str
    errors: list[str] = []
    truncated: bool = False
    durationSeconds: float = 0.0


class RushError(BaseModel):
    """Error line ending an ``/rush`` stream without a result.

    A stream ending this way means the outcome is unknown: the
    command may still be running on an executor that disappeared.

    Attributes:
        type: Always ``error``.
        errors: What went wrong, in words.
    """

    type: Literal["error"] = "error"
    errors: str
