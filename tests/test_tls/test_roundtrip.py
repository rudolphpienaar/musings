"""End-to-end TLS test: a real socket, real certificates, real handshakes.

The ASGI test transport never touches TLS, so this test mints a private
CA with ``scripts/pki_make.sh``, serves the app with uvicorn in a thread
under mutual TLS, and checks the three handshakes that matter: a client
with the pinned CA and a client certificate gets through, a client
without a certificate is refused, and a client that does not trust the
CA refuses the server. It is skipped where openssl or bash is absent.
"""

from __future__ import annotations

import os
import shutil
import socket
import ssl
import subprocess
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn

from app.core.config import TlsServerConfig
from app.main import app_create
from client.transport import ClientTlsOptions, client_build

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pki_make.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None or shutil.which("bash") is None,
    reason="openssl and bash are needed to mint test certificates",
)


@dataclass
class TlsFixture:
    """A served TLS endpoint and the files that reach it.

    Attributes:
        baseUrl: ``https://127.0.0.1:<port>`` of the running server.
        caFile: Authority the server certificate chains to.
        clientCert: ``(cert, key)`` pair signed by the same authority.
    """

    baseUrl: str
    caFile: Path
    clientCert: tuple[str, str]


def port_free() -> int:
    """Pick a free loopback TCP port.

    Returns:
        A port number that was unbound at the moment of asking.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def tls_server(tmp_path: Path) -> Generator[TlsFixture, None, None]:
    """Mint a PKI and serve the app under mutual TLS on loopback."""
    env = {**os.environ, "PKI_DIR": str(tmp_path / "pki")}
    for args in (["ca"], ["server"], ["client", "--name", "tester"]):
        subprocess.run(
            ["bash", str(SCRIPT), *args], env=env, check=True, capture_output=True
        )
    pki = tmp_path / "pki"
    tlsConfig = TlsServerConfig(
        certFile=pki / "server.pem", keyFile=pki / "server.key", clientCaFile=pki / "ca.pem"
    )
    port = port_free()
    config = uvicorn.Config(
        app_create(), host="127.0.0.1", port=port, log_level="warning",
        **tlsConfig.uvicornKwargs_get(),
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start under TLS"
    yield TlsFixture(
        baseUrl=f"https://127.0.0.1:{port}",
        caFile=pki / "ca.pem",
        clientCert=(str(pki / "clients" / "tester.pem"), str(pki / "clients" / "tester.key")),
    )
    server.should_exit = True
    thread.join(timeout=5.0)


def test_pinned_ca_and_client_cert_reach_health(tls_server: TlsFixture) -> None:
    """The intended client, built by the shared factory, completes the handshake."""
    options = ClientTlsOptions(caFile=str(tls_server.caFile), clientCert=tls_server.clientCert)
    with httpx.Client(
        base_url=tls_server.baseUrl, verify=options.sslContext_build(), trust_env=False
    ) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_client_build_reaches_health_under_mtls(
    tls_server: TlsFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``client_build`` reads the TLS variables and gets through end to end."""
    monkeypatch.setenv("MUSINGS_URL", tls_server.baseUrl + "/api/v1")
    monkeypatch.setenv("MUSINGS_TOKEN", "test-token-123")
    monkeypatch.setenv("MUSINGS_CA_CERT", str(tls_server.caFile))
    monkeypatch.setenv("MUSINGS_CLIENT_CERT", tls_server.clientCert[0])
    monkeypatch.setenv("MUSINGS_CLIENT_KEY", tls_server.clientCert[1])
    with client_build(timeout=5.0) as client:
        response = client.get("/health")
    assert response.status_code == 200


def test_client_without_certificate_is_refused(tls_server: TlsFixture) -> None:
    """Mutual TLS turns away a client that trusts the CA but presents nothing."""
    options = ClientTlsOptions(caFile=str(tls_server.caFile), clientCert=None)
    with httpx.Client(
        base_url=tls_server.baseUrl, verify=options.sslContext_build(), trust_env=False
    ) as client:
        with pytest.raises((httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError)):
            client.get("/api/v1/health")


def test_untrusting_client_refuses_server(tls_server: TlsFixture) -> None:
    """A client on the system trust store does not accept the private CA."""
    options = ClientTlsOptions(caFile=None, clientCert=tls_server.clientCert)
    with httpx.Client(
        base_url=tls_server.baseUrl, verify=options.sslContext_build(), trust_env=False
    ) as client:
        with pytest.raises(httpx.ConnectError) as excinfo:
            client.get("/api/v1/health")
    assert isinstance(excinfo.value.__cause__, ssl.SSLError) or "CERTIFICATE" in str(
        excinfo.value
    ).upper()
