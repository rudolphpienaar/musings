"""Tests for server settings, including ``.env`` file loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import MusingsSettings


def test_settings_load_token_from_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The token is read from an explicit ``.env`` file when unset."""
    monkeypatch.delenv("MUSINGS_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("MUSINGS_TOKEN=from-file-token\n", encoding="utf-8")
    settings = MusingsSettings(_env_file=env_file)
    assert settings.musingsToken == "from-file-token"


def test_settings_environment_beats_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real environment variable wins over the ``.env`` value."""
    monkeypatch.setenv("MUSINGS_TOKEN", "real-env-token")
    env_file = tmp_path / ".env"
    env_file.write_text("MUSINGS_TOKEN=from-file-token\n", encoding="utf-8")
    settings = MusingsSettings(_env_file=env_file)
    assert settings.musingsToken == "real-env-token"


def test_settings_ignores_client_only_env_file_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Client-only keys in a shared ``.env`` do not break the server."""
    monkeypatch.delenv("MUSINGS_TOKEN", raising=False)
    monkeypatch.delenv("MUSINGS_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MUSINGS_TOKEN=from-file-token\n"
        "MUSINGS_URL=http://127.0.0.1:8000/api/v1\n"
        "MUSINGS_RUSH_BASE_DIR=/work\n",
        encoding="utf-8",
    )
    settings = MusingsSettings(_env_file=env_file)
    assert settings.musingsToken == "from-file-token"


def test_bind_derived_from_url_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server binds where the URL points when host/port are unset."""
    monkeypatch.delenv("MUSINGS_TOKEN", raising=False)
    monkeypatch.delenv("MUSINGS_HOST", raising=False)
    monkeypatch.delenv("MUSINGS_PORT", raising=False)
    monkeypatch.delenv("MUSINGS_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MUSINGS_TOKEN=from-file-token\n"
        "MUSINGS_URL=http://127.0.0.1:8888/api/v1\n",
        encoding="utf-8",
    )
    settings = MusingsSettings(_env_file=env_file)
    assert settings.musingsHost == "127.0.0.1"
    assert settings.musingsPort == 8888


def test_explicit_host_port_beat_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicitly set host or port always wins over the URL."""
    monkeypatch.delenv("MUSINGS_TOKEN", raising=False)
    monkeypatch.delenv("MUSINGS_HOST", raising=False)
    monkeypatch.delenv("MUSINGS_PORT", raising=False)
    monkeypatch.delenv("MUSINGS_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MUSINGS_TOKEN=from-file-token\n"
        "MUSINGS_URL=http://127.0.0.1:8888/api/v1\n"
        "MUSINGS_HOST=0.0.0.0\n"
        "MUSINGS_PORT=9000\n",
        encoding="utf-8",
    )
    settings = MusingsSettings(_env_file=env_file)
    assert settings.musingsHost == "0.0.0.0"
    assert settings.musingsPort == 9000


def test_url_without_port_uses_scheme_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A portless URL falls back to its scheme default."""
    monkeypatch.delenv("MUSINGS_TOKEN", raising=False)
    monkeypatch.delenv("MUSINGS_HOST", raising=False)
    monkeypatch.delenv("MUSINGS_PORT", raising=False)
    monkeypatch.delenv("MUSINGS_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MUSINGS_TOKEN=from-file-token\n"
        "MUSINGS_URL=http://example.invalid/api/v1\n",
        encoding="utf-8",
    )
    settings = MusingsSettings(_env_file=env_file)
    assert settings.musingsHost == "example.invalid"
    assert settings.musingsPort == 80
