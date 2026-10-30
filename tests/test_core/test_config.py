"""Tests for server settings, including ``.env`` file loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import DEFAULT_PORT, MusingsSettings, TlsConfigError


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


def test_port_derived_from_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server listens on the port MUSINGS_URL names."""
    monkeypatch.delenv("MUSINGS_TOKEN", raising=False)
    monkeypatch.delenv("MUSINGS_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MUSINGS_TOKEN=from-file-token\n"
        "MUSINGS_URL=http://127.0.0.1:8888/api/v1\n",
        encoding="utf-8",
    )
    settings = MusingsSettings(_env_file=env_file)
    assert settings.port_derive() == 8888


def test_port_defaults_without_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no URL configured the server falls back to the default port."""
    monkeypatch.delenv("MUSINGS_TOKEN", raising=False)
    monkeypatch.delenv("MUSINGS_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("MUSINGS_TOKEN=from-file-token\n", encoding="utf-8")
    settings = MusingsSettings(_env_file=env_file)
    assert settings.port_derive() == DEFAULT_PORT


def test_url_without_port_uses_scheme_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A portless URL falls back to its scheme default."""
    monkeypatch.delenv("MUSINGS_TOKEN", raising=False)
    monkeypatch.delenv("MUSINGS_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MUSINGS_TOKEN=from-file-token\n"
        "MUSINGS_URL=https://example.invalid/api/v1\n",
        encoding="utf-8",
    )
    settings = MusingsSettings(_env_file=env_file)
    assert settings.port_derive() == 443


def _settings_from(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> MusingsSettings:
    """Build settings from an env file body with the TLS variables cleared."""
    for name in (
        "MUSINGS_TOKEN",
        "MUSINGS_URL",
        "MUSINGS_TLS_CERT",
        "MUSINGS_TLS_KEY",
        "MUSINGS_TLS_CLIENT_CA",
    ):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("MUSINGS_TOKEN=from-file-token\n" + body, encoding="utf-8")
    return MusingsSettings(_env_file=env_file)


def test_plain_http_has_no_tls_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An http URL means no TLS at all, and no error."""
    settings = _settings_from(tmp_path, monkeypatch, "MUSINGS_URL=http://127.0.0.1:8000/api/v1\n")
    assert settings.tls_isEnabled() is False
    assert settings.tlsServer_config() is None


def test_https_without_cert_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An https URL with no certificate files is a configuration error."""
    settings = _settings_from(tmp_path, monkeypatch, "MUSINGS_URL=https://127.0.0.1:8443/api/v1\n")
    assert settings.tls_isEnabled() is True
    with pytest.raises(TlsConfigError, match="MUSINGS_TLS_CERT"):
        settings.tlsServer_config()


def test_https_with_missing_file_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Certificate paths must exist; a typo fails at startup, not in the handshake."""
    certPath = tmp_path / "server.pem"
    certPath.write_text("cert", encoding="utf-8")
    settings = _settings_from(
        tmp_path,
        monkeypatch,
        "MUSINGS_URL=https://127.0.0.1:8443/api/v1\n"
        f"MUSINGS_TLS_CERT={certPath}\n"
        f"MUSINGS_TLS_KEY={tmp_path / 'missing.key'}\n",
    )
    with pytest.raises(TlsConfigError, match="MUSINGS_TLS_KEY"):
        settings.tlsServer_config()


def test_https_config_assembles_uvicorn_kwargs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A complete TLS setup yields the uvicorn ssl arguments, mTLS included."""
    import ssl

    for name in ("server.pem", "server.key", "ca.pem"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    settings = _settings_from(
        tmp_path,
        monkeypatch,
        "MUSINGS_URL=https://127.0.0.1:8443/api/v1\n"
        f"MUSINGS_TLS_CERT={tmp_path / 'server.pem'}\n"
        f"MUSINGS_TLS_KEY={tmp_path / 'server.key'}\n"
        f"MUSINGS_TLS_CLIENT_CA={tmp_path / 'ca.pem'}\n",
    )
    config = settings.tlsServer_config()
    assert config is not None
    kwargs = config.uvicornKwargs_get()
    assert kwargs["ssl_certfile"] == str(tmp_path / "server.pem")
    assert kwargs["ssl_keyfile"] == str(tmp_path / "server.key")
    assert kwargs["ssl_ca_certs"] == str(tmp_path / "ca.pem")
    assert kwargs["ssl_cert_reqs"] == ssl.CERT_REQUIRED


def test_client_ca_without_tls_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutual TLS cannot be demanded on a plain http listener."""
    settings = _settings_from(
        tmp_path,
        monkeypatch,
        "MUSINGS_URL=http://127.0.0.1:8000/api/v1\n"
        f"MUSINGS_TLS_CLIENT_CA={tmp_path / 'ca.pem'}\n",
    )
    with pytest.raises(TlsConfigError, match="needs TLS"):
        settings.tlsServer_config()
