"""File-save models for the agent-to-user file path.

``POST /save`` writes posted content into a file under the configured
base directory. The ``dir`` argument may name nested directories; the
``file`` argument must be a plain file name. Path traversal outside the
base directory is rejected by the file service, not by these models.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FileSaveRequest(BaseModel):
    """Payload for saving a file.

    Attributes:
        dir: Directory under the base directory, possibly nested
            (for example ``job-123/output``). Empty means the base
            directory itself.
        file: Plain file name, with no path separators.
        content: File content as text.
    """

    dir: str = ""
    file: str = Field(min_length=1)
    content: str = ""


class FileSaveResponse(BaseModel):
    """Outcome of a file save.

    Attributes:
        path: Path of the written file, relative to the base directory.
        bytesWritten: Number of bytes written.
    """

    path: str
    bytesWritten: int
