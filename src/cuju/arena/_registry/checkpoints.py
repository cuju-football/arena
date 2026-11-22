# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The checkpoints of a team, the submissions it has had, and the one resolver that names them.

A team keeps one identity through many checkpoints. A checkpoint is the files of the submission,
every file of the team's folder outside `cuju-team`, named by the first 16 characters of their
`files_digest`, the same name that every rating of cuju-render 0.1.0 holds. Only the files give
the name, so the look of the team, its control and the format that plays it never change it.

The current checkpoint sits at the root of the team's folder, where Kaggle reads a submission,
and each earlier one under `cuju-team/checkpoints/<id>/`. The file `cuju-team/checkpoints.json`
lists them with a label, a training step, a note, a pin and the control each one uses. A team of
0.1.0 has no such file and reads as one checkpoint, which the first change writes.

An entrant is the identifier of a team, which means its current checkpoint, or `team@id`, which
names an earlier one. Every caller, a match, a tournament, a rating run and an evaluation, reads
an entrant through `resolve`.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from cuju.errors import ArgumentError

from .._store import home as _home
from .._store import runs as _runs
from .._text import messages as _messages
from .._text.messages import MessageError

# The length of a checkpoint's identifier, the front of its digest.
ID_LENGTH = 16
# The version of the file `checkpoints.json`.
IDENTITY = 1
FILE = 'checkpoints.json'
FOLDER = 'checkpoints'
SEPARATOR = '@'
# The folder inside `cuju-team` where a new submission is unpacked before it replaces the old one.
INCOMING = 'incoming.part'
# The most that the page uploads for a new checkpoint, as for a new team, and the seconds that the
# server waits for the next bytes of an upload.
MAXIMUM_BYTES = 100 * 1024 * 1024
IDLE_SECONDS = 60
LONGEST_LABEL = 80
LONGEST_NOTE = 500
# The set of the fields that the page and a caller change on a checkpoint.
FIELDS = ('label', 'note', 'step', 'pinned', 'control', 'current')

_ID = re.compile(r'[0-9a-f]{16}')
_DIGEST = re.compile(r'[0-9a-f]{64}')
_LOCK = threading.RLock()
# The identifier of each team folder's files by the sizes and times of its files, since the digest
# reads every byte.
_STAMPS: dict[str, tuple[Any, str]] = {}


def settings() -> dict[str, int]:
    """Returns the keeping rule, `checkpoints` the most kept and `checkpoint_mib` their size."""
    from .._config import layers as _layers

    table = _layers.table('teams')
    return {
        'checkpoints': int(table['checkpoints']),
        'checkpoint_mib': int(table['checkpoint_mib']),
    }


def split(entrant: Any) -> tuple[str, str | None]:
    """Returns the team and the checkpoint that an entrant names, `None` for the current one.

    Raises:
        ArgumentError: If the entrant is no text, or its part after `@` is no checkpoint.
    """
    if not isinstance(entrant, str) or not entrant:
        raise MessageError('server.teams.unknown', identifier=repr(entrant))
    team, separator, point = entrant.partition(SEPARATOR)
    if not separator:
        return team, None
    if not _ID.fullmatch(point):
        raise MessageError('server.checkpoints.entrant', entrant=repr(entrant))
    return team, point


def entrant(team: str, checkpoint: str | None = None) -> str:
    """Returns the text that names a team at a checkpoint, the bare team for the current one."""
    return team if checkpoint is None else f'{team}{SEPARATOR}{checkpoint}'


def _team_folder(identifier: str) -> Path:
    if not isinstance(identifier, str) or not _home.valid_identifier(identifier):
        raise MessageError('server.teams.unknown', identifier=repr(identifier))
    path = _home.folder('teams') / identifier
    if not path.is_dir():
        raise MessageError('server.teams.unknown', identifier=repr(identifier))
    return path


def _details(identifier: str) -> Path:
    from . import teams as _teams

    return _team_folder(identifier) / _teams.TEAM_FOLDER


