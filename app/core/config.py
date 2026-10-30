"""Environment-driven configuration for the musings service.

Configuration is read from the environment at import time, and from a
``.env`` file in the current working directory when one exists; real
environment variables take precedence over ``.env`` values. The bearer
token is required: the server refuses to start without it, so the
service is never accidentally left open.

``MUSINGS_URL`` is the one source of truth for where the service lives
on this machine: the clients connect to it, and the server listens on
its port. Which interfaces the server listens on is not configuration
at all but a startup decision, made visibly on the command line with
``musings-server --public``; a public address, when there is one, is a
fact about the network in front of the server and only the remote
client needs to know it.

The scheme of ``MUSINGS_URL`` decides the transport. ``https`` means
the server terminates TLS itself, with the certificate and key named
by ``MUSINGS_TLS_CERT`` and ``MUSINGS_TLS_KEY``, and refuses to start
without them. ``MUSINGS_TLS_CLIENT_CA`` turns on mutual TLS: every
connection must present a certificate signed by that authority, so the
bearer token stops being the only thing between the network and a
shell. The matching client-side settings (``MUSINGS_CA_CERT``,
``MUSINGS_CLIENT_CERT``, ``MUSINGS_CLIENT_KEY``) are read by the
clients from the environment and kept here only so an embedded poller
can inherit them.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_PORT = 8000
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class TlsConfigError(ValueError):
    """Raised when the TLS settings do not add up to a servable configuration."""


@dataclass
class TlsServerConfig:
    """Files the server hands to uvicorn to terminate TLS.

    Attributes:
        certFile: PEM certificate (with any chain) presented to clients.
        keyFile: PEM private key matching ``certFile``.
        clientCaFile: PEM authority that client certificates must chain
            to, or None when clients are not asked for a certificate.

    Example:
        >>> config = TlsServerConfig(Path("server.pem"), Path("server.key"), None)
        >>> config.uvicornKwargs_get()["ssl_certfile"]
        'server.pem'
    """

    certFile: Path
    keyFile: Path
    clientCaFile: Path | None

    def uvicornKwargs_get(self) -> dict[str, object]:
        """Translate into the keyword arguments ``uvicorn.run`` expects.

        Returns:
            Mapping of uvicorn ``ssl_*`` keyword arguments. When a client
            CA is set, ``ssl_cert_reqs`` is ``ssl.CERT_REQUIRED`` so a
            connection without a valid client certificate is refused in
            the handshake, before any route runs.
        """
        import ssl

        kwargs: dict[str, object] = {
            "ssl_certfile": str(self.certFile),
            "ssl_keyfile": str(self.keyFile),
        }
        if self.clientCaFile is not None:
            kwargs["ssl_ca_certs"] = str(self.clientCaFile)
            kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED
        return kwargs


class MusingsSettings(BaseSettings):
    """Settings for the musings server and clients.

    Attributes:
        musingsToken: Bearer token required by every protected route.
        musingsBaseDir: Root directory under which ``/save`` writes files
            and into which the EOT marker is written on termination.
        musingsUrl: Service URL on this machine. The clients connect to
            it, and the server listens on its port (``port_derive``).
        jobTimeoutSeconds: Default execution timeout for poller-run jobs.
        maxOutputChars: Maximum captured stdout/stderr characters kept per
            job result; longer output is truncated.
        pollIntervalSeconds: Poller error backoff, and the guard spacing
            against a server that ignores the long-poll wait.
        pollWaitSeconds: How long the poller's claim long-poll parks on
            the server before returning empty and polling again.
        tlsCertFile: Server certificate, required when ``musingsUrl`` is
            ``https``.
        tlsKeyFile: Private key for ``tlsCertFile``.
        tlsClientCaFile: When set, the server requires every client to
            present a certificate signed by this authority (mutual TLS).
        caCertFile: Authority the clients trust for the server
            certificate; inherited by an embedded poller.
        clientCertFile: Certificate a client presents under mutual TLS;
            inherited by an embedded poller.
        clientKeyFile: Private key for ``clientCertFile``.
    """

    model_config = SettingsConfigDict(
        env_file=".env", populate_by_name=True, extra="ignore"
    )

    musingsToken: str = Field(validation_alias="MUSINGS_TOKEN")
    musingsBaseDir: Path = Field(
        default=Path("./data"), validation_alias="MUSINGS_BASE_DIR"
    )
    musingsUrl: str | None = Field(default=None, validation_alias="MUSINGS_URL")
    jobTimeoutSeconds: int = Field(default=300, validation_alias="JOB_TIMEOUT_SECONDS")
    maxOutputChars: int = Field(default=100000, validation_alias="MAX_OUTPUT_CHARS")
    pollIntervalSeconds: int = Field(default=10, validation_alias="POLL_INTERVAL_SECONDS")
    pollWaitSeconds: int = Field(default=20, validation_alias="POLL_WAIT_SECONDS")
    tlsCertFile: Path | None = Field(default=None, validation_alias="MUSINGS_TLS_CERT")
    tlsKeyFile: Path | None = Field(default=None, validation_alias="MUSINGS_TLS_KEY")
    tlsClientCaFile: Path | None = Field(
        default=None, validation_alias="MUSINGS_TLS_CLIENT_CA"
    )
    caCertFile: Path | None = Field(default=None, validation_alias="MUSINGS_CA_CERT")
    clientCertFile: Path | None = Field(
        default=None, validation_alias="MUSINGS_CLIENT_CERT"
    )
    clientKeyFile: Path | None = Field(default=None, validation_alias="MUSINGS_CLIENT_KEY")

    def scheme_get(self) -> str:
        """Return the scheme of ``MUSINGS_URL``.

        Returns:
            ``"https"`` or ``"http"``; ``"http"`` when no URL is set.
        """
        if self.musingsUrl is None:
            return "http"
        return urlparse(self.musingsUrl).scheme or "http"

    def tls_isEnabled(self) -> bool:
        """Report whether the server is to terminate TLS.

        Returns:
            True when ``MUSINGS_URL`` is an ``https`` URL.
        """
        return self.scheme_get() == "https"

    def tlsServer_config(self) -> TlsServerConfig | None:
        """Assemble and check the server's TLS files.

        Returns:
            The TLS configuration, or None when the URL is plain ``http``
            and no client CA is set.

        Raises:
            TlsConfigError: When the URL is ``https`` but a certificate or
                key is missing or unreadable, or when a client CA is set
                without TLS, which cannot work since there is no handshake
                to check the client certificate in.
        """
        if not self.tls_isEnabled():
            if self.tlsClientCaFile is not None:
                raise TlsConfigError(
                    "MUSINGS_TLS_CLIENT_CA is set but MUSINGS_URL is not https; "
                    "mutual TLS needs TLS."
                )
            return None
        if self.tlsCertFile is None or self.tlsKeyFile is None:
            raise TlsConfigError(
                "MUSINGS_URL is https, so MUSINGS_TLS_CERT and MUSINGS_TLS_KEY "
                "must both name files (see scripts/pki_make.sh)."
            )
        for label, path in (
            ("MUSINGS_TLS_CERT", self.tlsCertFile),
            ("MUSINGS_TLS_KEY", self.tlsKeyFile),
            ("MUSINGS_TLS_CLIENT_CA", self.tlsClientCaFile),
        ):
            if path is not None and not path.is_file():
                raise TlsConfigError(f"{label} names {path}, which is not a readable file.")
        return TlsServerConfig(
            certFile=self.tlsCertFile,
            keyFile=self.tlsKeyFile,
            clientCaFile=self.tlsClientCaFile,
        )

    def port_derive(self) -> int:
        """Derive the port the server listens on from ``MUSINGS_URL``.

        Returns:
            The URL's explicit port; its scheme default (80 for http,
            443 for https) when the URL names none; ``DEFAULT_PORT``
            when no URL is configured at all.
        """
        if self.musingsUrl is None:
            return DEFAULT_PORT
        parsed = urlparse(self.musingsUrl)
        if parsed.port is not None:
            return parsed.port
        if parsed.scheme == "https":
            return 443
        return 80


@lru_cache
def settings_get() -> MusingsSettings:
    """Return the cached settings instance.

    Returns:
        The process-wide musings settings.
    """
    return MusingsSettings()
