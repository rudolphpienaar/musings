"""Rush routes: synchronous command rendezvous.

A submitter POSTs a command and holds the response open while an
attached executor runs it; output streams back as JSON lines, and
the final line is the execution envelope. Executors claim work
through a long-poll, so they only need outbound connections.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse

from app.core.security import token_verify
from app.models.rush import RushChunk, RushResult, RushSubmitRequest
from app.services.rush_store import (
    NoExecutorError,
    RushStore,
    UnclaimedSubmissionError,
    UnknownSubmissionError,
    cwd_validate,
    rush_store_get,
)

router = APIRouter()


@router.post("/rush")
async def rush_post(
    payload: RushSubmitRequest,
    store: RushStore = Depends(rush_store_get),
    _token: str = Depends(token_verify),
) -> StreamingResponse:
    """Submit a command and stream its execution back.

    The response stays open until the executor finishes: JSON lines
    for output chunks, closed by the result envelope. When no
    executor is attached the request fails fast instead of hanging.

    Args:
        payload: Command string and working directory.
        store: Rendezvous store.
        _token: Verified bearer token (unused beyond verification).

    Returns:
        A streaming newline-delimited JSON response.

    Raises:
        HTTPException: 400 on an escaping working directory, 503
            when no executor is attached.
    """
    try:
        cwd = cwd_validate(payload.cwd)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    try:
        record = store.submission_create(payload.command, cwd)
    except NoExecutorError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return StreamingResponse(
        store.stream_lines(record.submission.submissionId),
        media_type="application/x-ndjson",
    )


@router.get("/rush/next")
async def rush_next(
    executorId: str = "default",
    store: RushStore = Depends(rush_store_get),
    _token: str = Depends(token_verify),
):
    """Claim the next pending submission, waiting if necessary.

    This is the executor's long-poll: it returns the next
    submission, or 204 when the wait expires with nothing pending.
    Every call also marks the executor as attached.

    Args:
        executorId: Executor identifier, for presence tracking.
        store: Rendezvous store.
        _token: Verified bearer token (unused beyond verification).

    Returns:
        The claimed submission, or an empty 204 response.
    """
    submission = await store.submission_claim(executorId)
    if submission is None:
        return Response(status_code=204)
    return submission


@router.post("/rush/{submissionId}/chunk", status_code=202)
async def rush_chunk_post(
    submissionId: str,
    payload: RushChunk,
    store: RushStore = Depends(rush_store_get),
    _token: str = Depends(token_verify),
) -> dict:
    """Append an output chunk to a submission's stream.

    Args:
        submissionId: Target submission.
        payload: Output chunk to deliver.
        store: Rendezvous store.
        _token: Verified bearer token (unused beyond verification).

    Returns:
        An acceptance acknowledgement.

    Raises:
        HTTPException: 404 for an unknown submission, 409 for one
            that was never claimed.
    """
    try:
        store.chunk_post(submissionId, payload)
    except UnknownSubmissionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except UnclaimedSubmissionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"status": "accepted"}


@router.post("/rush/{submissionId}/result", status_code=202)
async def rush_result_post(
    submissionId: str,
    payload: RushResult,
    store: RushStore = Depends(rush_store_get),
    _token: str = Depends(token_verify),
) -> dict:
    """Close a submission's stream with the result envelope.

    Args:
        submissionId: Target submission.
        payload: Final execution envelope.
        store: Rendezvous store.
        _token: Verified bearer token (unused beyond verification).

    Returns:
        An acceptance acknowledgement.

    Raises:
        HTTPException: 404 for an unknown submission, 409 for one
            that was never claimed.
    """
    try:
        store.result_post(submissionId, payload)
    except UnknownSubmissionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except UnclaimedSubmissionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"status": "accepted"}
