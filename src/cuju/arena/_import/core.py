# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The import of match files into the library, which `cuju.arena.add_recordings` runs.

A file is a GRF dump, a Kaggle episode, an action log of cuju, a recording of cuju or a bundle of
a recording and its attachments, told apart by its content. A dump that starts at its kick off
plays again in cuju and is checked step by step against the file, and when every step is equal it
becomes an exact recording with every joint. An action log plays again with the check of its
digests. Anything else, a goal dump, a dump that starts later, a dump whose match plays
differently and a Kaggle episode, becomes a recording of positions, version 3, which says so in
its header and its provenance. Each import is a run of kind `import` in the runs manifest, with
one episode for each recording.

The settings of the table `[import]` choose the fidelity, the device, the folder and the title,
and limit the size of a file, the files of one import and the recordings of one file.
"""

from __future__ import annotations

import gzip
import hashlib
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .._config import layers as _layers
from .._format import attachments as _attachments
from .._format import recording as _recording
from .._store import library as _library
from .._store import runs as _runs
from .._text import messages as _messages
from .._text.messages import MessageError
from . import grf as _grf
from . import kaggle as _kaggle
from . import pickles as _pickles
from . import positions as _positions
from . import replay as _replay

# The kinds of file that an import takes, by the name that `source` gives them.
KINDS = ('grf_dump', 'kaggle', 'action_log', 'cjr', 'cjz')
# The bytes that the content check reads of a file.
_HEAD_BYTES = 64
# GRF: the first opcode of a pickle of protocol 2 or later.
_PROTOCOL = 0x80
# The version of a recording of positions.
POSITIONS_VERSION = 3

Progress = Callable[[dict[str, Any]], None]


def settings(config: Any = None, overrides: Any = ()) -> dict[str, Any]:
    """Returns the settings of the table `[import]`."""
    return _layers.table('import', config, overrides)


def imported_line(entry: dict[str, Any]) -> str:
    """Returns the line that the command prints for a recording that an import saved."""
    if entry['fidelity'] == 'exact' and entry['verified_steps'] is not None:
        kept = f'exact, {_messages.counted(entry["verified_steps"], "step")} checked'
    else:
        kept = entry['fidelity']
    line = f'{entry["id"]}  {entry["file"]}  {kept}'
    reason = entry.get('reason')
    if isinstance(reason, dict) and reason.get('text'):
        line += f', {reason["text"]}'
    for warning in entry.get('warnings') or []:
        line += f'\n    {warning.get("text", warning)}'
    return line


def kind_of(path: Path) -> str | None:
    """Returns the kind of a file from its content, one of `KINDS`, or `None` for another.

    A recording starts with its magic, plain or under gzip, a bundle is a ZIP archive with
    `bundle.json`, an action log is a ZIP archive with `log.json`, a GRF dump starts with the
    opcode of a pickle, and a Kaggle episode is a JSON object.
    """
    with open(path, 'rb') as stream:
        head = stream.read(_HEAD_BYTES)
    if head.startswith(_recording.MAGIC):
        return 'cjr'
    if head[:2] == b'\x1f\x8b':
        try:
            with gzip.open(path, 'rb') as stream:
                inner = stream.read(len(_recording.MAGIC))
        except (OSError, EOFError):
            return None
        return 'cjr' if inner == _recording.MAGIC else None
    if head[:4] == b'PK\x03\x04':
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
        except (OSError, zipfile.BadZipFile):
            return None
        if _attachments.BUNDLE_INDEX in names:
            return 'cjz'
        return 'action_log' if 'log.json' in names else None
    if len(head) >= 2 and head[0] == _PROTOCOL and 2 <= head[1] <= 5:
        return 'grf_dump'
    if _kaggle.looks_like(head):
        return 'kaggle'
    return None


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def checked(paths: list[Path], table: dict[str, Any]) -> list[tuple[Path, str]]:
    """Returns each file with its kind, after the limits of the settings.

    Raises:
        ArgumentError: If there are more files than `max_files`, a file is missing, larger than
            `max_bytes`, or of no kind that the import takes.
    """
    if len(paths) > table['max_files']:
        raise MessageError('server.import.count', most=table['max_files'], count=len(paths))
    found = []
    for path in paths:
        if not path.is_file():
            raise MessageError('server.import.missing', path=str(path))
        size = path.stat().st_size
        if size > table['max_bytes']:
            raise MessageError(
                'server.import.size', most=table['max_bytes'], size=size, path=str(path)
            )
        kind = kind_of(path)
        if kind is None:
            raise MessageError('server.import.kind', path=str(path))
        found.append((path, kind))
    return found


def _title(table: dict[str, Any], path: Path, name: str | None) -> Any:
    if table['title']:
        return str(table['title'])[:120]
    return _messages.label('server.import.title', name=(name or path.name)[:80])


def _entry(
    path: Path,
    source: str,
    fidelity: str,
    verified: int | None,
    difference: dict[str, Any] | None,
    warnings: list[dict[str, Any]],
    imported: dict[str, Any],
    reason: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        'id': path.stem,
        'path': str(path),
        'source': source,
        'fidelity': fidelity,
        'verified_steps': verified,
        'first_difference': difference,
        'warnings': warnings,
        'reason': reason,
        'file': imported['file'],
        'sha256': imported['sha256'],
    }


def _positions_import(
    steps: list[_grf.Step],
    path: Path,
    imported: dict[str, Any],
    reason: dict[str, Any],
    title: Any,
    scenario: str,
    agents: tuple[int, int],
    action_set: str | None,
    teams: list[dict[str, Any]] | None,
    ended: bool,
) -> Path:
    """Writes the steps as a recording of positions into the library and returns its path."""
    provenance = {
        'source': 'import',
        'imported_from': {**imported, 'fidelity': 'positions', 'reason': reason},
    }
    if action_set is not None and sum(agents):
        provenance['action_set'] = action_set
    head = _positions.header(title, scenario, steps, teams, provenance, agents, action_set)
    frames = _positions.frames(steps, agents, action_set, ended)
    return _library.save(head, frames, version=POSITIONS_VERSION)


def _dump(
    path: Path,
    imported: dict[str, Any],
    table: dict[str, Any],
    title: Any,
    report: Progress,
) -> list[dict[str, Any]]:
    """Imports a GRF dump, exactly where it plays again, else as positions."""
    warnings: list[dict[str, Any]] = []
    truncated = False
    try:
        entries = _pickles.steps(path.read_bytes())
    except _pickles.TruncatedError as cut:
        entries, truncated = cut.steps, True
    except _pickles.RefusedError as error:
        raise MessageError('server.import.refused', path=str(path), found=error.found) from None
    if not entries:
        raise MessageError('server.import.empty', path=str(path))
    first = entries[0]
    debug = first.get('debug') if isinstance(first.get('debug'), dict) else {}
    config = debug.get('config') if isinstance(debug.get('config'), dict) else None
    start = debug.get('frame_cnt')
    reason = None
    difference = None
    verified = 0
    if table['fidelity'] == 'positions':
        reason = _messages.label('server.import.reason.chosen')
    elif truncated:
        reason = _messages.label('server.import.reason.truncated', steps=len(entries))
    elif config is None:
        reason = _messages.label('server.import.reason.no_config')
    elif start != 0:
        reason = _messages.label('server.import.reason.mid_episode', step=repr(start))
    elif any(not isinstance(entry.get('observation'), dict) for entry in entries):
        # A step without its observation leaves the check nothing to compare.
        missing = sum(not isinstance(entry.get('observation'), dict) for entry in entries)
        reason = _messages.label('server.import.reason.unobserved', count=missing)
    else:

        def played(done: int, total: int) -> None:
            report({'stage': 'checking', 'step': done, 'steps': total, 'progress': done / total})

        result = _replay.dump(
            entries, {**imported, 'fidelity': 'exact'}, title, table['device'], played
        )
        if result.paths:
            skipped = sorted({field.split('.', 1)[-1] for field in result.skipped})
            if skipped:
                warnings.append(
                    _messages.label('server.import.warning.unchecked', fields=', '.join(skipped))
                )
            return [
                _entry(
                    saved,
                    'grf_dump',
                    'exact',
                    result.verified_steps,
                    None,
                    warnings,
                    imported,
                    None,
                )
                for saved in result.paths
            ]
        reason, difference, verified = result.reason, result.first_difference, result.verified_steps
    if table['fidelity'] == 'exact':
        raise MessageError(
            'server.import.not_exact', path=str(path), reason=(reason or {}).get('text', '')
        )
    report({'stage': 'positions', 'progress': 1.0})
    if config is not None:
        agents = _grf.agent_counts(config.get('players'))
    else:
        agents = None
    observed = [entry for entry in entries if isinstance(entry.get('observation'), dict)]
    if not observed:
        raise MessageError('server.import.no_observations', path=str(path))
    if agents is None:
        view = observed[0]['observation']
        agents = (
            len(view.get('left_agent_controlled_player') or []),
            len(view.get('right_agent_controlled_player') or []),
        )
    try:
        steps = [_grf.dump_step(entry, *agents) for entry in observed]
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise MessageError(
            'server.import.unreadable', path=str(path), detail=f'{type(error).__name__}: {error}'
        ) from None
    names = [name for step in steps for name in (step.actions or [])]
    action_set = (config or {}).get('action_set')
    if action_set not in _grf.ACTION_SETS:
        action_set = _grf.action_set_of(names) if names else None
    scenario = str((config or {}).get('level') or 'grf_dump')[:100]
    ended = steps[-1].steps_left == 0
    saved = _positions_import(
        steps, path, imported, reason, title, scenario, agents, action_set, None, ended
    )
    if truncated:
        warnings.append(_messages.label('server.import.warning.truncated', steps=len(entries)))
    return [
        _entry(saved, 'grf_dump', 'positions', verified, difference, warnings, imported, reason)
    ]


def _kaggle_import(
    path: Path,
    imported: dict[str, Any],
    table: dict[str, Any],
    title: Any,
    report: Progress,
) -> list[dict[str, Any]]:
    """Imports a Kaggle episode as positions."""
    try:
        episode = _kaggle.read(path.read_bytes())
    except _kaggle.EpisodeError as error:
        raise MessageError('server.import.kaggle', path=str(path), detail=str(error)) from None
    reason = _messages.label('server.import.reason.kaggle')
    if table['fidelity'] == 'exact':
        raise MessageError('server.import.not_exact', path=str(path), reason=reason['text'])
    warnings = []
    if episode.turned_back:
        warnings.append(
            _messages.label('server.import.warning.turned_back', count=episode.turned_back)
        )
    if episode.disagreeing:
        warnings.append(
            _messages.label('server.import.warning.disagreeing', count=episode.disagreeing)
        )
    if episode.action_set is None:
        warnings.append(_messages.label('server.import.warning.actions'))
    report({'stage': 'positions', 'progress': 1.0})
    teams = [{'name': name} for name in episode.teams] if episode.teams else None
    saved = _positions_import(
        episode.steps,
        path,
        imported,
        reason,
        title,
        episode.scenario,
        episode.agents,
        episode.action_set,
        teams,
        episode.ended,
    )
    return [_entry(saved, 'kaggle', 'positions', 0, None, warnings, imported, reason)]


def _log_import(
    path: Path,
    imported: dict[str, Any],
    table: dict[str, Any],
    title: Any,
    report: Progress,
) -> list[dict[str, Any]]:
    """Imports an action log of cuju, checked call by call."""

    def played(done: int, total: int) -> None:
        report({'stage': 'checking', 'step': done, 'steps': total, 'progress': done / total})

    try:
        result = _replay.action_log(
            path,
            {**imported, 'fidelity': 'exact'},
            title,
            table['environments'],
            table['device'],
            table['max_recordings'],
            played,
        )
    except ValueError as error:
        raise MessageError('server.import.log', path=str(path), detail=str(error)) from None
    if not result.paths:
        if result.first_difference is not None:
            raise MessageError(
                'server.import.log_differs', path=str(path), call=result.first_difference['step']
            )
        raise MessageError('server.import.log_empty', path=str(path))
    warnings = []
    if result.limited:
        warnings.append(
            _messages.label('server.import.warning.limit', most=table['max_recordings'])
        )
    if result.reason is not None:
        warnings.append(result.reason)
    entries = []
    for saved in result.paths:
        header = _recording.read_header(saved)
        verified = header.get('provenance', {}).get('imported_from', {}).get('verified_steps')
        entries.append(
            _entry(
                saved,
                'action_log',
                'exact',
                verified,
                result.first_difference,
                warnings,
                imported,
                None,
            )
        )
    return entries


def _cjr_import(path: Path, imported: dict[str, Any]) -> list[dict[str, Any]]:
    """Adds a recording file of cuju to the library as it is."""
    identifier = _library.import_file(path)
    saved = _library.path_of(identifier)
    header = _recording.read_header(saved)
    fidelity = 'positions' if header.get('fidelity') == 'positions' else 'exact'
    return [_entry(saved, 'cjr', fidelity, None, None, [], imported, None)]


def _cjz_import(path: Path, imported: dict[str, Any]) -> list[dict[str, Any]]:
    """Adds the recording of a bundle to the library with its attachments."""
    identifier = _library.import_bundle(path)
    saved = _library.path_of(identifier)
    header = _recording.read_header(saved)
    fidelity = 'positions' if header.get('fidelity') == 'positions' else 'exact'
    return [_entry(saved, 'cjz', fidelity, None, None, [], imported, None)]


def import_file(
    path: Path,
    kind: str,
    table: dict[str, Any],
    name: str | None = None,
    report: Progress | None = None,
) -> list[dict[str, Any]]:
    """Imports one file of a kind that `kind_of` gave, and returns its entries.

    Args:
        path: The file.
        kind: Its kind, one of `KINDS`.
        table: The settings of `[import]`.
        name: The name that the file had where it came from, for the title, by default its own.
        report: Called with each report of progress, a mapping of `stage`, `progress` and, while
            a match plays again, `step` and `steps`.
    """
    report = report or (lambda found: None)
    imported = {'file': (name or path.name)[:200], 'sha256': _digest(path), 'source': kind}
    title = _title(table, path, name)
    if kind == 'grf_dump':
        entries = _dump(path, imported, table, title, report)
    elif kind == 'kaggle':
        entries = _kaggle_import(path, imported, table, title, report)
    elif kind == 'action_log':
        entries = _log_import(path, imported, table, title, report)
    elif kind == 'cjz':
        entries = _cjz_import(path, imported)
    else:
        entries = _cjr_import(path, imported)
    folder = table['folder']
    if folder and kind not in ('cjr', 'cjz'):
        for entry in entries:
            _library.update(entry['id'], folder=folder)
    return entries


def run(
    paths: list[Path],
    config: Any = None,
    overrides: Any = (),
    names: list[str] | None = None,
    report: Progress | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Imports files as `add_recordings` does, and returns the entries and the run's identifier.

    Args:
        paths: The files.
        config, overrides: The settings, as `cuju.arena.config` takes them.
        names: The name each file had where it came from, by default their own.
        report: Called with each report of progress, which also names the `file` by its index.
    """
    table = settings(config, overrides)
    found = checked(paths, table)
    run_id = _runs.create(
        'import',
        name=', '.join(path.name for path in paths)[:200],
        config={'import': {key: value for key, value in table.items()}},
    )['id']
    entries: list[dict[str, Any]] = []
    try:
        for index, (path, kind) in enumerate(found):

            def step(found: dict[str, Any], index: int = index) -> None:
                if report is not None:
                    report({**found, 'file': index, 'files': len(paths)})

            given = names[index] if names else None
            for entry in import_file(path, kind, table, given, step):
                entry['run'] = run_id
                entries.append(entry)
                summary = _library.details(entry['id']) if _library.path_of(entry['id']) else {}
                _runs.append_episode(
                    run_id,
                    {
                        'recording': entry['id'],
                        'file': entry['file'],
                        'sha256': entry['sha256'],
                        'source': entry['source'],
                        'fidelity': entry['fidelity'],
                        'verified_steps': entry['verified_steps'],
                        'scenario': summary.get('scenario'),
                        'score': summary.get('score'),
                        'outcome': summary.get('outcome'),
                        'length': summary.get('length'),
                    },
                )
    except _replay.CancelledError:
        _runs.finish(run_id, _aggregates(entries), state='cancelled')
        raise
    except BaseException:
        _runs.finish(run_id, _aggregates(entries), state='failed')
        raise
    _runs.finish(run_id, _aggregates(entries))
    return entries, run_id


