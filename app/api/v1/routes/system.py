"""System routes: public health and authenticated EOT termination."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

from app import __version__
from app.core.security import token_verify
from app.models.system import EotRequest, EotResponse, HealthResponse
from app.services import termination
from app.services.file_store import FileStore, file_store_get

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_get() -> HealthResponse:
    """Report service health. This route is intentionally public.

    Returns:
        The health payload.
    """
    return HealthResponse(version=__version__)


@router.post("/eot", response_model=EotResponse)
async def eot_post(
    payload: EotRequest,
    backgroundTasks: BackgroundTasks,
    fileStore: FileStore = Depends(file_store_get),
    _token: str = Depends(token_verify),
) -> EotResponse:
    """Write the bare EOT marker and shut the server down.

    The endpoint does only this: the payload model guards against
    generic POSTs, the marker is written before the response is
    returned, and the process exits only after the response has
    been sent, so the caller always observes the termination
    outcome. In ChRIS mode the marker lands in the plugin output
    directory, and the process exit is what lets the workflow DAG
    continue.

    Args:
        payload: Termination request; ``confirm`` must be ``EOT``.
        backgroundTasks: Runner for the post-response shutdown.
        fileStore: File store, for the base directory.
        _token: Verified bearer token (unused beyond verification).

    Returns:
        The termination outcome.
    """
    eotPath = termination.eot_write(fileStore.baseDir, payload.reason)
    termination.shutdown_schedule(backgroundTasks)
    return EotResponse(eotPath=eotPath.name)
