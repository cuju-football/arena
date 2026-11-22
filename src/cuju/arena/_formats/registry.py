# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The formats this renderer knows, built in, a person's own and those a team suggests.

- Built-in formats ship in `cuju/render/formats/builtin/<id>/`, read only, and run their code as
  the renderer's own. Their identifier in a context is `<id>`, or `<id>/<version>`.
- A person's formats live in `<home>/formats/<id>/`, added, imported, exported and removed
  through `add`, `export` and `remove`, the routes and the command line.
- A team may carry suggested formats in `cuju-team/formats/<id>/`, which `suggested(team)` lists
  and which play only once a person adds them.

A format whose `format.toml` does not read, or whose script fails to import, is broken. It
refuses nobody, holds no verdicts, and no context can choose it. A format whose code failed
during a match is marked failing with its message, which `failing` reads, until it is replaced.

A format is Python that runs with the permissions of the person who runs the renderer, so every
place that adds one shows `TRUST`.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .._store import home as _home
from .._text.messages import MessageError
from .._version import __version__
from . import ceilings as _ceilings
from . import contract as _contract

# The line every place that adds a format shows.
TRUST = 'A format is Python that runs with your permissions. Add only formats you trust.'
# The folders of the formats, under the package and under the home folder.
BUILTIN = Path(__file__).resolve().parents[1] / 'formats' / 'builtin'
HOME = 'formats'
SUGGESTED = Path('cuju-team') / 'formats'
# The file of the formats that failed, under the home folder.
_STATUS = 'format-status.json'
# The ceilings of a format's folder, design v5 11.
MAXIMUM_BYTES = 16 * 1024 * 1024
MAXIMUM_FILES = 1000
_IDENTIFIER = re.compile(r'[a-z][a-z0-9-]{0,39}')
_LOCK = threading.RLock()


@dataclasses.dataclass(frozen=True)
class Format:
    """A format the renderer knows.

    Attributes:
        id: Its identifier, the name of its folder.
        folder: Its folder.
        builtin: Whether it ships with the renderer.
        spec: Its `contract.Spec`, or `None` when it is broken.
        broken: Why it is broken, or `None`.
        suggested_by: The team that suggests it, for a suggested format, else `None`.
    """

    id: str
    folder: Path
    builtin: bool
    spec: _contract.Spec | None
    broken: str | None = None
    suggested_by: str | None = None

    @property
    def module(self) -> str | None:
        """The module of a built-in format's script, which the match host imports."""
        if not self.builtin or self.spec is None:
            return None
        package = Path(__file__).resolve().parents[1]
        script = (self.folder / self.spec.script).resolve()
        if not script.is_relative_to(package):
            return None
        parts = script.relative_to(package).with_suffix('').parts
        return '.'.join(('cuju', 'arena', *parts))

    @property
    def name(self) -> str:
        """The name to show, the spec's or the identifier for a broken format."""
        return self.spec.name if self.spec is not None else self.id

    @property
    def label(self) -> str:
        """The identifier with its version for a built-in format, such as `kaggle/1`."""
        if self.builtin and self.spec is not None and self.spec.version:
            return f'{self.id}/{self.spec.version}'
        return self.id

    def as_dict(self) -> dict[str, Any]:
        """Returns what the routes and the command line show of the format."""
        return {
            'id': self.id,
            'label': self.label,
            'name': self.name,
            'builtin': self.builtin,
            'broken': self.broken,
            'failing': failing(self.id),
            'suggested_by': self.suggested_by,
            'spec': None if self.spec is None else self.spec.as_dict(),
        }


def _read(identifier: str, folder: Path, builtin: bool, team: str | None = None) -> Format:
    try:
        spec = _contract.read(folder, builtin)
    except _contract.ContractError as error:
        return Format(identifier, folder, builtin, None, str(error), team)
    if not _contract.satisfies(__version__, spec.requires):
        message = f'needs {_contract.needs(spec.requires)}'
        return Format(identifier, folder, builtin, None, message, team)
    return Format(identifier, folder, builtin, spec, None, team)


def builtin_folder() -> Path:
    """Returns the folder of the built-in formats."""
    return BUILTIN


def home_folder() -> Path:
    """Returns the folder of a person's formats, made when missing."""
    return _home.folder(HOME)


