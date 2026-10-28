"""Message models for the plain information inbox.

Messages are the unstructured counterpart to jobs: free-form
information sent to the service, stored in submission order, with no
execution semantics attached.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MessageCreate(BaseModel):
    """Payload for posting a message.

    Attributes:
        content: Message body text.
        author: Who sent the message, for display only.
    """

    content: str = Field(min_length=1)
    author: str = "anonymous"


class Message(BaseModel):
    """A stored message.

    Attributes:
        messageId: Unique message identifier.
        content: Message body text.
        author: Who sent the message.
        createdAt: When the message was posted.
    """

    messageId: str
    content: str
    author: str
    createdAt: datetime
