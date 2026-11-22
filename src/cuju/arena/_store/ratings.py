# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The evidence of the ratings, one small row for each result, and what else the ratings keep.

A rating of Evaluate belongs to a model on a scenario, and it is fitted from every result that
the home folder holds of that scenario. This module keeps those results and nothing that is
derived from them, so a table is made again from the rows alone, by `_evaluate.ratings`.

The contract

- `record(result)` keeps one result once and returns its row, or `None` for a result that
  counts for nothing or is held already. A result is a mapping of `scenario`, the exact name of
  the scenario or `None` where it is unknown, `home` and `away`, each a side, `score`, the goals
  of the home side then of the away side, and `source`, one of `SOURCES`. It may hold `ref`,
  what its source names it by, `phase`, `time`, `seed`, a text that names the play of the
  match, `recording`, the identifier of its recording, and `actions`, a mapping of the `run`
  and the `number` under which the actions of the match are kept.
- A side is a mapping of `key`, the key of the team, such as a team identifier, `builtin:0.60`
  or `policy:name`, and optionally `checkpoint`, the identifier of what the team played with,
  the fields of its name, `name`, `name_id` and `name_values`, its `kind`, and the `label` and
  the `step` of its checkpoint. The key and the checkpoint together name a model, and `model`
  joins them into its identifier, such as `rex@3fa9c1d200a1b2c3`.
- `results(scenario)` returns the rows in the order they were kept, those of one scenario, those
  without one for `None`, or every row.
- `scenarios()` returns the number of rows of each scenario.
- `models()` returns what the store knows of each model beside its results, by identifier.
- `point(source, ref, recording)` writes the recording of a result that was drawn later.
- `reset(key)` deletes every row of a team key and its earlier rating.
- `signature()` returns a value that changes with every change of the rows.

A result counts once. A second result with the source and the reference of a row, with the
recording of a row, or with the scenario, the two models and the seed of a row, is the same
match and is left out, and so is a result between two sides of one model.

The files are `ratings/results.jsonl` of the home folder, one row on each line, a line that a
stop cut short being skipped, and `ratings/models.json`, the name and the kind of each model,
written through a temporary file. A row holds the keys and the checkpoints of its sides and no
name, so a table of thousands of matches stays small.

The earlier ratings

Before the tables of Evaluate, tournaments moved one rating for each team key by the Elo rule
from 1500, whatever the scenario. Those ratings stay readable as they stand. `earlier()` returns
them by key and `earlier_history(key)` the results that moved one, from `ratings/ratings.json`
and `ratings/history.jsonl`, and a home folder without them takes `tournaments/ratings.json` on
the first read. No result joins them any more, and none of them enters the table of a scenario.

The rating runs

A rating run of `_evaluate.ratings` keeps its document in `ratings/runs/<id>.json`, which
`run`, `runs`, `save_run` and `remove_run` read and write through a temporary file.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .. import plugins as _plugins
from .._text.messages import MessageError
from . import home as _home
from . import runs as _runs

# The version of the rows and of the file of the earlier ratings.
VERSION = 2
# The sources of a result, a match of a tournament's draw, a match of a rating run, a match of an
# evaluation and a requested match.
SOURCES = ('tournament', 'rating', 'evaluation', 'match')
# The folder of the home that holds the ratings, and its files.
FOLDER = 'ratings'
_RESULTS = 'results.jsonl'
_MODELS = 'models.json'
_TABLE = 'ratings.json'
_HISTORY = 'history.jsonl'
_RUNS = 'runs'
# The file of the ratings that tournaments kept before the store, in the folder of tournaments.
_EARLIER = Path('tournaments') / 'ratings.json'
# What `results` takes for the rows of every scenario.
EVERY = '*'
# The fields of a side that a row keeps, and those that the file of the models keeps.
_NAMED = ('name', 'name_id', 'name_values')
_DESCRIBED = (*_NAMED, 'kind', 'label', 'step')
# One thread at a time changes the ratings, so no result undoes another.
_LOCK = threading.RLock()
# The separator of the suffix of a model's submission format, twelve hexadecimal digits.
FORMAT_SEPARATOR = '~'
_FORMAT = re.compile(r'[0-9a-f]{12}')
# The versions a row records, of the programs that played it and of each side's frameworks.
_VERSION = re.compile(r'[A-Za-z0-9_.+-]{1,64}')
# The rows as last read, with the size and the time of the file they were read from.
_CACHE: dict[str, Any] = {}


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _folder() -> Path:
    return _home.folder(FOLDER)


