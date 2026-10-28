"""File store: safe writes under a configured base directory.

The store is the security boundary for ``POST /save``: every write is
resolved against the base directory and rejected when it would escape
it, whether through ``..`` components, absolute paths, or separators
in the file name. Parent directories are created on demand, and every
successful write is recorded for the EOT manifest.
"""

from __future__ import annotations

from pathlib import Path

from app.models.file import FileSaveResponse


class PathTraversalError(ValueError):
    """Raised when a requested path would escape the base directory."""


def path_resolve(baseDir: Path, dirName: str, fileName: str) -> Path:
    """Resolve a dir/file pair strictly under a base directory.

    Args:
        baseDir: Root directory all writes are confined to.
        dirName: Directory under the base, possibly nested and empty.
        fileName: Plain file name with no path separators.

    Returns:
        The resolved absolute path of the target file.

    Raises:
        PathTraversalError: When the target would escape the base.
    """
    baseResolved = baseDir.resolve()
    if Path(fileName).name != fileName or fileName in ("", ".", ".."):
        raise PathTraversalError(f"Invalid file name: {fileName!r}.")
    parts: list[str] = []
    if dirName:
        dirPath = Path(dirName)
        if dirPath.is_absolute():
            raise PathTraversalError(f"Absolute directory rejected: {dirName!r}.")
        for part in dirPath.parts:
            if part == "..":
                raise PathTraversalError(f"Parent escape rejected: {dirName!r}.")
            parts.append(part)
    candidate = (baseResolved.joinpath(*parts, fileName) if parts else baseResolved / fileName)
    if not candidate.is_relative_to(baseResolved):
        raise PathTraversalError(f"Path escapes base directory: {dirName!r}.")
    return candidate


class FileStore:
    """File writer confined to one base directory."""

    def __init__(self, baseDir: Path) -> None:
        """Initialize the store and record its base directory.

        Args:
            baseDir: Root directory for all writes.
        """
        self.baseDir = baseDir
        self.savedPaths: list[str] = []

    def file_save(self, dirName: str, fileName: str, content: str) -> FileSaveResponse:
        """Write content to a file under the base directory.

        Args:
            dirName: Directory under the base, possibly nested.
            fileName: Plain file name.
            content: Text content to write.

        Returns:
            The save outcome, with the path relative to the base.

        Raises:
            PathTraversalError: When the target escapes the base.
        """
        target = path_resolve(self.baseDir, dirName, fileName)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        target.write_bytes(data)
        relativePath = str(target.relative_to(self.baseDir.resolve()))
        self.savedPaths.append(relativePath)
        return FileSaveResponse(path=relativePath, bytesWritten=len(data))

    def saved_list(self) -> list[str]:
        """List paths written during this run, relative to the base.

        Returns:
            Saved paths in write order.
        """
        return list(self.savedPaths)


_FILE_STORE: FileStore | None = None


def file_store_get() -> FileStore:
    """Return the process-wide file store (FastAPI dependency).

    Returns:
        The shared file store, rooted at the configured base directory.
    """
    global _FILE_STORE
    if _FILE_STORE is None:
        from app.core.config import settings_get

        _FILE_STORE = FileStore(settings_get().musingsBaseDir)
    return _FILE_STORE
