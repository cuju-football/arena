# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The index of the moments of every recording of the library, and the search over it.

The index keeps one small file per recording in the folder `index` of the renderer's home, with
the moments that `_events` finds in its frames and what a search of policy signals and a view
over many episodes read without decoding the file. Beside each it keeps the statistics of the
recording, which `_store.stats` describes, in the folder `stats`.

A recording is indexed alone, when it is saved or imported, when a search or a page asks for
it, or at the server's pass over the library, and the pass lets go of the index between
recordings, so the moments of one recording never wait for the rest. A recording whose file
changes is read again, and the files of a recording deleted for good go. A recording in the Trash
keeps its files, so a restore finds them at once, and a change of the version of the moments or
of the index reads every recording again.

An entry of the index holds these fields.

- `version`, the version of the index and of the moments, and `id`, the recording.
- `signature`, the size and time of the recording and its section attachments.
- `header`, the title, scenario, rate, teams and players of the recording.
- `moments`, the moments in the order of their steps.
- `unavailable`, the kinds of moment that the recording cannot show, such as those that need the
  touches of the ball in a recording of positions whose source gave none.
- `signals`, the extremes and candidates of the annotations, which `_store.stats.signals`
  describes.
- `heat`, the heat bins of the players and the ball, which `_store.stats.heat` describes.
- `run` and `checkpoint`, from the provenance of the recording, and `run_key`, the key of the run
  that plays it again, each a mapping or `None`.
- `has`, the sections beside the frames, such as `PLYR`, `ACTS` and `STAT`, with `ANNO` and `GRID`
  for the annotations and overlays that the attachments of the recording or an older file carry.