def _aggregates(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        'recordings': len(entries),
        'exact': sum(entry['fidelity'] == 'exact' for entry in entries),
        'positions': sum(entry['fidelity'] == 'positions' for entry in entries),
    }


def add_recordings(
    *paths: str | Path, config: Any = None, overrides: Any = ()
) -> list[dict[str, Any]]:
    """Imports GRF dumps, Kaggle episodes, action logs, recordings and bundles into the library.

    A GRF dump that starts at its kick off plays again in cuju from its configuration and actions,
    and each step is compared with the file. When every step is equal the recording is exact and
    holds every joint. An action log plays again with the check of its digests. A goal dump, a
    dump that starts later or plays differently, and a Kaggle episode become recordings of
    positions, which the page shows in 2D and which say so in their header. A dump is read by a
    reader that admits plain data and numpy arrays alone, so no code of the file runs.

    Args:
        *paths: The files, of any of the five kinds, told apart by their content.
        config: The settings, a file, a mapping of tables, a `Config` or a list of them, laid over
            the shipped defaults as `cuju.arena.config` describes. The table `[import]` serves
            the import.
        overrides: Settings such as `import.fidelity=positions`, laid over `config`.

    Returns:
        One entry for each recording, with its `id` and `path` in the library, the `source` of
        the file, `grf_dump`, `kaggle`, `action_log`, `cjr` or `cjz`, the `fidelity`, `exact` or
        `positions`, the `verified_steps` found equal to the file, the `first_difference`, a
        mapping of `step`, `field` and `detail` or `None`, the `warnings` and the `reason` for
        positions as labels, the `file` name, its `sha256` and the `run` of the import in the runs
        manifest.

    Raises:
        ArgumentError: If a file is missing, too large, of no kind that the import takes, holds
            what a dump does not, or does not read, or more files are given than the settings
            allow.
        ConfigurationError: If a setting is invalid.
    """
    return run([Path(path).expanduser() for path in paths], config, overrides)[0]
