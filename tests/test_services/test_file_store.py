"""Tests for path resolution and the file store."""

from __future__ import annotations

import pytest

from app.services.file_store import FileStore, PathTraversalError, path_resolve


def test_path_resolve_nested(tmp_path) -> None:
    """Nested dirs under the base resolve cleanly."""
    resolved = path_resolve(tmp_path, "a/b", "c.txt")
    assert str(resolved).endswith("a/b/c.txt")


def test_path_resolve_rejects_escapes(tmp_path) -> None:
    """Parent escapes, absolute dirs, and separator file names fail."""
    with pytest.raises(PathTraversalError):
        path_resolve(tmp_path, "../out", "x.txt")
    with pytest.raises(PathTraversalError):
        path_resolve(tmp_path, "/tmp", "x.txt")
    with pytest.raises(PathTraversalError):
        path_resolve(tmp_path, "", "sub/x.txt")
    with pytest.raises(PathTraversalError):
        path_resolve(tmp_path, "", "..")


def test_file_save_roundtrip(tmp_path) -> None:
    """Saved content reads back, and the path is recorded."""
    store = FileStore(tmp_path)
    outcome = store.file_save("job-1", "a.txt", "hello")
    assert outcome.path == "job-1/a.txt"
    assert outcome.bytesWritten == 5
    assert (tmp_path / "job-1" / "a.txt").read_text() == "hello"
    assert store.saved_list() == ["job-1/a.txt"]
