"""System models: health, EOT termination.

The EOT (end-of-transmission) marker is the contract with downstream
consumers, including ChRIS workflows: its presence in the output
directory means transmission for this run is complete. The EOT
endpoint does only terminate, and its payload model is the guard
that keeps a generic POST from shutting the server down.
"""

from __future__ import annotations

from typing import Literal

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


class EotRequest(BaseModel):
    """Payload for requesting server termination.

    Attributes:
        confirm: Must be exactly ``EOT``; the guard against generic
            POSTs triggering a shutdown.
        reason: Human-readable reason for termination, recorded in
            the EOT marker.
    """

    confirm: Literal["EOT"]
    reason: str = ""


class EotResponse(BaseModel):
    """Outcome of a termination request.

    Attributes:
        status: Always ``terminating``: the response is sent before the
            process exits.
        eotPath: Path of the EOT marker, relative to the base directory.
    """

    status: str = "terminating"
    eotPath: str


class EotMarker(BaseModel):
    """Content of the EOT marker file.

    The marker carries no accounting: no job counts, no file index.
    Its presence is the completion signal; its content is only the
    reason it was written.

    Attributes:
        status: ``complete`` for a normal termination.
        reason: Reason supplied with the termination request.
    """

    status: str = "complete"
    reason: str = ""
