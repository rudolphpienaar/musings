"""Termination: write the EOT marker, then end the process.

The EOT marker is written before the process exits, and the process
exits only after the termination response has been sent (the route
schedules the shutdown as a background task). The shutdown hook is a
module-level callable so tests can replace the real process exit with
a recorder.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks

from app.models.system import EotManifest

EOT_FILENAME = "EOT"

# Overridable for tests. When None, the default hook exits the process.
shutdown_hook: Callable[[], None] | None = None


def shutdown_default() -> None:
    """Default shutdown: brief pause, then exit with status 0.

    The pause lets the termination response flush to the client
    before the process disappears.
    """
    time.sleep(0.2)
    os._exit(0)


def shutdown_schedule(backgroundTasks: BackgroundTasks) -> None:
    """Schedule process shutdown after the response is sent.

    Args:
        backgroundTasks: FastAPI background task runner from the route.
    """
    hook = shutdown_hook if shutdown_hook is not None else shutdown_default
    backgroundTasks.add_task(hook)


def eot_write(
    baseDir: Path,
    filesWritten: list[str],
    jobCount: int,
    reason: str = "",
) -> Path:
    """Write the EOT marker file into the base directory.

    Args:
        baseDir: Directory receiving the marker (the output directory
            in ChRIS mode).
        filesWritten: Paths saved during this run, relative to base.
        jobCount: Number of jobs finished during this run.
        reason: Termination reason for the manifest.

    Returns:
        The absolute path of the written marker.
    """
    manifest = EotManifest(
        status="complete",
        terminatedAt=datetime.now(timezone.utc),
        reason=reason,
        filesWritten=filesWritten,
        jobCount=jobCount,
    )
    target = baseDir / EOT_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return target
