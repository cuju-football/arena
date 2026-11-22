# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The hidden folder of the arena, which holds saved matches, teams, tournaments and ratings."""

from __future__ import annotations

import os
import re
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any

from cuju.errors import Error

# The variable that moves the folder, read on every call so that a test can set it.
HOME_VARIABLE = 'CUJU_ARENA_HOME'
# The variable that moved the folder of the renderer, where the data of 0.1.0 sits.
_EARLIER_VARIABLE = 'CUJU_RENDER_HOME'
# The folders and files that 0.1.0 kept in the folder of the renderer and the arena now owns.
ADOPTED = (
    'matches',
    'teams',
    'tournaments',
    'ratings',
    'runs',
    'formats',
    'format-cache',
    'terminals',
    'verdicts',
    'scenarios',
    'stats',
    'logs',
    'plugins',
    'sample-teams.json',
    'samples.json',
    'format-status.json',
    'team-folders.json',
    'tournaments.key',
)

_IDENTIFIER = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
_ADOPTION_LOCK = threading.Lock()
_ADOPTED_ONCE: set[str] = set()


def home() -> Path:
    """Returns the folder of the arena, `~/.cuju/arena` or the one `CUJU_ARENA_HOME` names.

    A first start with the default folder missing adopts the data that 0.1.0 kept in the folder of
    the renderer, as `adopt` tells.
    """
    folder = os.environ.get(HOME_VARIABLE)
    if folder:
        return Path(folder).expanduser().resolve()
    path = Path.home() / '.cuju' / 'arena'
    with _ADOPTION_LOCK:
        if not path.exists() and str(path) not in _ADOPTED_ONCE:
            _ADOPTED_ONCE.add(str(path))
            adopt(path, _earlier())
    return path


def _earlier() -> Path:
    """Returns the folder of the renderer, where 0.1.0 kept the data that the arena owns now."""
    folder = os.environ.get(_EARLIER_VARIABLE)
    return Path(folder).expanduser().resolve() if folder else Path.home() / '.cuju' / 'render'


def adopt(target: Path, source: Path) -> list[str]:
    """Moves what 0.1.0 kept in `source` for the arena into `target`, once, and says so.

    The folders and files of `ADOPTED` move by a rename of each entry, so no byte is copied and
    nothing is deleted, and an entry that `target` already holds stays where it is. A file
    `render.toml` of `source` that holds tables of the arena gives them to a new `arena.toml` of
    `target`. The original stays beside it as `render.toml.bak`, and a new `render.toml` keeps the
    tables that are not the arena's, when it has any. It runs only while `target` does not exist,
    and it prints one line to the error output that names what moved and where.

    Returns:
        The names that moved, empty when `source` held none of them.
    """
    if target.exists() or not source.is_dir():
        return []
    found = [name for name in ADOPTED if (source / name).exists()]
    tables = _arena_tables(source / 'render.toml')
    if not found and not tables:
        return []
    try:
        target.mkdir(parents=True)
    except FileExistsError:
        return []
    moved = []
    for name in found:
        try:
            os.replace(source / name, target / name)
        except OSError:
            continue
        moved.append(name)
    note = ''
    if tables and _split_settings(source / 'render.toml', target / 'arena.toml', tables):
        moved.append('the tables of render.toml')
        note = ', kept render.toml as render.toml.bak'
    if moved:
        print(
            f'cuju arena moved {", ".join(moved)} from {source} to {target}, '
            f'the folder of the arena since this version{note}, and deleted nothing',
            file=sys.stderr,
            flush=True,
        )
    return moved


def _arena_tables(path: Path) -> dict[str, Any]:
    """Returns the tables of a file of settings that the arena owns, empty when it has none."""
    if not path.is_file():
        return {}
    from .._config import files as _files
    from .._config import layers as _layers

    try:
        found = _files.read(path)
    except Error:
        return {}
    names = set(_layers.ARENA.schema().defaults)
    return {name: table for name, table in found.items() if name in names}


def _toml(tables: dict[str, Any]) -> str:
    """Returns tables of settings as TOML, a key on each line and a nested table inline."""
    from .._config import files as _files

    lines: list[str] = []
    for name, table in tables.items():
        lines.append(f'[{_files.key_text(name)}]')
        for key, value in table.items():
            lines.append(f'{_files.key_text(key)} = {_files.value_text(value)}')
        lines.append('')
    return '\n'.join(lines)


def _split_settings(path: Path, arena: Path, tables: dict[str, Any]) -> bool:
    """Writes `arena` from the arena tables of `path`, keeps `path` as a `.bak`, and rewrites it."""
    from .._config import files as _files

    try:
        arena.write_text(_toml(tables), encoding='utf-8')
        rest = {name: table for name, table in _files.read(path).items() if name not in tables}
        os.replace(path, path.with_name(path.name + '.bak'))
        if rest:
            path.write_text(_toml(rest), encoding='utf-8')
    except (OSError, Error):
        return False
    return True


def folder(kind: str) -> Path:
    """Returns the subfolder `matches`, `teams`, `runs` or the like, created when missing."""
    path = home() / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def python_command() -> list[str]:
    """Returns the start of the command of a Python process that the arena starts.

    Python 3.11 brought `-P`, which keeps the working folder off the path of modules, so no folder
    of the home, such as `teams` or `requests`, imports as a package in the process.
    """
    return [sys.executable, '-P'] if sys.version_info >= (3, 11) else [sys.executable]


def process_environment() -> dict[str, str]:
    """Returns the environment of a Python process that the arena starts.

    The process reads and writes its pipes in UTF-8, the encoding the arena reads and writes
    them in, whatever the code page of the system.
    """
    return {**os.environ, 'PYTHONIOENCODING': 'utf-8'}


def process_folder() -> Path:
    """Returns the working folder of a Python process that the arena starts, made when missing.

    That is the home folder, which `-P` keeps off the path of modules. Python 3.10 has no `-P`,
    so there the process starts in the folder `run` of the home, which the arena keeps empty.
    """
    folder = home() if sys.version_info >= (3, 11) else home() / 'run'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def new_identifier(prefix: str) -> str:
    """Returns an identifier that sorts by creation time, such as `m-20261106-101500-3fa9`."""
    return f'{prefix}-{time.strftime("%Y%m%d-%H%M%S")}-{secrets.token_hex(2)}'


def valid_identifier(identifier: str) -> bool:
    """Returns whether `identifier` is safe as a file name, so no request can leave the folder."""
    return bool(_IDENTIFIER.fullmatch(identifier))


def alive(pid: int) -> bool:
    """Returns whether a process runs, without signalling it."""
    if pid <= 0:
        return False
    if sys.platform == 'win32':
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        # Windows: 259 is `STILL_ACTIVE`.
        return code.value == 259
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