def _write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + '.part')
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, path)


def model(key: str, checkpoint: str | None = None, format: str | None = None) -> str:
    """Returns the identifier of a model, a team key, the checkpoint that it played with and the
    suffix of the submission format it played under, which Kaggle open, the play of a side that
    chose no format, leaves out."""
    found = f'{key}@{checkpoint}' if checkpoint else key
    return f'{found}{FORMAT_SEPARATOR}{format}' if format else found


def model_of(side: dict[str, Any]) -> str:
    """Returns the identifier of the model of a side of a row, see `model`."""
    return model(side['key'], side.get('checkpoint'), side.get('format'))


# The rows.


def signature() -> tuple[int, int]:
    """Returns the size and the time of the file of the rows, which change with every row."""
    try:
        found = (_folder() / _RESULTS).stat()
    except OSError:
        return (0, 0)
    return (found.st_size, found.st_mtime_ns)


def _versions(value: Any) -> dict[str, str]:
    """Returns the versions a row or a side records, by program, those that read alone."""
    if not isinstance(value, dict):
        return {}
    return {
        name: version
        for name, version in value.items()
        if isinstance(name, str)
        and _VERSION.fullmatch(name)
        and isinstance(version, str)
        and _VERSION.fullmatch(version)
    }


def _format_fields(side: dict[str, Any]) -> dict[str, Any]:
    """Returns what a side keeps of its submission format.

    `format` is the suffix of the model's identifier, `submission_format` the format's identity
    as it played, `control` the effective control where it differs from the team's own, and
    `versions` the frameworks the format's process imported.
    """
    found: dict[str, Any] = {}
    if isinstance(side.get('format'), str) and _FORMAT.fullmatch(side['format']):
        found['format'] = side['format']
    identity = side.get('submission_format')
    if isinstance(identity, dict) and isinstance(identity.get('format'), str):
        found['submission_format'] = identity
    if isinstance(side.get('control'), dict):
        found['control'] = side['control']
    versions = _versions(side.get('versions'))
    if versions:
        found['versions'] = versions
    return found


def _row(entry: Any) -> dict[str, Any] | None:
    """Returns a line of the file as a row, or `None` for one that does not read as a row."""
    if not isinstance(entry, dict):
        return None
    sides = []
    for name in ('home', 'away'):
        side = entry.get(name)
        if not isinstance(side, dict) or not isinstance(side.get('key'), str) or not side['key']:
            return None
        checkpoint = side.get('checkpoint')
        found = {
            'key': side['key'],
            'checkpoint': checkpoint if isinstance(checkpoint, str) else None,
        }
        found.update(_format_fields(side))
        sides.append(found)
    score = entry.get('score')
    if not (isinstance(score, list) and len(score) == 2 and all(map(_whole, score))):
        return None
    if entry.get('source') not in SOURCES:
        return None
    scenario = entry.get('scenario')
    row = {
        'time': float(entry['time']) if _number(entry.get('time')) else 0.0,
        'scenario': scenario if isinstance(scenario, str) and scenario else None,
        'home': sides[0],
        'away': sides[1],
        'score': [int(score[0]), int(score[1])],
        'source': entry['source'],
    }
    for name in ('ref', 'phase', 'seed', 'recording'):
        if isinstance(entry.get(name), str):
            row[name] = entry[name]
    snapshots = _runs.side_snapshots(entry.get('sides'))
    if snapshots is not None:
        row['sides'] = snapshots
    versions = _versions(entry.get('versions'))
    if versions:
        row['versions'] = versions
    actions = entry.get('actions')
    if (
        isinstance(actions, dict)
        and isinstance(actions.get('run'), str)
        and _whole(actions.get('number'))
    ):
        row['actions'] = {'run': actions['run'], 'number': actions['number']}
    return row


