"""Tests for the rush executor's working-directory decision."""

from __future__ import annotations

from pathlib import Path

from client.rush_executor import cwd_resolve


def test_unconfined_accepts_absolute_paths(tmp_path: Path) -> None:
    """Without --clamp any directory on the machine may be named."""
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    assert cwd_resolve(str(base), str(outside), clamp=False) == str(outside.resolve())
    assert cwd_resolve(str(base), "..", clamp=False) == str(tmp_path.resolve())


def test_empty_cwd_is_the_base_in_both_modes(tmp_path: Path) -> None:
    """An empty working directory lands in the base, confined or not."""
    assert cwd_resolve(str(tmp_path), "", clamp=False) == str(tmp_path.resolve())
    assert cwd_resolve(str(tmp_path), "", clamp=True) == str(tmp_path.resolve())


def test_relative_cwd_joins_the_base(tmp_path: Path) -> None:
    """A relative directory is taken from the base."""
    assert cwd_resolve(str(tmp_path), "a/b", clamp=True) == str((tmp_path / "a" / "b").resolve())


def test_clamped_refuses_escapes(tmp_path: Path) -> None:
    """With --clamp a path outside the base, however spelled, is refused."""
    base = tmp_path / "base"
    base.mkdir()
    assert cwd_resolve(str(base), "..", clamp=True) is None
    assert cwd_resolve(str(base), str(tmp_path), clamp=True) is None
    assert cwd_resolve(str(base), "a/../../x", clamp=True) is None


def test_clamped_accepts_absolute_path_inside_base(tmp_path: Path) -> None:
    """An absolute path that stays inside the base is fine when clamped."""
    inside = tmp_path / "inside"
    inside.mkdir()
    assert cwd_resolve(str(tmp_path), str(inside), clamp=True) == str(inside.resolve())
