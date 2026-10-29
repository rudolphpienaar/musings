"""Tests for the ``.env`` loader used by every client."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from client.env import env_load


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the variables these tests set, so runs do not leak."""
    for key in ("MUSINGS_TOKEN", "MUSINGS_URL", "MUSINGS_PLAIN", "MUSINGS_QUOTED"):
        monkeypatch.delenv(key, raising=False)


def test_env_load_missing_file_is_silent(tmp_path: Path, clean_env: None) -> None:
    """A missing ``.env`` file loads nothing and raises nothing."""
    assert env_load(tmp_path / ".env") == 0
    assert "MUSINGS_TOKEN" not in os.environ


def test_env_load_parses_values(tmp_path: Path, clean_env: None) -> None:
    """Plain, quoted, exported, and commented lines are handled."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\n"
        "MUSINGS_TOKEN=from-file-token\n"
        "MUSINGS_URL=http://127.0.0.1:8000/api/v1\n"
        "export MUSINGS_PLAIN=plain\n"
        'MUSINGS_QUOTED="quoted value"\n'
        "MALFORMED LINE WITHOUT EQUALS\n",
        encoding="utf-8",
    )
    assert env_load(env_file) == 4
    assert os.environ["MUSINGS_TOKEN"] == "from-file-token"
    assert os.environ["MUSINGS_URL"] == "http://127.0.0.1:8000/api/v1"
    assert os.environ["MUSINGS_PLAIN"] == "plain"
    assert os.environ["MUSINGS_QUOTED"] == "quoted value"


def test_env_load_never_overrides_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """A real environment variable wins over the ``.env`` value."""
    monkeypatch.setenv("MUSINGS_TOKEN", "real-env-token")
    env_file = tmp_path / ".env"
    env_file.write_text("MUSINGS_TOKEN=from-file-token\n", encoding="utf-8")
    assert env_load(env_file) == 0
    assert os.environ["MUSINGS_TOKEN"] == "real-env-token"
