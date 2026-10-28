"""Tests for the REPL client's working-directory handling."""

from __future__ import annotations

from client.repl import cwd_resolve


def test_cwd_resolve() -> None:
    """Cd resolution joins, pops, and clamps at the base."""
    assert cwd_resolve("", None) == ""
    assert cwd_resolve("a", None) == ""
    assert cwd_resolve("", "a") == "a"
    assert cwd_resolve("a", "b") == "a/b"
    assert cwd_resolve("a/b", "..") == "a"
    assert cwd_resolve("a", "../..") == ""
    assert cwd_resolve("a", "/") == ""
    assert cwd_resolve("a", "/x") == "x"
    assert cwd_resolve("a/b", "../c") == "a/c"
    assert cwd_resolve("", ".") == ""