def _files(path: Path, skip: str = 'cuju-team') -> list[Path]:
    """Returns the files of a submission folder that its digest covers, in the digest's order."""
    return sorted(
        item
        for item in path.rglob('*')
        if item.is_file()
        and item.relative_to(path).parts[0] != skip
        and not _runs.tool_part(item.relative_to(path).parts)
        and '__pycache__' not in item.relative_to(path).parts
    )


def current_id(identifier: str) -> str | None:
    """Returns the identifier of a team's current checkpoint, the digest of the files at its root.

    The answer is kept until a file changes its size or its time. The built-in AI, the random
    entrant, a team the registry lacks and a folder that cannot be read have none.
    """
    if not isinstance(identifier, str) or not _home.valid_identifier(identifier):
        return None
    path = _home.folder('teams') / identifier
    if not path.is_dir():
        return None
    try:
        stamp = tuple(
            (item.relative_to(path).as_posix(), item.stat().st_size, item.stat().st_mtime_ns)
            for item in _files(path)
        )
        with _LOCK:
            held = _STAMPS.get(str(path))
        if held is not None and held[0] == stamp:
            return held[1]
        found = _runs.team_checkpoint(identifier)['id']
    except (OSError, ArgumentError):
        return None
    with _LOCK:
        _STAMPS[str(path)] = (stamp, found)
    return found


def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%S')


def _measure(path: Path) -> tuple[int, int]:
    """Returns the number of files and the bytes of a submission folder as its digest covers it."""
    files = _files(path)
    return len(files), sum(item.stat().st_size for item in files)


def _control(value: Any, format: str) -> dict[str, Any] | None:
    from . import teams as _teams

    try:
        return _teams.check_control(copy.deepcopy(value), format)
    except (ArgumentError, KeyError, TypeError):
        return None


def _clean(item: Any, format: str) -> dict[str, Any] | None:
    """Returns a checkpoint of the file as the registry holds it, or `None` for a bad entry."""
    if not isinstance(item, dict):
        return None
    digest, identifier = item.get('digest'), item.get('id')
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        return None
    if identifier != digest[:ID_LENGTH]:
        return None
    label, note, step = item.get('label'), item.get('note'), item.get('step')
    added, files, size = item.get('added'), item.get('files'), item.get('bytes')

    def whole(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    return {
        'id': identifier,
        'digest': digest,
        'label': label[:LONGEST_LABEL] if isinstance(label, str) else '',
        'step': step if whole(step) else None,
        'added': added if isinstance(added, str) else '',
        'note': note[:LONGEST_NOTE] if isinstance(note, str) else '',
        'files': files if whole(files) else 0,
        'bytes': size if whole(size) else 0,
        'control': _control(item.get('control'), format),
        'pinned': item.get('pinned') is True,
    }


def _path(identifier: str) -> Path:
    return _details(identifier) / FILE


def _read(identifier: str, format: str) -> dict[str, Any] | None:
    """Returns the checkpoints of a team's file, each cleaned, or `None` when it holds none."""
    path = _path(identifier)
    try:
        with path.open('rb') as stream:
            data = stream.read(1024 * 1024 + 1)
        document = json.loads(data) if len(data) <= 1024 * 1024 else None
    except (OSError, ValueError, RecursionError):
        document = None
    if not isinstance(document, dict) or not isinstance(document.get('checkpoints'), list):
        return None
    found, seen = [], set()
    team_folder = path.parent.parent
    for item in document['checkpoints']:
        entry = _clean(item, format)
        if entry is None or entry['id'] in seen:
            continue
        seen.add(entry['id'])
        found.append(entry)
    current = document.get('current')
    if current not in seen:
        return None
    # An earlier checkpoint whose files are gone has nothing to play, so the file drops it.
    found = [
        entry
        for entry in found
        if entry['id'] == current or (team_folder / 'cuju-team' / FOLDER / entry['id']).is_dir()
    ]
    return {'current': current, 'checkpoints': found}


def _write(identifier: str, document: dict[str, Any]) -> None:
    path = _path(identifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        'identity': IDENTITY,
        'current': document['current'],
        'checkpoints': [
            {key: value for key, value in entry.items() if key != 'current'}
            for entry in document['checkpoints']
        ],
    }
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix='.part')
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write((json.dumps(body, indent=2) + '\n').encode())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _first(team: dict[str, Any], folder: Path) -> dict[str, Any]:
    """Returns the checkpoint that a team's files form now, with the team's control."""
    digest = _runs.files_digest(folder, ('cuju-team',))
    count, size = _measure(folder)
    return {
        'id': digest[:ID_LENGTH],
        'digest': digest,
        'label': '',
        'step': None,
        'added': team.get('created') or _now(),
        'note': '',
        'files': count,
        'bytes': size,
        'control': copy.deepcopy(team.get('control')),
        'pinned': False,
    }