A query is a line of words and filters, such as `goal team:Greens after:60`, as `QUERY_HELP`
describes and the repository's `docs/moments-and-search.md` documents.
"""

from __future__ import annotations

import bisect
import contextlib
import json
import math
import queue
import re
import threading
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from .. import plugins as _plugins
from .._analysis import Recording
from .._format import actions as _actions
from .._format import annotations as _annotations
from .._format import attachments as _attachments
from .._format import recording as _recording
from .._store import dates as _dates
from .._store import home as _home
from .._store import library as _library
from .._store import references as _references
from .._store import stats as _stats
from .._text import messages as _messages
from .._text.messages import MessageError
from . import captions as _captions
from . import detectors as _detectors
from . import events as _events

# The version of the files of the index, beside the version of the moments.
INDEX_VERSION = 3
VERSION = f'{INDEX_VERSION}.{_events.VERSION}'
# The most results a search returns unless it asks for others, and the most it may ask for.
LIMIT = 50
MAXIMUM_LIMIT = 1000
# Seconds before a moment that its result opens Watch at.
LEAD_SECONDS = 4.0
# The kinds of moment that need the touches of the ball and the calls of the referee, which a
# recording of positions from a source without them cannot show.
TOUCH_KINDS = ('shot', 'save', 'block', 'woodwork', 'nearMiss', 'pass', 'tackle')
REFEREE_KINDS = ('foul', 'card', 'offside')
# The lock of the memory of the index, held for a moment at a time and never while a recording
# is read, so one recording never waits for another.
_LOCK = threading.RLock()
# The entries of each renderer folder by recording, so a sync that started for one folder keeps
# to it while another one becomes the home.
_CACHES: dict[str, dict[str, tuple[list[int], dict[str, Any]]]] = {}
# The lock of each recording that is being read, by folder and recording, so two threads never
# read the same recording at once.
_READING: dict[tuple[str, str], threading.Lock] = {}
# What the passes over each folder did so far, for the progress that a page shows.
_PASSES: dict[str, dict[str, Any]] = {}
# The recordings that wait to be indexed in the background, as folder and identifier.
_WAITING: queue.Queue[tuple[Path, str]] = queue.Queue()
_WORKER: list[threading.Thread] = []

# The words that name kinds of moments, each with the kind and a test of its data.
_WORDS: dict[str, tuple[str, Any]] = {}


def _word(names: str, kind: str, test: Any = None) -> None:
    for name in names.split():
        _WORDS[name] = (kind, test)


_word('goal goals', 'goal')
_word('own-goal own-goals owngoal', 'goal', lambda d: bool(d.get('ownGoal')))
_word('shot shots', 'shot')
_word('header headers', 'shot', lambda d: bool(d.get('header')))
_word('save saves', 'save')
_word('block blocks', 'block')
_word('woodwork', 'woodwork')
_word('post posts', 'woodwork', lambda d: d.get('part') != 'bar')
_word('bar crossbar', 'woodwork', lambda d: d.get('part') == 'bar')
_word('near-miss near-misses nearmiss miss misses', 'nearMiss')
_word('pass passes', 'pass')
_word('tackle tackles slide slides', 'tackle')
_word('foul fouls', 'foul')
_word('card cards', 'card')
_word('yellow yellows yellow-card', 'card', lambda d: d.get('colour') == 'yellow')
_word('red reds red-card', 'card', lambda d: d.get('colour') == 'red')
_word('offside offsides', 'offside')
_word('restart restarts', 'restart')
_word('set-piece set-pieces setpiece', 'restart', lambda d: d.get('restart') != 'kickoff')
for _name, _restart in (
    ('corner corners', 'corner'),
    ('free-kick free-kicks freekick', 'free_kick'),
    ('penalty penalties', 'penalty'),
    ('throw-in throw-ins throwin', 'throw_in'),
    ('goal-kick goal-kicks goalkick', 'goal_kick'),
    ('kickoff kick-off kickoffs', 'kickoff'),
):
    _word(_name, 'restart', lambda d, restart=_restart: d.get('restart') == restart)
_word('possession turnover turnovers', 'possession')
_word('halftime half-time', 'halftime')
_word('fulltime full-time', 'fulltime')

_KEYS = (
    'kind',
    'team',
    'player',
    'scenario',
    'source',
    'tournament',
    'match',
    'date',
    'since',
    'until',
    'after',
    'before',
    'score',
    'final',
    'outcome',
    'limit',
    'value-drop',
    'surprise',
    'entropy',
    'reward',
    'ann',
)
_TEAM_SIDES = {'left': 0, 'home': 0, '0': 0, 'right': 1, 'away': 1, '1': 1}
# The filters that find moments of what a policy did, from the signals that the index keeps.
SIGNAL_KEYS = ('value-drop', 'surprise', 'entropy', 'reward', 'ann')
# The words that find the same moments as a signal filter without a value.
_SIGNAL_WORDS = {
    'value-drop': 'value-drop',
    'value-drops': 'value-drop',
    'surprise': 'surprise',
    'surprises': 'surprise',
    'unlikely': 'surprise',
}
# The kind of moment of each signal, which the results carry.
SIGNAL_KINDS = ('value-drop', 'surprise', 'entropy', 'annotation')
_SOURCES = {
    'requested': 'requested_match',
    'requested_match': 'requested_match',
    'match': 'requested_match',
    'game': 'human_game',
    'played': 'human_game',
    'human_game': 'human_game',
    'benchmark': 'benchmark',
    'recorder': 'recorder',
    'live': 'live',
    'import': 'import',
    'imported': 'import',
}
_STATES = {
    'leading': 1,
    'winning': 1,
    'ahead': 1,
    'level': 0,
    'drawing': 0,
    'tied': 0,
    'trailing': -1,
    'losing': -1,
    'behind': -1,
}
_TERM = re.compile(r'(-?)(?:([A-Za-z][A-Za-z-]*):)?(?:"([^"]*)"?|(\S+))')
_DATE = re.compile(r'\d{4}(-\d{2}(-\d{2})?)?')
_SCORE = re.compile(r'(\d+)-(\d+)')
# A number as a query writes it, and a comparison with one, such as `>0.5` or `value<-0.5`.
_NUMBER = r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)'
_BOUND = re.compile(rf'(>=|<=|>|<)({_NUMBER})')
_NAMED_BOUND = re.compile(rf'(.+?)(>=|<=|>|<)({_NUMBER})')
_PROBABILITY = re.compile(_NUMBER)

QUERY_HELP = (
    'words name kinds, such as goal, shot, save, pass, tackle, card, yellow, red, foul, '
    'offside, corner, free-kick, penalty, throw-in, goal-kick, set-piece, possession, '
    'woodwork and near-miss, and other words match the title, scenario, teams and players. '
    'Filters are kind:, team:, player:, scenario:, source:, tournament:, match:, date:, '
    'since:, until:, after:, before:, score:, final:, outcome: and limit:, a minus sign before '
    'a term excludes what it matches, and quotes hold words together, as in team:"Harbour Rovers". '
    'The signals of a policy find moments too, value-drop or value-drop:<name> for a value '
    'that fell, surprise or surprise:0.05 for an unlikely action, entropy:>1.5, reward:>0 and '
    'ann:<name><0.5 for a named value past a bound.'
)


def fold(text: Any) -> str:
    """Returns text in lower case without accents, so a query matches whatever way it is typed."""
    normal = unicodedata.normalize('NFD', str(text if text is not None else ''))
    return ''.join(c for c in normal if not unicodedata.combining(c)).lower()


def _cache(home: Path) -> dict[str, tuple[list[int], dict[str, Any]]]:
    return _CACHES.setdefault(str(home), {})


def _signature(path: Path) -> list[int]:
    """Returns the size and time of a recording and its section attachments."""
    stat = path.stat()
    found = [stat.st_size, stat.st_mtime_ns]
    return found + _attachments.section_signature(path)


def _header(document: dict[str, Any]) -> dict[str, Any]:
    """Returns what the index keeps of a header, the teams with their rosters and the players."""
    teams = []
    for team in document.get('teams') or []:
        team = team if isinstance(team, dict) else {}
        kept = {'name': team.get('name'), 'roster': _captions.players_of(team)}
        if team.get('id') is not None:
            kept['id'] = team['id']
        teams.append(kept)
    players = [
        {key: player.get(key) for key in ('team', 'index', 'goalkeeper')}
        for player in document.get('players') or []
    ]
    return {
        'title': document.get('title'),
        'scenario': document.get('scenario'),
        'steps_per_second': document.get('steps_per_second') or 10,
        'teams': teams,
        'players': players,
    }


def frames_of(path: Path, document: dict[str, Any]) -> list[bytes]:
    """Returns the frames of a recording file as the rules of the moments read them.

    A recording of positions, version 3, holds track frames, and each becomes a frame whose
    players stand at their root positions with the rest of their poses at zero, which is all of
    a pose that the rules read.
    """
    if document.get('version', 1) != 3:
        return list(_recording.frames(path))
    players = len(document['players'])
    tracks = list(_recording.tracks(path))
    if not tracks:
        return []
    import numpy as np

    head = _stats._FRAME_HEAD
    roots_end = head + 6 * players
    padded = -(-players // 4) * 4
    track_bytes = len(tracks[0])
    raw = np.frombuffer(b''.join(tracks), np.uint8).reshape(len(tracks), track_bytes)
    full = np.zeros((len(tracks), _stats.frame_bytes(players)), np.uint8)
    full[:, :head] = raw[:, :head]
    nodes = full[:, head : head + players * _stats._NODE_BYTES].reshape(len(tracks), players, -1)
    nodes[:, :, :6] = raw[:, head:roots_end].reshape(len(tracks), players, 6)
    full[:, head + players * _stats._NODE_BYTES :] = raw[:, track_bytes - 3 * padded :]
    return [row.tobytes() for row in full]


def _unavailable(document: dict[str, Any], frames: Sequence[bytes]) -> list[str]:
    """Returns the kinds of moment that a recording cannot show, for a recording of positions
    whose frames hold no touch of the ball."""
    if document.get('version', 1) != 3 or not frames:
        return []
    players = len(document['players'])
    padded = -(-players // 4) * 4
    size = _stats.frame_bytes(players)
    at = size - padded
    touched = any(any(frame[at : at + players]) for frame in frames if len(frame) == size)
    return [] if touched else [*TOUCH_KINDS, *REFEREE_KINDS]


def _provenance_of(document: dict[str, Any], key: str) -> dict[str, Any] | None:
    provenance = document.get('provenance')
    found = provenance.get(key) if isinstance(provenance, dict) else None
    return found if isinstance(found, dict) else None


def _has(document: dict[str, Any], sections: dict[str, bytes]) -> list[str]:
    """Returns the sections of a recording, with those whose layout its attachments carry."""
    listed = document.get('sections')
    if isinstance(listed, list):
        found = list(listed)
    else:
        found = [tag for tag in sections if tag not in ('FRMS', 'TRKS')]
    for tag in _attachments.SECTIONS.values():
        name = tag.decode()
        if name in sections and name not in found:
            found.append(name)
    return found


def analysis_recording(
    path: Path, document: dict[str, Any] | None = None, frames: list[bytes] | None = None
) -> Recording:
    """Reads the frames and attachments used by analysis plugins."""
    if document is None:
        document = _recording.read_header(path)
    if frames is None:
        frames = frames_of(path, document)
    inline = _recording._read(path)[1] if document.get('version', 1) != 1 else {}
    sections = {tag.decode('latin-1'): payload for tag, payload in inline.items()}
    attached = _attachments.find(path, document, inline).attachments
    for kind, tag in _attachments.SECTIONS.items():
        part = attached.get(kind)
        if part is not None and part.version == 1:
            sections[tag.decode()] = part.body
    notes_payload = sections.get(_annotations.TAG.decode())
    notes = _annotations.decode(notes_payload) if notes_payload is not None else []
    actions = None
    acts = sections.get(_actions.TAG.decode())
    agents = document.get('agents')
    if acts is not None and agents:
        with contextlib.suppress(Exception):
            actions = _actions.decode(acts, document['frame_count'], sum(agents))
    return Recording(document, tuple(frames), sections, notes, actions)


class _Selection:
    def __init__(self, config: Any = None, overrides: Any = ()) -> None:
        self.settings = _settings(config, overrides)
        self.names = tuple(
            dict.fromkeys(name.removeprefix('builtin.') for name in self.settings['detectors'])
        )
        self.settings['detectors'] = self.names
        self.config = config
        self.overrides = overrides
        self.signature = None
        if self.names != _detectors.STANDARDS:
            for name in self.names:
                _plugins.get('moments', name, config, overrides)
            revision = _plugins.signature() if any('.' in name for name in self.names) else None
            self.signature = [list(self.names), revision]

    def catalog(self, recording: Recording, standard: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _detectors.catalog(recording, self.names, standard, self.config, self.overrides)


def standard_moments(
    recording: Recording, unavailable: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    """Returns the standard moments of play that the recording supports."""
    if unavailable is None:
        unavailable = _unavailable(recording.header, recording.frames)
    found = _plugins.call('moments', 'cuju', 'detect', recording)
    if unavailable:
        return [moment for moment in found if moment['kind'] not in unavailable]
    return found


def file_catalog(
    path: Path, document: dict[str, Any], frames: list[bytes], *, rooted: bool = False
) -> list[dict[str, Any]]:
    """Finds the configured catalog of a file outside the library."""
    selected = _Selection()
    recording = Recording(document, tuple(frames))
    standard = []
    if 'cuju' in selected.names:
        standard = standard_moments(recording, () if rooted else None)
    if any('.' in name for name in selected.names):
        recording = analysis_recording(path, document, None if rooted else frames)
    return selected.catalog(recording, standard)


def _read_recording(
    path: Path, selected: _Selection | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns the index entry of the recording file at `path` and its statistics."""
    selected = _Selection() if selected is None else selected
    recording = analysis_recording(path)
    document, frames = recording.header, recording.frames
    unavailable = _unavailable(document, frames)
    standard = standard_moments(recording, unavailable)
    moments = selected.catalog(recording, standard)
    statistics = _stats.compute(document, frames, standard, recording.actions, path.stem)
    entry = {
        'version': VERSION,
        'id': path.stem,
        'signature': _signature(path),
        'header': _header(document),
        'moments': moments,
        'unavailable': unavailable,
        'signals': _stats.signal_summary(recording),
        'heat': statistics['heat'],
        'run': _provenance_of(document, 'run'),
        'checkpoint': _provenance_of(document, 'checkpoint'),
        'run_key': document.get('run_key') if isinstance(document.get('run_key'), dict) else None,
        'has': _has(document, recording.sections),
    }
    if selected.signature is not None:
        entry['detectors'] = selected.signature
    return entry, statistics


