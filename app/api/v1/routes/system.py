"""System routes: public health and authenticated termination."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

from app import __version__
from app.core.security import token_verify
from app.models.system import HealthResponse, TerminateRequest, TerminateResponse
from app.services import termination
from app.services.file_store import FileStore, file_store_get
from app.services.job_store import JobStore, store_get

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_get() -> HealthResponse:
    """Report service health. This route is intentionally public.

    Returns:
        The health payload.
    """
    return HealthResponse(version=__version__)


@router.post("/terminate", response_model=TerminateResponse)
async def terminate_post(
    payload: TerminateRequest,
    backgroundTasks: BackgroundTasks,
    fileStore: FileStore = Depends(file_store_get),
    jobStore: JobStore = Depends(store_get),
    _token: str = Depends(token_verify),
) -> TerminateResponse:
    """Write the EOT marker and shut the server down.

    The EOT marker is written before the response is returned, and
    the process exits only after the response has been sent, so the
    caller always observes the termination outcome. In ChRIS mode
    the marker lands in the plugin output directory, and the process
    exit is what lets the workflow DAG continue.

    Args:
        payload: Termination request, with an optional reason.
        backgroundTasks: Runner for the post-response shutdown.
        fileStore: File store, for the saved-files manifest.
        jobStore: Job store, for the finished-job count.
        _token: Verified bearer token (unused beyond verification).

    Returns:
        The termination outcome.
    """
    baseDir = fileStore.baseDir
    jobCount = await jobStore.job_count_finished()
    eotPath = termination.eot_write(
        baseDir, fileStore.saved_list(), jobCount, payload.reason
    )
    termination.shutdown_schedule(backgroundTasks)
    return TerminateResponse(eotPath=eotPath.name)
