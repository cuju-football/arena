# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The ratings of Evaluate, one table for each scenario, and the rating runs that fill them.

A rating belongs to a model on a scenario. A model is a team at a checkpoint, the key of the
team with the digest of the files that it plays with, so a team whose files change starts again
without evidence while its earlier checkpoint keeps its rating. The built-in AI at a difficulty
is a model as well, and a policy without files is rated by its name. Every result that the
renderer plays on a scenario is evidence for the table of that scenario, a match of a
tournament's draw, a match of a rating run, a match of an evaluation and a requested match,
each kept once as a row of `_store.ratings`. The table is made again from those rows alone. A
match of a set length is another game than its scenario, so it joins a table of its own, named
by the scenario and the length, such as `5_vs_5@400`, and no rating run plays for such a table.

The anchor

One model of every table is its fixed point, the built-in AI at the difficulty that the key
`anchor` of the table `[ratings]` names. It stands at 1000 in every table and by every method,
and every other rating reads against it.

The two methods

`points` is the Elo rule of the tournaments, match by match in the order the results were kept.
Every model starts at 1000, a match moves each side by 20 times the gap between the share of a
win that it took, 1, a half or 0, and the share that the ratings expected, and the anchor stays
where it is, so a match against it moves the other side alone. Its value depends on the order of
the matches, which is why the rows keep their order. Beside it stands a deviation, the one of
`_evaluate.schedule`, `1 / sqrt(1 / 350**2 + (ln 10 / 400)**2 * sum)` over the matches of the
model, where each match adds `p * (1 - p)` at the share `p` that the ratings expected of it. It
tells how much the matches could tell, it is an approximation, and it is 0 for the anchor.

`bradley_terry` fits the model of Bradley and Terry to every row of the table at once, a draw
counting as half a win for each side, with the anchor held at 1000. Its prior is that of
`_tournaments.results`, one draw of each model against a virtual model, which here has the
strength of the anchor, so a model without a match stands at 1000 with a deviation near 350,
as by points. The order of the rows changes nothing. Its deviation comes from the curvature of
the fit at its ratings, the inverse of the matrix of the second derivatives, so a model that
is tied to the anchor through few matches shows a wide interval however many matches it played.

A rating method is a plugin of the kind `rating_method/1`, `fit(rows, anchor, options)`, and
these two are its standards, `PointsMethod` and `BradleyTerryMethod`. The key `method` of
`[ratings]` may name the method of any plugin, and a table then holds its rating beside those of
the two standards. The evidence rule, which chooses the recorded results that count, is a plugin
of the kind `evidence/1` as well, and `default` keeps the results of the sources that count.

Both read on one scale, 400 points for each tenfold of strength. The interval of a rating is
the rating less and plus its deviation times the two sided quantile of the normal distribution
at the confidence. The key `method` of `[ratings]` says which of the two ranks a table and
seeds a draw, `points` by default, and a row always holds both.

Evidence

Each row tells its `matches`, how many of them met the anchor in `anchor_matches`, and the
`width` of its interval. The table `[ratings.evidence]` names what a model needs before it
counts as rated, and `lacking` tells what a model still misses.

Rating runs

`rate` gathers evidence for chosen models on one scenario. Each of them first plays the anchor
as often as `[ratings.run] anchor_matches` says, and the schedule of `_evaluate.schedule` then
picks each next pairing where the order is least settled, until the order is settled or the most
matches are played. The run plays as an evaluation does. Matches of the built-in AI alone play in
a batch, a team plays in a process of its own, and the run keeps the seed and the actions of
every match, in the folder of actions that evaluations use, and no frame. A match that the rule
`[ratings.run] keep` names is drawn again from them as a recording, by `_evaluate.replay`, any
other match can be drawn later, and `check` plays every match again and confirms its score. The
document of a run is kept after every change, so a run goes on after a restart with the same
pairings.
"""

from __future__ import annotations

import collections
import concurrent.futures
import contextlib
import copy
import json
import logging
import math
import random
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from cuju.errors import ArgumentError, ConfigurationError

from .. import plugins as _plugins
from .._config import layers as _layers
from .._formats import context as _context
from .._registry import checkpoints as _checkpoints
from .._registry import teams as _teams
from .._store import home as _home
from .._store import ratings as _store
from .._store import runs as _runs
from .._text import messages as _messages
from .._text.messages import MessageError
from .._tournaments import results as _results
from .._tournaments import tournament as _rules
from . import schedule as _schedule

_LOGGER = logging.getLogger('cuju')
# The two methods that ship, which every row of a table holds beside the one that ranks it.
STANDARD = ('points', 'bradley_terry')
# The rating of the anchor of every table, and of a model before its first match.
ANCHOR_RATING = 1000.0
# How far one match moves a rating by points, the step of the tournaments.
STEP = _rules.RATING_STEP
# The states of a rating run, and those in which it has ended.
STATES = ('queued', 'running', 'done', 'failed', 'cancelled')
ENDED = ('done', 'failed', 'cancelled')
# The most models that one rating run measures.
MAX_MODELS = 64
_SCALE = 400 / math.log(10)
_LOCK = threading.Lock()
_TABLES: collections.OrderedDict[Any, dict[str, Any]] = collections.OrderedDict()


# The settings, the anchor and the models.


class _Stop(BaseException):
    """Ends the play of a rating run that goes on later, as the close of its owner asks."""


def table_name(scenario: str, length: int | None = None) -> str:
    """Returns the name of the table of a scenario, with the length of a match that sets one."""
    return scenario if length is None else f'{scenario}@{int(length)}'


def settings(config: Any = None, overrides: Any = ()) -> dict[str, Any]:
    """Returns the table `[ratings]` of the settings, with its tables `evidence` and `run`.

    Raises:
        ArgumentError: If the method or the evidence rule is no name that a plugin offers.
    """
    found = _layers.table('ratings', config, overrides)
    _check_method(found['method'], config, overrides)
    _plugins.get('evidence', found['rule'], config, overrides)
    return found


def _check_method(method: Any, config: Any = None, overrides: Any = ()) -> None:
    choices = _plugins.names('rating_method', config, overrides)
    if method not in choices:
        raise MessageError('server.ratings.method', method=repr(method), methods=', '.join(choices))


def builtin_key(difficulty: Any) -> str:
    """Returns the key of the built-in AI at a difficulty, or at that of the scenario."""
    known = isinstance(difficulty, int | float) and not isinstance(difficulty, bool)
    return f'builtin:{f"{float(difficulty):.2f}" if known else "scenario"}'


def builtin_side(difficulty: Any) -> dict[str, Any]:
    """Returns the built-in AI at a difficulty, or at that of the scenario, as a side."""
    known = isinstance(difficulty, int | float) and not isinstance(difficulty, bool)
    name = (
        _messages.label('server.tournament.label.builtin_ability', ability=f'{difficulty:.2f}')
        if known
        else _messages.label('server.tournament.label.builtin')
    )
    return {
        'key': builtin_key(difficulty),
        'checkpoint': None,
        'kind': 'builtin',
        'team': _teams.BUILTIN,
        'difficulty': float(difficulty) if known else None,
        **_messages.named('name', name),
    }


def anchor_side(found: dict[str, Any]) -> dict[str, Any]:
    """Returns the anchor of the tables that the settings name, as a side."""
    return builtin_side(float(found['anchor']))


def checkpoint_of(team: str) -> str | None:
    """Returns the checkpoint of a team of the registry, the digest of its model files.

    The digest is the identifier that `runs.team_checkpoint` gives, kept until a file of the
    team changes its size or its time. An entrant `team@checkpoint` names its checkpoint already.
    A side without files, such as the built-in AI, and a team that the registry lacks have none.
    """
    if not isinstance(team, str):
        return None
    name, separator, point = team.partition('@')
    if separator:
        return point if _checkpoints.exists(name, point) else None
    if name == _teams.BUILTIN:
        return None
    return _checkpoints.current_id(name)


def _checkpoint_metadata(key: str, checkpoint: str) -> dict[str, Any]:
    try:
        return _checkpoints.get(key, checkpoint)
    except ArgumentError:
        return {}


def team_side(team: str, *, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Returns a team of the registry at the checkpoint of its files, as a side.

    Raises:
        ArgumentError: If the registry holds no such team.
    """
    entry = _teams.entry(team)
    if entry['kind'] == 'builtin':
        return builtin_side(None)
    shown = entry.get('entrant', entry['id'])
    if snapshot is None:
        snapshot = _checkpoints.snapshot(shown)
    details = snapshot or {}
    return {
        'key': entry['id'],
        'checkpoint': details.get('id'),
        'snapshot': snapshot,
        'kind': 'team',
        'team': shown,
        'difficulty': None,
        'label': details.get('label') or entry['id'],
        'step': details.get('step'),
        **_messages.named('name', _teams.shown_name(entry)),
    }


def side_of(text: Any) -> dict[str, Any]:
    """Returns the model that a text names, as a side.

    The text is `builtin` for the built-in AI at the difficulty of the scenario, `builtin` with
    a colon and a difficulty from 0 to 1, such as `builtin:0.6`, or the identifier of a team of
    the registry, alone or after `team` and a colon.

    Raises:
        ArgumentError: If the text names none of these.
    """
    if isinstance(text, str) and (text == 'builtin' or text.startswith('builtin:')):
        level = text.partition(':')[2]
        if level in ('', 'scenario'):
            return builtin_side(None)
        try:
            difficulty = float(level)
        except ValueError:
            difficulty = -1.0
        if not 0 <= difficulty <= 1:
            raise MessageError('server.ratings.model', model=repr(text))
        return builtin_side(difficulty)
    if text == 'random':
        side = _teams.side_entry(_teams.RANDOM_ACTIONS)
        return {
            'key': _rules.RANDOM_KEY,
            'checkpoint': None,
            'kind': 'random',
            'team': side['id'],
            'difficulty': None,
            **_messages.named('name', _teams.shown_name(side)),
        }
    if isinstance(text, str) and text:
        return team_side(text[5:] if text.startswith('team:') else text)
    raise MessageError('server.ratings.model', model=repr(text))


def _named(holder: dict[str, Any]) -> dict[str, Any]:
    return {key: holder[key] for key in ('name', 'name_id', 'name_values') if key in holder}


def _stored(side: dict[str, Any]) -> dict[str, Any]:
    """Returns what a result keeps of a side, its key, its checkpoint, its name and its kind."""
    found = {'key': side['key'], 'checkpoint': side.get('checkpoint'), **_named(side)}
    for name in ('kind', 'label', 'step'):
        if side.get(name) is not None:
            found[name] = side[name]
    return found


def _match_snapshots(sides: dict[str, Any], match: dict[str, Any]) -> list[dict[str, Any]]:
    """Returns the saved checkpoints of a rating match in home and away order."""
    found = []
    for role in ('home', 'away'):
        side = sides[match[role]]
        point = side.get('snapshot')
        if point is None and side.get('checkpoint') is not None:
            point = {'id': side['checkpoint'], 'label': side.get('label'), 'step': side.get('step')}
        found.append({'checkpoint': point})
    return found


# The two methods.


def _deviation(information: float) -> float:
    return 1 / math.sqrt(1 / _schedule.PRIOR_DEVIATION**2 + information / _SCALE**2)


def points(
    games: list[tuple[str, str, list[int]]], anchor: str
) -> tuple[dict[str, dict[str, float]], list[tuple[float, float, float, float]]]:
    """Returns the ratings by points of the models of games, and how each game moved them.

    Args:
        games: Each game in the order it was played, the model of its home side, that of its
            away side and the score.
        anchor: The model that stays at `ANCHOR_RATING`.

    Returns:
        The `rating` and the `deviation` of each model, and for each game the rating of its
        home side before and after it, then that of its away side.
    """
    ratings: dict[str, float] = {anchor: ANCHOR_RATING}
    information: dict[str, float] = {anchor: 0.0}
    moves = []
    for home, away, score in games:
        first = ratings.setdefault(home, ANCHOR_RATING)
        second = ratings.setdefault(away, ANCHOR_RATING)
        if home == away:
            moves.append((first, first, second, second))
            continue
        share = _schedule.expected(first, second)
        actual = 1.0 if score[0] > score[1] else 0.5 if score[0] == score[1] else 0.0
        change = STEP * (actual - share)
        if home != anchor:
            ratings[home] = round(first + change, 2)
        if away != anchor:
            ratings[away] = round(second - change, 2)
        for key in (home, away):
            information[key] = information.get(key, 0.0) + share * (1 - share)
        moves.append((first, ratings[home], second, ratings[away]))
    found = {
        key: {
            'rating': rating,
            'deviation': 0.0 if key == anchor else _deviation(information.get(key, 0.0)),
        }
        for key, rating in ratings.items()
    }
    return found, moves


