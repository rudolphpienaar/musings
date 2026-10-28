"""Job routes: submit, claim, inspect, and finish jobs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.security import token_verify
from app.models.job import Job, JobCreate, JobResult
from app.services.job_store import JobStore, store_get

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
async def job_pending_get(store: JobStore = Depends(store_get)) -> Response | Job:
    """Claim the oldest queued job for execution.

    When the queue is empty this returns 204 No Content, which the
    poller treats as "nothing to do". Claiming is atomic, so a job
    is never handed to two pollers.

    Args:
        store: Job store.

    Returns:
        The claimed job, or an empty 204 response.
    """
    job = await store.job_claim_next()
    if job is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return job


@router.get("/jobs/{jobId}", response_model=Job)
async def job_get(jobId: str, store: JobStore = Depends(store_get)) -> Job:
    """Fetch one job by id.

    Args:
        jobId: Job identifier.
        store: Job store.

    Returns:
        The job.

    Raises:
        HTTPException: 404 when the job is unknown.
    """
    job = await store.job_get(jobId)
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
