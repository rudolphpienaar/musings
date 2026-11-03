"""Tests for the executor supervisor script."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rush_executor_supervise.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash needed")


def test_supervisor_restarts_and_reports_exit_status(tmp_path: Path) -> None:
    """Each death is logged with its exit status and the command is restarted."""
    fake = tmp_path / "fake_executor.sh"
    fake.write_text("#!/usr/bin/env bash\necho running; exit 3\n", encoding="utf-8")
    fake.chmod(0o755)
    env = {
        **os.environ,
        "SUPERVISE_COMMAND": str(fake),
        "SUPERVISE_MAX_RESTARTS": "2",
        "SUPERVISE_MAX_BACKOFF_SECONDS": "1",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 1
    assert result.stdout.count("running") == 2
    assert result.stderr.count("executor died after") == 2
    assert "exit 3" in result.stderr
    assert "giving up after 2 restarts" in result.stderr


def test_supervisor_names_the_signal(tmp_path: Path) -> None:
    """An exit status above 128 is explained as the signal that caused it."""
    fake = tmp_path / "fake_executor.sh"
    fake.write_text("#!/usr/bin/env bash\nkill -TERM $$\n", encoding="utf-8")
    fake.chmod(0o755)
    env = {**os.environ, "SUPERVISE_COMMAND": str(fake), "SUPERVISE_MAX_RESTARTS": "1"}
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30
    )
    assert "exit 143 (TERM)" in result.stderr
