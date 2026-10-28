"""File-save route: the agent-to-user file path."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import token_verify
from app.models.file import FileSaveRequest, FileSaveResponse
from app.services.file_store import FileStore, PathTraversalError, file_store_get

router = APIRouter(dependencies=[Depends(token_verify)])


@router.post("/save", response_model=FileSaveResponse, status_code=status.HTTP_201_CREATED)
async def file_save(
    payload: FileSaveRequest, store: FileStore = Depends(file_store_get)
) -> FileSaveResponse:
    """Save posted content to a file under the base directory.

    Args:
        payload: Directory, file name, and content.
        store: File store.

    Returns:
        The save outcome.

    Raises:
        HTTPException: 400 when the target would escape the base.
    """
    try:
        return store.file_save(payload.dir, payload.file, payload.content)
    except PathTraversalError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