def bradley_terry(
    games: list[tuple[str, str, list[int]]], anchor: str
) -> dict[str, dict[str, float]]:
    """Returns the ratings of the models of games by the fit of Bradley and Terry.

    The fit maximises the likelihood of every game together with the prior, with the anchor
    held where it is, by the method of Newton, and the deviation of each rating comes from the
    inverse of the matrix of the second derivatives at the fit.

    Args:
        games: Each game, the model of its home side, that of its away side and the score.
        anchor: The model that stands at `ANCHOR_RATING`.

    Returns:
        The `rating` and the `deviation` of each model.
    """
    keys = list(
        dict.fromkeys(key for home, away, _ in games for key in (home, away) if key != anchor)
    )
    found = {anchor: {'rating': ANCHOR_RATING, 'deviation': 0.0}}
    count = len(keys)
    if not count:
        return found
    index = {key: position for position, key in enumerate(keys)}
    index[anchor] = count
    played = [
        (index[home], index[away], 1.0 if a > b else 0.5 if a == b else 0.0)
        for home, away, (a, b) in games
        if home != away
    ]
    prior = float(_results.PRIOR)

    def likelihood(values: list[float]) -> float:
        terms = 0.0
        for first, second, share in played:
            gap = values[first] - values[second]
            terms -= share * _softplus(-gap) + (1 - share) * _softplus(gap)
        virtual = sum(_softplus(value) + _softplus(-value) for value in values[:-1])
        return terms - prior / 2 * virtual

    def curvature(values: list[float]) -> tuple[list[float], list[list[float]]]:
        slope = [0.0] * (count + 1)
        matrix = [[0.0] * (count + 1) for _ in slope]
        for first, second, share in played:
            expected = 1 / (1 + math.exp(values[second] - values[first]))
            shift = share - expected
            slope[first] += shift
            slope[second] -= shift
            weight = expected * (1 - expected)
            matrix[first][first] += weight
            matrix[second][second] += weight
            matrix[first][second] -= weight
            matrix[second][first] -= weight
        for position, value in enumerate(values[:-1]):
            middle = 1 / (1 + math.exp(-value))
            slope[position] += prior * (0.5 - middle)
            matrix[position][position] += prior * middle * (1 - middle)
        slope.pop()
        matrix.pop()
        return slope, matrix

    strength = [0.0] * (count + 1)
    value = likelihood(strength)
    for _ in range(200):
        slope, matrix = curvature(strength)
        lower = _cholesky(matrix)
        step = _solve_cholesky(lower, slope)
        step.append(0.0)
        # Halve a step until it raises the likelihood.
        for _ in range(60):
            candidate = [current + change for current, change in zip(strength, step, strict=True)]
            moved = likelihood(candidate)
            if moved >= value:
                break
            step = [change / 2 for change in step]
        strength = candidate
        value = moved
        if max(map(abs, step)) < 1e-12:
            break
    _, matrix = curvature(strength)
    lower = _cholesky(matrix)
    for position, key in enumerate(keys):
        # The inverse diagonal is the squared norm of a column of the inverse factor.
        column = [0.0] * count
        column[position] = 1.0
        solved = _solve_lower(lower, column, start=position)
        found[key] = {
            'rating': ANCHOR_RATING + _SCALE * strength[position],
            'deviation': _SCALE * math.sqrt(sum(item * item for item in solved)),
        }
    return found


def _softplus(value: float) -> float:
    return max(value, 0.0) + math.log1p(math.exp(-abs(value)))


def _cholesky(matrix: list[list[float]]) -> list[list[float]]:
    """Factor the positive definite curvature of the fit into a lower triangular matrix."""
    lower: list[list[float]] = []
    for row, values in enumerate(matrix):
        entries: list[float] = []
        for column in range(row):
            product = sum(a * b for a, b in zip(entries, lower[column], strict=False))
            entries.append((values[column] - product) / lower[column][column])
        entries.append(math.sqrt(values[row] - sum(value * value for value in entries)))
        lower.append(entries)
    return lower


def _solve_lower(lower: list[list[float]], values: list[float], *, start: int = 0) -> list[float]:
    result = [0.0] * len(lower)
    for index in range(start, len(lower)):
        row = lower[index]
        product = sum(row[column] * result[column] for column in range(start, index))
        result[index] = (values[index] - product) / row[index]
    return result


def _solve_cholesky(lower: list[list[float]], values: list[float]) -> list[float]:
    result = _solve_lower(lower, values)
    for index in range(len(lower) - 1, -1, -1):
        product = sum(lower[row][index] * result[row] for row in range(index + 1, len(lower)))
        result[index] = (result[index] - product) / lower[index][index]
    return result


class PointsMethod:
    """The rating method `points`, the Elo rule match by match, as a `rating_method/1`."""

    def fit(
        self, rows: list[tuple[str, str, list[int]]], anchor: str, options: dict[str, Any]
    ) -> dict[str, dict[str, float]]:
        """Returns the ratings by points, see `points`, with the intervals of the options."""
        return _intervals(points(rows, anchor)[0], options['quantile'])


class BradleyTerryMethod:
    """The rating method `bradley_terry`, the fit of every row at once, as a `rating_method/1`."""

    def fit(
        self, rows: list[tuple[str, str, list[int]]], anchor: str, options: dict[str, Any]
    ) -> dict[str, dict[str, float]]:
        """Returns the ratings by the fit of Bradley and Terry, see `bradley_terry`."""
        return _intervals(bradley_terry(rows, anchor), options['quantile'])


class DefaultEvidence:
    """The evidence rule `default`, the results of the sources that `[ratings] sources` names."""

    def keep(self, rows: list[dict[str, Any]], options: dict[str, Any]) -> list[dict[str, Any]]:
        """Returns the rows whose source counts, in their order."""
        return [row for row in rows if row['source'] in options['sources']]


def _intervals(found: dict[str, dict[str, float]], quantile: float) -> dict[str, dict[str, float]]:
    return {key: _interval(entry, quantile) for key, entry in found.items()}


def _fitted(anchor: str, models: set[str], quantile: float, anchor_rating: float) -> Any:
    """Returns a function that checks the answer of a method's `fit`, which raises ValueError."""

    def check(answer: Any) -> dict[str, dict[str, float]]:
        if not isinstance(answer, dict):
            raise ValueError(f'expected a mapping of each model to its rating, found {answer!r}')
        found: dict[str, dict[str, float]] = {}
        for key in sorted(models | {anchor}):
            entry = answer.get(key)
            if not isinstance(entry, dict):
                raise ValueError(f'expected a rating of the model {key!r}')
            numbers = {}
            for name in ('rating', 'deviation', 'low', 'high'):
                value = entry.get(name)
                if value is None and name in ('low', 'high'):
                    continue
                if not isinstance(value, int | float) or isinstance(value, bool):
                    raise ValueError(f'expected a number as {name} of {key!r}, found {value!r}')
                if not math.isfinite(value):
                    raise ValueError(f'expected a finite {name} of {key!r}, found {value!r}')
                numbers[name] = float(value)
            if numbers['deviation'] < 0:
                raise ValueError(
                    f'expected a deviation of {key!r} from 0, found {numbers["deviation"]}'
                )
            if 'low' not in numbers or 'high' not in numbers:
                spread = quantile * numbers['deviation']
                numbers.setdefault('low', numbers['rating'] - spread)
                numbers.setdefault('high', numbers['rating'] + spread)
            if not numbers['low'] <= numbers['rating'] <= numbers['high']:
                raise ValueError(f'expected low, rating and high of {key!r} in that order')
            found[key] = {
                name: round(numbers[name], 2) for name in ('rating', 'low', 'high', 'deviation')
            }
        held = found[anchor]
        if held['rating'] != round(anchor_rating, 2) or held['deviation'] != 0:
            raise ValueError(f'expected the anchor at {anchor_rating} with a deviation of 0')
        return found

    return check


def _fit(
    method: str, games: list[tuple[str, str, list[int]]], anchor: str, found: dict[str, Any]
) -> dict[str, dict[str, float]]:
    """Returns the rating of each model by a method, as `rating_method/1` gives it.

    The method is called with a copy of the games, the key of the anchor and the options, a
    mapping of `confidence`, `quantile`, the two sided quantile of the normal distribution at the
    confidence, and `anchor_rating`.
    """
    confidence = float(found['confidence'])
    quantile = _schedule.quantile(confidence)
    models = {key for game in games for key in game[:2]}
    options = {'confidence': confidence, 'quantile': quantile, 'anchor_rating': ANCHOR_RATING}
    return _plugins.call(
        'rating_method',
        method,
        'fit',
        [(home, away, list(score)) for home, away, score in games],
        anchor,
        options,
        check=_fitted(anchor, models, quantile, ANCHOR_RATING),
    )


# The table of a scenario.


def _interval(entry: dict[str, float], quantile: float) -> dict[str, float]:
    spread = quantile * entry['deviation']
    return {
        'rating': round(entry['rating'], 2),
        'low': round(entry['rating'] - spread, 2),
        'high': round(entry['rating'] + spread, 2),
        'deviation': round(entry['deviation'], 2),
    }


def _lacking(
    matches: int, anchor_matches: int, width: float, required: dict[str, Any]
) -> dict[str, Any] | None:
    """Returns what a model misses of the evidence that the settings ask, or `None`."""
    found: dict[str, Any] = {}
    if matches < required['matches']:
        found['matches'] = required['matches'] - matches
    if anchor_matches < required['anchor_matches']:
        found['anchor_matches'] = required['anchor_matches'] - anchor_matches
    if required.get('width') is not None and width > required['width']:
        found['width'] = round(width, 2)
    return found or None


def _signature(found: dict[str, Any]) -> tuple[Any, ...]:
    required = found['evidence']
    return (
        found['method'],
        found['rule'],
        _plugins.signature(),
        float(found['anchor']),
        float(found['confidence']),
        tuple(found['sources']),
        required['matches'],
        required['anchor_matches'],
        required.get('width'),
    )


def _method_notes(found: dict[str, Any]) -> dict[str, Any]:
    methods: dict[str, Any] = {
        'points': {
            'rule': 'elo',
            'step': STEP,
            'start': ANCHOR_RATING,
            'order': 'the order of the results',
            'interval': 'information',
        },
        'bradley_terry': {
            'rule': 'fit',
            'prior_draws': _results.PRIOR,
            'prior_against': 'the anchor',
            'order': 'none',
            'interval': 'curvature',
        },
    }
    if found['method'] not in STANDARD:
        # The method of a plugin that ranks the table, which names the plugin it comes from.
        methods[found['method']] = {'rule': 'plugin', 'plugin': found['method'].partition('.')[0]}
    return {
        'method': found['method'],
        'methods': methods,
        'anchor_rating': ANCHOR_RATING,
        'confidence': float(found['confidence']),
    }


# How a row of Kaggle open reads its format, a row that names none, as every row before formats.
KAGGLE_OPEN = {'format': 'kaggle/1', 'name': 'Kaggle', 'version': '1', 'preset': 'open'}


def format_of_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Returns the format a model of a table played under, `{format, name, version, preset,
    digest}`, Kaggle open for a team that names none, or `None` for the built-in AI and the
    random entrant, which play under no format."""
    if row.get('kind') != 'team':
        return None
    identity = row.get('submission_format') or KAGGLE_OPEN
    return {
        'format': identity.get('format'),
        'name': identity.get('name'),
        'version': identity.get('version'),
        'preset': identity.get('preset'),
        'digest': identity.get('digest'),
    }


def _formats_listed(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Returns each format among the models of a table, with the count of its models, Kaggle
    first and the others by name."""
    found: dict[str, dict[str, Any]] = {}
    for row in models:
        shown = format_of_row(row)
        if shown is None:
            continue
        held = found.setdefault(
            shown['format'],
            {
                'format': shown['format'],
                'name': shown['name'],
                'version': shown['version'],
                'models': 0,
            },
        )
        held['models'] += 1
    return sorted(
        found.values(), key=lambda entry: (entry['format'] != 'kaggle/1', str(entry['name']))
    )