def build(path: Path) -> dict[str, Any]:
    """Returns the index entry of the recording file at `path`, as the module describes it."""
    return _read_recording(path)[0]


def _write(folder: Path, entry: dict[str, Any]) -> None:
    target = folder / f'{entry["id"]}.json'
    temporary = target.with_name(target.name + '.part')
    temporary.write_text(json.dumps(entry, separators=(',', ':')), encoding='utf-8')
    temporary.replace(target)


def _read(folder: Path, identifier: str) -> dict[str, Any] | None:
    try:
        entry = json.loads((folder / f'{identifier}.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return entry if isinstance(entry, dict) else None


def _failed(identifier: str, signature: list[int], error: Exception) -> dict[str, Any]:
    """Returns the empty entry of a recording that could not be read, kept until it changes."""
    return {
        'version': VERSION,
        'id': identifier,
        'signature': signature,
        'header': {'teams': [], 'players': []},
        'moments': [],
        'unavailable': [],
        'signals': _stats.signals([]),
        'heat': None,
        'run': None,
        'checkpoint': None,
        'run_key': None,
        'has': [],
        'error': f'{type(error).__name__}: {error}',
    }


def _index_one(
    home: Path, identifier: str, path: Path, again: bool = False, selected: _Selection | None = None
) -> tuple[Any, str]:
    """Brings the entry of one recording up to date and returns it with what was done.

    Only the lock of this recording is held while it is read, so other recordings index and
    answer meanwhile.

    Args:
        home: The renderer folder.
        identifier: The recording.
        path: Its file.
        again: Whether to read the recording again although its entry is up to date, as when its
            statistics are missing.

    Returns:
        The entry, or `None` for a file that is gone, and `kept`, `loaded`, `built` or `failed`.
    """
    selected = _Selection() if selected is None else selected
    with _LOCK:
        lock = _READING.setdefault((str(home), identifier), threading.Lock())
    with lock:
        try:
            signature = _signature(path)
        except OSError:
            return None, 'kept'
        cache = _cache(home)
        with _LOCK:
            held = cache.get(identifier)
        if (
            not again
            and held is not None
            and held[0] == signature
            and held[1].get('version') == VERSION
            and held[1].get('detectors') == selected.signature
        ):
            return held[1], 'kept'
        index = home / 'index'
        index.mkdir(parents=True, exist_ok=True)
        entry = None if again else _read(index, identifier)
        done = 'loaded'
        if (
            entry is None
            or entry.get('version') != VERSION
            or entry.get('signature') != signature
            or entry.get('detectors') != selected.signature
        ):
            try:
                entry, statistics = _read_recording(path, selected)
                _stats.write(identifier, statistics, home)
                done = 'built'
            except Exception as error:
                if isinstance(error, MessageError) and error.message_id.startswith(
                    'server.plugins.'
                ):
                    raise
                entry, done = _failed(identifier, signature, error), 'failed'
                if selected.signature is not None:
                    entry['detectors'] = selected.signature
            _write(index, entry)
        with _LOCK:
            cache[identifier] = (signature, entry)
        return entry, done


def sync(
    *, selected: _Selection | None = None, entries: dict[str, Any] | None = None
) -> dict[str, int]:
    """Brings the index up to date with the library and returns what it did.

    The pass reads one recording at a time and holds no lock between them, so a recording that
    a page asks for meanwhile indexes alone and answers at once.

    Returns:
        The number of `recordings` the index holds, those it `built` now, those it `removed` and
        those it `failed` to read, which keep an empty entry until their file changes.
    """
    selected = _Selection() if selected is None else selected
    home = _home.home()
    matches = home / 'matches'
    matches.mkdir(parents=True, exist_ok=True)
    recordings = {path.stem: path for path in matches.glob('*.cjr')}
    index = home / 'index'
    index.mkdir(parents=True, exist_ok=True)
    statistics = home / _stats.FOLDER
    removed = 0
    with _LOCK:
        cache = _cache(home)
        for path in index.glob('*.json'):
            if path.stem not in recordings:
                path.unlink(missing_ok=True)
                (statistics / path.name).unlink(missing_ok=True)
                removed += 1
        for folder in (index, statistics):
            for path in folder.glob('*.json.part'):
                path.unlink(missing_ok=True)
        for identifier in list(cache):
            if identifier not in recordings:
                del cache[identifier]
        progress = _PASSES.setdefault(str(home), {})
        progress.update(running=True, indexed=0, total=len(recordings))
    built = failed = 0
    try:
        for identifier, path in recordings.items():
            found, done = _index_one(home, identifier, path, selected=selected)
            if entries is not None and found is not None:
                entries[identifier] = found
            built += done in ('built', 'failed')
            failed += done == 'failed'
            with _LOCK:
                progress['indexed'] += 1
    finally:
        with _LOCK:
            progress['running'] = False
    with _LOCK:
        held = sum(identifier in cache for identifier in recordings)
    return {'recordings': held, 'built': built, 'removed': removed, 'failed': failed}


def entry(identifier: str) -> dict[str, Any]:
    """Returns the index entry of a recording of the library, reading that recording alone when
    its entry is missing or out of date.

    Raises:
        ArgumentError: If the library holds no such recording outside the Trash.
    """
    path = _library.path_of(identifier)
    if path is None:
        raise MessageError('server.library.unknown', identifier=repr(identifier))
    found, _ = _index_one(_home.home(), identifier, path)
    if found is None:
        raise MessageError('server.library.unknown', identifier=repr(identifier))
    return found


def statistics(identifier: str) -> dict[str, Any]:
    """Returns the statistics of a recording of the library, as `_store.stats` describes them,
    reading the recording when they are missing or out of date.

    Raises:
        ArgumentError: If the library holds no such recording outside the Trash.
    """
    held = entry(identifier)
    home = _home.home()
    found = _stats.read(identifier, home)
    if found is None and 'error' not in held:
        path = _library.path_of(identifier)
        if path is not None:
            _index_one(home, identifier, path, again=True)
            found = _stats.read(identifier, home)
    if found is None:
        raise MessageError('server.library.statistics', identifier=repr(identifier))
    return found


def index_soon(identifier: str) -> None:
    """Indexes a recording of the library in a background thread of this process.

    The thread starts at the first call and serves every later one, a recording at a time. A
    process that exits first leaves the recording to the server, which indexes it when asked.
    """
    _WAITING.put((_home.home(), identifier))
    with _LOCK:
        if not _WORKER or not _WORKER[0].is_alive():
            worker = threading.Thread(target=_work, daemon=True, name='cuju-render-index')
            _WORKER[:] = [worker]
            worker.start()


def _work() -> None:
    while True:
        home, identifier = _WAITING.get()
        try:
            path = home / 'matches' / f'{identifier}.cjr'
            if _home.valid_identifier(identifier) and path.is_file():
                _index_one(home, identifier, path)
        except Exception:
            pass
        finally:
            _WAITING.task_done()


def wait() -> None:
    """Waits until every recording that `index_soon` was given is indexed."""
    _WAITING.join()


def progress() -> dict[str, Any]:
    """Returns how far the index of the library is, for the page to show while it builds.

    Returns:
        `indexed`, the recordings whose entry is up to date, `total`, the recordings of the
        library, Trash included, and `running`, whether a pass over the library is under way.
    """
    home = _home.home()
    selected = _Selection()
    matches = home / 'matches'
    total = indexed = 0
    with _LOCK:
        cache = dict(_cache(home))
        running = bool(_PASSES.get(str(home), {}).get('running'))
    if matches.is_dir():
        for path in matches.glob('*.cjr'):
            total += 1
            held = cache.get(path.stem)
            with contextlib.suppress(OSError):
                if (
                    held is not None
                    and held[0] == _signature(path)
                    and held[1].get('version') == VERSION
                    and held[1].get('detectors') == selected.signature
                ):
                    indexed += 1
    return {'indexed': indexed, 'total': total, 'running': running}


def sync_soon() -> bool:
    """Starts a pass over the library in a background thread, unless one runs, and returns
    whether it started one."""
    home = str(_home.home())
    with _LOCK:
        if _PASSES.get(home, {}).get('running'):
            return False
        _PASSES.setdefault(home, {}).update(running=True)
    threading.Thread(target=_quiet_sync, daemon=True, name='cuju-render-index-pass').start()
    return True


def _quiet_sync() -> None:
    try:
        sync()
    except Exception:
        with _LOCK:
            _PASSES.setdefault(str(_home.home()), {})['running'] = False


def _terms(query: str) -> list[tuple[bool, str | None, str]]:
    """Returns the terms of a query, each whether it is negated, its key or `None`, its value."""
    terms = []
    for match in _TERM.finditer(query or ''):
        negated, key, quoted, plain = match.groups()
        value = quoted if quoted is not None else plain
        if key is None and not value:
            continue
        if key is not None:
            key = key.lower()
            if key not in _KEYS:
                raise MessageError(
                    'server.search.filter', filters=', '.join(k + ':' for k in _KEYS), key=key
                )
        terms.append((bool(negated), key, value))
    return terms


def _number(text: str) -> float:
    return float(text)


def _integer(text: str, message_id: str, value: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise MessageError(message_id, value=repr(value)) from None


def _signal(negated: bool, key: str, value: str | None) -> dict[str, Any]:
    """Returns the parsed term of a signal filter, or of a signal word when `value` is `None`.

    Raises:
        ArgumentError: If the term is negated or its value is not one that the filter takes.
    """
    term = f'{key}:{value}' if value is not None else key
    if negated:
        raise MessageError('server.search.signal_negated', term=f'-{term}')
    found: dict[str, Any] = {'type': 'signal', 'signal': key, 'name': None, 'op': None}
    found['bound'] = None
    if key == 'value-drop':
        if value is not None:
            if not value.strip():
                raise MessageError('server.search.value_name', value=repr(value))
            found['name'] = value
    elif key == 'surprise':
        if value is not None:
            if not _PROBABILITY.fullmatch(value.strip()) or not 0 <= float(value) <= 1:
                raise MessageError('server.search.probability', value=repr(value))
            found['bound'] = _number(value)
    elif key in ('entropy', 'reward'):
        held = _BOUND.fullmatch((value or '').strip())
        if held is None:
            raise MessageError('server.search.bound', key=key, value=repr(value or ''))
        found['op'], found['bound'] = held.group(1), _number(held.group(2))
        if key == 'reward':
            found['signal'], found['name'] = 'annotation', 'reward'
    else:
        held = _NAMED_BOUND.fullmatch((value or '').strip())
        if held is None or not held.group(1).strip():
            raise MessageError('server.search.annotation', value=repr(value or ''))
        found['signal'] = 'annotation'
        found['name'] = held.group(1).strip()
        found['op'], found['bound'] = held.group(2), _number(held.group(3))
    return found


def parse(query: str) -> list[dict[str, Any]]:
    """Returns the terms of a query, as the page's `recording/moments-query.js` reads them too.

    The repository's `tests/support/query_cases.json` holds the cases that both readers answer
    alike. Each term is a mapping with its `type`.

    - `kind`, a word that names a kind of moment, with the `word` and whether it is `negated`.
    - `word`, any other word, with its `text` and whether it is `negated`.
    - `filter`, a filter of the moments, with its `key`, its `value` and whether it is `negated`.
    - `signal`, a signal of the policy, with the `signal`, which is `value-drop`, `surprise`,
      `entropy` or `annotation`, the `name` of the value it reads, the comparison `op`, one of
      `>`, `>=`, `<` and `<=`, and the `bound`, each `None` where the term gives none.

    Raises:
        ArgumentError: If the query holds an unknown filter or a signal term out of shape.
    """
    found = []
    for negated, key, value in _terms(query):
        if key in SIGNAL_KEYS:
            found.append(_signal(negated, key, value))
        elif key is not None:
            found.append({'type': 'filter', 'key': key, 'value': value, 'negated': negated})
        elif fold(value) in _SIGNAL_WORDS:
            found.append(_signal(negated, _SIGNAL_WORDS[fold(value)], None))
        elif fold(value) in _WORDS:
            found.append({'type': 'kind', 'word': value, 'negated': negated})
        else:
            found.append({'type': 'word', 'text': value, 'negated': negated})
    return found


def _settings(config: Any = None, overrides: Any = ()) -> dict[str, Any]:
    from .._config import layers as _layers

    return _layers.table('search', config, overrides)


def thresholds(config: Any = None, overrides: Any = ()) -> dict[str, float]:
    """Returns the thresholds of the policy signals."""
    return {key: value for key, value in _settings(config, overrides).items() if key != 'detectors'}


def _passes(value: float, op: str, bound: float) -> bool:
    if op == '>':
        return value > bound
    if op == '>=':
        return value >= bound
    if op == '<':
        return value < bound
    return value <= bound


def _who(key: str) -> tuple[int | None, str]:
    """Returns the player of a series key, `None` for the whole match, and the value's name."""
    who, _, name = key.partition(':')
    return (int(who) if who.isdigit() else None), name


def _signal_moment(
    signal: str, frame: int, player: int | None, data: dict[str, Any]
) -> dict[str, Any]:
    who = 'match' if player is None else str(player)
    name = data.get('name')
    tail = f'-{name}' if name is not None else ''
    moment: dict[str, Any] = {'id': f'{signal}-{frame}-{who}{tail}', 'kind': signal, 'step': frame}
    if player is not None:
        moment['player'] = player
    moment['data'] = data
    return moment


def _signal_candidates(
    terms: list[dict[str, Any]], signals: dict[str, Any], rate: float, settings: dict[str, Any]
) -> list[dict[str, Any]]:
    """Returns the moments that the signal terms of a query find in the signals of a recording.

    The signals are those that the index keeps, as `_store.stats.signals` describes them, and the
    thresholds of `settings`, the `[search]` table, apply here, so a change of them needs no new
    index. A moment holds its `id`, `kind`, `step`, which is its frame, its `player` where it has
    one, and its `data`.

    - `value-drop` finds each kept fall of a value that, joined with the kept falls and climbs of
      the same series in the `window` before it, comes to at least `value_drop`, once a window.
    - `surprise` finds each kept frame whose chosen action had at most the probability of the
      term, or of `surprise`.
    - `entropy` finds the frame of each player's highest entropy past a bound above, or of the
      lowest past a bound below.
    - `annotation` finds, in each series of the name whose extreme lies past the bound, the
      kept climb or fall where the value passed it, the first kept one that surely did or else
      the largest.

    Args:
        terms: The terms of the query, as `parse` returns them.
        signals: The signals of the recording.
        rate: The frames a second of the recording.
        settings: The thresholds, `value_drop`, `window` and `surprise`.
    """
    series = signals.get('series') or {}
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(moment: dict[str, Any]) -> None:
        if moment['id'] not in seen:
            seen.add(moment['id'])
            found.append(moment)

    for term in terms:
        if term.get('type') != 'signal':
            continue
        signal = term['signal']
        if signal == 'value-drop':
            least = float(settings['value_drop'])
            span = float(settings['window']) * rate
            for key, held in series.items():
                player, name = _who(key)
                if term['name'] is not None and fold(name) != fold(term['name']):
                    continue
                changes = sorted(
                    (int(frame), float(change))
                    for frame, change in [*held.get('drops', ()), *held.get('rises', ())]
                )
                last = None
                for frame, change in changes:
                    if change >= 0:
                        continue
                    net = sum(c for f, c in changes if frame - span < f <= frame)
                    if -net + 1e-9 < least or (last is not None and frame - last < span):
                        continue
                    last = frame
                    data = {'name': name, 'change': round(net, 6)}
                    add(_signal_moment('value-drop', frame, player, data))
        elif signal == 'surprise':
            most = float(term['bound'] if term['bound'] is not None else settings['surprise'])
            for who, frames in (signals.get('unlikely') or {}).items():
                player = int(who) if who.isdigit() else None
                for frame, probability in frames:
                    if probability <= most:
                        data = {'probability': probability}
                        add(_signal_moment('surprise', int(frame), player, data))
        elif signal == 'entropy':
            above = term['op'] in ('>', '>=')
            for who, held in (signals.get('entropy') or {}).items():
                player = int(who) if who.isdigit() else None
                frame, value = held['max'] if above else held['min']
                if _passes(value, term['op'], term['bound']):
                    data = {'entropy': value}
                    add(_signal_moment('entropy', int(frame), player, data))
        else:
            above = term['op'] in ('>', '>=')
            for key, held in series.items():
                player, name = _who(key)
                if fold(name) != fold(term['name']):
                    continue
                if not _passes(held['max'] if above else held['min'], term['op'], term['bound']):
                    continue
                steps = held.get('rises' if above else 'drops') or []
                if not steps:
                    continue
                base = held['min'] if above else held['max']
                sure = [s for s in sorted(steps) if _passes(base + s[1], term['op'], term['bound'])]
                frame = int((sure or steps)[0][0])
                data = {'name': name, 'op': term['op'], 'bound': term['bound']}
                add(_signal_moment('annotation', frame, player, data))
    found.sort(key=lambda moment: (moment['step'], moment['id']))
    return found


@dataclass(frozen=True)
class _SignalRecording:
    term: dict[str, Any]
    signals: dict[str, Any]
    rate: float
    settings: dict[str, Any]


class SignalDetector:
    """Finds a policy signal in the summary of a recording."""

    def __init__(self, signal: str) -> None:
        self.signal = signal

    def detect(self, recording: Recording | _SignalRecording) -> list[dict[str, Any]]:
        if isinstance(recording, Recording):
            signal = 'entropy:>0' if self.signal == 'entropy' else self.signal
            recording = _SignalRecording(
                parse(signal)[0],
                _stats.signal_summary(recording),
                recording.header.get('steps_per_second') or 10,
                thresholds(),
            )
        return _signal_candidates(
            [recording.term], recording.signals, recording.rate, recording.settings
        )


def signal_moments(
    terms: list[dict[str, Any]], signals: dict[str, Any], rate: float, settings: dict[str, Any]
) -> list[dict[str, Any]]:
    """Finds the selected policy signals named by a query."""
    selected = settings.get('detectors', _detectors.STANDARDS)
    found = {}
    for term in terms:
        if term.get('type') != 'signal':
            continue
        name = _detectors.SIGNALS.get(term['signal'])
        if name is None:
            moments = _signal_candidates([term], signals, rate, settings)
        elif name in selected:
            moments = _plugins.call(
                'moments', name, 'detect', _SignalRecording(term, signals, rate, settings)
            )
        else:
            continue
        for moment in moments:
            found.setdefault(moment['id'], moment)
    return sorted(found.values(), key=lambda moment: (moment['step'], moment['id']))


def _timed(
    found: list[dict[str, Any]], moments: list[dict[str, Any]], header: dict[str, Any]
) -> list[dict[str, Any]]:
    """Gives signal moments their time, the clock of the match and the score, from the moments
    of the same recording around them."""
    rate = header.get('steps_per_second') or 10
    marks = sorted(
        ((m['step'], m['clock'], m.get('score')) for m in moments if 'clock' in m),
        key=lambda mark: mark[0],
    )
    steps = [mark[0] for mark in marks]
    for moment in _placed(found, header):
        step = moment['step']
        at = bisect.bisect_right(steps, step)
        before = marks[at - 1] if at else None
        after = marks[at] if at < len(marks) else None
        if before and after:
            (s0, c0, _), (s1, c1, _) = before, after
            clock = c0 + (step - s0) * (c1 - c0) / (s1 - s0)
        elif before:
            clock = before[1] + (step - before[0]) * 1000 / rate
        elif after:
            clock = max(0, after[1] - (after[0] - step) * 1000 / rate)
        else:
            clock = step * 1000 / rate
        moment['clock'] = int(clock)
        moment['score'] = list(before[2]) if before and before[2] else [0, 0]
    return found


def _placed(found: list[dict[str, Any]], header: dict[str, Any]) -> list[dict[str, Any]]:
    """Gives signal moments their time in the recording and the team of their player."""
    rate = header.get('steps_per_second') or 10
    players = header.get('players') or []
    for moment in found:
        moment['t'] = round(moment['step'] / rate, 3)
        k = moment.get('player')
        if k is not None and 0 <= k < len(players) and players[k].get('team') in (0, 1):
            moment['team'] = players[k]['team']
    return found


class Query:
    """A query read once, which tells the moments of a recording that it finds.

    The search of the library and the filter `keep` of a recorder read queries through this
    class, so both take the same language.
    """

    def __init__(
        self,
        query: str,
        limit: int | None = None,
        resolve: Callable[[str, str], str | None] | None = None,
    ) -> None:
        """Reads the query.

        Raises:
            ArgumentError: If the query holds an unknown filter or a value out of place.
        """
        self.query = query
        self.limit = limit
        self.kinds: list[Any] = []
        self.tests: list[Any] = []
        self.words: list[tuple[bool, str]] = []
        self.signals: list[dict[str, Any]] = []
        self.timing = False
        for term in parse(query):
            kind = term['type']
            if kind == 'signal':
                self.signals.append(term)
            elif kind == 'kind':
                test = _kind_test(term['word'])
                if term['negated']:
                    self.tests.append(lambda item, test=test: not test(item))
                else:
                    self.kinds.append(test)
            elif kind == 'word':
                self.words.append((term['negated'], fold(term['text'])))
            elif term['key'] == 'limit':
                value = term['value']
                number = _integer(value, 'server.search.limit_text', value)
                if self.limit is None:
                    self.limit = number
            else:
                key, value = term['key'], term['value']
                reference = resolve(key, value) if resolve else None
                test = _filter(key, value, reference)
                if term['negated']:
                    self.tests.append(lambda item, test=test: not test(item))
                else:
                    self.tests.append(test)
                self.timing |= term['key'] in ('after', 'before', 'score', 'outcome')

    def needs_events(self) -> bool:
        """Whether the moments of the rules of play take part, which a query of signals and of
        the team and player alone leaves out."""
        return bool(self.kinds) or not self.signals or self.timing or bool(self.words)

    def find(
        self,
        record: dict[str, Any],
        moments: list[dict[str, Any]],
        signals: dict[str, Any] | None,
        header: dict[str, Any],
        settings: dict[str, Any] | None,
        later: bool = False,
    ) -> list[_Moment]:
        """Returns the moments of one recording that the query finds.

        Args:
            record: The recording as the filters read it, as `_record` returns it.
            moments: The moments of the rules of play of the recording.
            signals: The signals of its annotations, as the index keeps them.
            header: What the index keeps of its header.
            settings: The thresholds of the signals, the `[search]` table.
            later: Whether a moment of a signal may wait for its clock and score until `clock`
                is called on it, when no filter of the query reads them.
        """
        found = []
        if self.signals and signals:
            rate = header.get('steps_per_second') or 10
            made = signal_moments(self.signals, signals, rate, settings or {})
            if later and not self.timing:
                for moment in _placed(made, header):
                    item = _Moment(moment, record)
                    item.pending = (moments, header)
                    found.append(item)
            else:
                found += [_Moment(moment, record) for moment in _timed(made, moments, header)]
        if self.kinds or not self.signals:
            for moment in moments:
                item = _Moment(moment, record)
                if not self.kinds or any(test(item) for test in self.kinds):
                    found.append(item)
        if not self.tests and not self.words:
            return found
        text = ''
        if self.words:
            text = fold(
                ' '.join(
                    [
                        record['id'],
                        record['title'],
                        record['scenario'].replace('_', ' '),
                        record['folder'],
                        *[str(name) for name in record['teams'] if name],
                        *record['tags'],
                    ]
                )
            )
        kept = []
        for item in found:
            if not all(test(item) for test in self.tests):
                continue
            if self.words:
                name = fold((item.player() or {}).get('name'))
                if not all(((word in text) or word in name) != out for out, word in self.words):
                    continue
            kept.append(item)
        return kept


def _minutes(value: str, key: str) -> float:
    """Returns a time of the match clock, given as minutes, mm:ss, 90s or 60m, in milliseconds."""
    text = value.strip().lower()
    try:
        if ':' in text:
            minutes, seconds = text.split(':', 1)
            clock = (float(minutes) * 60 + float(seconds)) * 1000
        elif text.endswith('s'):
            clock = float(text[:-1]) * 1000
        else:
            clock = float(text.removesuffix('m')) * 60000
    except ValueError:
        pass
    else:
        if math.isfinite(clock):
            return clock
    raise MessageError('server.search.minutes', key=key, value=repr(value))


class _Moment:
    """One moment of one recording with what the filters read of it."""

    def __init__(self, moment: dict[str, Any], record: dict[str, Any]) -> None:
        self.moment = moment
        self.record = record
        # The moments and header that give a moment of a signal its clock and score, until then.
        self.pending: tuple[list[dict[str, Any]], dict[str, Any]] | None = None

    def clock(self) -> _Moment:
        """Gives a moment of a signal its clock and score, if it waits for them."""
        if self.pending is not None:
            _timed([self.moment], *self.pending)
            self.pending = None
        return self

    @property
    def data(self) -> dict[str, Any]:
        return self.moment.get('data') or {}

    def player(self) -> dict[str, Any] | None:
        k = self.moment.get('player')
        players = self.record['context']['players']
        return players[k] if k is not None and 0 <= k < len(players) else None

    def before(self) -> list[int] | None:
        """Returns the score of the left and the right team just before the moment."""
        score = self.moment.get('score')
        if not isinstance(score, list):
            return None
        if self.moment['kind'] == 'goal' and self.moment.get('team') in (0, 1):
            score = list(score)
            score[self.moment['team']] -= 1
        return score


def _kind_test(value: str) -> Any:
    names = [fold(name) for name in value.split(',') if name.strip()]
    tests = []
    for name in names:
        if name in _WORDS:
            tests.append(_WORDS[name])
        elif _detectors.custom_kind(name):
            tests.append((name, None))
        else:
            raise MessageError('server.search.kind', name=repr(name))

    def test(item: _Moment) -> bool:
        return any(
            item.moment['kind'] == kind and (check is None or check(item.data))
            for kind, check in tests
        )

    return test


def _filter(key: str, value: str, reference: str | None = None) -> Any:
    """Returns the test of one filter of a query."""
    text = fold(value).strip()
    if key == 'kind':
        return _kind_test(value)
    if key == 'team':
        if text in _TEAM_SIDES:
            return lambda item: item.moment.get('team') == _TEAM_SIDES[text]

        def team(item: _Moment) -> bool:
            side = item.moment.get('team')
            if side not in (0, 1):
                return False
            identifiers = item.record['team_ids']
            identifier = identifiers[side] if side < len(identifiers) else None
            names = item.record['context']['teams']
            if reference is not None and identifier is not None:
                return identifier == reference
            return (identifier is not None and text == fold(identifier)) or text in fold(
                names[side]
            )

        return team
    if key == 'player':
        if text.isdigit():
            number = _integer(text, 'server.search.player', value)
            return lambda item: (item.player() or {}).get('number') == number
        return lambda item: bool(text) and text in fold((item.player() or {}).get('name'))
    if key == 'scenario':

        def scenario(item: _Moment) -> bool:
            name = str(item.record['scenario'] or '')
            return text in fold(name) or text.replace(' ', '_') in fold(name)

        return scenario
    if key == 'source':
        if text == 'tournament':
            return lambda item: isinstance(item.record['provenance'].get('tournament'), dict)
        if text == 'unknown':
            return lambda item: not item.record['provenance'].get('source')
        if text not in _SOURCES:
            raise MessageError('server.search.source', value=repr(value))
        wanted = _SOURCES[text]
        return lambda item: item.record['provenance'].get('source') == wanted
    if key == 'tournament':

        def tournament(item: _Moment) -> bool:
            held = item.record['provenance'].get('tournament')
            if not isinstance(held, dict):
                return False
            if reference is not None:
                return held.get('id') == reference
            return any(text in fold(held.get(field)) for field in ('name', 'id', 'stage'))

        return tournament
    if key == 'match':
        if reference is not None:
            return lambda item: item.record['id'] == reference
        return lambda item: text == fold(item.record['id']) or text in fold(item.record['title'])
    if key in ('date', 'since', 'until'):
        try:
            if key == 'date' and text in ('today', 'yesterday'):
                text = _dates.day(text)
            else:
                if not _DATE.fullmatch(text):
                    raise ValueError(text)
                _dates.day(text + {4: '-01-01', 7: '-01'}.get(len(text), ''))
        except ValueError:
            raise MessageError('server.search.date', key=key, value=repr(value)) from None
        if key == 'date':
            return lambda item: str(item.record['created'] or '').startswith(text)
        if key == 'since':
            return lambda item: str(item.record['created'] or '')[: len(text)] >= text
        return lambda item: str(item.record['created'] or '')[: len(text)] <= text
    if key in ('after', 'before'):
        bound = _minutes(value, key)
        if key == 'after':
            return lambda item: item.moment.get('clock', 0) >= bound
        return lambda item: item.moment.get('clock', 0) < bound
    if key in ('score', 'final'):
        exact = _SCORE.fullmatch(text)
        message_id = 'server.search.final_score' if key == 'final' else 'server.search.score'
        if exact:
            wanted = [_integer(digits, message_id, value) for digits in exact.groups()]
            if key == 'final':
                return lambda item: item.record.get('final_score') == wanted
            return lambda item: item.moment.get('score') == wanted
        if key == 'final' or text not in _STATES:
            raise MessageError(message_id, value=repr(value))
        state = _STATES[text]

        def standing(item: _Moment) -> bool:
            score, side = item.before(), item.moment.get('team')
            if score is None or side not in (0, 1):
                return False
            margin = score[side] - score[1 - side]
            return (margin > 0) - (margin < 0) == state

        return standing
    if key == 'outcome':

        def outcome(item: _Moment) -> bool:
            data = item.data
            if fold(data.get('outcome')) == text:
                return True
            if 'won' in data and text in ('won', 'lost'):
                return data['won'] is (text == 'won')
            if 'saved' in data and text in ('saved', 'scored'):
                return data['saved'] is (text == 'saved')
            return False

        return outcome
    raise MessageError('server.search.unknown_filter', key=key)


def _text_of(*values: Any) -> str:
    """Returns the first of values that is text and not empty, or the empty text."""
    return next((value for value in values if isinstance(value, str) and value), '')


def _record(entry_: dict[str, Any], summary: dict[str, Any], registry: list[Any]) -> dict[str, Any]:
    # An entry of the index or a summary of an older install may hold fields of another type.
    header = entry_['header'] if isinstance(entry_.get('header'), dict) else {}
    teams = header.get('teams') if isinstance(header.get('teams'), list) else []
    # A title that cuju made, such as that of a sample, travels as a label too.
    labels = [_recording.label_of(document, 'title') for document in (summary, header)]
    return {
        'id': entry_['id'],
        'title': _text_of(summary.get('title'), header.get('title'), entry_['id']),
        'title_label': next((found for found in labels if isinstance(found, dict)), None),
        'scenario': _text_of(summary.get('scenario'), header.get('scenario')),
        'created': _text_of(summary.get('created')),
        'provenance': summary.get('provenance') or {},
        'final_score': summary.get('score'),
        'teams': summary.get('teams')
        or [team.get('name') if isinstance(team, dict) else None for team in teams],
        'team_ids': [team.get('id') if isinstance(team, dict) else None for team in teams],
        'tags': summary.get('tags') or [],
        'folder': summary.get('folder') or '',
        'context': _captions.context(header, registry),
        'steps_per_second': header.get('steps_per_second') or 10,
    }


def describe(moment: dict[str, Any], context: dict[str, Any]) -> str:
    """Returns a short plain line of a moment, its caption or, for a pass, a change of
    possession and a kick off, a line of the same table."""
    return describe_label(moment, context)['text']


def describe_label(moment: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Returns the line of `describe` as a label, its message id of the language files, its
    English text and its values, so the page shows it in its own language."""
    table = _captions.messages()
    if _detectors.custom_kind(moment['kind']):
        return _captions.label(table, 'describe.moment', {'kind': moment['kind']})
    if moment['kind'] in SIGNAL_KINDS:
        return _signal_label(table, moment, context)
    made = _captions.caption(moment, context, table, labels=True)
    if made is not None:
        return _captions.label(table, *made)
    data = moment.get('data') or {}
    side = moment.get('team')
    team = _captions._team(table, context, side, True) if side in (0, 1) else None
    k = moment.get('player')
    known = k is not None and 0 <= k < len(context['players'])
    if moment['kind'] == 'pass' and known:
        own = _captions._team(table, context, context['players'][k]['team'], True)
        player = _captions._player(table, context, k, True)
        identifier = {
            'complete': 'describe.pass.complete',
            'intercepted': 'describe.pass.intercepted',
        }.get(data.get('outcome'), 'describe.pass')
        return _captions.label(table, identifier, {'player': player, 'team': own})
    if moment['kind'] == 'possession' and team:
        return _captions.label(table, 'describe.possession', {'team': team})
    if moment['kind'] == 'restart':
        if data.get('restart') == 'kickoff' and team:
            return _captions.label(table, 'describe.restart.kickoff', {'team': team})
        return _captions.label(table, 'describe.restart', {})
    return _captions.label(table, 'describe.moment', {'kind': moment['kind']})


def _signal_label(
    table: dict[str, str], moment: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """Returns the line of a moment of a policy signal as a label, by player or for the whole
    match."""
    data = moment.get('data') or {}
    kind = moment['kind']
    values: dict[str, Any] = {}
    if kind == 'value-drop':
        ids = ('describe.signal.drop', 'describe.signal.drop.match')
        values = {'name': data.get('name'), 'change': round(abs(data.get('change') or 0), 3)}
    elif kind == 'surprise':
        ids = ('describe.signal.surprise', 'describe.signal.surprise.match')
        values = {'probability': data.get('probability')}
    elif kind == 'entropy':
        ids = ('describe.signal.entropy', 'describe.signal.entropy.match')
        values = {'entropy': round(data.get('entropy') or 0, 3)}
    else:
        ids = ('describe.signal.annotation', 'describe.signal.annotation.match')
        values = {'name': data.get('name'), 'op': data.get('op'), 'bound': data.get('bound')}
    k = moment.get('player')
    if k is not None and 0 <= k < len(context['players']):
        values['player'] = _captions._player(table, context, k, True)
        values['team'] = _captions._team(table, context, context['players'][k]['team'], True)
        return _captions.label(table, ids[0], values)
    return _captions.label(table, ids[1], values)


def _result(item: _Moment) -> dict[str, Any]:
    moment, record = item.moment, item.record
    player = item.player()
    seek = max(0.0, round(moment['t'] - LEAD_SECONDS, 1))
    side = moment.get('team')
    clock = int(moment.get('clock', 0))
    result = {
        'match': record['id'],
        **_messages.named('title', record['title_label'] or record['title']),
        'scenario': record['scenario'],
        'created': record['created'],
        'source': record['provenance'].get('source'),
        'id': moment['id'],
        'kind': moment['kind'],
        'step': moment['step'],
        't': moment['t'],
        'seek': seek,
        'clock': clock,
        'minute': f'{clock // 60000}:{clock // 1000 % 60:02d}',
        'team': side,
        'team_name': record['context']['teams'][side] if side in (0, 1) else None,
        'player': moment.get('player'),
        'number': player['number'] if player else None,
        'name': (player['name'] or None) if player else None,
        'score': moment.get('score'),
        **_messages.named('text', describe_label(moment, record['context'])),
        'data': moment.get('data') or {},
        'url': f'#match={record["id"]}&t={seek:g}',
    }
    return result


def search(
    query: str = '', limit: int | None = None, *, config: Any = None, overrides: Any = ()
) -> dict[str, Any]:
    """Finds the moments of the library that a query names, newest recording first.

    Args:
        query: Words and filters, as `QUERY_HELP` describes, such as `goal team:Greens after:60`
            or `value-drop team:left`.
        limit: The most results to return, by default the query's `limit:` or 50.
        config: The settings, whose `[search]` table holds the thresholds of the policy
            signals, as `cuju.arena.config` describes.
        overrides: Settings such as `search.value_drop=0.5`, laid over `config`.

    Returns:
        The `query`, the `results` with their recording, moment, time to open Watch at, team,
        player, score and a plain line of `text` each, the `total` number of matches before the
        limit, the number of `recordings` searched, the number of `matched_recordings` with
        matches before the limit and what the index did in `index`.

    Raises:
        ArgumentError: If the query holds an unknown filter or a value out of place.
    """

    @cache
    def recordings() -> dict[str, Any]:
        return {summary['id']: summary for summary in _library.matches()}

    @cache
    def teams() -> list[dict[str, Any]]:
        try:
            from .._registry import teams as _teams

            return _teams.teams()
        except Exception:
            return []

    tournaments = None

    def resolve(key: str, value: str) -> str | None:
        nonlocal tournaments
        if key == 'team' and fold(value).strip() not in _TEAM_SIDES:
            return _references.team(value.strip(), teams())
        if key == 'match':
            return _references.kept(fold(value), recordings().values(), 'm-')
        if key == 'tournament':
            if tournaments is None:
                from .._tournaments.tournament_runner import Tournaments

                saved = Tournaments(start=False)
                try:
                    tournaments = saved.entries()
                finally:
                    saved.close()
            return _references.kept(fold(value), tournaments, 'cup-')
        return None

    reading = Query(query, limit, resolve)
    limit = LIMIT if reading.limit is None else reading.limit
    if not 1 <= limit <= MAXIMUM_LIMIT:
        raise MessageError('server.search.limit_range', most=MAXIMUM_LIMIT, limit=limit)
    selected = _Selection(config, overrides)
    settings = selected.settings if reading.signals else None
    entries: dict[str, Any] = {}
    index = sync(selected=selected, entries=entries)
    summaries = recordings()
    registry = teams()
    found = []
    searched = 0
    matched = 0
    for identifier, held in entries.items():
        summary = summaries.get(identifier)
        if summary is None:
            continue
        searched += 1
        signals = held.get('signals') if isinstance(held.get('signals'), dict) else {}
        # A query of signals alone skips at once a recording without annotations.
        if reading.signals and not reading.kinds and not any(signals.values()):
            continue
        record = _record(held, summary, registry)
        header = held['header'] if isinstance(held.get('header'), dict) else {}
        moments = reading.find(
            record, held['moments'], held.get('signals'), header, settings, later=True
        )
        if moments:
            matched += 1
            found.extend(moments)
    found.sort(
        key=lambda item: (item.record['created'], item.record['id'], -item.moment['t']),
        reverse=True,
    )
    return {
        'query': query,
        'results': [_result(item.clock()) for item in found[:limit]],
        'total': len(found),
        'recordings': searched,
        'matched_recordings': matched,
        'index': index,
    }


def moments_of(identifier: str) -> dict[str, Any]:
    """Returns the moments of a recording of the library, each with its plain line of text, and
    the kinds of moment that the recording cannot show."""
    held = entry(identifier)
    summary = _library.details(identifier)
    try:
        from .._registry import teams as _teams

        registry = _teams.teams()
    except Exception:
        registry = []
    record = _record(held, summary, registry)
    return {
        'match': identifier,
        'version': VERSION,
        'moments': [_result(_Moment(moment, record)) for moment in held['moments']],
        # The kinds of moment that the recording cannot show, which the page names as such.
        'unavailable': list(held.get('unavailable') or []),
    }


def _source(target: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Returns the header and moments of a library recording or of a recording file."""
    if isinstance(target, str) and _library.path_of(target) is not None:
        held = entry(target)
        return held['header'], held['moments']
    path = Path(target)
    if not path.is_file():
        raise MessageError('server.search.target', target=repr(target))
    document = _recording.read_header(path)
    frames = frames_of(path, document)
    return document, file_catalog(path, document, frames)


def _cues(target: str | Path) -> list[dict[str, Any]]:
    """Returns the caption cues of a recording, with their times as the page computes them."""
    header, moments = _source(target)
    moments = [moment for moment in moments if not _detectors.custom_kind(moment['kind'])]
    return _captions.cues(moments, _captions.context(header))


def captions(target: str | Path) -> list[dict[str, Any]]:
    """Returns the caption cues of a recording of the library or of a recording file.

    The times of the cues are in seconds to the millisecond, as a WebVTT file writes them.
    """
    found = _cues(target)
    for cue in found:
        cue['start'], cue['end'] = round(cue['start'], 3), round(cue['end'], 3)
    return found


def captions_vtt(target: str | Path) -> str:
    """Returns the captions of a recording of the library or of a recording file as WebVTT."""
    return _captions.to_vtt(_cues(target))
