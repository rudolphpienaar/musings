"""Tests for the shared client factory's TLS option resolution."""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest

from client.transport import tlsOptions_build


def test_http_url_ignores_tls_variables(tmp_path: Path) -> None:
    """For a plain http URL the TLS variables are not consulted at all."""
    options = tlsOptions_build(
        "http://127.0.0.1:8000/api/v1",
        {"MUSINGS_CA_CERT": str(tmp_path / "absent.pem")},
    )
    assert options is None


def test_https_without_ca_uses_system_trust() -> None:
    """With no CA named the system trust store applies; verification is never off."""
    options = tlsOptions_build("https://example.invalid/api/v1", {})
    assert options is not None
    assert options.caFile is None
    assert options.clientCert is None
    context = options.sslContext_build()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_https_pins_ca_and_presents_client_cert(tmp_path: Path) -> None:
    """A named CA becomes the verify path and a cert pair is passed through."""
    for name in ("ca.pem", "me.pem", "me.key"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    options = tlsOptions_build(
        "https://127.0.0.1:8443/api/v1",
        {
            "MUSINGS_CA_CERT": str(tmp_path / "ca.pem"),
            "MUSINGS_CLIENT_CERT": str(tmp_path / "me.pem"),
            "MUSINGS_CLIENT_KEY": str(tmp_path / "me.key"),
        },
    )
    assert options is not None
    assert options.caFile == str(tmp_path / "ca.pem")
    assert options.clientCert == (str(tmp_path / "me.pem"), str(tmp_path / "me.key"))


def test_client_cert_without_key_exits(tmp_path: Path) -> None:
    """Half a client identity is a configuration error."""
    (tmp_path / "me.pem").write_text("cert", encoding="utf-8")
    with pytest.raises(SystemExit):
        tlsOptions_build(
            "https://127.0.0.1:8443/api/v1",
            {"MUSINGS_CLIENT_CERT": str(tmp_path / "me.pem")},
        )


def test_missing_ca_file_exits(tmp_path: Path) -> None:
    """A CA path that does not exist fails before any connection is attempted."""
    with pytest.raises(SystemExit):
        tlsOptions_build(
            "https://127.0.0.1:8443/api/v1",
            {"MUSINGS_CA_CERT": str(tmp_path / "absent.pem")},
        )