def table(
    scenario: str | None,
    config: Any = None,
    overrides: Any = (),
    *,
    method: str | None = None,
    format: str | None = None,
) -> dict[str, Any]:
    """Returns the table of a scenario, every model with its rating, best first.

    The mapping holds the `scenario`, the `anchor` with its `key`, its `difficulty`, its
    `rating` and its `matches`, the `method` that ranks the table, `methods`, what each method
    is made of, the `confidence` of the intervals, the `sources` that count, `evidence`, what a
    model needs to count as rated, `results`, the rows that count, and `models`.

    Each model holds its `id`, its `key` and `checkpoint`, its name, its `kind`, its `rank`,
    and `rating`, `low`, `high` and `deviation` by the method of the table, with the same four
    under `points` and under `bradley_terry`. Beside them stand its `matches`, its
    `anchor_matches`, its `wins`, `draws` and `losses`, its matches by source in `sources`,
    the `first` and `last` time of its results, the `width` of its interval, whether it is
    `rated`, what it is `lacking`, whether it is the `anchor`, and `latest`, true for the
    checkpoint of its team whose result is the newest. `current` tells for a team of the
    registry whether that checkpoint is the one of its files now, and is `None` otherwise.

    Args:
        scenario: The exact name of the scenario, or `None` for the results without one.
        config: The settings, as `cuju.arena.config` takes them.
        overrides: Settings such as `ratings.method=bradley_terry`, laid over `config`.
        method: The method that ranks the table, in place of the key `method` of the settings.
        format: The label of a format, such as `kaggle/1`, which keeps the teams that played
            under it and the built-in AI, ranked among themselves, or `None` for every model.
            A team whose rows name no format played Kaggle open.

    Every team's row also holds its `submission_format` as `format_of_row` gives it, and the
    mapping holds `formats`, each format of the table with the count of its models, and
    `format`, the filter or `None`.

    Raises:
        ArgumentError: If the method is no name that a plugin of the kind `rating_method` offers.
    """
    found = settings(config, overrides)
    if method is not None:
        _check_method(method, config, overrides)
        found['method'] = method
    made = _table(scenario, found)
    made['formats'] = _formats_listed(made['models'])
    made['format'] = format or None
    for row in made['models']:
        row['submission_format'] = format_of_row(row)
    if format:
        kept = [
            row
            for row in made['models']
            if row['submission_format'] is None or row['submission_format']['format'] == format
        ]
        for rank, row in enumerate(kept):
            row['rank'] = rank + 1
        made['models'] = kept
    return made


def _table(scenario: str | None, found: dict[str, Any]) -> dict[str, Any]:
    """Returns the table of a scenario under a resolved table `[ratings]` of the settings."""
    stamp = (str(_home.home()), _store.signature(), scenario, _signature(found))
    with _LOCK:
        held = _TABLES.get(stamp)
    if held is None:
        held = _build(scenario, found)
        with _LOCK:
            _TABLES[stamp] = held
            while len(_TABLES) > 16:
                _TABLES.popitem(last=False)
    made = copy.deepcopy(held)
    snapshots = made.pop('_snapshots')
    for row in made['models']:
        row['current'] = None
        checkpoint = row['checkpoint']
        if row['kind'] != 'team' or checkpoint is None:
            continue
        key = row['key']
        if row['latest']:
            now = checkpoint_of(key)
            row['current'] = None if now is None else now == checkpoint
        # Older tournament evidence used the entrant reference as the label.
        if (
            row['id'] not in snapshots
            and row['step'] is None
            and row['label'] in (key, f'{key}@{checkpoint}')
        ):
            details = _checkpoint_metadata(key, checkpoint)
            if details:
                row['label'] = details.get('label') or key
                row['step'] = details.get('step')
    return made


def _counted(scenario: str | None, found: dict[str, Any]) -> list[dict[str, Any]]:
    """Returns the rows of a scenario whose source the settings count, oldest first."""
    options = {'sources': list(found['sources'])}
    rows = _store.results(scenario)
    return _plugins.call('evidence', found['rule'], 'keep', rows, options, check=_kept(rows))


def _kept(rows: list[dict[str, Any]]) -> Any:
    """Returns a function that checks that an evidence rule only dropped rows of those it got."""

    def check(answer: Any) -> list[dict[str, Any]]:
        if not isinstance(answer, list | tuple):
            raise ValueError(f'expected a list of rows, found {answer!r}')
        found = []
        position = 0
        for row in answer:
            while position < len(rows) and rows[position] is not row:
                position += 1
            if position == len(rows):
                raise ValueError('expected rows of those given, each once and in their order')
            found.append(row)
            position += 1
        return found

    return check


def _build(scenario: str | None, found: dict[str, Any]) -> dict[str, Any]:
    rows = _counted(scenario, found)
    anchor = anchor_side(found)
    home = _store.model(anchor['key'])
    games = [
        (
            _store.model_of(row['home']),
            _store.model_of(row['away']),
            row['score'],
        )
        for row in rows
    ]
    names = [*STANDARD, *([] if found['method'] in STANDARD else [found['method']])]
    fitted = {name: _fit(name, games, home, found) for name in names}
    known = _store.models()
    counts: dict[str, dict[str, Any]] = {}
    newest: dict[str, tuple[int, str]] = {}
    for position, (row, game) in enumerate(zip(rows, games, strict=True)):
        own, other = row['score']
        for identifier, side, mine, theirs, against in (
            (game[0], row['home'], own, other, game[1]),
            (game[1], row['away'], other, own, game[0]),
        ):
            entry = counts.setdefault(
                identifier,
                {
                    'key': side['key'],
                    'checkpoint': side['checkpoint'],
                    'format': side.get('format'),
                    'submission_format': side.get('submission_format'),
                    'matches': 0,
                    'anchor_matches': 0,
                    'wins': 0,
                    'draws': 0,
                    'losses': 0,
                    'sources': dict.fromkeys(_store.SOURCES, 0),
                    'first': row['time'],
                    'last': row['time'],
                },
            )
            if game[0] == game[1]:
                continue
            entry['matches'] += 1
            entry['anchor_matches'] += against == home
            entry['wins' if mine > theirs else 'losses' if mine < theirs else 'draws'] += 1
            entry['sources'][row['source']] += 1
            entry['last'] = row['time']
            newest[side['key']] = (position, identifier)
    counts.setdefault(
        home,
        {
            'key': anchor['key'],
            'checkpoint': None,
            'format': None,
            'submission_format': None,
            'matches': 0,
            'anchor_matches': 0,
            'wins': 0,
            'draws': 0,
            'losses': 0,
            'sources': dict.fromkeys(_store.SOURCES, 0),
            'first': None,
            'last': None,
        },
    )
    models = []
    for identifier, entry in counts.items():
        described = known.get(identifier) or {}
        if identifier == home:
            described = {**anchor, **described}
        by = {name: fitted[name][identifier] for name in names}
        chosen = by[found['method']]
        width = round(chosen['high'] - chosen['low'], 2)
        is_anchor = identifier == home
        lacking = (
            None
            if is_anchor
            else _lacking(entry['matches'], entry['anchor_matches'], width, found['evidence'])
        )
        models.append(
            {
                'id': identifier,
                'key': entry['key'],
                'checkpoint': entry['checkpoint'],
                'name': described.get('name', entry['key']),
                **{
                    name: described[name]
                    for name in ('name_id', 'name_values')
                    if name in described
                },
                'kind': described.get('kind'),
                'label': described.get('label'),
                'step': described.get('step'),
                **chosen,
                'method': found['method'],
                **by,
                **{name: entry[name] for name in entry if name not in ('key', 'checkpoint')},
                'width': width,
                'rated': lacking is None,
                'lacking': lacking,
                'anchor': is_anchor,
                'latest': newest.get(entry['key'], (0, identifier))[1] == identifier,
            }
        )
    models.sort(key=lambda row: (-row['rating'], row['id']))
    for rank, row in enumerate(models):
        row['rank'] = rank + 1
    return {
        'scenario': scenario,
        'anchor': {
            'key': anchor['key'],
            'id': home,
            'difficulty': anchor['difficulty'],
            'rating': ANCHOR_RATING,
            'matches': counts[home]['matches'],
            **_named(anchor),
        },
        **_method_notes(found),
        'sources': list(found['sources']),
        'evidence': dict(found['evidence']),
        'results': len(rows),
        '_snapshots': {
            _store.model_of(result[role])
            for result in rows
            if 'sides' in result
            for role in ('home', 'away')
        },
        'models': models,
    }


def scenarios(config: Any = None, overrides: Any = ()) -> list[dict[str, Any]]:
    """Returns the scenarios that have a table, each with its rows and its models, by name.

    The results without a scenario, where the store holds some, come last under the scenario
    `None`.
    """
    found = settings(config, overrides)
    listed = []
    for scenario in _store.scenarios():
        rows = _counted(scenario, found)
        if not rows:
            continue
        models = {_store.model_of(row[side]) for row in rows for side in ('home', 'away')}
        listed.append(
            {
                'scenario': scenario,
                'results': len(rows),
                'models': len(models),
                'last': rows[-1]['time'],
            }
        )
    listed.sort(key=lambda entry: (entry['scenario'] is None, entry['scenario'] or ''))
    return listed


def by_key(
    scenario: str | None = _store.EVERY, config: Any = None, overrides: Any = ()
) -> dict[str, dict[str, Any]]:
    """Returns the newest rating row for each team key."""
    teams, _ = latest_rows(scenario, config, overrides)
    return teams


