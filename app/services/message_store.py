"""In-memory message store for the information inbox."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from app.models.message import Message, MessageCreate


class MessageStore:
    """Append-only in-memory store for posted messages."""

    def __init__(self) -> None:
        """Initialize an empty store."""
        self.messages: list[Message] = []
        self.lock = asyncio.Lock()

    async def message_post(self, payload: MessageCreate) -> Message:
        """Store a message.

        Args:
            payload: Submission payload.

        Returns:
            The stored message.
        """
        async with self.lock:
            message = Message(
                messageId=uuid.uuid4().hex,
                content=payload.content,
                author=payload.author,
                createdAt=datetime.now(timezone.utc),
            )
            self.messages.append(message)
            return message

    async def message_list(self) -> list[Message]:
        """List messages in submission order.

        Returns:
            Every stored message.
        """
        async with self.lock:
            return list(self.messages)


MESSAGE_STORE = MessageStore()


def message_store_get() -> MessageStore:
    """Return the process-wide message store (FastAPI dependency).

    Returns:
        The shared message store.
    """
    return MESSAGE_STORE
