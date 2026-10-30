"""Tests for the server entry point's exposure and TLS gating."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from app.core.config import settings_get
from app.main import pollerUrl_derive, server_run


@pytest.fixture
def uvicorn_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Capture ``uvicorn.run`` keyword arguments instead of serving."""
    calls: list[dict[str, object]] = []

    def run_fake(app: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("app.main.uvicorn.run", run_fake)
    return calls


@pytest.fixture
def fresh_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[None, None, None]:
    """Clear the settings cache and the TLS variables around each test.

    Also runs from an empty directory, so a real ``.env`` in the
    repository root cannot leak its TLS setup into these tests.
    """
    monkeypatch.chdir(tmp_path)
    for name in (
        "MUSINGS_URL",
        "MUSINGS_TLS_CERT",
        "MUSINGS_TLS_KEY",
        "MUSINGS_TLS_CLIENT_CA",
    ):
        monkeypatch.delenv(name, raising=False)
    settings_get.cache_clear()
    yield
    settings_get.cache_clear()


def test_loopback_plain_http_is_allowed(
    uvicorn_calls: list[dict[str, object]], fresh_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default loopback listener needs no TLS and no flag."""
    monkeypatch.setenv("MUSINGS_URL", "http://127.0.0.1:8000/api/v1")
    server_run([])
    assert uvicorn_calls[0]["host"] == "127.0.0.1"
    assert "ssl_certfile" not in uvicorn_calls[0]


def test_public_plain_http_is_refused(
    uvicorn_calls: list[dict[str, object]], fresh_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Listening beyond loopback in the clear is refused without --insecure."""
    monkeypatch.setenv("MUSINGS_URL", "http://127.0.0.1:8000/api/v1")
    with pytest.raises(SystemExit) as excinfo:
        server_run(["--public"])
    assert excinfo.value.code == 2
    assert uvicorn_calls == []


def test_public_plain_http_with_insecure_is_allowed(
    uvicorn_calls: list[dict[str, object]], fresh_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--insecure accepts the cleartext exposure on purpose."""
    monkeypatch.setenv("MUSINGS_URL", "http://127.0.0.1:8000/api/v1")
    server_run(["--public", "--insecure"])
    assert uvicorn_calls[0]["host"] == "0.0.0.0"


def test_https_without_files_is_refused(
    uvicorn_calls: list[dict[str, object]], fresh_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An https URL with no certificate is a startup error, even on loopback."""
    monkeypatch.setenv("MUSINGS_URL", "https://127.0.0.1:8443/api/v1")
    with pytest.raises(SystemExit):
        server_run([])
    assert uvicorn_calls == []


def test_public_https_passes_tls_files_to_uvicorn(
    uvicorn_calls: list[dict[str, object]],
    fresh_settings: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With TLS configured, --public needs no --insecure and uvicorn gets the files."""
    for name in ("server.pem", "server.key"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    monkeypatch.setenv("MUSINGS_URL", "https://127.0.0.1:8443/api/v1")
    monkeypatch.setenv("MUSINGS_TLS_CERT", str(tmp_path / "server.pem"))
    monkeypatch.setenv("MUSINGS_TLS_KEY", str(tmp_path / "server.key"))
    server_run(["--public"])
    call = uvicorn_calls[0]
    assert call["host"] == "0.0.0.0"
    assert call["port"] == 8443
    assert call["ssl_certfile"] == str(tmp_path / "server.pem")
    assert "ssl_ca_certs" not in call


def test_poller_url_defaults_to_loopback_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """A public hostname that resolves off-box is replaced by 127.0.0.1."""
    monkeypatch.setattr(
        "app.main.socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("73.167.233.252", 0))],
    )
    url = pollerUrl_derive("https://musings.example.org:8888/api/v1", 8888, "https")
    assert url == "https://127.0.0.1:8888/api/v1"


def test_poller_url_keeps_hostname_that_resolves_to_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An /etc/hosts override keeps the name, so a public certificate verifies."""
    monkeypatch.setattr(
        "app.main.socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("127.0.0.1", 0))],
    )
    url = pollerUrl_derive("https://musings.example.org:8888/api/v1", 8888, "https")
    assert url == "https://musings.example.org:8888/api/v1"


def test_poller_url_without_configured_url() -> None:
    """With no MUSINGS_URL at all the poller dials plain loopback."""
    assert pollerUrl_derive(None, 8000, "http") == "http://127.0.0.1:8000/api/v1"


def test_poller_url_unresolvable_name_falls_back() -> None:
    """A name that does not resolve is not trusted to be loopback."""
    url = pollerUrl_derive("https://no-such-host.invalid:8888/api/v1", 8888, "https")
    assert url == "https://127.0.0.1:8888/api/v1"
