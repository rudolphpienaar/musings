"""Load a local ``.env`` file into the process environment.

The server and every client read their configuration from the
environment. This module lets a ``.env`` file in the current working
directory supply those variables, so a token and a service URL do not
have to be exported by hand in every shell. Real environment variables
always win: a variable already present in the environment is never
overwritten by the file, and a missing file is silently ignored.

Only ``KEY=VALUE`` lines are read. Blank lines, ``#`` comments, and an
optional ``export `` prefix are skipped, and a value wrapped in
matching single or double quotes is unwrapped.
"""

from __future__ import annotations

import os
from pathlib import Path


def env_load(env_path: Path | None = None) -> int:
    """Load variables from a ``.env`` file without overriding the environment.

    Args:
        env_path: Path to the ``.env`` file. When None, ``.env`` in the
            current working directory is used.

    Returns:
        The number of variables newly set from the file.
    """
    path = env_path if env_path is not None else Path(".env")
    if not path.is_file():
        return 0
    loaded = 0
    for rawLine in path.read_text(encoding="utf-8").splitlines():
        line = rawLine.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not all(char.isalnum() or char == "_" for char in key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded
