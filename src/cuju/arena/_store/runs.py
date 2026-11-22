# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The runs manifest, one small file for each run of matches that the renderer keeps together.

A run gathers the episodes of one benchmark, evaluation, tournament or import batch. Its file is
`runs/<id>.json` in the renderer's home, written through a temporary file at every change, so a
stop halfway leaves the file before the change whole and a run survives a restart of the server.

The manifest holds these fields.

- `id`, the identifier of the run, such as `r-20261117-101500-3fa9`.
- `kind`, one of `KINDS`, `benchmark`, `evaluation`, `tournament` or `import`.
- `created`, the local time the run began, as `2026-11-17T10:15:00`.
- `name`, a name for people, and `run`, the series that the run belongs to, such as a training
  run whose checkpoints it evaluates, each text or `None`.
- `scenarios`, `seeds` and `opponents`, lists of what the run plays, an opponent being a team
  identifier or a mapping such as `{'kind': 'builtin', 'difficulty': 0.6}`.
- `checkpoints`, each a mapping of `id`, `label`, `step` and `digest`, as `checkpoint` returns.
- `episodes`, each a mapping of what one episode gave, such as its `recording`, `checkpoint`,
  `opponent`, `scenario`, `seed`, `side`, `outcome`, `score`, `returns` and `length`.
- `aggregates`, a mapping that the run's owner rebuilds from the episodes.
- `config`, the settings the run was made with, and `queue`, the state of its matches played on
  demand, which `queue.save_queue` writes.
- `state`, one of `STATES`, and `ended`, the local time it ended or `None`.

A file of an older version or a hand edit may hold a field of the wrong type. The reader keeps
every field it can and leaves out each one that does not fit, and an episode or checkpoint that
is no mapping is left out alone, so a damaged file still lists.

A checkpoint names the model that played. `checkpoint` takes what a caller passes, an identifier,
a mapping of `id`, `label` and `step`, or the folder of a model, and `team_checkpoint` names a
team of the registry by a digest of the files of its folder, so the same files give the same
identifier and any change of them gives another.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any

from .._text.messages import MessageError
from . import home as _home

# The kinds of run and the states that a run passes through.
KINDS = ('benchmark', 'evaluation', 'tournament', 'import')
STATES = ('queued', 'running', 'done', 'failed', 'cancelled')
# The folder of the home that holds the manifests.
FOLDER = 'runs'
# The characters of a checkpoint's digest that its identifier keeps.
_ID_LENGTH = 16
# The longest text that a checkpoint's identifier or label may hold.
_TEXT_LENGTH = 200
# The type of a list, which the function `list` of this module hides by its name.
_LIST = list
# One thread at a time changes a manifest, so no change undoes another.
_LOCK = threading.RLock()
# The folders of a team that hold no model, the renderer's own details of the team.
_TEAM_DETAILS = 'cuju-team'
# Every folder of a team whose name starts so belongs to a tool and stays out of its digests.
TOOL_PREFIX = 'cuju-'


def tool_part(parts: tuple[str, ...]) -> bool:
    """Returns whether a path inside a team's folder lies in a folder that belongs to a tool.

    The folders `cuju-team`, `cuju-look` and any other `cuju-*` at the top of the team's folder are
    kept, carried and left out of the digest of the files, and the arena never reads them.
    """
    return len(parts) > 1 and parts[0].startswith(TOOL_PREFIX)


def folder() -> Path:
    """Returns the folder of the manifests, made when missing."""
    return _home.folder(FOLDER)


def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%S')


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _text(value: Any) -> bool:
    return isinstance(value, str)


def _optional_text(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _plain(value: Any) -> bool:
    """Returns whether a value is plain JSON with finite numbers, as a manifest keeps it."""
    if value is None or isinstance(value, str | bool):
        return True
    if isinstance(value, int | float):
        return math.isfinite(value)
    if isinstance(value, _LIST):
        return all(map(_plain, value))
    if isinstance(value, dict):
        return all(isinstance(key, str) and _plain(item) for key, item in value.items())
    return False


def _list_of_plain(value: Any) -> bool:
    return isinstance(value, _LIST) and _plain(value)


def _mapping(value: Any) -> bool:
    return isinstance(value, dict) and _plain(value)


# Each field of a manifest with the test that its value passes, in the order of the file.
_FIELDS: tuple[tuple[str, Any], ...] = (
    ('id', _text),
    ('kind', lambda value: value in KINDS),
    ('created', _text),
    ('name', _optional_text),
    ('run', _optional_text),
    ('scenarios', _list_of_plain),
    ('seeds', _list_of_plain),
    ('opponents', _list_of_plain),
    ('checkpoints', None),
    ('episodes', None),
    ('aggregates', _mapping),
    ('config', _mapping),
    ('queue', lambda value: value is None or _mapping(value)),
    ('state', lambda value: value in STATES),
    ('ended', _optional_text),
)
_DEFAULTS: dict[str, Any] = {
    'name': None,
    'run': None,
    'scenarios': [],
    'seeds': [],
    'opponents': [],
    'checkpoints': [],
    'episodes': [],
    'aggregates': {},
    'config': {},
    'queue': None,
    'state': 'running',
    'ended': None,
}


def checkpoint(value: Any) -> dict[str, Any] | None:
    """Returns a checkpoint in the form a manifest and a recording keep, or `None` for none.

    Args:
        value: `None`, an identifier as text or a whole number, a mapping of `id`, `label`,
            `step` and `digest`, each optional but the identifier, or the path of a model's file
            or folder, whose content digest becomes the identifier.

    Raises:
        ArgumentError: If the value is none of these, or a field holds the wrong type.
    """
    if value is None:
        return None
    if isinstance(value, os.PathLike):
        path = Path(value)
        digest = files_digest(path)
        return {'id': digest[:_ID_LENGTH], 'label': path.name, 'digest': digest}
    if isinstance(value, bool):
        raise MessageError('server.runs.checkpoint', checkpoint=repr(value))
    if isinstance(value, str | int):
        value = {'id': value}
    if not isinstance(value, dict):
        raise MessageError('server.runs.checkpoint', checkpoint=repr(value))
    identifier = value.get('id')
    if isinstance(identifier, int) and not isinstance(identifier, bool):
        identifier = str(identifier)
    if not isinstance(identifier, str) or not 0 < len(identifier) <= _TEXT_LENGTH:
        raise MessageError('server.runs.checkpoint', checkpoint=repr(value))
    found: dict[str, Any] = {'id': identifier}
    label, step, digest = value.get('label'), value.get('step'), value.get('digest')
    if label is not None:
        if not isinstance(label, str) or len(label) > _TEXT_LENGTH:
            raise MessageError('server.runs.checkpoint', checkpoint=repr(value))
        found['label'] = label
    if step is not None:
        if isinstance(step, bool) or not isinstance(step, int):
            raise MessageError('server.runs.checkpoint', checkpoint=repr(value))
        found['step'] = step
    if digest is not None:
        if not isinstance(digest, str) or len(digest) > _TEXT_LENGTH:
            raise MessageError('server.runs.checkpoint', checkpoint=repr(value))
        found['digest'] = digest
    return found


def files_digest(path: Path, skip: tuple[str, ...] = ()) -> str:
    """Returns the SHA-256 in hexadecimal of the files of a folder, or of one file.

    The digest covers the path of each file within the folder and its bytes, in the order of the
    paths, so the same files give the same digest wherever the folder lies, and a renamed, added,
    removed or changed file gives another.

    Args:
        path: The folder or file.
        skip: The names of folders at the top of the folder that the digest leaves out. Every
            folder `cuju-*` at the top is left out as well.

    Raises:
        ArgumentError: If the path names neither a file nor a folder.
    """
    path = Path(path)
    if path.is_file():
        files = [(path.name, path)]
    elif path.is_dir():
        files = sorted(
            (item.relative_to(path).as_posix(), item)
            for item in path.rglob('*')
            if item.is_file()
            and item.relative_to(path).parts[0] not in skip
            and not tool_part(item.relative_to(path).parts)
            and '__pycache__' not in item.relative_to(path).parts
        )
    else:
        raise MessageError('server.runs.checkpoint', checkpoint=repr(str(path)))
    digest = hashlib.sha256()
    for name, item in files:
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(4, 'little') + encoded)
        data = item.read_bytes()
        digest.update(len(data).to_bytes(8, 'little'))
        digest.update(data)
    return digest.hexdigest()


def team_checkpoint(team: str) -> dict[str, Any]:
    """Returns the checkpoint of a team of the registry, named by the digest of its model files.

    The digest covers the files of the team's folder that its submission holds, and leaves out the
    renderer's own details of the team, its name, kit, images and sounds, and its earlier
    checkpoints, so editing those keeps the identifier. The identifier is the front of that digest,
    the files alone.

    Args:
        team: A team of the registry, or an entrant `team@checkpoint`, which names the earlier
            checkpoint of the team whose files the digest `checkpoint` is the front of.

    Raises:
        ArgumentError: If the team is unknown, or does not hold the checkpoint.
    """
    if not isinstance(team, str):
        raise MessageError('server.teams.unknown', identifier=repr(team))
    name, separator, _ = team.partition('@')
    if not _home.valid_identifier(name):
        raise MessageError('server.teams.unknown', identifier=repr(team))
    path = _home.folder('teams') / name
    if not path.is_dir():
        raise MessageError('server.teams.unknown', identifier=repr(team))
    if separator:
        from .._registry import checkpoints as _checkpoints

        found = _checkpoints.resolve(team)
        digest = _checkpoints.get(name, found['checkpoint'])['digest']
        return {'id': found['checkpoint'], 'label': name, 'digest': digest}
    digest = files_digest(path, (_TEAM_DETAILS,))
    return {'id': digest[:_ID_LENGTH], 'label': name, 'digest': digest}


def side_snapshots(value: Any) -> list[dict[str, Any]] | None:
    """Returns the two sides of a saved result with their checkpoint snapshots."""
    if not isinstance(value, _LIST) or len(value) != 2:
        return None
    try:
        points = [checkpoint(side['checkpoint']) for side in value]
    except (MessageError, KeyError, TypeError):
        return None
    return [
        {'checkpoint': {key: point.get(key) for key in ('id', 'label', 'step')}}
        if point is not None
        else {'checkpoint': None}
        for point in points
    ]


def _checkpoints(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, _LIST):
        return []
    found = []
    for item in value:
        try:
            checked = checkpoint(item) if isinstance(item, dict) else None
        except MessageError:
            checked = None
        if checked is not None:
            found.append(checked)
    return found


def _episodes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, _LIST):
        return []
    return [item for item in value if _mapping(item)]


def _clean(document: Any, identifier: str) -> dict[str, Any] | None:
    """Returns a manifest as read, with each field that does not fit left out or at its default.

    A manifest without a known kind is no run, and gives `None`.
    """
    if not isinstance(document, dict) or document.get('kind') not in KINDS:
        return None
    clean: dict[str, Any] = {'id': identifier}
    for field, test in _FIELDS:
        if field == 'id':
            continue
        value = document.get(field)
        if field == 'checkpoints':
            clean[field] = _checkpoints(value)
        elif field == 'episodes':
            clean[field] = _episodes(value)
        elif field in document and test(value):
            clean[field] = value
        elif field in _DEFAULTS:
            clean[field] = _DEFAULTS[field]
    clean.setdefault('created', '')
    return clean


def _path(identifier: Any) -> Path | None:
    if not isinstance(identifier, str) or not _home.valid_identifier(identifier):
        return None
    return folder() / f'{identifier}.json'


def _write(document: dict[str, Any]) -> None:
    """Writes a manifest through a temporary file, so a stop halfway leaves the old file whole."""
    target = folder() / f'{document["id"]}.json'
    temporary = target.with_name(target.name + '.part')
    temporary.write_text(json.dumps(document, separators=(',', ':')), encoding='utf-8')
    os.replace(temporary, target)


def _read(identifier: str) -> dict[str, Any] | None:
    path = _path(identifier)
    if path is None:
        return None
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return _clean(document, identifier)


def create(
    kind: str,
    identifier: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Starts a run and writes its manifest, and returns the manifest.

    Args:
        kind: One of `KINDS`.
        identifier: The identifier of the run, by default a new one, `r-` and the time.
        **fields: Any field of the manifest but `id`, `kind` and `created`, such as `name`,
            `run`, `scenarios`, `seeds`, `opponents`, `checkpoints`, `config` and `state`.

    Raises:
        ArgumentError: If the kind, the identifier or a field does not fit, or the run exists.
    """
    if kind not in KINDS:
        raise MessageError('server.runs.kind', kind=repr(kind), kinds=', '.join(KINDS))
    unknown = sorted(set(fields) - set(_DEFAULTS))
    if unknown:
        raise MessageError('server.runs.field', field=', '.join(unknown))
    with _LOCK:
        if identifier is None:
            identifier = _home.new_identifier('r')
            while (folder() / f'{identifier}.json').exists():
                identifier = _home.new_identifier('r')
        path = _path(identifier)
        if path is None:
            raise MessageError('server.runs.identifier', identifier=repr(identifier))
        if path.exists():
            raise MessageError('server.runs.exists', identifier=repr(identifier))
        document = {'id': identifier, 'kind': kind, 'created': _now(), **_DEFAULTS}
        for field, value in fields.items():
            document[field] = _checked(field, value)
        _write(document)
        return document


def _checked(field: str, value: Any) -> Any:
    """Returns the value of a field of a manifest, or raises `ArgumentError` if it does not fit."""
    if field == 'checkpoints':
        if not isinstance(value, _LIST):
            raise MessageError('server.runs.value', field=field)
        return [checkpoint(item) for item in value]
    if field == 'episodes':
        if not isinstance(value, _LIST) or not all(_mapping(item) for item in value):
            raise MessageError('server.runs.value', field=field)
        return _LIST(value)
    test = dict(_FIELDS)[field]
    if not test(value):
        raise MessageError('server.runs.value', field=field)
    return value


def get(identifier: str) -> dict[str, Any] | None:
    """Returns the manifest of a run, or `None` for an unknown or unreadable one."""
    with _LOCK:
        return _read(identifier)


def list(kind: str | None = None, run: str | None = None) -> list[dict[str, Any]]:
    """Returns the manifests of the runs of matches, newest first.

    Each manifest names a benchmark, an evaluation, a tournament or a batch of imports, with its
    scenarios, seeds, opponents, checkpoints, episodes, aggregates and state.

    Args:
        kind: Only the runs of this kind, or every kind.
        run: Only the runs of this series, as their field `run` names it, or every one.
    """
    found = []
    with _LOCK:
        for path in folder().glob('*.json'):
            document = _read(path.stem)
            if document is None:
                continue
            if kind is not None and document['kind'] != kind:
                continue
            if run is not None and document.get('run') != run:
                continue
            found.append(document)
    found.sort(key=lambda document: (str(document.get('created')), document['id']), reverse=True)
    return found


def update(identifier: str, **fields: Any) -> dict[str, Any]:
    """Changes fields of a run's manifest and returns the manifest.

    Raises:
        ArgumentError: If the run is unknown or a field does not fit.
    """
    unknown = sorted(set(fields) - set(_DEFAULTS))
    if unknown:
        raise MessageError('server.runs.field', field=', '.join(unknown))
    with _LOCK:
        document = _known(identifier)
        for field, value in fields.items():
            document[field] = _checked(field, value)
        _write(document)
        return document


def _known(identifier: str) -> dict[str, Any]:
    document = _read(identifier)
    if document is None:
        raise MessageError('server.runs.unknown', identifier=repr(identifier))
    return document


def append_episode(identifier: str, episode: dict[str, Any]) -> dict[str, Any]:
    """Adds the result of one episode to a run and returns the manifest.

    Args:
        identifier: The run.
        episode: What the episode gave, plain JSON, such as its `recording`, `checkpoint`,
            `opponent`, `scenario`, `seed`, `side`, `outcome`, `score`, `returns` and `length`.

    Raises:
        ArgumentError: If the run is unknown or the episode is no mapping of plain JSON.
    """
    if not _mapping(episode):
        raise MessageError('server.runs.value', field='episodes')
    with _LOCK:
        document = _known(identifier)
        document['episodes'] = [*document['episodes'], dict(episode)]
        _write(document)
        return document


def finish(
    identifier: str, aggregates: dict[str, Any] | None = None, state: str = 'done'
) -> dict[str, Any]:
    """Ends a run, with its aggregates where given, and returns the manifest.

    Args:
        identifier: The run.
        aggregates: What the run's owner made of its episodes, kept as given, or `None` to keep
            those it has.
        state: `done`, `failed` or `cancelled`.

    Raises:
        ArgumentError: If the run is unknown, the state is no end or the aggregates do not fit.
    """
    if state not in ('done', 'failed', 'cancelled'):
        raise MessageError('server.runs.value', field='state')
    with _LOCK:
        document = _known(identifier)
        if aggregates is not None:
            document['aggregates'] = _checked('aggregates', aggregates)
        document['state'] = state
        document['ended'] = _now()
        _write(document)
        return document


def remove(identifier: str) -> bool:
    """Deletes the manifest of a run, and returns whether there was one.

    The recordings of the run stay in the library, which keeps them as any other.
    """
    path = _path(identifier)
    if path is None:
        return False
    with _LOCK:
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True
