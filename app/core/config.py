"""Environment-driven configuration for the musings service.

Configuration is read from the environment at import time, and from a
``.env`` file in the current working directory when one exists; real
environment variables take precedence over ``.env`` values. The bearer
token is required: the server refuses to start without it, so the
service is never accidentally left open.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MusingsSettings(BaseSettings):
    """Settings for the musings server and clients.

    Attributes:
        musingsToken: Bearer token required by every protected route.
        musingsBaseDir: Root directory under which ``/save`` writes files
            and into which the EOT marker is written on termination.
        musingsHost: Interface the server binds.
        musingsPort: Port the server binds.
        jobTimeoutSeconds: Default execution timeout for poller-run jobs.
        maxOutputChars: Maximum captured stdout/stderr characters kept per
            job result; longer output is truncated.
        pollIntervalSeconds: Delay between poller claim attempts.
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
    jobTimeoutSeconds: int = Field(default=300, validation_alias="JOB_TIMEOUT_SECONDS")
    maxOutputChars: int = Field(default=100000, validation_alias="MAX_OUTPUT_CHARS")
    pollIntervalSeconds: int = Field(default=10, validation_alias="POLL_INTERVAL_SECONDS")


@lru_cache
def settings_get() -> MusingsSettings:
    """Return the cached settings instance.

    Returns:
        The process-wide musings settings.
    """
    return MusingsSettings()