def _rows() -> list[dict[str, Any]]:
    """Returns every row of the file, read again only once the file has changed."""
    stamp = (str(_folder()), signature())
    if _CACHE.get('stamp') == stamp:
        return _CACHE['rows']
    try:
        text = (_folder() / _RESULTS).read_text(encoding='utf-8')
    except OSError:
        text = ''
    found = []
    for line in text.splitlines():
        try:
            row = _row(json.loads(line))
        except ValueError:
            continue
        if row is not None:
            found.append(row)
    _formats_of(found)
    _CACHE.update(stamp=stamp, rows=found)
    return found


def _formats_of(rows: list[dict[str, Any]]) -> None:
    """Gives every team side of rows from before formats the format it played under, in place.

    A match of a strict tournament played as `strict/1` with the tournament's stored limits as
    its options, and every other match as Kaggle open, which a team side without a format reads
    as. The file keeps its lines as they were written, and a table is made from the rows on every
    read, so nothing is fitted again.
    """
    from .._formats import migrate as _migrate

    _migrate.rows(rows, _home.home() / 'tournaments', explicit=False)


def _models_of(row: dict[str, Any]) -> tuple[str, str]:
    return (model_of(row['home']), model_of(row['away']))


def _held(row: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    """Returns whether the rows hold the match of a row already."""
    ref, recording, seed = row.get('ref'), row.get('recording'), row.get('seed')
    for other in rows:
        if ref is not None and other.get('ref') == ref and other['source'] == row['source']:
            return True
        if recording is not None and other.get('recording') == recording:
            return True
        if (
            seed is not None
            and other.get('seed') == seed
            and other['scenario'] == row['scenario']
            and _models_of(other) == _models_of(row)
        ):
            return True
    return False


def _side(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns what a row and what the file of the models keep of a side of a result."""
    if not isinstance(value, dict) or not isinstance(value.get('key'), str) or not value['key']:
        raise MessageError('server.ratings.side', side=repr(value))
    checkpoint = value.get('checkpoint')
    if not (checkpoint is None or (isinstance(checkpoint, str) and checkpoint)):
        raise MessageError('server.ratings.side', side=repr(value))
    described = {name: value[name] for name in _DESCRIBED if value.get(name) is not None}
    described.setdefault('name', value['key'])
    return {'key': value['key'], 'checkpoint': checkpoint, **_format_fields(value)}, described


def record(result: dict[str, Any]) -> dict[str, Any] | None:
    """Keeps one result as a row of evidence, once, and returns the row.

    Args:
        result: The result, as the module describes it.

    Returns:
        The row as kept, or `None` for a result between two sides of one model and for a
        result that the rows hold already, which counts for nothing more.

    Raises:
        ArgumentError: If a side, the score, the source or another field does not fit.
    """
    if not isinstance(result, dict):
        raise MessageError('server.ratings.result', result=repr(result))
    home, home_described = _side(result.get('home'))
    away, away_described = _side(result.get('away'))
    score = result.get('score')
    if not (isinstance(score, list | tuple) and len(score) == 2 and all(map(_whole, score))):
        raise MessageError('server.ratings.score', score=repr(score))
    source = result.get('source')
    if source not in SOURCES:
        raise MessageError('server.ratings.source', source=repr(source), sources=', '.join(SOURCES))
    scenario = result.get('scenario')
    texts = {name: result.get(name) for name in ('ref', 'phase', 'seed', 'recording')}
    if not (scenario is None or (isinstance(scenario, str) and scenario)) or not all(
        value is None or isinstance(value, str) for value in texts.values()
    ):
        raise MessageError('server.ratings.result', result=repr(result))
    moment = result.get('time')
    row = _row(
        {
            'time': float(moment) if _number(moment) else time.time(),
            'scenario': scenario,
            'home': home,
            'away': away,
            'score': list(score),
            'source': source,
            **{name: value for name, value in texts.items() if value is not None},
            'actions': result.get('actions'),
            'versions': result.get('versions'),
            'sides': result.get('sides'),
        }
    )
    if row is None:
        raise MessageError('server.ratings.result', result=repr(result))
    if (home['key'], home['checkpoint']) == (away['key'], away['checkpoint']):
        return None
    with _LOCK:
        if _held(row, _rows()):
            return None
        with (_folder() / _RESULTS).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row) + '\n')
        _CACHE.pop('stamp', None)
        known = _load_models()
        changed = False
        for side, described in ((home, home_described), (away, away_described)):
            identifier = model_of(side)
            entry = {
                **{name: side[name] for name in ('key', 'checkpoint', 'format') if name in side},
                **described,
            }
            if 'submission_format' in side:
                entry['submission_format'] = side['submission_format']
            previous = known.get(identifier, {})
            if source == 'tournament':
                for field in ('label', 'step'):
                    if field in previous:
                        entry.setdefault(field, previous[field])
            if previous != entry:
                known[identifier] = entry
                changed = True
        if changed:
            _write(_folder() / _MODELS, json.dumps({'version': VERSION, 'models': known}))
    return dict(row)


def results(scenario: str | None = EVERY) -> list[dict[str, Any]]:
    """Returns the rows of one scenario, of none for `None`, or every row, oldest first."""
    with _LOCK:
        rows = _rows()
        if scenario == EVERY:
            return [dict(row) for row in rows]
        return [dict(row) for row in rows if row['scenario'] == scenario]


def scenarios() -> dict[str | None, int]:
    """Returns the number of rows of each scenario, `None` for the rows without one."""
    found: dict[str | None, int] = {}
    with _LOCK:
        for row in _rows():
            found[row['scenario']] = found.get(row['scenario'], 0) + 1
    return found


def _load_models() -> dict[str, dict[str, Any]]:
    try:
        document = json.loads((_folder() / _MODELS).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    found = document.get('models') if isinstance(document, dict) else None
    if not isinstance(found, dict):
        return {}
    return {
        key: entry
        for key, entry in found.items()
        if isinstance(key, str) and isinstance(entry, dict) and isinstance(entry.get('key'), str)
    }


def models() -> dict[str, dict[str, Any]]:
    """Returns what the store knows of each model by identifier, as its last result gave it.

    An entry holds the `key` and the `checkpoint` of the model, the fields of its name, its
    `kind`, and the `label` and the `step` of its checkpoint, each where a result gave one. A
    model under a format that no result named yet, such as a strict tournament's from before
    formats, takes the entry of the same team at the same checkpoint.
    """
    with _LOCK:
        known = _load_models()
        for row in _rows():
            for side in (row['home'], row['away']):
                identifier = model_of(side)
                base = model(side['key'], side['checkpoint'])
                if identifier not in known and base in known:
                    known[identifier] = {**known[base], 'format': side.get('format')}
        return known


def _rewrite(rows: list[dict[str, Any]]) -> None:
    _write(_folder() / _RESULTS, ''.join(json.dumps(row) + '\n' for row in rows))
    _CACHE.pop('stamp', None)


def point(source: str, ref: str, recording: str | None) -> bool:
    """Writes the recording of the result of a source and a reference, drawn after the match.

    Returns:
        Whether the rows hold such a result.
    """
    with _LOCK:
        rows = [dict(row) for row in _rows()]
        found = False
        for row in rows:
            if row['source'] == source and row.get('ref') == ref:
                found = True
                if recording is None:
                    row.pop('recording', None)
                else:
                    row['recording'] = recording
        if found:
            _rewrite(rows)
        return found


def reset(key: str) -> bool:
    """Deletes every result of a team key, at any checkpoint, and its earlier rating.

    Returns:
        Whether the store held anything of the key.
    """
    with _LOCK:
        rows = _rows()
        kept = [row for row in rows if key not in (row['home']['key'], row['away']['key'])]
        changed = len(kept) != len(rows)
        if changed:
            _rewrite(kept)
            known = {
                identifier: entry
                for identifier, entry in _load_models().items()
                if entry['key'] != key
            }
            _write(_folder() / _MODELS, json.dumps({'version': VERSION, 'models': known}))
        table = _load_earlier()
        if key in table:
            del table[key]
            _save_earlier(table)
            lines = [entry for entry in _lines() if entry['key'] != key]
            _write(_folder() / _HISTORY, ''.join(json.dumps(entry) + '\n' for entry in lines))
            changed = True
        return changed


# The earlier ratings, one for each team key whatever the scenario.


def _clean(table: Any) -> dict[str, dict[str, Any]]:
    """Returns the records of a table that hold a rating, each with its counts in their types."""
    if not isinstance(table, dict):
        return {}
    found = {}
    for key, entry in table.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        if not _number(entry.get('rating')):
            continue
        entry = dict(entry)
        if not _whole(entry.get('matches')):
            entry['matches'] = 0
        sources = entry.get('sources')
        if not isinstance(sources, dict):
            sources = {}
        entry['sources'] = {
            name: count
            for name, count in sources.items()
            if isinstance(name, str) and _whole(count)
        }
        found[key] = entry
    return found


def _first() -> dict[str, dict[str, Any]]:
    """Returns the ratings that tournaments kept before the store, counted under `tournament`."""
    try:
        table = json.loads((_home.home() / _EARLIER).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    found = _clean(table)
    for entry in found.values():
        if not entry['sources'] and entry['matches']:
            entry['sources'] = {'tournament': entry['matches']}
    return found


def _load_earlier() -> dict[str, dict[str, Any]]:
    path = _folder() / _TABLE
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        table = _first()
        if table:
            _save_earlier(table)
        return table
    except (OSError, ValueError):
        return {}
    return _clean(document.get('teams') if isinstance(document, dict) else None)


def _save_earlier(table: dict[str, dict[str, Any]]) -> None:
    _write(_folder() / _TABLE, json.dumps({'version': VERSION, 'teams': table}))


def earlier() -> dict[str, dict[str, Any]]:
    """Returns the earlier rating of each team key, as the module describes them.

    A record holds the name of the team, a label with `name_id` and `name_values` where cuju
    made the name, `rating`, the rating by the Elo rule from 1500, `matches`, the results that
    moved it, and `sources`, those results counted by their source.
    """
    with _LOCK:
        return _load_earlier()


def _lines() -> list[dict[str, Any]]:
    try:
        text = (_folder() / _HISTORY).read_text(encoding='utf-8')
    except OSError:
        return []
    found = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get('key'), str):
            found.append(entry)
    return found


def earlier_history(key: str) -> list[dict[str, Any]]:
    """Returns the results that moved the earlier rating of a team key, oldest first.

    Each is a mapping of `time`, `opponent`, `score`, `before` and `after`, the rating around
    the result, `source` and `phase`, and `ref` where its source named it.
    """
    with _LOCK:
        return [
            {name: value for name, value in entry.items() if name != 'key'}
            for entry in _lines()
            if entry['key'] == key
        ]


# The documents of the rating runs.


def _run_path(identifier: Any) -> Path | None:
    if not isinstance(identifier, str) or not _home.valid_identifier(identifier):
        return None
    folder = _folder() / _RUNS
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{identifier}.json'


def run(identifier: str) -> dict[str, Any] | None:
    """Returns the document of a rating run, or `None` for none of that identifier."""
    path = _run_path(identifier)
    if path is None:
        return None
    try:
        with _LOCK:
            source = path.read_text(encoding='utf-8')
        document = json.loads(source)
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) and document.get('id') == identifier else None


def runs() -> list[dict[str, Any]]:
    """Returns the document of every rating run, newest first."""
    folder = _folder() / _RUNS
    found = []
    for path in sorted(folder.glob('*.json'), reverse=True) if folder.is_dir() else ():
        document = run(path.stem)
        if document is not None:
            found.append(document)
    found.sort(key=lambda document: document.get('created') or 0, reverse=True)
    return found


def save_run(document: dict[str, Any]) -> None:
    """Writes the document of a rating run through a temporary file.

    Raises:
        ArgumentError: If the document names no identifier that is safe as a file name.
    """
    path = _run_path(document.get('id') if isinstance(document, dict) else None)
    if path is None:
        raise MessageError('server.ratings.run', run=repr(document))
    with _LOCK:
        _write(path, json.dumps(document))


def remove_run(identifier: str) -> bool:
    """Deletes the document of a rating run, and returns whether there was one."""
    path = _run_path(identifier)
    if path is None:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


# The ratings that a tournament's file keeps of its entrants.

# The version of the field `elo` of a tournament's file, which a change of its form raises.
TOURNAMENT_VERSION = 2
# Where the rating that seeded an entrant came from.
ORIGINS = ('given', 'known', 'measured', 'lacking')


def _interval(value: Any) -> list[float] | None:
    if isinstance(value, list) and len(value) == 2 and all(map(_number, value)):
        return [float(bound) for bound in value]
    return None


def _seeding(value: Any, version: int) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not _number(value.get('rating')):
        return None
    origin = value.get('origin')
    if origin not in ORIGINS:
        # A file of the first version tells only whether its seeding phase measured the rating.
        origin = 'measured' if value.get('measured') is True else 'given'
    found = {
        'rating': float(value['rating']),
        'interval': _interval(value.get('interval')),
        'matches': value['matches'] if _whole(value.get('matches')) else 0,
        'origin': origin,
        'measured': origin == 'measured',
    }
    if version >= 2:
        anchor = value.get('anchor_matches')
        found['anchor_matches'] = anchor if _whole(anchor) else 0
        checkpoint = value.get('checkpoint')
        found['checkpoint'] = checkpoint if isinstance(checkpoint, str) else None
    return found


def _final(value: Any) -> dict[str, Any] | None:
    if _number(value):
        # A file of the first version kept the number alone.
        return {'rating': float(value), 'interval': None, 'matches': None}
    if not isinstance(value, dict) or not _number(value.get('rating')):
        return None
    return {
        'rating': float(value['rating']),
        'interval': _interval(value.get('interval')),
        'matches': value['matches'] if _whole(value.get('matches')) else None,
    }


def tournament_elo(value: Any) -> dict[str, Any]:
    """Returns the field `elo` of a tournament's file in its current form.

    The field holds `scale`, `table` for ratings of the table of a scenario, where the anchor
    stands at 1000, and `earlier` for the ratings of a file written before the tables, by the
    Elo rule from 1500. Beside it stand `method`, the name of the rating method of every rating
    of the field, the `scenario` of the table, the key of its `anchor`, and `entrants`, which
    keeps for each entrant key `seeding` and `final`. A method of a plugin adds `plugins`, a list
    of `{name, version}` of the plugin as it was when the ratings were made, so a later read
    without the plugin still shows them, and a method that ships adds nothing.

    `seeding` is the rating that seeded the entrant, a mapping of `rating`, `interval`, the
    lower and upper bound or `None`, `matches`, the matches behind it, `anchor_matches`, those
    of them against the anchor, `checkpoint`, the checkpoint of the model that the rating
    belongs to, and `origin`. The origin is `given` for a rating that the configuration states,
    `known` for one that the table held already, `measured` for one whose evidence the
    tournament gathered first, and `lacking` for one that the tournament took as it stood with
    less evidence than its settings ask. `measured` repeats whether the origin is `measured`.
    `seeding` is `None` for an entrant without one. `final` is the rating after the tournament,
    a mapping of `rating`, `interval` and `matches`, or `None`.

    A file written before the field, or one whose field does not fit, reads as a field
    without entrants, and a file of the first version reads with its numbers as they stand, so
    a tournament opened later shows the same seeding without playing it again.
    """
    entrants = value.get('entrants') if isinstance(value, dict) else None
    version = value.get('version') if isinstance(value, dict) else None
    version = version if _whole(version) and version >= 1 else TOURNAMENT_VERSION
    found: dict[str, Any] = {}
    for key, entry in entrants.items() if isinstance(entrants, dict) else ():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        found[key] = {
            'seeding': _seeding(entry.get('seeding'), max(version, TOURNAMENT_VERSION)),
            'final': _final(entry.get('final')),
        }
    scale = value.get('scale') if isinstance(value, dict) else None
    if scale not in ('table', 'earlier'):
        scale = 'earlier' if version < 2 else 'table'
    scenario = value.get('scenario') if isinstance(value, dict) else None
    anchor = value.get('anchor') if isinstance(value, dict) else None
    method = value.get('method') if isinstance(value, dict) else None
    used = _plugins.stamps(value.get('plugins') if isinstance(value, dict) else None)
    return {
        'version': TOURNAMENT_VERSION,
        'scale': scale,
        'method': method if isinstance(method, str) and method else 'points',
        'scenario': scenario if isinstance(scenario, str) else None,
        'anchor': anchor if isinstance(anchor, str) else None,
        'entrants': found,
        **({'plugins': used} if used else {}),
    }


def keep_method(elo: dict[str, Any], method: str) -> dict[str, Any]:
    """Keeps the rating method of the field `elo`, with the plugin behind it, and returns it.

    A method of a plugin is kept as its qualified name and `plugins`, the plugin's name and
    version. A method that ships leaves `plugins` out.
    """
    elo['method'] = method
    used = _plugins.stamp([method])
    if used:
        elo['plugins'] = used
    else:
        elo.pop('plugins', None)
    return elo


def keep_seeding(
    elo: dict[str, Any],
    key: str,
    rating: float,
    interval: list[float] | None = None,
    matches: int = 0,
    origin: str = 'measured',
    anchor_matches: int = 0,
    checkpoint: str | None = None,
) -> dict[str, Any]:
    """Keeps the rating that seeded one entrant in the field `elo` of a tournament.

    Args:
        elo: The field, as `tournament_elo` returns it, which the call changes and returns.
        key: The entrant key.
        rating: The rating that seeded the entrant.
        interval: The lower and upper bound of the rating, or `None` for a given rating.
        matches: The matches behind the rating.
        origin: One of `ORIGINS`, as `tournament_elo` describes them.
        anchor_matches: The matches against the anchor among them.
        checkpoint: The checkpoint of the model that the rating belongs to, or `None`.

    Raises:
        ArgumentError: If a value does not fit.
    """
    found = _seeding(
        {
            'rating': rating,
            'interval': interval,
            'matches': matches,
            'origin': origin,
            'anchor_matches': anchor_matches,
            'checkpoint': checkpoint,
        },
        TOURNAMENT_VERSION,
    )
    fits = (
        isinstance(key, str)
        and found is not None
        and origin in ORIGINS
        and found['matches'] == matches
        and found['anchor_matches'] == anchor_matches
    )
    if not fits:
        raise MessageError('server.ratings.seeding', key=repr(key))
    entry = elo.setdefault('entrants', {}).setdefault(key, {'seeding': None, 'final': None})
    entry['seeding'] = found
    return elo


def keep_final(elo: dict[str, Any], rows: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    """Keeps the rating of each entrant after the tournament in the field `elo`, and returns it.

    Args:
        elo: The field, which the call changes.
        rows: For each entrant key the row of its model in the table of the scenario, with its
            `rating`, `low`, `high` and `matches`, or `None` for an entrant without one.
    """
    entrants = elo.setdefault('entrants', {})
    for key, row in rows.items():
        entry = entrants.setdefault(key, {'seeding': None, 'final': None})
        entry['final'] = (
            None
            if row is None
            else _final(
                {
                    'rating': row['rating'],
                    'interval': [row['low'], row['high']],
                    'matches': row['matches'],
                }
            )
        )
    return elo
