"""Message routes: the plain information inbox."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.security import token_verify
from app.models.message import Message, MessageCreate
from app.services.message_store import MessageStore, message_store_get

router = APIRouter(dependencies=[Depends(token_verify)])


@router.post(
    "/messages", response_model=Message, status_code=status.HTTP_201_CREATED
)
async def message_post(
    payload: MessageCreate, store: MessageStore = Depends(message_store_get)
) -> Message:
    """Post a message to the inbox.

    Args:
        payload: Submission payload.
        store: Message store.

    Returns:
        The stored message.
    """
    return await store.message_post(payload)


@router.get("/messages", response_model=list[Message])
async def message_list(store: MessageStore = Depends(message_store_get)) -> list[Message]:
    """List messages in submission order.

    Args:
        store: Message store.

    Returns:
        Every stored message.
    """
    return await store.message_list()
