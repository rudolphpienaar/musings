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
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_PORT = 8000


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
