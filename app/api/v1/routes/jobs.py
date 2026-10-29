"""Job routes: submit, claim, inspect, and finish jobs.

Claiming and result fetching both long-poll when asked: ``wait``
caps at 30 seconds so a parked request always returns before common
proxy timeouts, and the caller simply polls again. The default of 0
preserves the original immediate behavior.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.core.security import token_verify
from app.models.job import Job, JobCreate, JobResult, PollerStatus
from app.services.job_store import JobStore, store_get

WAIT_CAP_SECONDS = 30.0

router = APIRouter(dependencies=[Depends(token_verify)])


@router.post("/jobs", response_model=Job, status_code=status.HTTP_201_CREATED)
async def job_submit(payload: JobCreate, store: JobStore = Depends(store_get)) -> Job:
    """Submit a command for execution.

    Args:
        payload: Submission payload.
        store: Job store.

    Returns:
        The created queued job.
    """
    return await store.job_create(payload)


@router.get("/jobs", response_model=list[Job])
async def job_list(store: JobStore = Depends(store_get)) -> list[Job]:
    """List all jobs in submission order.

    Args:
        store: Job store.

    Returns:
        Every job known to this run.
    """
    return await store.job_list()


@router.get("/jobs/pending", response_model=None)
async def job_pending_get(
    wait: float = Query(default=0.0, ge=0.0, le=WAIT_CAP_SECONDS),
    store: JobStore = Depends(store_get),
) -> Response | Job:
    """Claim the oldest queued job for execution.

    With ``wait`` the request long-polls: it parks until a job is
    queued, and returns 204 No Content only when the wait expires
    with nothing to claim. The poller treats 204 as "poll again", so
    a freshly submitted job is claimed within a network round-trip
    instead of a poll interval. Every call stamps poller presence,
    which ``GET /jobs/poller`` reports. Claiming is atomic, so a job
    is never handed to two pollers.

    Args:
        wait: Seconds to wait for a job when the queue is empty;
            0 returns immediately.
        store: Job store.

    Returns:
        The claimed job, or an empty 204 response.
    """
    store.poller_touch()
    job = await store.job_claim_next(waitSeconds=wait)
    if job is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return job


@router.get("/jobs/poller", response_model=PollerStatus)
async def poller_status_get(store: JobStore = Depends(store_get)) -> PollerStatus:
    """Report whether a poller is attached to this run.

    Advisory presence, mirroring the rush executor presence window:
    attached means some poller hit ``GET /jobs/pending`` recently.
    Submissions queue either way; clients use this to warn a human
    that nothing will execute their commands.

    Args:
        store: Job store.

    Returns:
        The poller attachment status.
    """
    return store.poller_status()


@router.get("/jobs/{jobId}", response_model=Job)
async def job_get(
    jobId: str,
    wait: float = Query(default=0.0, ge=0.0, le=WAIT_CAP_SECONDS),
    store: JobStore = Depends(store_get),
) -> Job:
    """Fetch one job by id, optionally waiting for its result.

    With ``wait`` the request long-polls: it parks until the job
    reaches a terminal state and returns the moment the result is
    posted. When the wait expires first the job is returned in its
    current state, so the caller polls again.

    Args:
        jobId: Job identifier.
        wait: Seconds to wait for a terminal state; 0 returns the
            current state immediately.
        store: Job store.

    Returns:
        The job.

    Raises:
        HTTPException: 404 when the job is unknown.
    """
    job = await store.job_wait(jobId, wait) if wait > 0 else await store.job_get(jobId)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown job.")
    return job


@router.post("/jobs/{jobId}/result", response_model=Job)
async def job_result_submit(
    jobId: str, result: JobResult, store: JobStore = Depends(store_get)
) -> Job:
    """Attach an execution result to a claimed job and close it.

    Args:
        jobId: Job identifier.
        result: Execution result posted by the poller.
        store: Job store.

    Returns:
        The finished job.

    Raises:
        HTTPException: 404 when the job is unknown, 409 when it is
            not in the claimed state.
    """
    job = await store.result_submit(jobId, result)
    if job is None:
        existing = await store.job_get(jobId)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Unknown job."
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is {existing.state}, not claimed.",
        )
    return job
