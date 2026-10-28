"""System models: health, termination, and the EOT manifest.

The EOT (end-of-transmission) marker is the contract with downstream
consumers, including ChRIS workflows: its presence in the output
directory means transmission for this run is complete, and its content
is a manifest of what was delivered.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Service health payload.

    Attributes:
        service: Service name.
        version: Service version.
        status: Health status string.
    """

    service: str = "musings"
    version: str
    status: str = "ok"


class TerminateRequest(BaseModel):
    """Payload for requesting server termination.

    Attributes:
        reason: Human-readable reason for termination, recorded in the
            EOT manifest.
    """

    reason: str = ""


class TerminateResponse(BaseModel):
    """Outcome of a termination request.

    Attributes:
        status: Always ``terminating``: the response is sent before the
            process exits.
        eotPath: Path of the EOT marker, relative to the base directory.
    """

    status: str = "terminating"
    eotPath: str


class EotManifest(BaseModel):
    """Content of the EOT marker file.

    Attributes:
        status: ``complete`` for a normal termination, ``timeout`` when
            the wrapper gave up waiting (written by the wrapper, not
            by this endpoint).
        terminatedAt: When termination was requested.
        reason: Reason supplied with the termination request.
        filesWritten: Paths saved through ``/save`` during this run,
            relative to the base directory.
        jobCount: Number of jobs finished during this run.
    """

    status: str = "complete"
    terminatedAt: datetime
    reason: str = ""
    filesWritten: list[str] = []
    jobCount: int = 0