def formats() -> list[Format]:
    """Returns every format, the built-in ones first, each group by identifier."""
    found = []
    for root, builtin in ((builtin_folder(), True), (home_folder(), False)):
        if not root.is_dir():
            continue
        for folder in sorted(root.iterdir()):
            if folder.is_dir() and _IDENTIFIER.fullmatch(folder.name):
                if builtin or (folder / _contract.FILE).is_file():
                    found.append(_read(folder.name, folder, builtin))
    return found


def get(identifier: str) -> Format:
    """Returns a format by its identifier, `kaggle`, `kaggle/1` or a person's `league-5v5`.

    Raises:
        ArgumentError: If no format has the identifier, or a built-in has another version.
    """
    name, _, version = str(identifier).partition('/')
    for found in formats():
        if found.id == name:
            if version and (found.spec is None or found.spec.version != version):
                raise MessageError('server.formats.version', format=repr(identifier))
            return found
    raise MessageError('server.formats.unknown', format=repr(identifier))


def choosable(identifier: str) -> Format:
    """Returns a format that a context may choose, one that is not broken.

    Raises:
        ArgumentError: If it is unknown or broken.
    """
    found = get(identifier)
    if found.spec is None:
        raise MessageError('server.formats.broken', format=found.id, reason=found.broken)
    return found


def suggested(team_folder: Path, team: str | None = None) -> list[Format]:
    """Returns the formats a team's folder suggests, in `cuju-team/formats/`."""
    root = Path(team_folder) / SUGGESTED
    if not root.is_dir():
        return []
    return [
        _read(folder.name, folder, False, team)
        for folder in sorted(root.iterdir())
        if folder.is_dir() and _IDENTIFIER.fullmatch(folder.name)
    ]


# Failing formats.


def _status_path() -> Path:
    return _home.home() / _STATUS