def _document(identifier: str, write: bool = True) -> dict[str, Any]:
    """Returns a team's checkpoints, made from its files when it has none, and kept in step.

    A file of the team's folder that changed outside the renderer is a new checkpoint, whose
    control, pin and files are the new ones. The checkpoint it replaces has lost its files, so it
    leaves the list, and its ratings stay in the tables.

    Raises:
        ArgumentError: If the team is unknown, or is not a Kaggle team.
    """
    from . import teams as _teams

    team = _teams.entry(identifier)
    if team['kind'] != 'kaggle':
        raise MessageError('server.checkpoints.none', identifier=repr(identifier))
    folder = _team_folder(identifier)
    with _LOCK:
        document = _read(identifier, team['format'])
        actual = current_id(identifier)
        if actual is None:
            raise MessageError('server.teams.unknown_kaggle', identifier=repr(identifier))
        if document is None:
            document = {'current': actual, 'checkpoints': [_first(team, folder)]}
            changed = True
        elif document['current'] != actual:
            old = next(
                item for item in document['checkpoints'] if item['id'] == document['current']
            )
            fresh = _first(team, folder)
            fresh['control'] = copy.deepcopy(old['control'] or team.get('control'))
            fresh['added'] = _now()
            document['checkpoints'] = [
                item for item in document['checkpoints'] if item['id'] != old['id']
            ]
            document['checkpoints'].append(fresh)
            document['current'] = fresh['id']
            changed = True
        else:
            changed = False
        for item in document['checkpoints']:
            if item['control'] is None:
                item['control'] = copy.deepcopy(team.get('control'))
                changed = True
        if changed and write:
            _write(identifier, document)
    return document


