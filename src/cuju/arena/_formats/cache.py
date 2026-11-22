# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The cache folders of formats, `<home>/format-cache/`, kept between runs, least recently used out.

Each format identity and checkpoint has a folder of its own, which a format reads and writes as
`Match.cache`, such as a model compiled once for the run. A folder past `[formats]
format_cache_gib` is emptied before its next match, and while every folder together holds more
than `[formats] cache_gib`, the folders used least recently leave first. The cache never goes
into an export. Answers that a deterministic format gives still depend only on the inputs of the
contract, so a cache only saves time.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .._store import home as _home

FOLDER = 'format-cache'
_GIB = 1024**3
_NAME = re.compile(r'[^A-Za-z0-9_-]+')


def _size(folder: Path) -> int:
    total = 0
    for root, _, files in os.walk(folder):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def _settings(config: Any = None, overrides: Any = ()) -> dict[str, Any]:
    from .._config import layers as _layers

    return _layers.table('formats', config=config, overrides=overrides)


def folder(identity_key: str, checkpoint: str | None, config: Any = None) -> Path:
    """Returns the cache folder of a format identity and a checkpoint, made, trimmed and touched.

    Args:
        identity_key: The format's identity as `identity.key` gives it.
        checkpoint: The team's checkpoint, or `None`.
        config: The settings, or `None` for the layered files.
    """
    settings = _settings(config)
    name = _NAME.sub('-', f'{identity_key}-{checkpoint or "none"}')[:80]
    target = _home.folder(FOLDER) / name
    if target.is_dir() and _size(target) > float(settings['format_cache_gib']) * _GIB:
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    now = time.time()
    os.utime(target, (now, now))
    trim(float(settings['cache_gib']) * _GIB, keep=target)
    return target


def trim(limit: float, keep: Path | None = None) -> list[str]:
    """Removes the folders used least recently until all of them hold at most `limit` bytes.

    Returns:
        The names of the folders removed.
    """
    root = _home.folder(FOLDER)
    entries = []
    for entry in root.iterdir():
        if entry.is_dir():
            entries.append((entry.stat().st_mtime, entry, _size(entry)))
    total = sum(size for _, _, size in entries)
    removed = []
    for _, entry, size in sorted(entries, key=lambda found: found[0]):
        if total <= limit:
            break
        if keep is not None and entry == keep:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        total -= size
        removed.append(entry.name)
    return removed