def _status(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


@contextlib.contextmanager
def _status_lock(path: Path) -> Iterator[None]:
    """Hold the status file's update lock across processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, path.with_suffix('.lock').open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            yield


def _write_status(path: Path, status: dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix='.part')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(status, stream)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(temporary)


def failing(identifier: str) -> dict[str, Any] | None:
    """Returns the last failure of a format's code, `{message, time}`, or `None`."""
    found = _status(_status_path()).get(identifier)
    return found if isinstance(found, dict) else None


def mark_failing(identifier: str, message: str) -> None:
    """Records that a format's code failed, which the menu shows until it is replaced."""
    path = _status_path()
    with _status_lock(path):
        status = _status(path)
        status[identifier] = {'message': ' '.join(str(message).split())[:300], 'time': time.time()}
        _write_status(path, status)


def _clear_failing(identifier: str) -> None:
    path = _status_path()
    with _status_lock(path):
        status = _status(path)
        if status.pop(identifier, None) is not None:
            _write_status(path, status)


# Adding, exporting and removing.


def _files(folder: Path) -> list[Path]:
    """Returns the files of a format's folder that it consists of, in order."""
    return sorted(
        path
        for path in folder.rglob('*')
        if path.is_file()
        and not path.is_symlink()
        and '__pycache__' not in path.relative_to(folder).parts
        and path.suffix != '.pyc'
    )


def digest(folder: Path) -> str:
    """Returns the SHA-256 of a format's folder, every file's path and bytes in order."""
    found = hashlib.sha256()
    for path in _files(folder):
        relative = path.relative_to(folder).as_posix().encode('utf-8')
        data = path.read_bytes()
        found.update(len(relative).to_bytes(4, 'little') + relative)
        found.update(len(data).to_bytes(8, 'little') + data)
    return found.hexdigest()


def _check_folder(folder: Path) -> None:
    files = _files(folder)
    if any(path.is_symlink() for path in folder.rglob('*')):
        raise MessageError('server.formats.links')
    if len(files) > MAXIMUM_FILES or sum(path.stat().st_size for path in files) > MAXIMUM_BYTES:
        raise MessageError('server.formats.size', files=MAXIMUM_FILES, mib=16)


def _unzip(data: bytes, target: Path) -> None:
    from .._registry import teams as _teams

    if data.count(b'PK\x01\x02') > MAXIMUM_FILES:
        raise MessageError('server.formats.size', files=MAXIMUM_FILES, mib=16)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as error:
        raise MessageError('server.formats.archive', error=error) from error
    with archive:
        members = archive.infolist()
        if sum(member.file_size for member in members) > MAXIMUM_BYTES:
            raise MessageError('server.formats.size', files=MAXIMUM_FILES, mib=16)
        for member in members:
            _teams._inside(target, _teams._check_member(member.filename))
        archive.extractall(target)
    # An archive that holds one folder with the format in it reads as that folder.
    entries = [entry for entry in target.iterdir() if entry.name != '__MACOSX']
    if not (target / _contract.FILE).is_file() and len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for entry in list(inner.iterdir()):
            shutil.move(str(entry), str(target / entry.name))
        inner.rmdir()


def add(
    source: Path | str | bytes,
    identifier: str | None = None,
    replace: bool = False,
) -> Format:
    """Adds a format from a folder or the bytes of a zip archive, and returns it.

    The folder is copied into `<home>/formats/<id>/`. Its `format.toml` must read under a
    contract this renderer serves and its `requires` must hold, so a broken format is never
    added. A format is Python that runs with the person's permissions, see `TRUST`.

    Args:
        source: The format's folder, or a zip archive's bytes.
        identifier: The identifier to keep it under, by default the folder's name, or for an
            archive the name of the format in lowercase with hyphens.
        replace: Whether to replace a format of the same identifier.

    Raises:
        ArgumentError: If the folder does not fit the contract or the ceilings, the identifier
            is taken by a built-in format, or it is taken and `replace` is false.
    """
    with tempfile.TemporaryDirectory(prefix='cuju-format-') as scratch:
        staged = Path(scratch) / 'format'
        if isinstance(source, bytes):
            staged.mkdir()
            _unzip(source, staged)
        else:
            folder = Path(source)
            if not folder.is_dir():
                raise MessageError('server.formats.folder', folder=str(folder))
            _check_folder(folder)
            shutil.copytree(
                folder, staged, symlinks=False, ignore=shutil.ignore_patterns('__pycache__')
            )
            identifier = identifier or folder.name.lower()
        _check_folder(staged)
        try:
            spec = _contract.read(staged)
            # A format whose options allow more time than its ceilings hold is refused here.
            _ceilings.check_ranges(spec)
        except _contract.ContractError as error:
            raise MessageError('server.formats.contract', reason=str(error)) from None
        if not _contract.satisfies(__version__, spec.requires):
            raise MessageError('server.formats.requires', needs=_contract.needs(spec.requires))
        if identifier is None:
            identifier = re.sub(r'[^a-z0-9]+', '-', spec.name.lower()).strip('-') or 'format'
        identifier = identifier[:40]
        if not _IDENTIFIER.fullmatch(identifier):
            raise MessageError('server.formats.identifier', identifier=repr(identifier))
        if (builtin_folder() / identifier).is_dir():
            raise MessageError('server.formats.builtin', identifier=identifier)
        target = home_folder() / identifier
        with _LOCK:
            if target.exists():
                if not replace:
                    raise MessageError('server.formats.taken', identifier=identifier)
                shutil.rmtree(target)
            shutil.copytree(staged, target)
            _clear_failing(identifier)
    return _read(identifier, target, False)


def export(identifier: str) -> tuple[str, bytes]:
    """Returns a file name and the bytes of a zip archive of a format, built in or a person's.

    Raises:
        ArgumentError: If no format has the identifier.
    """
    found = get(identifier)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in _files(found.folder):
            archive.write(path, f'{found.id}/{path.relative_to(found.folder).as_posix()}')
    return f'{found.id}.zip', buffer.getvalue()


def remove(identifier: str) -> bool:
    """Deletes a person's format and returns whether it existed. Built-in formats stay.

    Raises:
        ArgumentError: For a built-in format.
    """
    if (builtin_folder() / identifier).is_dir():
        raise MessageError('server.formats.builtin', identifier=identifier)
    if not _IDENTIFIER.fullmatch(identifier):
        return False
    target = home_folder() / identifier
    if not target.is_dir():
        return False
    with _LOCK:
        shutil.rmtree(target)
        _clear_failing(identifier)
    return True
