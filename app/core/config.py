"""Environment-driven configuration for the musings service.

Configuration is read from the environment at import time, and from a
``.env`` file in the current working directory when one exists; real
environment variables take precedence over ``.env`` values. The bearer
token is required: the server refuses to start without it, so the
service is never accidentally left open.

The bind address follows one rule: ``MUSINGS_HOST`` and
``MUSINGS_PORT`` win when they are set, and otherwise the server
derives its bind host and port from ``MUSINGS_URL``, so a single URL
sets both the clients and the server.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MusingsSettings(BaseSettings):
    """Settings for the musings server and clients.

    Attributes:
        musingsToken: Bearer token required by every protected route.
        musingsBaseDir: Root directory under which ``/save`` writes files
            and into which the EOT marker is written on termination.
        musingsHost: Interface the server binds.
        musingsPort: Port the server binds.
        musingsUrl: Service URL the clients use; also the fallback the
            server derives its bind host and port from when they are
            not set explicitly.
        jobTimeoutSeconds: Default execution timeout for poller-run jobs.
        maxOutputChars: Maximum captured stdout/stderr characters kept per
            job result; longer output is truncated.
        pollIntervalSeconds: Poller error backoff, and the guard spacing
            against a server that ignores the long-poll wait.
        pollWaitSeconds: How long the poller's claim long-poll parks on
            the server before returning empty and polling again.
    """

    model_config = SettingsConfigDict(
        env_file=".env", populate_by_name=True, extra="ignore"
    )

    musingsToken: str = Field(validation_alias="MUSINGS_TOKEN")
    musingsBaseDir: Path = Field(
        default=Path("./data"), validation_alias="MUSINGS_BASE_DIR"
    )
    musingsHost: str = Field(default="127.0.0.1", validation_alias="MUSINGS_HOST")
    musingsPort: int = Field(default=8000, validation_alias="MUSINGS_PORT")
    musingsUrl: str | None = Field(default=None, validation_alias="MUSINGS_URL")
    jobTimeoutSeconds: int = Field(default=300, validation_alias="JOB_TIMEOUT_SECONDS")
    maxOutputChars: int = Field(default=100000, validation_alias="MAX_OUTPUT_CHARS")
    pollIntervalSeconds: int = Field(default=10, validation_alias="POLL_INTERVAL_SECONDS")
    pollWaitSeconds: int = Field(default=20, validation_alias="POLL_WAIT_SECONDS")

    @model_validator(mode="after")
    def bind_derive(self) -> MusingsSettings:
        """Derive the bind host and port from the URL when unset.

        Returns:
            The settings, with the bind address filled in from
            ``MUSINGS_URL`` when ``MUSINGS_HOST`` or ``MUSINGS_PORT``
            were not set explicitly. An explicitly set host or port
            always wins; a URL without a port falls back to its
            scheme default (80 for http, 443 for https).
        """
        if self.musingsUrl is None:
            return self
        parsed = urlparse(self.musingsUrl)
        if "musingsHost" not in self.model_fields_set and parsed.hostname:
            self.musingsHost = parsed.hostname
        if "musingsPort" not in self.model_fields_set:
            if parsed.port is not None:
                self.musingsPort = parsed.port
            elif parsed.scheme == "https":
                self.musingsPort = 443
            elif parsed.scheme == "http":
                self.musingsPort = 80
        return self


@lru_cache
def settings_get() -> MusingsSettings:
    """Return the cached settings instance.

    Returns:
        The process-wide musings settings.
    """
    return MusingsSettings()