def _shown(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Returns the checkpoints of a document, the newest first, each marked when it is current."""
    shown = [
        {**copy.deepcopy(item), 'current': item['id'] == document['current']}
        for item in document['checkpoints']
    ]
    return list(reversed(shown))


def listing(identifier: str) -> dict[str, Any]:
    """Returns the checkpoints of a team for the menu, the newest first.

    The answer holds `team`, `current`, the identifier of the checkpoint that plays, `checkpoints`,
    each with `id`, `digest`, `label`, `step`, `added`, `note`, `files`, `bytes`, `control`,
    `pinned` and `current`, `limits`, the keeping rule and what an upload may be, and `trust`, the
    line to show before a submission is added.

    Raises:
        ArgumentError: If the team is unknown or has no files, as the built-in AI.
    """
    document = _document(identifier)
    return {
        'team': identifier,
        'current': document['current'],
        'checkpoints': _shown(document),
        'limits': {
            **settings(),
            'upload_bytes': MAXIMUM_BYTES,
            'idle_seconds': IDLE_SECONDS,
        },
        'trust': _messages.label('server.teams.trust'),
    }


def _find(document: dict[str, Any], point: str, identifier: str) -> dict[str, Any]:
    for item in document['checkpoints']:
        if item['id'] == point:
            return item
    raise MessageError('server.checkpoints.unknown', team=repr(identifier), checkpoint=repr(point))


def get(identifier: str, point: str) -> dict[str, Any]:
    """Returns one checkpoint of a team, as `listing` holds it.

    Raises:
        ArgumentError: If the team or the checkpoint is unknown.
    """
    document = _document(identifier)
    item = _find(document, point, identifier)
    return {**copy.deepcopy(item), 'current': item['id'] == document['current']}


def snapshot(text: str, *, with_digest: bool = False) -> dict[str, Any] | None:
    """Returns the identifier, label, step and optional digest of the selected checkpoint."""
    with _LOCK:
        found = resolve(text)
        point = found['checkpoint']
        if point is None:
            return None
        team = found['team']
        row = _find(_document(team, write=False), point, team)
        keys = ('id', 'label', 'step', 'digest') if with_digest else ('id', 'label', 'step')
        return {key: row[key] for key in keys}


def resolve(text: Any) -> dict[str, Any]:
    """Returns where the files of an entrant lie and which checkpoint they are.

    Every caller that plays a team, a match, a tournament, a rating run and an evaluation, reads
    its entrants through this function. The answer holds `entrant`, the text that names it again,
    the team alone for the current checkpoint, `team`, `checkpoint`, the identifier of the files,
    `current`, whether they are the team's current ones, `folder`, the folder whose root holds
    `main.py`, and `control`, the control the checkpoint uses. The built-in AI and the random
    entrant have no files, so their `checkpoint`, `folder` and `control` are `None`.

    Raises:
        ArgumentError: If the entrant names no team of the registry, or a checkpoint that the
            team does not hold.
    """
    from . import teams as _teams

    team, point = split(text)
    if team in (_teams.BUILTIN, _teams.RANDOM_ACTIONS):
        if point is not None:
            raise MessageError('server.checkpoints.none', identifier=repr(team))
        return {
            'entrant': team,
            'team': team,
            'checkpoint': None,
            'current': True,
            'folder': None,
            'control': None,
        }
    folder = _team_folder(team)
    actual = current_id(team)
    if point is None or point == actual:
        return {
            'entrant': team if point is None else entrant(team, point),
            'team': team,
            'checkpoint': actual,
            'current': True,
            'folder': folder,
            'control': None,
        }
    document = _document(team)
    item = _find(document, point, team)
    return {
        'entrant': entrant(team, point),
        'team': team,
        'checkpoint': point,
        'current': item['id'] == document['current'],
        'folder': folder
        if item['id'] == document['current']
        else folder / 'cuju-team' / FOLDER / point,
        'control': copy.deepcopy(item['control']),
    }


def exists(identifier: str, point: str) -> bool:
    """Returns whether a team holds a checkpoint, the current one included."""
    try:
        resolve(entrant(identifier, point))
    except ArgumentError:
        return False
    return True


def mirror_control(identifier: str, control: dict[str, Any]) -> None:
    """Gives the current checkpoint of a team the control that the team now has.

    Nothing is written for a team without a list of checkpoints, whose one checkpoint reads the
    team's control as it stands.
    """
    with contextlib.suppress(ArgumentError, OSError):
        with _LOCK:
            if not _path(identifier).is_file():
                return
            document = _document(identifier, write=False)
            for item in document['checkpoints']:
                if item['id'] == document['current']:
                    item['control'] = copy.deepcopy(control)
            _write(identifier, document)


def _move_files(source: Path, target: Path, skip: tuple[str, ...] = ()) -> None:
    """Moves every item of a folder into another, leaving out the names of `skip`."""
    target.mkdir(parents=True, exist_ok=True)
    for item in sorted(source.iterdir()):
        if item.name not in skip:
            shutil.move(str(item), str(target / item.name))


def _kept(item: Path) -> bool:
    """Returns whether a top-level item of a team's folder stays where it is in a swap."""
    return item.is_dir() and item.name.startswith(_runs.TOOL_PREFIX)


def _swap(identifier: str, document: dict[str, Any], incoming: Path, incoming_id: str) -> None:
    """Puts the files of `incoming` at the root and the current ones into the checkpoints folder.

    A failure on the way puts back what moved, so the folder is never left half changed.
    """
    from . import teams as _teams

    folder = _team_folder(identifier)
    archive = folder / _teams.TEAM_FOLDER / FOLDER / document['current']
    moved_out: list[str] = []
    moved_in: list[str] = []
    try:
        archive.mkdir(parents=True, exist_ok=True)
        for item in sorted(folder.iterdir()):
            if not _kept(item):
                shutil.move(str(item), str(archive / item.name))
                moved_out.append(item.name)
        for item in sorted(incoming.iterdir()):
            if not _kept(item):
                shutil.move(str(item), str(folder / item.name))
                moved_in.append(item.name)
    except BaseException:
        for name in moved_in:
            with contextlib.suppress(OSError):
                shutil.move(str(folder / name), str(incoming / name))
        for name in moved_out:
            with contextlib.suppress(OSError):
                shutil.move(str(archive / name), str(folder / name))
        with contextlib.suppress(OSError):
            archive.rmdir()
        raise
    document['current'] = incoming_id


def _prune(identifier: str, document: dict[str, Any]) -> list[str]:
    """Drops the oldest checkpoints that are not pinned until the keeping rule holds.

    The current checkpoint and the pinned ones stay. Returns the identifiers that went. Their
    ratings stay in the tables.
    """
    rule = settings()
    folder = _details(identifier) / FOLDER
    dropped: list[str] = []
    while True:
        items = document['checkpoints']
        total = sum(item['bytes'] for item in items)
        if len(items) <= rule['checkpoints'] and total <= rule['checkpoint_mib'] * 1024 * 1024:
            return dropped
        victim = next(
            (item for item in items if item['id'] != document['current'] and not item['pinned']),
            None,
        )
        if victim is None:
            return dropped
        shutil.rmtree(folder / victim['id'], ignore_errors=True)
        document['checkpoints'] = [item for item in items if item['id'] != victim['id']]
        dropped.append(victim['id'])


def add(
    identifier: str,
    filename: str,
    data: bytes,
    label: str = '',
    step: int | None = None,
    note: str = '',
) -> dict[str, Any]:
    """Adds new files to a team as its new current checkpoint and returns `listing`.

    The files are a folder bundle, a zip, a tar or an entry file, under the safety limits of a new
    team. The team keeps its name, its kit, its ground and everything else of its `cuju-team`
    folder. The checkpoint starts with the control of the one before and without any rating. The
    current files move under `cuju-team/checkpoints/`, and the keeping rule then drops the oldest
    checkpoints that are not pinned.

    Raises:
        ArgumentError: If the team is unknown or has no files, the files are too large or do not
            unpack, hold no entry file, are the files that already play or an earlier checkpoint.
    """
    from . import teams as _teams

    if len(data) > MAXIMUM_BYTES:
        raise MessageError('server.teams.submission.size', size=len(data))
    checked = _checked(label, note, step)
    team = _teams.entry(identifier)
    if team['kind'] != 'kaggle':
        raise MessageError('server.checkpoints.none', identifier=repr(identifier))
    # The ground of a team comes from its first checkpoint, so it is recorded before any file moves.
    _teams.first_checkpoint(identifier)
    with _LOCK:
        document = _document(identifier)
        incoming = _details(identifier) / INCOMING
        shutil.rmtree(incoming, ignore_errors=True)
        incoming.mkdir(parents=True)
        try:
            try:
                _teams._extract(data, filename, incoming)
            except OSError as error:
                raise MessageError('server.teams.submission.unpack', error=error) from error
            # The look of the team stays as it is, so the folder of an export brings no other.
            shutil.rmtree(incoming / _teams.TEAM_FOLDER, ignore_errors=True)
            digest = _runs.files_digest(incoming, (_teams.TEAM_FOLDER,))
            point = digest[:ID_LENGTH]
            if point == document['current']:
                raise MessageError('server.checkpoints.same', team=repr(identifier))
            if any(item['id'] == point for item in document['checkpoints']):
                raise MessageError('server.checkpoints.known', checkpoint=repr(point))
            count, size = _measure(incoming)
            previous = next(i for i in document['checkpoints'] if i['id'] == document['current'])
            _swap(identifier, document, incoming, point)
        finally:
            shutil.rmtree(incoming, ignore_errors=True)
        document['checkpoints'].append(
            {
                'id': point,
                'digest': digest,
                'label': checked['label'],
                'step': checked['step'],
                'added': _now(),
                'note': checked['note'],
                'files': count,
                'bytes': size,
                'control': copy.deepcopy(previous['control'] or team.get('control')),
                'pinned': False,
            }
        )
        _prune(identifier, document)
        _write(identifier, document)
        with _LOCK:
            _STAMPS.pop(str(_team_folder(identifier)), None)
    return listing(identifier)


def _checked(label: Any, note: Any, step: Any) -> dict[str, Any]:
    """Returns a label, a note and a step as a checkpoint keeps them, or refuses them."""
    if not isinstance(label, str) or len(label) > LONGEST_LABEL:
        raise MessageError('server.checkpoints.label', most=LONGEST_LABEL)
    if not isinstance(note, str) or len(note) > LONGEST_NOTE:
        raise MessageError('server.checkpoints.note', most=LONGEST_NOTE)
    if step is not None and (isinstance(step, bool) or not isinstance(step, int) or step < 0):
        raise MessageError('server.checkpoints.step', step=repr(step))
    return {'label': label.strip(), 'note': note.strip(), 'step': step}


def change(identifier: str, point: str, **fields: Any) -> dict[str, Any]:
    """Changes the label, note, step, pin or control of a checkpoint, or makes it current.

    Args:
        identifier: The team.
        point: The checkpoint.
        **fields: Any of `FIELDS`. `current=True` makes the checkpoint play, and its files move to
            the root while the current ones move under `cuju-team/checkpoints/`. A team keeps the
            control of the checkpoint that plays, so the team's control follows it.

    Returns:
        The checkpoint as `get` returns it.

    Raises:
        ArgumentError: If the team or the checkpoint is unknown, a field is unknown or holds a
            value that fails its check.
    """
    from . import teams as _teams

    unknown = sorted(set(fields) - set(FIELDS))
    if unknown:
        raise MessageError('server.checkpoints.fields', fields=', '.join(unknown))
    team = _teams.entry(identifier)
    with _LOCK:
        document = _document(identifier)
        item = _find(document, point, identifier)
        if 'label' in fields or 'note' in fields or 'step' in fields:
            checked = _checked(
                fields.get('label', item['label']),
                fields.get('note', item['note']),
                fields.get('step', item['step']),
            )
            item.update(checked)
        if 'pinned' in fields:
            if not isinstance(fields['pinned'], bool):
                raise MessageError('server.checkpoints.pinned', value=repr(fields['pinned']))
            item['pinned'] = fields['pinned']
        if fields.get('control') is not None:
            item['control'] = _teams.check_control(fields['control'], team['format'])
        if 'current' in fields:
            if fields['current'] is not True:
                raise MessageError('server.checkpoints.current', value=repr(fields['current']))
            if item['id'] != document['current']:
                source = _details(identifier) / FOLDER / item['id']
                _swap(identifier, document, source, item['id'])
                shutil.rmtree(source, ignore_errors=True)
                with _LOCK:
                    _STAMPS.pop(str(_team_folder(identifier)), None)
        _write(identifier, document)
        moved = item['id'] == document['current']
        control = item['control']
    if moved and control is not None:
        # The team's own file follows the checkpoint that plays.
        with contextlib.suppress(ArgumentError):
            _teams.update(identifier, control=control)
    return get(identifier, point)


def remove(identifier: str, point: str) -> bool:
    """Removes an earlier checkpoint with its files, and returns whether the team held it.

    The ratings of the checkpoint stay in the tables. The current checkpoint stays, since a team
    always has one.

    Raises:
        ArgumentError: If the team is unknown, or the checkpoint is the current one.
    """
    with _LOCK:
        document = _document(identifier)
        if point == document['current']:
            raise MessageError('server.checkpoints.remove_current')
        if not any(item['id'] == point for item in document['checkpoints']):
            return False
        document['checkpoints'] = [i for i in document['checkpoints'] if i['id'] != point]
        shutil.rmtree(_details(identifier) / FOLDER / point, ignore_errors=True)
        _write(identifier, document)
    return True