def latest_rows(
    scenario: str | None = _store.EVERY, config: Any = None, overrides: Any = ()
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Returns the newest rating rows, first by team key and then by model identifier."""
    names = (
        [entry['scenario'] for entry in scenarios(config, overrides)]
        if scenario == _store.EVERY
        else [scenario]
    )
    teams: dict[str, dict[str, Any]] = {}
    models: dict[str, dict[str, Any]] = {}
    for name in names:
        for row in table(name, config, overrides)['models']:
            if not row['matches']:
                continue
            row['scenario'] = name
            last = row['last'] or 0
            team, model = row['key'], row['id']
            if row['latest'] and (team not in teams or last >= (teams[team]['last'] or 0)):
                teams[team] = row
            if model not in models or last >= (models[model]['last'] or 0):
                models[model] = row
    return teams, models


def overview(config: Any = None, overrides: Any = ()) -> dict[str, Any]:
    """Returns what the ratings hold, the scenarios with a table and how the tables are made.

    The mapping holds `scenarios`, as `scenarios` returns them, the `anchor`, the `method` with
    `methods` and the `confidence`, the `sources` that count, the `evidence` that a model
    needs, `earlier`, the number of team keys with a rating of before the tables, and
    `formats`, each format that a model of the tables played under with its count of models.
    """
    found = settings(config, overrides)
    anchor = anchor_side(found)
    every = [
        {'kind': entry.get('kind', 'team'), 'submission_format': entry.get('submission_format')}
        for entry in _store.models().values()
        if entry.get('kind', 'team') == 'team'
    ]
    return {
        'scenarios': scenarios(config, overrides),
        # Every format that a model of the tables played under, for a filter of the tables.
        'formats': _formats_listed(every),
        'anchor': {
            'key': anchor['key'],
            'difficulty': anchor['difficulty'],
            'rating': ANCHOR_RATING,
            **_named(anchor),
        },
        **_method_notes(found),
        'sources': list(found['sources']),
        'evidence': dict(found['evidence']),
        'earlier': len(_store.earlier()),
    }


def team(
    key: str,
    scenario: str | None,
    config: Any = None,
    overrides: Any = (),
    *,
    method: str | None = None,
) -> dict[str, Any] | None:
    """Returns one team of the table of a scenario across its checkpoints, or `None`.

    The mapping holds the `key` and the name of the team, the `scenario`, the `method`,
    `checkpoints`, the row of each of its checkpoints as `table` gives it, the newest first,
    `latest`, the row that stands for the team, the checkpoint whose result is the newest,
    `history`, and `opponents`. The history holds every result of the team in the order it was
    kept, each with its `time`, the `checkpoint` that played, the `opponent` with its key, its
    checkpoint and its name, the `score` with the goals of the team first, the `source`, the
    `phase` and the `ref` of the result, its `recording` and its `actions` where it has them,
    and `points`, the rating by points of the checkpoint before and after it. `opponents`
    counts the `matches`, `wins`, `draws` and `losses` against each model that the team met.
    """
    made = table(scenario, config, overrides, method=method)
    rows = [row for row in made['models'] if row['key'] == key]
    if not rows:
        return None
    found = settings(config, overrides)
    counted = _counted(scenario, found)
    home = made['anchor']['id']
    games = [
        (
            _store.model_of(row['home']),
            _store.model_of(row['away']),
            row['score'],
        )
        for row in counted
    ]
    _, moves = points(games, home)
    names = {row['id']: row for row in made['models']}
    history = []
    met: dict[str, dict[str, Any]] = {}
    for row, game, move in zip(counted, games, moves, strict=True):
        if game[0] == game[1]:
            continue
        for mine, other, identifier, score, before, after in (
            ('home', 'away', game[1], row['score'], move[0], move[1]),
            ('away', 'home', game[0], row['score'][::-1], move[2], move[3]),
        ):
            if row[mine]['key'] != key:
                continue
            against = names.get(identifier, {})
            history.append(
                {
                    'time': row['time'],
                    'checkpoint': row[mine]['checkpoint'],
                    'opponent': {
                        'id': identifier,
                        'key': row[other]['key'],
                        'checkpoint': row[other]['checkpoint'],
                        **_named(against),
                    },
                    'score': list(score),
                    'source': row['source'],
                    'phase': row.get('phase'),
                    'ref': row.get('ref'),
                    'recording': row.get('recording'),
                    'actions': row.get('actions'),
                    'points': {'before': before, 'after': after},
                }
            )
            entry = met.setdefault(
                identifier,
                {
                    'id': identifier,
                    'key': row[other]['key'],
                    'checkpoint': row[other]['checkpoint'],
                    **_named(against),
                    'matches': 0,
                    'wins': 0,
                    'draws': 0,
                    'losses': 0,
                },
            )
            entry['matches'] += 1
            entry[
                'wins' if score[0] > score[1] else 'losses' if score[0] < score[1] else 'draws'
            ] += 1
    rows.sort(key=lambda row: (not row['latest'], -(row['last'] or 0)))
    latest = next((row for row in rows if row['latest']), rows[0])
    return {
        'key': key,
        **_named(latest),
        'kind': latest['kind'],
        'scenario': scenario,
        'method': made['method'],
        'anchor': made['anchor'],
        'latest': latest,
        'checkpoints': rows,
        'history': history,
        'opponents': list(met.values()),
    }


def earlier() -> dict[str, Any]:
    """Returns the ratings of before the tables, one for each team key whatever the scenario.

    The mapping holds `teams`, each with its `key`, its name, its `rating`, its `matches` and
    its `sources`, the best first, and tells that they follow the Elo rule from 1500, on a
    scale of their own that no table shares.
    """
    teams = [{'key': key, **entry} for key, entry in _store.earlier().items()]
    teams.sort(key=lambda entry: (-entry['rating'], entry['key']))
    return {
        'teams': teams,
        'method': 'points',
        'scale': 'earlier',
        'start': _rules.BASE_RATING,
        'step': STEP,
    }


def evidence(
    sides: list[dict[str, Any]],
    scenario: str | None,
    config: Any = None,
    overrides: Any = (),
    *,
    submission_format: Any = None,
) -> dict[str, Any]:
    """Returns the evidence that the table of a scenario holds of some models, and what lacks.

    Args:
        sides: The models, each a side with its `key` and its `checkpoint`.
        scenario: The scenario.
        config: The settings.
        overrides: Settings laid over `config`.
        submission_format: The format the models would play under, as a rating run takes it,
            with `per_entrant` keyed by a model's text, or `None` for Kaggle open. A team under
            a format is another model than the same team under Kaggle open, so the report reads
            the rows of each team as that format plays it.

    Returns:
        A mapping of the `scenario`, the `anchor`, the `method`, `required`, the evidence that
        a model needs, `models`, and `complete`, whether none of them lacks anything. Each
        model holds its `id`, `key`, `checkpoint` and name, its `rating`, `low`, `high` and
        `deviation` by the method, the same under `points` and `bradley_terry`, its `matches`,
        its `anchor_matches`, the `width` of its interval, whether it is `rated`, and
        `lacking`, a mapping of the `matches` and the `anchor_matches` that it misses and of
        its `width` where that is too wide, or `None`. A model without a result stands at the
        rating of the anchor with the interval of a model that has played nothing.
    """
    chosen = _context.check(submission_format)
    return _evidence(sides, scenario, settings(config, overrides), chosen)


def format_suffix(chosen: dict[str, Any] | None, side: dict[str, Any]) -> str | None:
    """Returns the suffix of the model of a team side under a checked format, `None` for none.

    The suffix is the one that a result of the side records once it played under the format,
    `format_side` of what `Policies.formats` gives, for a format whose player reports no
    revision. A format whose player reports one makes a model of each revision.
    """
    from .._formats import identity as _identity
    from .._formats import side as _side

    if chosen is None or side.get('kind') != 'team':
        return None
    choice = _side_choice(chosen, side)
    if choice is None:
        return None
    return _identity.model_suffix(_side.choose(choice).identity(), None)


def _evidence(
    sides: list[dict[str, Any]],
    scenario: str | None,
    found: dict[str, Any],
    chosen: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Returns the evidence of some models under a resolved table `[ratings]` and a checked
    format, `None` for Kaggle open."""
    if chosen is not None:
        sides = [{**side, 'format': format_suffix(chosen, side)} for side in sides]
    made = _table(scenario, found)
    held = {row['id']: row for row in made['models']}
    quantile = _schedule.quantile(float(made['confidence']))
    fresh = _interval({'rating': ANCHOR_RATING, 'deviation': _schedule.PRIOR_DEVIATION}, quantile)
    models = []
    for side in sides:
        identifier = _store.model_of(side)
        row = held.get(identifier)
        if row is None:
            width = round(fresh['high'] - fresh['low'], 2)
            lacking = _lacking(0, 0, width, made['evidence'])
            row = {
                'id': identifier,
                **fresh,
                'points': dict(fresh),
                'bradley_terry': dict(fresh),
                'matches': 0,
                'anchor_matches': 0,
                'width': width,
                'rated': lacking is None,
                'lacking': lacking,
                'anchor': False,
            }
        models.append(
            {
                'id': identifier,
                'key': side['key'],
                'checkpoint': side.get('checkpoint'),
                **_named(side),
                'kind': side.get('kind'),
                **{
                    name: row[name]
                    for name in (
                        'rating',
                        'low',
                        'high',
                        'deviation',
                        'points',
                        'bradley_terry',
                        'matches',
                        'anchor_matches',
                        'width',
                        'rated',
                        'lacking',
                        'anchor',
                    )
                },
            }
        )
    return {
        'scenario': scenario,
        'anchor': made['anchor'],
        'method': made['method'],
        'required': made['evidence'],
        'models': models,
        'complete': all(row['lacking'] is None for row in models),
    }


def lacking_text(report: dict[str, Any]) -> str:
    """Returns in words which models of an evidence report lack what, one after another."""
    parts = []
    for row in report['models']:
        lacking = row['lacking']
        if not lacking:
            continue
        missing = []
        if 'matches' in lacking:
            missing.append(_messages.text('server.ratings.lacks_matches', count=lacking['matches']))
        if 'anchor_matches' in lacking:
            missing.append(
                _messages.text('server.ratings.lacks_anchor', count=lacking['anchor_matches'])
            )
        if 'width' in lacking:
            missing.append(
                _messages.text(
                    'server.ratings.lacks_width',
                    width=f'{lacking["width"]:.0f}',
                    most=f'{report["required"]["width"]:.0f}',
                )
            )
        parts.append(
            _messages.text(
                'server.ratings.lacks',
                model=str(row.get('name') or row['id']),
                what=', '.join(missing),
            )
        )
    return ', '.join(parts)


# The results that the other parts of the arena hand over.


def _keep(result: dict[str, Any]) -> dict[str, Any] | None:
    """Keeps a result in the store, and logs a failure in place of raising it."""
    try:
        return _store.record({**result, 'versions': program_versions()})
    except (OSError, ArgumentError) as error:
        _LOGGER.warning('cuju arena could not keep a result of the ratings, %s', error)
        return None


def play_seed(seed: Any, index: Any = 0, side: str = 'left') -> str:
    """Returns the text that names the play of a match, its seed, its index and the side of
    its first model, so the same match between the same models counts once."""
    return f'{int(seed)}/{int(index)}/{side}'


def requested_match(
    scenario: str,
    teams: list[str],
    difficulty: Any,
    score: list[int],
    recording: str | None,
    seed: int,
    length: int | None = None,
    tournament: Any = None,
    formats: list[Any] | None = None,
    evidence: bool = True,
    snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Keeps the result of a requested match as evidence of its scenario, and returns its row.

    A match of a tournament is handed over by its tournament, so it is left out here, and a
    match of a set length joins the table of its scenario at that length. A team side under a
    submission format is a model of its own under that format, and the row records the format's
    identity and the frameworks its process imported. The row records the installed package
    versions as well. A format that says `[results] evidence = false` keeps no row.

    Args:
        scenario: The scenario.
        teams: The team of the left and of the right side, a registry identifier or `builtin`.
        difficulty: The difficulty that the request gave the built-in AI of each side.
        score: The goals of the left and of the right side.
        recording: The identifier of the recording.
        seed: The seed of the match.
        length: The steps that the request set, or `None`.
        tournament: What the match keeps of its tournament, or `None`.
        formats: What each side kept of its format, as `Policies.formats` gives it, or `None`.
        evidence: Whether every side's format lets its matches be evidence.
        snapshots: The checkpoints already captured by the match, or `None`.
    """
    if tournament is not None or not evidence:
        return None
    given = list(difficulty) if isinstance(difficulty, list | tuple) else [None, None]
    sides = []
    for position, name in enumerate(teams):
        try:
            if name == _teams.BUILTIN:
                sides.append(builtin_side(given[position] if position < len(given) else None))
            elif name == _teams.RANDOM_ACTIONS:
                shown = _teams.shown_name(_teams.side_entry(name))
                sides.append(
                    {'key': _rules.RANDOM_KEY, 'kind': 'random', **_messages.named('name', shown)}
                )
            else:
                snapshot = snapshots[position]['checkpoint'] if snapshots is not None else None
                sides.append(team_side(str(name), snapshot=snapshot))
        except ArgumentError:
            return None
    stored = [_stored(side) for side in sides]
    for side, played in zip(stored, formats or [None, None], strict=True):
        if played is not None:
            side.update(format_side(played))
    return _keep(
        {
            'scenario': table_name(scenario, length),
            'home': stored[0],
            'away': stored[1],
            'score': [int(score[0]), int(score[1])],
            'source': 'match',
            'ref': recording,
            'recording': recording,
            'seed': play_seed(seed),
            'sides': snapshots,
        }
    )


def format_side(played: dict[str, Any]) -> dict[str, Any]:
    """Returns what a ratings side keeps of the format it played under.

    Args:
        played: The side's format as `Policies.formats` gives it, its `identity`, its effective
            `control`, or `None` for the team's own, and the `versions` of its frameworks.
    """
    from .._formats import identity as _identity

    found: dict[str, Any] = {}
    if not _identity.is_default(played['identity']):
        # A side of Kaggle open keeps what every side before formats kept, and reads as it.
        found['submission_format'] = played['identity']
        found['versions'] = dict(played.get('versions') or {})
    suffix = _identity.model_suffix(played['identity'], played.get('control'))
    if suffix is not None:
        found['format'] = suffix
    if played.get('control') is not None:
        found['control'] = played['control']
    return found


def program_versions() -> dict[str, str]:
    """Returns the package versions that each new row records."""
    from importlib.metadata import PackageNotFoundError, version

    import numpy

    import cuju

    from .._version import __version__

    versions = {
        'cuju': str(getattr(cuju, '__version__', 'unknown')),
        'cuju-arena': __version__,
        'numpy': numpy.__version__,
    }
    try:
        versions['cuju-render'] = version('cuju-render')
    except PackageNotFoundError:
        pass
    return versions


def evaluation_result(
    identifier: str,
    made: dict[str, Any],
    found: dict[str, Any],
    entry: dict[str, Any],
    formats: list[Any] | None = None,
) -> dict[str, Any] | None:
    """Keeps one counted match of an evaluation as evidence, and returns its row.

    The policy is rated under its key at the checkpoint of the evaluation, where it has one,
    and a team among the opponents at the checkpoint of its files. A policy without a key, a
    function without a name, is rated nowhere.

    Args:
        identifier: The evaluation.
        made: The plan of the evaluation, with its `policy`, `opponents` and `sides`.
        found: The table `[evaluate]` of the evaluation.
        entry: The match as the schedule counted it, with its `number`, `away`, `meeting` and
            `score`, the goals of the policy first.
        formats: What the policy and the opponent kept of their format, in that order, as
            `Policies.formats` gives it, or `None`.
    """
    policy = made['policy']
    if policy.get('key') is None or entry.get('score') is None:
        return None
    other = next((o for o in made['opponents'] if o['key'] == entry['away']), None)
    if other is None:
        return None
    document = _runs.get(identifier) or {}
    config = document.get('config') or {}
    saved = config.get('checkpoint_snapshots')
    held = (document.get('checkpoints') or [None])[0] or {}
    own = {'key': policy['key'], 'kind': policy['kind'], **_named(policy)}
    if policy['kind'] != 'builtin' and held.get('id'):
        own.update(checkpoint=str(held['id']), label=held.get('label'), step=held.get('step'))
    against = {'key': other['key'], 'kind': other['kind'], **_named(other)}
    snapshots = None
    if saved is not None:
        point = saved['opponents'][other['key']]
        snapshots = [{'checkpoint': saved['policy']}, {'checkpoint': point}]
        if point is not None:
            against.update(checkpoint=point['id'], label=point['label'], step=point['step'])
    elif other['kind'] == 'team':
        against['checkpoint'] = checkpoint_of(other['team'])
    for side, played in zip((own, against), formats or [None, None], strict=True):
        if played is not None:
            side.update(format_side(played))
    sides = made['sides']
    meeting = int(entry.get('meeting') or 0)
    return _keep(
        {
            'scenario': found['scenario'],
            'home': own,
            'away': against,
            'score': list(entry['score']),
            'source': 'evaluation',
            'phase': 'evaluation',
            'ref': f'{identifier}/{entry["number"]}',
            'seed': play_seed(found['seed'], meeting // len(sides), sides[meeting % len(sides)]),
            'actions': {'run': identifier, 'number': int(entry['number'])},
            'sides': snapshots,
        }
    )


def entrant_side(entrant: dict[str, Any], difficulty: Any = None) -> dict[str, Any]:
    """Returns an entrant of a tournament as a side, a team at the checkpoint of its files.

    Args:
        entrant: The entrant, as the rules of the tournaments resolve it.
        difficulty: The difficulty that a match gave the built-in AI of the entrant's side,
            which names the model of an entrant of the built-in AI, or `None` for the
            entrant's own.
    """
    kind = entrant['kind']
    if kind == 'builtin':
        level = difficulty if difficulty is not None else entrant.get('ability')
        return builtin_side(level)
    found = {'key': entrant['key'], 'checkpoint': None, 'kind': kind, **_named(entrant)}
    found['team'] = entrant.get('team')
    found['difficulty'] = None
    if kind == 'team':
        checkpoint = entrant.get('checkpoint') or checkpoint_of(entrant['team'])
        details = _checkpoint_metadata(entrant['key'], checkpoint) if checkpoint is not None else {}
        found.update(checkpoint=checkpoint, label=details.get('label'), step=details.get('step'))
    return found


def tournament_result(
    identifier: str,
    entrants: list[dict[str, Any]],
    match: dict[str, Any],
    score: list[int],
    recording: str | None,
    formats: list[Any] | None = None,
) -> dict[str, Any] | None:
    """Keeps the result of a match of a tournament's draw as evidence, and returns its row.

    A match of a set length joins the table of its scenario at that length. `formats` holds
    what each side kept of its format, as `Policies.formats` gives it, or `None`.
    """
    given = match.get('ability') or [None, None]
    sides = [
        entrant_side({**entrants[match[name]], 'checkpoint': None}, given[position])
        for position, name in enumerate(('home', 'away'))
    ]
    stored = [_stored(side) for side in sides]
    for side, played in zip(stored, formats or [None, None], strict=True):
        if isinstance(played, dict) and played.get('identity') is not None:
            side.update(format_side(played))
    return _keep(
        {
            'scenario': table_name(match['scenario'], match.get('length')),
            'home': stored[0],
            'away': stored[1],
            'score': [int(score[0]), int(score[1])],
            'source': 'tournament',
            'phase': 'main',
            'ref': f'{identifier}/{match["key"]}',
            'recording': recording,
            'seed': play_seed(match['seed']) if match.get('seed') is not None else None,
        }
    )


# The rating runs.


def _spread(row: dict[str, Any] | None) -> tuple[float, float | None]:
    """Returns the rating and the deviation by points that a side starts a run with."""
    if row is None:
        return ANCHOR_RATING, None
    return float(row['points']['rating']), float(row['points']['deviation'])


def create(
    measured: list[dict[str, Any]],
    scenario: str,
    found: dict[str, Any],
    *,
    job: str = 'python',
    owner: str | None = None,
    phase: str = 'rating',
    known: list[dict[str, Any]] | None = None,
    at_once: int | None = None,
    seed: int | None = None,
    submission_format: Any = None,
) -> dict[str, Any]:
    """Writes the document of a new rating run and returns it.

    Args:
        measured: The models to gather evidence for, each a side.
        scenario: The scenario.
        found: The table `[ratings]`.
        job: Who plays the run, `python`, `command`, `server` or `tournament`.
        owner: What the run belongs to, such as `tournament:ID`, or `None`.
        phase: The phase that the results of the run keep, `rating` or `seeding`.
        known: Other models that the measured ones may meet, each a side, which may hold a
            `rating` given beforehand. A known model keeps its rating through the run.
        at_once: The matches at once, in place of `[ratings.run] at_once`.
        seed: The seed of the run, in place of `[ratings.run] seed`.
        submission_format: The format every team of the run plays under, one choice for all of
            them as `_formats.context` reads it, with `per_entrant` keyed by a model's text, or
            `None` for Kaggle open.

    Raises:
        ArgumentError: If no model is left to measure, a model comes twice, there are too
            many, a team does not fit the scenario, or the format or its options do not fit.
    """
    from . import evaluate as _evaluate
    from . import runner as _runner

    run = found['run']
    anchor = anchor_side(found)
    _evaluate.keep_rule(run['keep'])
    sides: dict[str, dict[str, Any]] = {}
    models = set()
    for side in measured:
        model = _store.model(side['key'], side.get('checkpoint'))
        if model == anchor['key']:
            continue
        if model in models:
            raise MessageError('server.ratings.twice', model=repr(model))
        models.add(model)
        key = side['key']
        if phase == 'rating' and side['kind'] == 'team':
            key = side['team']
        sides[key] = {**side, 'measured': True}
    if not sides:
        raise MessageError('server.ratings.nobody')
    if len(sides) > MAX_MODELS:
        raise MessageError('server.ratings.many', most=MAX_MODELS, count=len(sides))
    for side in known or ():
        model = _store.model(side['key'], side.get('checkpoint'))
        if model not in models and model != anchor['key']:
            models.add(model)
            sides[side['key']] = {**side, 'measured': False}
    # The anchor keeps what a caller told of it, such as the entrant that it is.
    told = next((side for side in known or () if side['key'] == anchor['key']), {})
    sides[anchor['key']] = {**told, **anchor, 'measured': False, 'rating': ANCHOR_RATING}
    for side in sides.values():
        if side['kind'] == 'team':
            # A team that cannot play the scenario is refused before the first match.
            _runner.team_setup(side['team'], _teams.BUILTIN, scenario)
    texts = [text for side in sides.values() for text in _entrant_texts(side)]
    chosen = _context.check(submission_format, texts)
    for side in sides.values():
        # A team under a format is the model of that format, whose rows the run starts from.
        suffix = format_suffix(chosen, side)
        if suffix is not None:
            side['format'] = suffix
    made = _table(scenario, found)
    held = {row['id']: row for row in made['models']}
    required = found['evidence']
    start: dict[str, dict[str, Any]] = {}
    for index, (key, side) in enumerate(sides.items()):
        row = held.get(_store.model_of(side))
        rating, deviation = _spread(row)
        if not side['measured'] and side.get('rating') is not None:
            rating, deviation = float(side['rating']), 0.0
        elif not side['measured'] and deviation is None:
            deviation = _schedule.PRIOR_DEVIATION
        side['index'] = index
        start[key] = {
            'rating': rating,
            'deviation': deviation,
            'matches': 0 if row is None else row['matches'],
            'anchor_matches': 0 if row is None else row['anchor_matches'],
        }
    names = list(sides)
    builtin = all(side['kind'] == 'builtin' for side in sides.values())
    mode = run['mode']
    if mode == 'batched' and not builtin:
        raise MessageError('server.ratings.batched')
    if mode == 'auto':
        mode = 'batched' if builtin else 'on_demand'
    each = max(int(run['matches']), int(run['anchor_matches']))
    drawn = seed if seed is not None else run.get('seed')
    if drawn is None:
        drawn = random.SystemRandom().randrange(2**31)
    identifier = _home.new_identifier('rate')
    while _store.run(identifier) is not None:
        identifier = _home.new_identifier('rate')
    document = {
        'id': identifier,
        'kind': 'rating',
        'phase': phase,
        'job': job,
        'owner': owner,
        'created': time.time(),
        'ended': None,
        'state': 'queued',
        'scenario': scenario,
        'seed': int(drawn),
        'anchor': anchor['key'],
        'models': [key for key in names if sides[key]['measured']],
        'sides': sides,
        'start': start,
        'plan': {
            'mode': mode,
            'each': each,
            'most': run.get('most'),
            'at_once': int(at_once or run['at_once']),
            'settle': run.get('settle'),
            'anchor_matches': int(run['anchor_matches']),
            'needs': {
                key: max(1, int(required['matches']) - start[key]['matches'])
                for key in names
                if sides[key]['measured']
            },
            'width': required.get('width'),
            'confidence': float(found['confidence']),
            # A run that picks its pairings by the standard rule keeps no name for it.
            **(
                {}
                if run.get('pairing', _schedule.PAIRING) == _schedule.PAIRING
                else {'pairing': run['pairing']}
            ),
        },
        'settings': copy.deepcopy(found),
        # The plugins that the run's method, evidence rule and pairing rule come from, as they
        # were, so a later read without them still shows the run.
        **_plugin_fields([found['method'], found['rule'], run.get('pairing', _schedule.PAIRING)]),
        'setups': [],
        'episodes': [],
        'queue': None,
        'submission_format': chosen,
        'formats': {
            key: _side_choice(chosen, side)
            for key, side in sides.items()
            if side['kind'] == 'team' and chosen is not None
        },
        'excluded': [],
        'record': {'state': 'none', 'drawn': 0, 'failed': 0},
        'before': _rows_of(sides, held),
        'after': None,
        **_messages.entry_fields(None),
    }
    _store.save_run(document)
    return document


def _entrant_texts(side: dict[str, Any]) -> list[str]:
    """Returns the texts that may name a side in `per_entrant`, its team and `team:` before it."""
    if side.get('kind') != 'team' or not isinstance(side.get('team'), str):
        return []
    return [side['team'], f'team:{side["team"]}', str(side['key']), f'team:{side["key"]}']


def _side_choice(chosen: dict[str, Any] | None, side: dict[str, Any]) -> dict[str, Any] | None:
    """Returns the choice of one team side of a run, its own options of `per_entrant` in it."""
    if chosen is None:
        return None
    own = next(
        (text for text in _entrant_texts(side) if text in (chosen.get('per_entrant') or {})),
        None,
    )
    return _context.for_entrant(chosen, own)


def _rows_of(sides: dict[str, dict[str, Any]], held: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Returns what the table holds of each side of a run, by key, `None` where it has none."""
    found: dict[str, Any] = {}
    for key, side in sides.items():
        row = held.get(_store.model_of(side))
        found[key] = (
            None
            if row is None
            else {
                name: row[name]
                for name in (
                    'rating',
                    'low',
                    'high',
                    'method',
                    'points',
                    'bradley_terry',
                    'matches',
                    'anchor_matches',
                    'rated',
                    'lacking',
                )
            }
        )
    return found


def _schedule_of(document: dict[str, Any]) -> _schedule.Schedule:
    """Returns the schedule of a run, taken up from the state that its document keeps."""
    plan, sides, start = document['plan'], document['sides'], document['start']
    names = list(sides)
    fixed = [key for key in names if not sides[key]['measured']]
    state = copy.deepcopy(document.get('queue'))
    if state and state.get('playing'):
        # The matches that a stop cut play again, before those that waited.
        state['next'] = [*state['playing'], *(state.get('next') or [])]
        state['playing'] = []
    return _schedule.Schedule(
        names,
        mode=plan['mode'],
        seed=document['seed'],
        ratings={key: start[key]['rating'] for key in names},
        deviations={
            key: start[key]['deviation'] for key in names if start[key]['deviation'] is not None
        },
        fixed=fixed,
        anchor=document['anchor'],
        anchor_matches=plan['anchor_matches'],
        needs=plan['needs'],
        width=plan['width'],
        each=plan['each'],
        most=plan['most'],
        at_once=plan['at_once'],
        settle=plan['settle'],
        confidence=plan['confidence'],
        pairing=plan.get('pairing', _schedule.PAIRING),
        state=state,
        # A format in a process of its own plays no more matches at once than the CPU sets fit.
        threads=_context.threads(document.get('submission_format')),
    )


def progress_of(document: dict[str, Any]) -> dict[str, Any]:
    """Returns where a rating run stands, as plain data that a view shows at any moment.

    The mapping holds the `run`, its `phase`, `rating` or `seeding`, its `state`, the
    `scenario`, the key of the `anchor`, the `mode` of its matches, `played`, the matches
    whose result counts, `total`, the matches that it plays, `most`, `settled` and `done`,
    `playing` and `next`, the pairings under way and to come, `ratings`, `history` and
    `entrants`. `ratings` holds the rating of each side by points as the run moved it, with
    its `low` and `high` bound, its `matches` in the run and whether it is `measured`, and
    `method` says so, `points`. `history` holds each counted match in order with its `number`,
    `home`, `away`, `meeting`, `seed`, `index`, `score` and the `ratings` of its two sides
    right after it. `entrants` holds for each key the name, the `kind`, the `index`, the
    `checkpoint` and the `model` of the side, and whether it is `measured`. It reads the
    document alone, so it holds after a restart as well.
    """
    queue = document.get('queue') or {}
    state = document.get('state')
    sides = document.get('sides') or {}
    return {
        'run': document.get('id'),
        'phase': document.get('phase') or 'rating',
        'state': state,
        'scenario': document.get('scenario'),
        'anchor': document.get('anchor'),
        'method': 'points',
        'mode': queue.get('mode') or (document.get('plan') or {}).get('mode'),
        'played': queue.get('count') or 0,
        'total': queue.get('total'),
        'most': queue.get('most'),
        'settled': bool(queue.get('settled')),
        'done': state in ENDED,
        'playing': queue.get('playing') or [],
        'next': queue.get('next') or [],
        'ratings': queue.get('intervals') or {},
        'history': queue.get('played') or [],
        'entrants': {
            key: {
                **_named(side),
                'kind': side.get('kind'),
                'index': side.get('index'),
                'checkpoint': side.get('checkpoint'),
                'model': _store.model_of(side),
                'label': side.get('label'),
                'step': side.get('step'),
                'measured': bool(side.get('measured')),
            }
            for key, side in sides.items()
        },
    }


def _plugin_fields(names: list[str]) -> dict[str, Any]:
    """Returns `plugins`, the `{name, version}` of the plugins behind some names, or nothing."""
    used = _plugins.stamp(names)
    return {'plugins': used} if used else {}


def _plugin_fields_of(document: dict[str, Any]) -> dict[str, Any]:
    """Returns the plugins that a stored run names, in the shape `_plugin_fields` gives."""
    used = _plugins.stamps(document.get('plugins'))
    return {'plugins': used} if used else {}


def view(document: dict[str, Any]) -> dict[str, Any]:
    """Returns a rating run as a command, a route and `rate` answer it.

    The mapping holds the `id` of the run, its `state`, `created` and `ended`, its `phase`,
    `job` and `owner`, its `scenario`, `seed` and `anchor`, the keys of its `models`, its
    `settings`, `progress`, as `progress_of` returns it, `matches`, `record`, `before` and
    `after`, its `submission_format`, `None` for Kaggle open, the models its format refused
    under `excluded`, each with its `problems`, and the error fields of a run that failed.
    `matches` holds each match with its `number`, `home`, `away`, `index`, `seed`, `score`,
    `length`, the `digest` and the file
    of its `actions`, whether it is kept in `keep`, and its `recording` once it is drawn.
    `record` tells of the drawing, its `state` and the matches `drawn` and `failed`. `before`
    and `after` hold what the table of the scenario held of each side before the run and
    holds now, with the rating by each method, the matches and what the side is lacking.
    """
    after = document.get('after')
    with contextlib.suppress(ArgumentError, ConfigurationError, OSError, KeyError):
        made = _table(document['scenario'], document['settings'])
        after = _rows_of(document['sides'], {row['id']: row for row in made['models']})
    return {
        'id': document['id'],
        'kind': 'rating',
        'state': document.get('state'),
        'created': document.get('created'),
        'ended': document.get('ended'),
        'phase': document.get('phase'),
        'job': document.get('job'),
        'owner': document.get('owner'),
        'scenario': document.get('scenario'),
        'seed': document.get('seed'),
        'anchor': document.get('anchor'),
        'models': document.get('models') or [],
        'settings': document.get('settings') or {},
        **_plugin_fields_of(document),
        'progress': progress_of(document),
        'matches': document.get('episodes') or [],
        'record': document.get('record') or {},
        'before': document.get('before'),
        'after': after,
        'submission_format': document.get('submission_format'),
        'excluded': document.get('excluded') or [],
        'error': document.get('error'),
        'error_id': document.get('error_id'),
        'error_values': document.get('error_values'),
    }


def _setup_of(document: dict[str, Any], home: str, away: str) -> dict[str, Any]:
    """Returns what a match of two sides of a run is created with, as an evaluation keeps it."""
    from . import runner as _runner

    sides = document['sides']
    first, second = sides[home], sides[away]
    scenario = document['scenario']
    pair = [first.get('difficulty'), second.get('difficulty')]
    if first['kind'] == 'builtin' and second['kind'] == 'builtin':
        return {
            'home': home,
            'away': away,
            'side': 'left',
            'asset': _runner.asset_of(scenario, (0, 0)),
            'agents': [0, 0],
            'options': {},
            'left': None,
            'right': None,
            'difficulty': pair,
        }
    names = [first.get('team') or _teams.BUILTIN, second.get('team') or _teams.BUILTIN]
    made = _runner.team_setup(names[0], names[1], scenario)
    return {
        'home': home,
        'away': away,
        'side': 'left',
        'asset': _runner.asset_of(scenario, tuple(made['agents'])),
        'agents': list(made['agents']),
        'options': {'action_set': made['action_set']},
        'left': names[0],
        'right': names[1],
        'difficulty': pair,
    }


class _Engine:
    """One run of the matches of a rating run, from the state that its document keeps."""

    def __init__(
        self,
        document: dict[str, Any],
        player: Callable[[dict[str, Any]], Any] | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        stopped: Callable[[], bool] | None = None,
    ) -> None:
        self.document = document
        self.id = document['id']
        self.player = player
        self.progress = progress
        self.cancelled = cancelled or (lambda: False)
        self.stopped = stopped or (lambda: False)
        # What `evaluate._Play._child` reads of the object that plays a match of teams.
        self.lock = threading.Lock()
        self.processes: set[subprocess.Popen] = set()
        # The match workers of the run, which `_on_demand` holds while it plays.
        self.workers: Any = None
        self.settings = {'scenario': document['scenario'], **document['settings']['run']}
        for key, side in document['sides'].items():
            if side['kind'] == 'team' and checkpoint_of(side['team']) != side.get('checkpoint'):
                raise MessageError('server.ratings.changed', team=repr(key))
        self.schedule = _schedule_of(document)
        # What each side kept of its format in a match, by the match's number, until it counts.
        self.formats: dict[int, list[Any]] = {}

    def _save(self) -> None:
        self.document['queue'] = self.schedule.state()
        self.document['episodes'].sort(key=lambda entry: entry['number'])
        held = _store.run(self.id)
        if held is not None and held.get('state') == 'cancelled':
            # A cancel from another process stands, and the run stops at its next look.
            self.document['state'] = 'cancelled'
        _store.save_run(self.document)
        if self.progress is not None:
            self.progress(progress_of(self.document))

    def _place(self, pairing: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Returns the index and the content of the setup of a pairing, made when new."""
        setups = self.document['setups']
        for index, setup in enumerate(setups):
            if setup['home'] == pairing['home'] and setup['away'] == pairing['away']:
                return index, setup
        setups.append(_setup_of(self.document, pairing['home'], pairing['away']))
        return len(setups) - 1, setups[-1]

    def _one(self, pairing: dict[str, Any], setup: dict[str, Any]) -> Any:
        """Plays one match and returns what it gave, its goals, its length and its actions."""
        from . import evaluate as _evaluate
        from . import runner as _runner

        spec = {
            'number': pairing['number'],
            'home': pairing['home'],
            'away': pairing['away'],
            'scenario': self.document['scenario'],
            'seed': self.document['seed'],
            'index': pairing['index'],
            'setup': setup,
            'device': self.settings['device'],
            'threads': int(self.settings['threads']),
        }
        if self.player is not None:
            return self.player(spec)
        if setup['left'] is None:
            return _runner.play_batch(
                setup['asset'],
                tuple(setup['agents']),
                setup['difficulty'],
                1,
                spec['seed'],
                options=setup['options'],
                device=spec['device'],
                threads=spec['threads'],
                first_index=spec['index'],
                cancelled=self.cancelled,
            )
        # A team plays in a process of its own, as an evaluation on demand plays it, a fresh
        # child or a match worker of the run where its format keeps its process for the run,
        # under the run's format, with its terminals kept under the run and the match.
        formats = self.document.get('formats') or {}
        return _evaluate._Play._child(
            self,
            {
                'left': setup['left'],
                'right': setup['right'],
                'scenario': spec['scenario'],
                'seed': spec['seed'],
                'index': spec['index'],
                'difficulty': setup['difficulty'],
                'device': spec['device'],
                'submission_format': [formats.get(pairing['home']), formats.get(pairing['away'])],
                'kind': 'rating',
                'terminal': f'{self.id}-m{pairing["number"]}',
            },
        )

    def _count(
        self, pairing: dict[str, Any], setup_index: int, played: Any, error: str | None
    ) -> None:
        """Keeps what one match gave, hands its result to the schedule and to the store."""
        from . import runner as _runner

        number = pairing['number']
        sides = self.document['sides']
        episode: dict[str, Any] = {
            'number': number,
            'home': pairing['home'],
            'away': pairing['away'],
            'sides': _match_snapshots(sides, pairing),
            'setup': setup_index,
            'index': pairing['index'],
            'seed': self.document['seed'],
            'score': None,
            'length': None,
            'digest': None,
            'actions': None,
            'keep': False,
            'recording': None,
            'time': time.time(),
        }
        fields: dict[str, Any] = {'time': episode['time']}
        score = None
        forfeit = getattr(played, 'forfeit', None)
        if forfeit is not None:
            # A forfeit that the format decided ends the match, which is never evidence.
            episode['forfeit'] = forfeit
            error = forfeit.get('reason') or error
            played = None
        if played is not None:
            self.formats[number] = list(getattr(played, 'formats', None) or [None, None])
            name = f'm{number}'
            (episode['digest'],) = _runner.write_chunk(self.id, name, [number], played)
            score = [int(played.goals[0][0]), int(played.goals[0][1])]
            episode.update(score=score, length=int(played.lengths[0]), actions=f'{name}.npz')
        else:
            episode['error'] = fields['error'] = error or _messages.text(
                'server.tournament.no_result'
            )
        # A match that a stop cut after it was kept plays again and takes the place of itself.
        self.document['episodes'] = [
            held for held in self.document['episodes'] if held['number'] != number
        ]
        self.document['episodes'].append(episode)
        for entry in self.schedule.finish(number, score, **fields):
            if entry.get('score') is None:
                continue
            for key in (entry['home'], entry['away']):
                # A result belongs to the checkpoint that the run began with and to no other.
                team = sides[key]
                if team['kind'] == 'team' and checkpoint_of(team['team']) != team.get('checkpoint'):
                    raise MessageError('server.ratings.changed', team=repr(key))
            stored = [_stored(sides[entry['home']]), _stored(sides[entry['away']])]
            for side, kept in zip(
                stored, self.formats.pop(entry['number'], [None, None]), strict=True
            ):
                if kept is not None:
                    side.update(format_side(kept))
            _keep(
                {
                    'scenario': self.document['scenario'],
                    'home': stored[0],
                    'away': stored[1],
                    'score': list(entry['score']),
                    'source': 'rating',
                    'phase': self.document.get('phase') or 'rating',
                    'ref': f'{self.id}/{entry["number"]}',
                    'time': entry.get('time'),
                    'seed': play_seed(self.document['seed'], entry['index']),
                    'actions': {'run': self.id, 'number': int(entry['number'])},
                    'sides': _match_snapshots(sides, entry),
                }
            )

    def _start(self, pairing: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        # A pair plays its matches on the indexes of the seed rule in the order it meets.
        pairing['index'] = pairing['meeting']
        pairing['seed'] = self.document['seed']
        return self._place(pairing)

    def _batched(self) -> None:
        """Plays every match of a batched run, the matches of each pair at once in one batch."""
        from . import runner as _runner

        due = self.schedule.due()
        groups: dict[int, list[dict[str, Any]]] = {}
        for pairing in due:
            index, _ = self._start(pairing)
            groups.setdefault(index, []).append(pairing)
        self._save()
        for index, group in groups.items():
            if self.stopped():
                raise _Stop
            if self.cancelled():
                raise KeyboardInterrupt
            setup = self.document['setups'][index]
            if self.player is not None or setup['left'] is not None:
                for pairing in group:
                    self._finish_one(pairing, index, setup)
                continue
            indexes = [pairing['index'] for pairing in group]
            first = min(indexes)
            played = _runner.play_batch(
                setup['asset'],
                tuple(setup['agents']),
                setup['difficulty'],
                max(indexes) - first + 1,
                self.document['seed'],
                options=setup['options'],
                device=self.settings['device'],
                threads=int(self.settings['threads']),
                first_index=first,
                cancelled=self.cancelled,
            )
            for pairing in sorted(group, key=lambda entry: entry['number']):
                column = pairing['index'] - first
                one = _runner.Played(
                    [played.goals[column]],
                    [played.lengths[column]],
                    played.actions[:, [column]],
                    seconds=played.seconds,
                )
                self._count(pairing, index, one, None)
            self._save()

    def _finish_one(self, pairing: dict[str, Any], index: int, setup: dict[str, Any]) -> None:
        played, error = None, None
        try:
            played = self._one(pairing, setup)
        except Exception as failure:
            error = _shown(failure)
        self._count(pairing, index, played, error)
        self._save()

    def _on_demand(self) -> None:
        """Plays the matches on demand, a few at a time, until the schedule has ended.

        Each slot of the schedule leases a match worker for a match whose format keeps its
        process for the run, so a model loads once for each slot, and the workers end with the
        run, whatever ends it.
        """
        from . import evaluate as _evaluate

        if self.player is not None:
            self._on_demand_slots()
            return
        with _evaluate.match_workers(self):
            self._on_demand_slots()

    def _on_demand_slots(self) -> None:
        waiting: dict[Any, tuple[dict[str, Any], int]] = {}
        workers = self.schedule.at_once
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            try:
                while True:
                    if self.stopped():
                        raise _Stop
                    if self.cancelled() or self.document.get('state') == 'cancelled':
                        raise KeyboardInterrupt
                    due = self.schedule.due()
                    for pairing in due:
                        index, setup = self._start(pairing)
                        waiting[executor.submit(self._one, pairing, setup)] = (pairing, index)
                    if due:
                        self._save()
                    if not waiting:
                        break
                    done, _ = concurrent.futures.wait(
                        waiting, timeout=0.5, return_when=concurrent.futures.FIRST_COMPLETED
                    )
                    for future in done:
                        pairing, index = waiting.pop(future)
                        played, error = None, None
                        try:
                            played = future.result()
                        except Exception as failure:
                            error = _shown(failure)
                        self._count(pairing, index, played, error)
                    if done:
                        self._save()
            except BaseException:
                with self.lock:
                    for process in list(self.processes):
                        process.kill()
                for future in waiting:
                    future.cancel()
                raise

    def run(self) -> None:
        """Plays every match that the schedule has left."""
        if self.schedule.mode == 'batched':
            self._batched()
        else:
            self._on_demand()
        self._save()


def _shown(error: BaseException) -> str:
    if isinstance(error, ArgumentError | RuntimeError):
        return str(error)
    return _messages.described(error)


def _outcome_episodes(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Returns the matches of a run as the keeping rule of the evaluations reads them."""
    found = []
    for episode in document.get('episodes') or []:
        score = episode.get('score')
        entry = {'number': episode['number'], 'outcome': None}
        if score is not None:
            entry.update(
                goals_for=score[0],
                goals_against=score[1],
                outcome='win' if score[0] > score[1] else 'loss' if score[0] < score[1] else 'draw',
            )
        found.append(entry)
    return found


def _draw_one(document: dict[str, Any], episode: dict[str, Any], entry: dict[str, Any]) -> str:
    """Draws one match of a run again from its actions and returns its recording.

    The match goes through the drawing of the evaluations, as the match of the home side
    against the away side, so it is held to its score and its length on the way.
    """
    from . import replay as _replay

    sides = document['sides']
    home, away = sides[episode['home']], sides[episode['away']]
    checkpoint = (
        {'id': home['checkpoint'], 'label': home.get('label') or episode['home']}
        if home.get('checkpoint')
        else None
    )
    shaped = {
        'id': document['id'],
        'kind': 'evaluation',
        'scenarios': [document['scenario']],
        'opponents': [_named(away)],
        'checkpoints': [checkpoint] if checkpoint else [],
        'config': {
            'setups': [document['setups'][episode['setup']]],
            'policy': _named(home),
            'evaluate': {'record': {'annotations': False}},
        },
    }
    match = {
        **episode,
        'setup': 0,
        'opponent': 0,
        'goals_for': episode['score'][0],
        'goals_against': episode['score'][1],
    }
    recording, _ = _replay._draw(shaped, match, entry)
    return recording


def draw(identifier: str, numbers: list[int] | None = None) -> dict[str, Any]:
    """Draws matches of a rating run as recordings, from the seed and the actions of each.

    Args:
        identifier: The run.
        numbers: The numbers of the matches, or `None` for the matches that the run keeps and
            has yet to draw.

    Returns:
        A mapping of the `id` of the run, `drawn`, the recording of each match drawn by its
        number, and `failed`, the reason of each match that could not be drawn by its number.

    Raises:
        ArgumentError: If there is no such run, or it holds no match of a number.
    """
    from . import runner as _runner

    document = _known(identifier)
    episodes = {episode['number']: episode for episode in document.get('episodes') or []}
    if numbers is None:
        numbers = [n for n, e in episodes.items() if e.get('keep') and not e.get('recording')]
    for number in numbers:
        if number not in episodes or episodes[number].get('score') is None:
            raise MessageError('server.ratings.no_match', number=repr(number))
    found = _runner.episodes_of(identifier)
    drawn: dict[str, str] = {}
    failed: dict[str, str] = {}
    for number in numbers:
        episode = episodes[number]
        if episode.get('recording'):
            drawn[str(number)] = episode['recording']
            continue
        entry = found.get(number)
        try:
            if entry is None:
                raise MessageError('server.evaluate.actions_missing', number=number)
            if _runner.episode_digest(entry['actions']) != episode.get('digest'):
                raise MessageError('server.evaluate.actions_changed', number=number)
            recording = _draw_one(document, episode, entry)
        except Exception as error:
            failed[str(number)] = _shown(error)
            continue
        drawn[str(number)] = recording
        _store.point('rating', f'{identifier}/{number}', recording)
        # The document is read again, since a run that still plays writes it meanwhile.
        document = _known(identifier)
        for held in document.get('episodes') or []:
            if held['number'] == number:
                held['recording'] = recording
        record = dict(document.get('record') or {})
        record['drawn'] = int(record.get('drawn') or 0) + 1
        document['record'] = record
        _store.save_run(document)
    if failed:
        document = _known(identifier)
        record = dict(document.get('record') or {})
        record['failed'] = int(record.get('failed') or 0) + len(failed)
        document['record'] = record
        _store.save_run(document)
    return {'id': identifier, 'drawn': drawn, 'failed': failed}


def check(identifier: str, device: str = 'cpu', threads: int = 0) -> dict[str, Any]:
    """Plays every match of a rating run again from its kept actions and confirms each result.

    The kept actions of each match are first held to the digest that the run keeps, and each
    match then plays again on its seed and its index, with the kept actions in place of the
    teams, and its score and its length are held to those of the run.

    Returns:
        A mapping of the `id` of the run, the matches `checked`, whether every one is the
        `same`, and `problems`, each with the `number` of its match, the `problem`, one of
        `missing`, `changed`, `score` and `length`, and what was `expected` and `found`.

    Raises:
        ArgumentError: If there is no such run.
    """
    import numpy as np

    from . import runner as _runner

    document = _known(identifier)
    found = _runner.episodes_of(identifier)
    problems: list[dict[str, Any]] = []
    episodes = [e for e in document.get('episodes') or [] if e.get('score') is not None]
    for episode in episodes:
        entry = found.get(episode['number'])
        if entry is None:
            problems.append({'number': episode['number'], 'problem': 'missing'})
            continue
        digest = _runner.episode_digest(entry['actions'])
        if digest != episode.get('digest'):
            problems.append(
                {
                    'number': episode['number'],
                    'problem': 'changed',
                    'expected': episode.get('digest'),
                    'found': digest,
                }
            )
            continue
        setup = document['setups'][episode['setup']]
        rows = np.ascontiguousarray(entry['actions'], dtype=np.uint8)
        width = sum(setup['agents'])
        played = _runner.play_batch(
            setup['asset'],
            tuple(setup['agents']),
            setup['difficulty'],
            1,
            int(episode['seed']),
            actions=rows.reshape(len(rows), 1, width),
            options=setup.get('options') or {},
            device=device,
            threads=threads,
            first_index=int(episode['index']),
        )
        if played.goals[0] != episode['score']:
            problems.append(
                {
                    'number': episode['number'],
                    'problem': 'score',
                    'expected': episode['score'],
                    'found': played.goals[0],
                }
            )
        elif played.lengths[0] != episode['length']:
            problems.append(
                {
                    'number': episode['number'],
                    'problem': 'length',
                    'expected': episode['length'],
                    'found': played.lengths[0],
                }
            )
    return {
        'id': identifier,
        'checked': len(episodes),
        'same': not problems,
        'problems': problems,
    }


def _known(identifier: str) -> dict[str, Any]:
    document = _store.run(identifier)
    if document is None:
        raise MessageError('server.ratings.no_run', identifier=repr(identifier))
    return document


def execute(
    identifier: str,
    *,
    player: Callable[[dict[str, Any]], Any] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    stopped: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Plays a stored rating run on from its document, to its end, and returns its document.

    Args:
        identifier: The run.
        player: Plays one match and returns its goals, its length and its actions as a
            `runner.Played`, in place of the batch and the process of a team. It receives a
            mapping of the `number`, the `home` and `away` key, the `scenario`, the `seed`,
            the `index` and the `setup` of the match. A test passes a function of its own.
        progress: A function called after every change with what `progress_of` returns.
        cancelled: A function that says whether to stop, which ends the run as cancelled.
        stopped: A function that says whether to leave off, which keeps the run as it stands,
            so a later call plays it on.

    Raises:
        ArgumentError: If there is no such run, it has ended, or the files of a team changed
            since the run began.
    """
    from . import evaluate as _evaluate

    document = _known(identifier)
    if document.get('state') in ENDED:
        raise MessageError('server.ratings.ended', state=document['state'])
    document['state'] = 'running'
    _store.save_run(document)
    try:
        if player is None:
            _check_entrants(document)
        engine = _Engine(document, player, progress, cancelled, stopped)
        engine.run()
        run = document['settings']['run']
        kept = _evaluate.kept_episodes(_outcome_episodes(document), run['keep'], int(run['limit']))
        for episode in document['episodes']:
            episode['keep'] = episode['number'] in kept
        if document.get('state') != 'cancelled':
            document.update(state='done', ended=time.time())
        document['record'] = {
            'state': 'waiting' if kept else 'none',
            'drawn': 0,
            'failed': 0,
        }
        _store.save_run(document)
    except _Stop:
        document['state'] = 'queued'
        with contextlib.suppress(OSError, ArgumentError):
            _store.save_run(document)
        return document
    except BaseException as error:
        cancelled_now = isinstance(error, KeyboardInterrupt)
        document.update(state='cancelled' if cancelled_now else 'failed', ended=time.time())
        if not cancelled_now:
            document.update(_messages.entry_fields(error, _shown(error)))
        with contextlib.suppress(OSError, ArgumentError):
            _store.save_run(document)
        if cancelled_now and cancelled is not None:
            return document
        raise
    if kept and player is None:
        draw(identifier)
        document = _known(identifier)
        record = dict(document.get('record') or {})
        record['state'] = 'done'
        document['record'] = record
    with contextlib.suppress(ArgumentError, ConfigurationError, OSError):
        made = _table(document['scenario'], document['settings'])
        document['after'] = _rows_of(document['sides'], {row['id']: row for row in made['models']})
    _store.save_run(document)
    if progress is not None:
        progress(progress_of(document))
    return document


def _check_entrants(document: dict[str, Any]) -> None:
    """Runs the pre-check of the run's format for each team it measures, once, at its start.

    A team that the format refuses leaves the run with its reasons under `excluded`, and its
    matches never play. A kept verdict answers at once.

    Raises:
        ArgumentError: If the format refused every team that the run measures.
    """
    if document.get('submission_format') is None or document.get('queue') is not None:
        return
    if document.get('checked'):
        return
    sides = document['sides']
    teams = [key for key, side in sides.items() if side['kind'] == 'team']
    refused = {}
    for key in teams:
        found = _context.ensure(
            (document.get('formats') or {}).get(key), [sides[key]['team']], document['scenario']
        )
        if found:
            refused[key] = found[sides[key]['team']]
    for key, reasons in refused.items():
        document['excluded'].append({'model': key, **_named(sides[key]), **reasons})
        sides.pop(key)
        document['start'].pop(key, None)
        document['plan']['needs'].pop(key, None)
        document['formats'].pop(key, None)
    document['models'] = [key for key in document['models'] if key not in refused]
    document['checked'] = True
    _store.save_run(document)
    if not document['models']:
        raise MessageError('server.ratings.refused')


def rate(
    models: Any,
    scenario: str,
    config: Any = None,
    overrides: Any = (),
    *,
    progress: Callable[[dict[str, Any]], None] | None = None,
    submission_format: Any = None,
) -> dict[str, Any]:
    """Gathers evidence for models on one scenario and returns the rating run.

    Every result of the run joins the table of the scenario, where each model is a team at
    the checkpoint of its files or the built-in AI at a difficulty. Each model first plays the
    anchor of the tables, the built-in AI at the difficulty of `[ratings] anchor`, as often
    as `[ratings.run] anchor_matches` says, so its rating is tied to the fixed point of the
    scale by matches of its own. The run then picks each next pairing among the models where
    their order is least settled, and ends once the order is settled or the most matches are
    played. It keeps the seed and the actions of every match, so any match can be drawn as a
    recording later, and it goes on after a stop with the same pairings.

    Args:
        models: The models, each the identifier of a team of the registry, alone or after
            `team` and a colon, or `builtin` with a colon and a difficulty, such as
            `builtin:0.3`. One text names one model.
        scenario: The exact name of the scenario, without agent counts.
        config: The settings, a file, a mapping of tables, a `Config` or a list of them, laid
            over the shipped defaults as `cuju.arena.config` describes.
        overrides: Settings such as `ratings.run.matches=12`, laid over `config`.
        progress: A function called after every change with where the run stands, the mapping
            under `progress` of the answer.
        submission_format: The format every team plays under, a format's identifier or a
            mapping `{id, preset, options, per_entrant}`, with `per_entrant` keyed by the text of
            a model, or `None` for Kaggle open. Each team must pass its pre-check at the start,
            and a team it refuses leaves the run, under `excluded`.

    Returns:
        The run as a mapping, with its `id`, its `state`, its `progress`, its `matches`, and
        under `after` the rating of each model in the table of the scenario by both methods,
        with its interval, its matches and what it still lacks.

    Raises:
        ArgumentError: If a model is unknown, comes twice or cannot play the scenario, or no
            model is left beside the anchor.
        ConfigurationError: If a setting is invalid.
        ChoiceError: If the settings name an unknown key.
    """
    found = settings(config, overrides)
    named = [models] if isinstance(models, str) else list(models or ())
    document = create(
        [side_of(name) for name in named], scenario, found, submission_format=submission_format
    )
    return view(execute(document['id'], progress=progress))


def resume(
    identifier: str, progress: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    """Plays a stored rating run on from its document, as the server's child process does.

    Raises:
        ArgumentError: If the run is unknown or has ended.
    """
    return view(execute(identifier, progress=progress))


def runs() -> list[dict[str, Any]]:
    """Returns the rating runs, newest first, each without its matches.

    Each entry holds the `id`, the `state`, `created` and `ended` of a run, its `phase`, its
    `owner`, its `scenario`, the keys of its `models`, the `played` matches of its `total`
    and whether its order is `settled`.
    """
    found = []
    for document in _store.runs():
        queue = document.get('queue') or {}
        found.append(
            {
                'id': document['id'],
                'state': document.get('state'),
                'created': document.get('created'),
                'ended': document.get('ended'),
                'phase': document.get('phase'),
                'owner': document.get('owner'),
                'scenario': document.get('scenario'),
                'models': document.get('models') or [],
                'played': queue.get('count') or 0,
                'total': queue.get('total'),
                'settled': bool(queue.get('settled')),
            }
        )
    return found


def get(identifier: str) -> dict[str, Any]:
    """Returns one rating run, as `view` describes it.

    Raises:
        ArgumentError: If there is no such run.
    """
    return view(_known(identifier))


def remove(identifier: str) -> bool:
    """Deletes a rating run that has ended, its document and its kept actions.

    The results of the run stay in the table, and its recordings stay in the library.

    Returns:
        Whether there was one.

    Raises:
        ArgumentError: If the run still plays.
    """
    from . import runner as _runner

    document = _store.run(identifier)
    if document is None:
        return False
    if document.get('state') not in ENDED:
        raise MessageError('server.ratings.running', state=document.get('state'))
    _runner.remove_actions(identifier)
    return _store.remove_run(identifier)


# The rating runs of the server, played one at a time, each in a child process.


def request(document: Any) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Returns the models, the scenario and the settings that the body of a request names.

    The body is a mapping of `models`, a list of texts as `rate` takes them, `scenario`, and
    optionally `ratings`, keys of the table `[ratings]` laid over the settings of the server, and
    `submission_format`, the format of every team, which `create` takes.

    Raises:
        ArgumentError: If the body is no such mapping or a setting does not fit.
    """
    if not isinstance(document, dict):
        raise MessageError('server.json_object')
    models, scenario = document.get('models'), document.get('scenario')
    if not isinstance(models, list) or not isinstance(scenario, str) or not scenario:
        raise MessageError('server.ratings.request')
    given = document.get('ratings') or {}
    allowed = {'models', 'scenario', 'ratings', 'submission_format'}
    if not isinstance(given, dict) or set(document) - allowed:
        raise MessageError('server.ratings.request')
    try:
        found = settings({'ratings': given})
    except (ConfigurationError, ArgumentError, LookupError) as error:
        raise MessageError('server.ratings.config', reason=str(error)) from error
    return [side_of(name) for name in models], scenario, found


def command(identifier: str) -> list[str]:
    """Returns the command of the child process that plays a stored rating run to its end."""
    return [*_home.python_command(), '-m', 'cuju.arena._evaluate.ratings', '--run', identifier]


class _Jobs:
    """The rating runs that a page or an agent asked the server for, played one at a time."""

    def __init__(self, starter: Callable[[str], subprocess.Popen] | None = None) -> None:
        """Takes up the rating runs of the server that a stop left waiting or under way.

        Args:
            starter: Starts the process that plays a stored run, by default the child process
                of `command`. A test passes a function of its own.
        """
        self._lock = threading.RLock()
        self._waiting: list[str] = []
        self._process: tuple[str, subprocess.Popen] | None = None
        self._wake = threading.Event()
        self._starter = starter or self._start
        for document in reversed(_store.runs()):
            if document.get('job') == 'server' and document.get('state') in ('queued', 'running'):
                self._waiting.append(document['id'])
                self._wake.set()
        threading.Thread(target=self._run, daemon=True, name='cuju-ratings').start()

    @staticmethod
    def _start(identifier: str) -> subprocess.Popen:
        return subprocess.Popen(
            command(identifier),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            cwd=_home.process_folder(),
            env=_home.process_environment(),
        )

    def add(self, document: Any) -> dict[str, Any]:
        """Queues the rating run that a request names and returns it as it stands."""
        measured, scenario, found = request(document)
        made = create(
            measured,
            scenario,
            found,
            job='server',
            submission_format=document.get('submission_format'),
        )
        with self._lock:
            self._waiting.append(made['id'])
        self._wake.set()
        return view(made)

    def cancel(self, identifier: str) -> bool:
        """Cancels a waiting or running rating run of the server, and says whether it did."""
        with self._lock:
            document = _store.run(identifier)
            if document is None or document.get('state') in ENDED:
                return False
            if identifier in self._waiting:
                self._waiting.remove(identifier)
            document.update(state='cancelled', ended=time.time())
            _store.save_run(document)
            process = self._process
        if process is not None and process[0] == identifier:
            process[1].kill()
        return True

    def _run(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            while True:
                with self._lock:
                    identifier = self._waiting.pop(0) if self._waiting else None
                if identifier is None:
                    break
                self._play(identifier)

    def _play(self, identifier: str) -> None:
        try:
            process = self._starter(identifier)
        except OSError as error:
            _fail(identifier, _messages.entry_fields(error, _messages.described(error)))
            return
        with self._lock:
            self._process = (identifier, process)
            document = _store.run(identifier)
            stopped = document is None or document.get('state') in ENDED
        if stopped:
            process.kill()
        tail: collections.deque[str] = collections.deque(maxlen=20)
        if process.stderr is not None:
            tail.extend(process.stderr)
        code = process.wait()
        with self._lock:
            self._process = None
        document = _store.run(identifier)
        if document is not None and document.get('state') not in ENDED:
            _fail(
                identifier,
                _messages.exit_fields('server.ratings.exit', code, ''.join(tail).strip()),
            )


def _fail(identifier: str, fields: dict[str, Any]) -> None:
    """Ends a rating run as failed with the error fields of its failure."""
    document = _store.run(identifier)
    if document is None:
        return
    document.update(state='failed', ended=time.time(), **fields)
    with contextlib.suppress(OSError, ArgumentError):
        _store.save_run(document)


_JOBS: _Jobs | None = None
_JOBS_LOCK = threading.Lock()


def jobs() -> _Jobs:
    """Returns the server's queue of rating runs, started on first use."""
    global _JOBS
    with _JOBS_LOCK:
        if _JOBS is None:
            _JOBS = _Jobs()
        return _JOBS


def main(arguments: list[str] | None = None) -> None:
    """Plays the stored rating run that the command line names to its end."""
    import argparse

    parser = argparse.ArgumentParser(prog='python -m cuju.arena._evaluate.ratings')
    parser.add_argument('--run', required=True, help='the identifier of a stored rating run')
    options = parser.parse_args(arguments)
    try:
        resume(options.run)
    except Exception as error:
        print(json.dumps({'error': _messages.described(error)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
