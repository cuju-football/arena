# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Evaluation runs, the matches of one policy against each of its opponents, and their results.

`evaluate` plays a policy against every opponent of the table `[evaluate]` of the settings and
returns an `Evaluation`. The policy is a function called with the observation of every match and
the batch, the text of a module and a function that makes one, `team:ID` for a team of the
registry, or `builtin` for GRF's built-in AI. An opponent is the built-in AI at a difficulty or
a team of the registry.

An evaluation is a run of the runs manifest, `runs/<id>.json` of kind `evaluation`, written as it
goes. The manifest keeps the resolved settings in `config`, the result of each match in
`episodes`, the state of its schedule in `queue` and its summary in `aggregates`, and the actions
of every match lie beside it, so `check` plays the evaluation again and confirms every score.

The matches play in one of two ways, which `_evaluate.schedule` describes. Batched, every match
against an opponent plays at once in one batch, and the runner keeps the actions alone, so play
runs at the speed of the batch. On demand, a few matches play at a time, each against the
opponent whose order with the policy is least settled, a team of the registry in a process of
its own. Match `k` against any opponent is index `k` of the seed rule of the seed, in either
way, so two policies evaluated with one seed meet the same matches.

After play the matches that `[evaluate.record] keep` picks are drawn again from their seeds and
actions by `_evaluate.replay`, in a process of its own unless the settings say otherwise, and
become recordings of one group of the library.

The server plays the evaluations that a page or an agent asks for one at a time, each in a child
process, `python -m cuju.arena._evaluate.runner --run ID`, which takes a stored evaluation up
from its manifest, so an evaluation survives a restart of the server.
"""

from __future__ import annotations

import collections
import concurrent.futures
import contextlib
import copy
import hashlib
import importlib
import importlib.util
import json
import logging
import math
import os
import random
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cuju.errors import ArgumentError, ConfigurationError

from .._config import layers as _layers
from .._formats import context as _context
from .._registry import checkpoints as _checkpoints
from .._registry import teams as _teams
from .._store import home as _home
from .._store import queue as _queue
from .._store import runs as _runs
from .._text import messages as _messages
from .._text.messages import MessageError
from .._tournaments import tournament as _rules
from . import runner as _runner
from . import schedule as _schedule

_LOGGER = logging.getLogger('cuju')
# The kinds of run that an `Evaluation` reads.
KINDS = ('evaluation', 'benchmark')
# What `[evaluate.record] keep` may name without a count, and with one.
KEEP = ('all', 'nothing', 'losses', 'draws', 'wins', 'goals')
KEEP_COUNTED = ('first', 'worst')
# A match on demand in a process of its own ends after this many seconds.
MATCH_SECONDS = 30 * 60
# The states in which a run has ended.
ENDED = ('done', 'failed', 'cancelled')
# The key of the policy in the schedule of an evaluation whose policy has no key of its own.
_POLICY = 'policy'
_SLUG = re.compile(r'[^a-z0-9._-]+')


def wilson(successes: int, trials: int, confidence: float = 0.95) -> list[float]:
    """Returns the Wilson score interval of a proportion, its lower and upper bound.

    The interval stays between 0 and 1 for any number of trials, and is 0 to 1 without trials.
    """
    if trials <= 0:
        return [0.0, 1.0]
    z = _schedule.quantile(confidence)
    rate = successes / trials
    scale = 1 + z * z / trials
    centre = (rate + z * z / (2 * trials)) / scale
    half = z * math.sqrt(rate * (1 - rate) / trials + z * z / (4 * trials * trials)) / scale
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def bootstrap(values: list[float], confidence: float, resamples: int, seed: Any) -> list[float]:
    """Returns the bootstrap interval of the mean of values, its lower and upper bound.

    The values are drawn again with replacement `resamples` times by a generator seeded with the
    digest of `seed`, so the same values always give the same interval, which runs between the
    two percentiles that leave the confidence between them. Without values or resamples the
    interval is the mean itself.
    """
    if not values:
        return [0.0, 0.0]
    count = len(values)
    if resamples <= 0 or all(value == values[0] for value in values):
        mean = round(sum(values) / count, 4)
        return [mean, mean]

    digest = hashlib.sha256(f'{seed}/bootstrap'.encode()).digest()
    # The original intervals used the single-integer MT19937 seed and masked draws of RandomState.
    # Initialize the same state so that an older manifest gives the same intervals.
    state = [int.from_bytes(digest[:4], 'little')]
    for index in range(1, 624):
        previous = state[-1]
        state.append((1812433253 * (previous ^ (previous >> 30)) + index) & 0xFFFFFFFF)
    generator = random.Random(0)
    generator.setstate((3, (*state, 624), None))
    bits = generator.getrandbits
    mask = (1 << (count - 1).bit_length()) - 1
    means = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(count):
            drawn = bits(32) & mask
            while drawn >= count:
                drawn = bits(32) & mask
            total += values[drawn]
        means.append(total / count)
    means.sort()

    def quantile(fraction: float) -> float:
        position = fraction * (resamples - 1)
        index = int(position)
        low = means[index]
        high = means[min(index + 1, resamples - 1)]
        weight = position - index
        gap = high - low
        return high - gap * (1 - weight) if weight >= 0.5 else low + gap * weight

    tail = (1 - confidence) / 2
    return [round(quantile(tail), 4), round(quantile(1 - tail), 4)]


def keep_rule(value: Any) -> tuple[str, int | None]:
    """Returns what `[evaluate.record] keep` names, a word and, for `first` and `worst`, a count.

    Raises:
        ArgumentError: If the value is none of the words, or its count is no whole number above 0.
    """
    if value is None:
        return 'nothing', None
    if isinstance(value, str):
        word, _, count = value.partition(':')
        if word in KEEP and not count:
            return word, None
        if word in KEEP_COUNTED and count.isdigit() and int(count) > 0:
            return word, int(count)
    raise MessageError('server.evaluate.keep', keep=repr(value))


def _goal_difference(episode: dict[str, Any]) -> int:
    return episode['goals_for'] - episode['goals_against']


@contextlib.contextmanager
def match_workers(owner: Any) -> Any:
    """Gives an owner of matches of teams the match workers of its run, and ends them with it.

    The owner, an evaluation or a rating run, holds them as `workers` while the block runs, and
    `_Play._child` leases one for each match whose format keeps its process for the run. A
    cancel, a crash or a stop by Ctrl-C kills them, and the end of the run closes them, so no
    worker or format process outlives the run.
    """
    from .._formats import worker as _worker

    workers = _worker.Workers(
        _runner.worker_command(), _home.process_environment(), _home.process_folder()
    )
    owner.workers = workers
    try:
        yield workers
    except BaseException:
        workers.kill()
        raise
    finally:
        workers.close()
        owner.workers = None


def _ceiling_rule(spec: dict[str, Any], words: dict[str, Any]) -> dict[str, Any]:
    """Returns the forfeit of a side whose call passed a ceiling, from the last words of its
    match process, with the format of that side."""
    from .._formats import ceilings as _ceilings

    names = []
    for team in (spec['left'], spec['right']):
        try:
            names.append(_teams.entry(team)['name'])
        except (ArgumentError, KeyError, OSError, ValueError):
            names.append(team)
    rule = _ceilings.rule(words, names)
    choices = spec.get('submission_format')
    pair = list(choices) if isinstance(choices, list | tuple) else [choices, choices]
    chosen = pair[0 if words['side'] == 'left' else 1]
    if isinstance(chosen, dict) and isinstance(chosen.get('id'), str):
        rule['submission_format'] = chosen['id']
    elif isinstance(chosen, str):
        rule['submission_format'] = chosen
    return rule


def kept_episodes(episodes: list[dict[str, Any]], keep: Any, limit: int) -> list[int]:
    """Returns the numbers of the matches that a keeping rule picks, at most `limit` of them.

    The matches come in the order they were numbered, and `worst` takes those of the least goal
    difference first. A match that ended without a result is never kept.
    """
    word, count = keep_rule(keep)
    played = [episode for episode in episodes if episode.get('outcome') is not None]
    played.sort(key=lambda episode: episode['number'])
    if word == 'nothing':
        found = []
    elif word == 'all':
        found = played
    elif word in ('losses', 'draws', 'wins'):
        outcome = {'losses': 'loss', 'draws': 'draw', 'wins': 'win'}[word]
        found = [episode for episode in played if episode['outcome'] == outcome]
    elif word == 'goals':
        found = [e for e in played if e['goals_for'] + e['goals_against'] > 0]
    elif word == 'first':
        found = played[:count]
    else:
        found = sorted(played, key=lambda e: (_goal_difference(e), e['number']))[:count]
    return [episode['number'] for episode in found[: max(0, int(limit))]]


# The policy and the opponents.


def _builtin_key(difficulty: float | None) -> str:
    return f'builtin:{"scenario" if difficulty is None else f"{difficulty:.2f}"}'


def _builtin_name(difficulty: float | None) -> dict[str, Any]:
    if difficulty is None:
        return _messages.label('server.tournament.label.builtin')
    return _messages.label('server.tournament.label.builtin_ability', ability=f'{difficulty:.2f}')


def load_policy(text: str, options: dict[str, Any]) -> Callable[..., Any]:
    """Returns the policy that a module and a function make, as `module:function` names them.

    The module is one that Python imports, with the working folder among its places, or a file
    that ends in `.py`. The function is called with the options as keyword arguments and returns
    the policy.

    Raises:
        ArgumentError: If the text names no function, or the function returns no policy.
    """
    source, _, name = text.rpartition(':')
    try:
        if not source or not name:
            raise ValueError('expected a module, a colon and a function')
        if source.endswith('.py'):
            path = Path(source).expanduser().resolve()
            spec = importlib.util.spec_from_file_location(f'cuju_policy_{path.stem}', path)
            if spec is None or spec.loader is None:
                raise ValueError(f'expected a Python file, found {source}')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        else:
            if os.getcwd() not in sys.path:
                sys.path.insert(0, os.getcwd())
            module = importlib.import_module(source)
        policy = getattr(module, name)(**options)
    except Exception as error:
        raise MessageError(
            'server.evaluate.policy_load', policy=repr(text), reason=_messages.described(error)
        ) from error
    if not callable(policy):
        raise MessageError(
            'server.evaluate.policy_load',
            policy=repr(text),
            reason=f'which returned {type(policy).__name__}, not a function',
        )
    return policy


def resolve_policy(policy: Any, settings: dict[str, Any]) -> dict[str, Any]:
    """Returns what plays an evaluation, its kind, its name, its key and its function.

    Args:
        policy: A function, `builtin`, `team:ID`, `module:function`, or `None` for the key
            `policy` of the settings.
        settings: The table `[evaluate]`.

    Returns:
        A mapping of `kind`, one of `function`, `builtin` and `team`, the `text` that names the
        policy or `None` for a function given itself, its `team`, its `function`, its `name`
        with the label of a name that cuju made, and `key`, the key of its rating or `None`.

    Raises:
        ArgumentError: If the policy is none of these or names an unknown team.
    """
    if policy is None:
        policy = settings.get('policy')
    if policy is None:
        raise MessageError('server.evaluate.policy_missing')
    given = settings.get('name')
    found: dict[str, Any] = {'kind': 'function', 'text': None, 'team': None, 'function': None}
    if callable(policy):
        found['function'] = policy
        name = given or getattr(policy, '__name__', None) or type(policy).__name__
    elif policy == 'builtin':
        found.update(kind='builtin', text=policy)
        name = given or _builtin_name(settings.get('difficulty'))
        found['key'] = _builtin_key(settings.get('difficulty'))
    elif isinstance(policy, str) and policy.startswith('team:'):
        team = _teams.side_entry(policy[5:])
        if team['kind'] == 'builtin':
            raise MessageError('server.evaluate.policy', policy=repr(policy))
        found.update(kind='team', text=policy, team=team.get('entrant', team['id']), key=team['id'])
        name = given or _teams.shown_name(team)
    elif isinstance(policy, str) and ':' in policy:
        found.update(text=policy)
        found['function'] = load_policy(policy, dict(settings.get('policy_options') or {}))
        name = given or policy
    else:
        raise MessageError('server.evaluate.policy', policy=repr(policy))
    if found['kind'] == 'function':
        # A function has a rating of its own only under a name that the settings give it.
        slug = _SLUG.sub('-', given.lower()).strip('-') if given else ''
        found['key'] = f'policy:{slug}' if slug else None
    found.update(_messages.named('name', name))
    return found


def resolve_opponents(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Returns the opponents of an evaluation, each with its kind, key and name.

    Raises:
        ArgumentError: If there is none, one comes twice, or a team is unknown.
    """
    found: list[dict[str, Any]] = []
    for raw in settings.get('opponents') or ():
        difficulty = raw.get('difficulty')
        entry: dict[str, Any] = {'kind': raw.get('kind'), 'difficulty': difficulty, 'team': None}
        if entry['kind'] == 'team':
            if not isinstance(raw.get('team'), str):
                raise MessageError('server.evaluate.opponent_team', team=repr(raw.get('team')))
            team = _teams.entry(raw['team'])
            if team['kind'] == 'builtin':
                raise MessageError('server.evaluate.opponent_team', team=repr(raw['team']))
            entry.update(team=team.get('entrant', team['id']), key=team['id'])
            entry.update(_messages.named('name', _teams.shown_name(team)))
        else:
            entry['key'] = _builtin_key(difficulty)
            entry.update(_messages.named('name', _builtin_name(difficulty)))
        found.append(entry)
    keys = [entry['key'] for entry in found]
    if not found or len(set(keys)) != len(keys):
        raise MessageError('server.evaluate.opponents', opponents=repr(keys))
    return found


class _KaggleFunction:
    """Calls a Kaggle agent function for every match of a batch, as `cuju.kaggle` runs it."""

    def __init__(self, function: Callable[..., Any], agents: int) -> None:
        self._function = function
        self._agents = agents
        self._bound: tuple[Any, Any] | None = None

    def __call__(self, observation: Any, batch: Any, finished: Any) -> Any:
        from cuju.kaggle import KaggleAgent

        if self._bound is None or self._bound[0] is not batch:
            several = list(range(self._agents)) if self._agents > 1 else None
            self._bound = (batch, KaggleAgent(self._function, batch, 0, agents=several))
        return self._bound[1].act(skip=finished)


def _played_by(policy: dict[str, Any], settings: dict[str, Any]) -> Callable[..., Any] | None:
    """Returns the function that the runner calls each step, or `None` for no function."""
    function = policy.get('function')
    if function is None:
        return None
    if settings['interface'] == 'kaggle':
        return _KaggleFunction(function, int(settings['agents']))
    return lambda observation, batch, finished: function(observation, batch)


def plan(policy: Any, settings: dict[str, Any]) -> dict[str, Any]:
    """Returns the plan of an evaluation, what plays, against whom and how, each part checked.

    The plan holds the `policy`, the `opponents`, the `mode`, `batched` or `on_demand`, the
    `sides` that the policy plays, and `setups`, what one batch or match of each opponent and
    side is created with, its `asset`, `agents`, `difficulty`, `options` and, for a match of
    teams, its `left` and `right` team.

    Raises:
        ArgumentError: If the policy, an opponent or the mode does not fit.
    """
    found = resolve_policy(policy, settings)
    opponents = resolve_opponents(settings)
    keep_rule(settings['record']['keep'])
    scenario = settings['scenario']
    own = settings.get('difficulty')
    sides = ['left', 'right'] if settings['sides'] == 'both' else ['left']
    teams = [entry for entry in opponents if entry['kind'] == 'team']
    if found['kind'] == 'function' and teams:
        raise MessageError('server.evaluate.function_team', team=repr(teams[0]['team']))
    in_batch = found['kind'] != 'team' and not teams
    mode = settings['mode']
    if mode == 'batched' and not in_batch:
        raise MessageError(
            'server.evaluate.batched', found=repr(found['text'] if not teams else teams[0]['team'])
        )
    if mode == 'auto':
        mode = 'batched' if in_batch else 'on_demand'
    options = dict(settings.get('batch') or {})
    if (
        found['kind'] == 'function'
        and settings['interface'] == 'kaggle'
        and options.get('representation') != 'raw'
    ):
        # A Kaggle agent reads the raw observation, which the batch then writes as well.
        options['with_raw'] = True
    agents = int(settings['agents']) if found['kind'] == 'function' else 0
    setups = []
    for opponent in opponents:
        for side in sides:
            pair = [own, opponent['difficulty']]
            setup: dict[str, Any] = {'side': side, 'opponent': opponent['key']}
            if in_batch:
                counts = (agents, 0) if side == 'left' else (0, agents)
                setup.update(
                    asset=_runner.asset_of(scenario, counts),
                    agents=list(counts),
                    options=options,
                    left=None,
                    right=None,
                )
            else:
                names = [found['team'] or 'builtin', opponent['team'] or 'builtin']
                if side == 'right':
                    names.reverse()
                made = _runner.team_setup(names[0], names[1], scenario)
                setup.update(
                    asset=_runner.asset_of(scenario, tuple(made['agents'])),
                    agents=list(made['agents']),
                    options={'action_set': made['action_set']},
                    left=names[0],
                    right=names[1],
                )
            setup['difficulty'] = pair if side == 'left' else pair[::-1]
            setups.append(setup)
    return {
        'policy': found,
        'opponents': opponents,
        'mode': mode,
        'sides': sides,
        'setups': setups,
        'in_batch': in_batch,
    }


def _entrants(made: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Returns the key of the policy in the schedule and the name of every side by key."""
    policy = made['policy']
    keys = [opponent['key'] for opponent in made['opponents']]
    own = policy['key'] or _POLICY
    if own in keys:
        own = f'{own}/{_POLICY}'
    names = {own: {**_named(policy), 'kind': 'policy', 'index': 0}}
    for index, opponent in enumerate(made['opponents']):
        names[opponent['key']] = {**_named(opponent), 'kind': opponent['kind'], 'index': index + 1}
    return own, names


def _named(holder: dict[str, Any]) -> dict[str, Any]:
    return {key: holder[key] for key in ('name', 'name_id', 'name_values') if key in holder}


def _versions() -> dict[str, str]:
    import importlib.metadata

    found = {}
    for key, package in (
        ('cuju', 'cuju-engine'),
        ('cuju-arena', 'cuju-arena'),
        ('cuju-render', 'cuju-render'),
    ):
        try:
            found[key] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            found[key] = 'unknown'
    return found


def _stored_config(made: dict[str, Any], settings: dict[str, Any], job: str) -> dict[str, Any]:
    """Returns what the manifest keeps of the plan and the settings of an evaluation."""
    policy = made['policy']
    own, names = _entrants(made)
    stored = copy.deepcopy(settings)
    # The settings name the policy by its text, and a function given itself by its name alone.
    stored['policy'] = policy['text']
    return {
        'evaluate': stored,
        'policy': {
            key: policy.get(key)
            for key in ('kind', 'text', 'team', 'key', 'name', 'name_id', 'name_values')
            if key in policy
        },
        'side': own,
        'entrants': names,
        'mode': made['mode'],
        'sides': made['sides'],
        'setups': made['setups'],
        'versions': _versions(),
        'job': job,
    }


# The summary.


def _side_stats(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Returns the football statistics of the policy over the matches that hold them."""
    held = [episode for episode in episodes if isinstance(episode.get('stats'), dict)]
    if not held:
        return {}
    sums: dict[str, list[float]] = collections.defaultdict(list)
    actions: collections.Counter[str] = collections.Counter()
    for episode in held:
        side = 0 if episode['side'] == 'left' else 1
        stats = episode['stats']
        for name in ('possession', 'pass_accuracy', 'shots', 'shots_on_target'):
            value = (stats.get(name) or [None, None])[side]
            if isinstance(value, (int, float)):
                sums[name].append(float(value))
        counts = ((stats.get('actions') or {}).get('counts') or [{}, {}])[side]
        actions.update({str(key): int(value) for key, value in counts.items()})
    found: dict[str, Any] = {'stats_episodes': len(held)}
    for name, values in sums.items():
        found[name] = round(sum(values) / len(values), 4) if values else None
    total = sum(actions.values())
    if total:
        found['actions'] = {
            key: round(value / total, 4)
            for key, value in sorted(actions.items(), key=lambda item: int(item[0]))
        }
    return found


def _summary_of(
    episodes: list[dict[str, Any]], confidence: float, resamples: int, seed: Any
) -> dict[str, Any]:
    """Returns the results of a set of matches, their counts, rates and intervals."""
    played = [episode for episode in episodes if episode.get('outcome') is not None]
    count = len(played)
    wins = sum(episode['outcome'] == 'win' for episode in played)
    draws = sum(episode['outcome'] == 'draw' for episode in played)
    goals_for = sum(episode['goals_for'] for episode in played)
    goals_against = sum(episode['goals_against'] for episode in played)
    differences = [float(_goal_difference(episode)) for episode in played]
    low, high = bootstrap(differences, confidence, resamples, seed)
    return {
        'episodes': count,
        'failed': len(episodes) - count,
        'wins': wins,
        'draws': draws,
        'losses': count - wins - draws,
        'win_rate': round(wins / count, 4) if count else None,
        'win_rate_interval': wilson(wins, count, confidence),
        'goals_for': goals_for,
        'goals_against': goals_against,
        'goal_difference': {
            'mean': round(sum(differences) / count, 4) if count else None,
            'low': low,
            'high': high,
        },
        'length': round(sum(episode['length'] for episode in played) / count, 1) if count else None,
        **_side_stats(played),
    }


def summarize(document: dict[str, Any]) -> dict[str, Any]:
    """Returns the summary of an evaluation from the matches of its manifest.

    The summary holds `confidence`, `overall`, the results over every match, and `opponents`,
    the results against each opponent with its `key`, `kind`, `difficulty`, `team` and name.
    Each set of results holds its `episodes`, the matches that `failed`, its `wins`, `draws` and
    `losses`, the `win_rate` with its Wilson interval in `win_rate_interval`, `goals_for` and
    `goals_against`, `goal_difference` with its `mean` and the `low` and `high` bound of a
    bootstrap, the mean `length` in steps, and, where matches hold statistics, the number of
    them in `stats_episodes`, the mean `possession`, `pass_accuracy`, `shots` and
    `shots_on_target` of the policy, and the share of each of its `actions`.
    """
    config = document.get('config') or {}
    settings = config.get('evaluate') or {}
    stats = settings.get('stats') or {}
    confidence = float(stats.get('confidence') or 0.95)
    resamples = int(stats.get('resamples') if stats.get('resamples') is not None else 1000)
    seed = settings.get('seed', 0)
    episodes = document.get('episodes') or []
    opponents = []
    for index, opponent in enumerate(document.get('opponents') or []):
        if not isinstance(opponent, dict):
            continue
        own = [episode for episode in episodes if episode.get('opponent') == index]
        opponents.append(
            {**opponent, **_summary_of(own, confidence, resamples, f'{seed}/{opponent.get("key")}')}
        )
    return {
        'confidence': confidence,
        'overall': _summary_of(episodes, confidence, resamples, seed),
        'opponents': opponents,
    }


def _summary_for(document: dict[str, Any]) -> dict[str, Any]:
    aggregates = document.get('aggregates') or {}
    kept = aggregates.get('summary')
    if document['kind'] == 'benchmark':
        return dict(kept or {})
    record = aggregates.get('record') or {}
    if (
        document['state'] == 'done'
        and record.get('state') not in ('waiting', 'running')
        and isinstance(kept, dict)
        and {'overall', 'opponents', 'confidence'} <= kept.keys()
    ):
        return kept
    return summarize(document)


def progress_of(document: dict[str, Any]) -> dict[str, Any]:
    """Returns where a run stands, as plain data that a view shows at any moment.

    The mapping holds `phase`, the kind of the run, its `state`, the `mode` of its matches,
    `played`, the matches whose result counts, `total`, the matches that it plays, `most`,
    `settled` and `done`, `playing` and `next`, the pairings under way and to come, `ratings`,
    the rating of each side with its `low` and `high` bound, its `matches` and whether it is
    `measured`, `history`, each played match in order with its `number`, `home`, `away`, `score`
    and the `ratings` of its two sides right after it, and `entrants`, the name and kind of the
    side of each key. It reads the manifest alone, so it holds after a restart as well.
    """
    queue = document.get('queue') or {}
    config = document.get('config') or {}
    state = document.get('state')
    return {
        'id': document['id'],
        'phase': document.get('kind'),
        'state': state,
        'mode': queue.get('mode') or config.get('mode'),
        'played': queue.get('count') or 0,
        'total': queue.get('total'),
        'most': queue.get('most'),
        'settled': bool(queue.get('settled')),
        'done': state in ENDED,
        'playing': queue.get('playing') or [],
        'next': queue.get('next') or [],
        'ratings': queue.get('intervals') or {},
        'history': queue.get('played') or [],
        'entrants': config.get('entrants') or {},
    }


# The evaluation as an object.


class Evaluation:
    """An evaluation run or a benchmark of a policy, read from the runs manifest.

    The object reads its run anew whenever it is asked, so it shows a run that still plays, in
    this process or another, as it stands.

    Attributes:
        id: The identifier of the run, such as `e-20261117-101500-3fa9`.
        name: The name of the model.
        run: The series that the run belongs to, or `None`.
        checkpoint: The checkpoint of the model, a mapping of `id`, `label` and `step`, or `None`.
        kind: `evaluation` or `benchmark`.
        state: `queued`, `running`, `done`, `failed` or `cancelled`.
        config: The resolved settings, stored with the results.
        summary: The results overall and against each opponent, as `summarize` describes them.
        episodes: The matches, each with its `number`, `opponent`, `index`, `side`, `seed`,
            `goals_for`, `goals_against`, `outcome`, `length` and `recording`.
        recordings: The identifiers of the recordings kept. Reading it waits for the process
            that draws them.
        progress: Where the run stands, as `progress_of` describes it.
        queue: The state of its schedule, with `playing`, `next`, `played`, `most` and `settled`.
    """

    def __init__(self, identifier: str, _process: subprocess.Popen | None = None) -> None:
        """Opens the evaluation of an identifier.

        The scheduler alone passes `_process`, the child process that plays the run.

        Raises:
            ArgumentError: If the runs manifest holds no evaluation or benchmark of that name.
        """
        self.id = identifier
        self._process = _process
        self._document()

    def _document(self) -> dict[str, Any]:
        document = _runs.get(self.id)
        if document is None or document['kind'] not in KINDS:
            raise MessageError('server.evaluate.unknown', identifier=repr(self.id))
        return document

    def __repr__(self) -> str:
        document = self._document()
        return f'Evaluation({self.id!r}, {document["state"]}, {len(document["episodes"])} matches)'

    @property
    def kind(self) -> str:
        return self._document()['kind']

    @property
    def name(self) -> str | None:
        return self._document().get('name')

    @property
    def run(self) -> str | None:
        return self._document().get('run')

    @property
    def checkpoint(self) -> dict[str, Any] | None:
        found = self._document().get('checkpoints') or []
        return found[0] if found else None

    @property
    def state(self) -> str:
        return self._document()['state']

    @property
    def config(self) -> dict[str, Any]:
        return self._document().get('config') or {}

    @property
    def summary(self) -> dict[str, Any]:
        document = self._document()
        return _summary_for(document)

    @property
    def episodes(self) -> list[dict[str, Any]]:
        return self._document().get('episodes') or []

    @property
    def progress(self) -> dict[str, Any]:
        return progress_of(self._document())

    @property
    def queue(self) -> dict[str, Any]:
        found = self.progress
        return {key: found[key] for key in ('playing', 'next', 'played', 'most', 'settled')}

    @property
    def recordings(self) -> list[str]:
        self.wait()
        return [episode['recording'] for episode in self.episodes if episode.get('recording')]

    def wait(self, timeout: float | None = None) -> bool:
        """Waits until the run has ended and its kept matches are drawn.

        Args:
            timeout: The seconds to wait at most, or `None` to wait as long as it takes.

        Returns:
            Whether the run has ended and no recording is left to draw.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            document = self._document()
            record = (document.get('aggregates') or {}).get('record') or {}
            if document['state'] in ENDED and record.get('state') not in ('waiting', 'running'):
                return True
            process = self._process
            if process is not None and process.poll() is not None:
                # The process that drew the recordings ended without saying so.
                document = self._document()
                record = (document.get('aggregates') or {}).get('record') or {}
                if record.get('state') in ('waiting', 'running'):
                    _set_record(self.id, state='failed', error=f'exit code {process.returncode}')
                self._process = None
                continue
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    def scalars(self, prefix: str = 'eval') -> dict[str, float]:
        """Returns the numbers that `log` writes, by tag.

        An evaluation gives `win_rate` with `win_rate_low` and `win_rate_high`, `goal_difference`
        with its two bounds, `goals_for` and `goals_against` for each match, `episodes`, and
        `possession`, `pass_accuracy`, `shots` and `shots_on_target` where matches hold
        statistics, overall under `prefix/` and for each opponent under `prefix/KEY/`. A
        benchmark gives its `score` and the goal rate of each task under `prefix/TASK/rate`.
        """
        summary = self.summary
        found: dict[str, float] = {}

        def add(tag: str, value: Any) -> None:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                found[f'{prefix}/{tag}'] = float(value)

        if self.kind == 'benchmark':
            add('score', summary.get('score'))
            for task in summary.get('tasks') or []:
                add(f'{task["task"]}/rate', task.get('rate'))
            return found

        def results(start: str, entry: dict[str, Any]) -> None:
            count = entry.get('episodes') or 0
            add(f'{start}win_rate', entry.get('win_rate'))
            add(f'{start}win_rate_low', entry['win_rate_interval'][0] if count else None)
            add(f'{start}win_rate_high', entry['win_rate_interval'][1] if count else None)
            difference = entry.get('goal_difference') or {}
            add(f'{start}goal_difference', difference.get('mean'))
            add(f'{start}goal_difference_low', difference.get('low') if count else None)
            add(f'{start}goal_difference_high', difference.get('high') if count else None)
            add(f'{start}goals_for', entry['goals_for'] / count if count else None)
            add(f'{start}goals_against', entry['goals_against'] / count if count else None)
            add(f'{start}episodes', count)
            for name in ('possession', 'pass_accuracy', 'shots', 'shots_on_target'):
                add(f'{start}{name}', entry.get(name))

        results('', summary['overall'])
        for opponent in summary['opponents']:
            results(f'{_SLUG.sub("_", str(opponent.get("key")).lower())}/', opponent)
        return found

    def log(self, target: Any, step: int | None = None, prefix: str = 'eval') -> None:
        """Writes the results as scalars to a TensorBoard writer or a W&B run.

        The target is told by what it can do, so neither package is imported. A writer of
        TensorBoard has `add_scalar`, which takes each number with its tag and the step. A run
        of W&B, or the module `wandb` itself, has `log`, which takes every number at once.

        Args:
            target: The writer or the run.
            step: The step of the scalars, by default the step of the checkpoint when it has
                one.
            prefix: What every tag starts with.

        Raises:
            ArgumentError: If the target has neither `add_scalar` nor `log`.
        """
        if step is None:
            step = (self.checkpoint or {}).get('step')
        scalars = self.scalars(prefix)
        if callable(getattr(target, 'add_scalar', None)):
            for tag, value in scalars.items():
                target.add_scalar(tag, value, step)
        elif callable(getattr(target, 'log', None)):
            if step is None:
                target.log(scalars)
            else:
                target.log(scalars, step=step)
        else:
            raise MessageError('server.evaluate.target', target=type(target).__name__)

    def to_dict(self) -> dict[str, Any]:
        """Returns the evaluation as plain data, as `GET /api/evaluations/ID` answers it.

        The mapping holds `id`, `kind`, `name`, `run`, `checkpoint`, `state`, `created`, `ended`,
        `config`, `summary`, `episodes`, `recordings`, `progress`, `queue`, `record`, the state
        of the drawing of its kept matches, and the three error fields. A benchmark also holds
        `benchmark`, its table as the Benchmark of the page reads it.
        """
        document = self._document()
        aggregates = document.get('aggregates') or {}
        progress = progress_of(document)
        found = {
            'id': self.id,
            'kind': document['kind'],
            'name': document.get('name'),
            'run': document.get('run'),
            'checkpoint': (document.get('checkpoints') or [None])[0],
            'state': document['state'],
            'created': document.get('created'),
            'ended': document.get('ended'),
            'config': document.get('config') or {},
            'summary': _summary_for(document),
            'episodes': document.get('episodes') or [],
            'recordings': [
                episode['recording']
                for episode in document.get('episodes') or []
                if episode.get('recording')
            ],
            'progress': progress,
            'queue': {
                key: progress[key] for key in ('playing', 'next', 'played', 'most', 'settled')
            },
            'record': aggregates.get('record') or {'state': 'none'},
            **_messages.entry_fields(None),
            **{
                key: aggregates[key]
                for key in ('error', 'error_id', 'error_values')
                if aggregates.get(key) is not None
            },
        }
        if document['kind'] == 'benchmark':
            benchmark = {
                key: value
                for key, value in aggregates.items()
                if key not in ('summary', 'record', 'error', 'error_id', 'error_values')
            }
            if 'cells' in benchmark:
                from .benchmark import _playable

                benchmark['playable'] = found['playable'] = _playable(benchmark['cells'])
            found['benchmark'] = benchmark
        return found


def _set_record(identifier: str, *, summary: dict[str, Any] | None = None, **fields: Any) -> None:
    """Changes what the manifest keeps of the drawing of the kept matches of a run."""
    document = _runs.get(identifier)
    if document is None:
        return
    aggregates = dict(document.get('aggregates') or {})
    aggregates['record'] = {**(aggregates.get('record') or {}), **fields}
    if summary is not None:
        aggregates['summary'] = summary
    _runs.update(identifier, aggregates=aggregates)


# Playing an evaluation.


def _episode(
    number: int,
    pairing: dict[str, Any],
    opponent: int,
    setup: int,
    index: int,
    side: str,
    seed: int,
    goals: list[int] | None,
    length: int | None,
    **fields: Any,
) -> dict[str, Any]:
    """Returns the entry of one match of the manifest, from the side of the policy."""
    entry: dict[str, Any] = {
        'number': number,
        'opponent': opponent,
        'opponent_key': pairing['away'],
        'setup': setup,
        'index': index,
        'environment': index,
        'side': side,
        'seed': seed,
        'goals_for': None,
        'goals_against': None,
        'outcome': None,
        'length': length,
        'recording': None,
        'keep': False,
        'stats': None,
    }
    if goals is not None:
        own, other = (goals[0], goals[1]) if side == 'left' else (goals[1], goals[0])
        entry.update(
            goals_for=own,
            goals_against=other,
            outcome='win' if own > other else 'loss' if own < other else 'draw',
        )
    entry.update(fields)
    return entry


class _Play:
    """One run of the matches of an evaluation, with what it keeps in memory meanwhile."""

    def __init__(
        self,
        identifier: str,
        made: dict[str, Any],
        settings: dict[str, Any],
        progress: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        self.id = identifier
        self.made = made
        self.settings = settings
        self.progress = progress
        self.function = _played_by(made['policy'], settings)
        document = _runs.get(identifier) or {}
        self.episodes: list[dict[str, Any]] = list(document.get('episodes') or [])
        self.checkpoint_snapshots = (document.get('config') or {}).get('checkpoint_snapshots')
        self.own, _ = _entrants(made)
        self.sides = made['sides']
        self.seed = int(settings['seed'])
        self.count = int(settings['episodes'])
        # The extras of the matches, by the number of the match, until the kept ones are known.
        self.extras: dict[int, dict[str, Any]] = {}
        self.room = int(settings['record']['extras_mb']) * 1024 * 1024
        if not settings['record']['annotations']:
            self.room = 0
        self.dropped = 0
        self.processes: set[subprocess.Popen] = set()
        self.lock = threading.Lock()
        # The match workers of the run, which `_on_demand` holds while it plays.
        self.workers: Any = None
        # The format that every team of the evaluation plays under, `[evaluate] format`, or
        # `None` for Kaggle open.
        self.choice = _context.check(_context.from_settings(settings))
        # What the policy and its opponent kept of their format in a match, by its number.
        self.formats: dict[int, list[Any]] = {}
        keys = [opponent['key'] for opponent in made['opponents']]
        at_once = 1 if made['policy']['kind'] == 'function' else int(settings['at_once'])
        self.schedule = _schedule.Schedule(
            [self.own, *keys],
            mode=made['mode'],
            seed=self.seed,
            # Every side starts where the anchor of the tables stands, so the ratings of the
            # schedule read on the same footing as those of a table.
            ratings=dict.fromkeys([self.own, *keys], _rules.TABLE_START),
            pairs=[(self.own, key) for key in keys],
            limit=self.count * len(self.sides),
            most=settings.get('most') if made['mode'] == 'on_demand' else None,
            at_once=at_once,
            settle=settings.get('settle'),
            confidence=float(settings['stats']['confidence']),
            pairing=settings.get('pairing', _schedule.PAIRING),
            state=_queue.queue_state(f'run:{identifier}', resume=True),
            # A format in a process of its own plays no more matches at once than the CPU sets
            # fit.
            threads=_context.threads(self.choice),
        )
        self.rated = bool(settings.get('rate')) and made['policy']['key'] is not None

    # What a pairing plays.

    def _place(self, pairing: dict[str, Any]) -> tuple[int, int, int, str]:
        """Returns the opponent, the setup, the index and the side of a pairing."""
        opponent = [entry['key'] for entry in self.made['opponents']].index(pairing['away'])
        side = pairing['meeting'] % len(self.sides)
        return (
            opponent,
            opponent * len(self.sides) + side,
            pairing['meeting'] // len(self.sides),
            (self.sides[side]),
        )

    def _snapshots(self, opponent: int, side: str) -> dict[str, Any]:
        snapshots = self.checkpoint_snapshots
        if snapshots is None:
            return {}
        key = self.made['opponents'][opponent]['key']
        pair = [snapshots['policy'], snapshots['opponents'][key]]
        if side == 'right':
            pair.reverse()
        return {'sides': [{'checkpoint': point} for point in pair]}

    def _save(self) -> None:
        """Writes the matches and the state of the schedule into the manifest."""
        _runner.keep_episodes(self.id, self.episodes)
        _queue.save_queue(f'run:{self.id}', self.schedule.state())
        if self.progress is not None:
            document = _runs.get(self.id)
            if document is not None:
                self.progress(progress_of(document))

    def _count(self, entries: list[dict[str, Any]]) -> None:
        """Hands the matches that the schedule has counted to the ratings, as evidence."""
        if not self.rated:
            return
        from . import ratings as _evidence

        for entry in entries:
            # Each result goes with its scenario, the checkpoint of its sides and its actions.
            _evidence.evaluation_result(
                self.id,
                self.made,
                self.settings,
                entry,
                self.formats.pop(entry['number'], None),
            )

    def _hold_extras(self, numbers: list[int], played: _runner.Played) -> None:
        """Keeps the extras of a batch in memory, each match with the steps that it played."""
        for column, number in enumerate(numbers):
            self.extras[number] = {
                name: values[: played.lengths[column], column]
                for name, values in played.extras.items()
            }
        self.dropped += played.dropped

    def _batch(self, group: list[dict[str, Any]]) -> None:
        """Plays the matches of one opponent and side at once and counts their results."""
        opponent, setup_index, _, side = self._place(group[0])
        setup = self.made['setups'][setup_index]
        indexes = [self._place(pairing)[2] for pairing in group]
        first = min(indexes)
        played = _runner.play_batch(
            setup['asset'],
            tuple(setup['agents']),
            setup['difficulty'],
            max(indexes) - first + 1,
            self.seed,
            policy=self.function,
            options=setup['options'],
            device=self.settings['device'],
            threads=int(self.settings['threads']),
            first_index=first,
            extras_bytes=self.room,
        )
        self.room = max(0, self.room - sum(value.nbytes for value in played.extras.values()))
        columns = [index - first for index in indexes]
        numbers = [pairing['number'] for pairing in group]
        chunk = _runner.Played(
            [played.goals[column] for column in columns],
            [played.lengths[column] for column in columns],
            played.actions[:, columns],
            {name: values[:, columns] for name, values in played.extras.items()},
            played.dropped,
            played.seconds,
        )
        name = f'o{opponent}-{side}-{first}'
        digests = _runner.write_chunk(self.id, name, numbers, chunk)
        self._hold_extras(numbers, chunk)
        for position, pairing in enumerate(group):
            pairing['seed'] = self.seed
            self.episodes.append(
                _episode(
                    pairing['number'],
                    pairing,
                    opponent,
                    setup_index,
                    indexes[position],
                    side,
                    self.seed,
                    chunk.goals[position],
                    chunk.lengths[position],
                    digest=digests[position],
                    actions=f'{name}.npz',
                    **self._snapshots(opponent, side),
                )
            )
        self.episodes.sort(key=lambda entry: entry['number'])
        for position, pairing in enumerate(group):
            goals = chunk.goals[position]
            score = goals if side == 'left' else goals[::-1]
            self._count(self.schedule.finish(pairing['number'], score))
        _set_seconds(self.id, played.seconds, len(group))
        self._save()

    def _one(self, pairing: dict[str, Any]) -> dict[str, Any]:
        """Plays one match on demand and returns what the manifest keeps of it."""
        opponent, setup_index, index, side = self._place(pairing)
        setup = self.made['setups'][setup_index]
        if setup['left'] is None:
            played = _runner.play_batch(
                setup['asset'],
                tuple(setup['agents']),
                setup['difficulty'],
                1,
                self.seed,
                policy=self.function,
                options=setup['options'],
                device=self.settings['device'],
                threads=int(self.settings['threads']),
                first_index=index,
                extras_bytes=self.room,
            )
            with self.lock:
                self.room = max(
                    0, self.room - sum(value.nbytes for value in played.extras.values())
                )
        else:
            played = self._child(
                {
                    'left': setup['left'],
                    'right': setup['right'],
                    'scenario': self.settings['scenario'],
                    'seed': self.seed,
                    'index': index,
                    'difficulty': setup['difficulty'],
                    'device': self.settings['device'],
                    'submission_format': self.choice,
                    'kind': 'evaluation',
                    'terminal': f'{self.id}-m{pairing["number"]}',
                }
            )
            if played.forfeit is not None:
                # A forfeit that the format decided ends the match, which is never evidence.
                rule = played.forfeit
                error = RuntimeError(rule.get('reason') or '')
                error.message_id, error.values = rule.get('reason_id'), rule.get('reason_values')
                raise error
            own = list(played.formats) if side == 'left' else list(played.formats)[::-1]
            with self.lock:
                self.formats[pairing['number']] = own
        name = f'm{pairing["number"]}'
        with self.lock:
            (digest,) = _runner.write_chunk(self.id, name, [pairing['number']], played)
            self._hold_extras([pairing['number']], played)
            _set_seconds(self.id, played.seconds, 1)
        return _episode(
            pairing['number'],
            pairing,
            opponent,
            setup_index,
            index,
            side,
            self.seed,
            played.goals[0],
            played.lengths[0],
            digest=digest,
            actions=f'{name}.npz',
            **self._snapshots(opponent, side),
        )

    def _child(self, spec: dict[str, Any]) -> _runner.Played:
        """Plays a match of teams in a process of its own and returns what it gave.

        A match whose format keeps its process for the run, `[process] scope = "run"`, plays
        in a match worker of the run's `workers`, where the owner holds them, so the side's
        process lives on into the slot's next match. Every other match plays in a fresh child.
        Either process runs as a match process, so a ceiling of a side in it ends it with last
        words, which give the forfeit of that side.

        Raises:
            RuntimeError: If the process ended without a result, with the reason.
        """
        import numpy as np

        from .._formats import ceilings as _ceilings
        from .._formats import worker as _worker

        workers = getattr(self, 'workers', None)
        if workers is not None and _worker.wanted(spec.get('submission_format')):
            with workers.lease() as found:
                with self.lock:
                    self.processes.add(found.process)
                try:
                    reports, code = found.play(spec, seconds=MATCH_SECONDS)
                finally:
                    with self.lock:
                        self.processes.discard(found.process)
                stderr = list(found.stderr)
            code = 0 if code is None else code
        else:
            process = subprocess.Popen(
                _runner.child_command(spec),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                cwd=_home.process_folder(),
                env=_ceilings.match_environment(_home.process_environment()),
            )
            with self.lock:
                self.processes.add(process)
            tail: collections.deque[str] = collections.deque(maxlen=40)
            reader = threading.Thread(target=lambda: tail.extend(process.stderr), daemon=True)
            reader.start()
            timer = threading.Timer(MATCH_SECONDS, process.kill)
            timer.daemon = True
            timer.start()
            reports = []
            try:
                for line in process.stdout:
                    try:
                        report = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(report, dict):
                        reports.append(report)
                code = process.wait()
            finally:
                timer.cancel()
                with self.lock:
                    self.processes.discard(process)
            reader.join(5.0 if code == _ceilings.EXIT_CEILING else 1.0)
            stderr = list(tail)
        width = sum(_runner.team_setup(spec['left'], spec['right'], spec['scenario'])['agents'])
        words = _ceilings.read_last_words(stderr) if code == _ceilings.EXIT_CEILING else None
        if words is not None:
            # The side whose call passed a ceiling of the renderer forfeits the match.
            return _runner.Played(
                [[0, 0]],
                [int(words.get('step') or 0)],
                np.zeros((0, 1, width), dtype=np.uint8),
                forfeit=_ceiling_rule(spec, words),
            )
        result, failure = None, None
        for report in reports:
            if isinstance(report.get('result'), dict):
                result = report['result']
            if isinstance(report.get('error'), str):
                failure = report
        if code != 0 or result is None:
            found = (
                _messages.report_fields(failure)
                if failure
                else _messages.exit_fields(
                    'server.evaluate.match_exit', code, ''.join(stderr).strip()
                )
            )
            error = RuntimeError(found['error'])
            if found['error_id'] is not None:
                error.message_id, error.values = found['error_id'], found['error_values']
            raise error
        rows = np.asarray(result['actions'], dtype=np.uint8)
        actions = rows.reshape(len(rows), 1, width)
        return _runner.Played(
            [list(result['goals'])],
            [int(result['length'])],
            actions,
            seconds=result['seconds'],
            formats=list(result.get('formats') or [None, None]),
            forfeit=result.get('forfeit') if isinstance(result.get('forfeit'), dict) else None,
        )

    def _on_demand(self) -> None:
        """Plays the matches on demand, a few at a time, until the schedule has ended.

        Each slot of the schedule leases a match worker for a match whose format keeps its
        process for the run, and the workers end with the run, whatever ends it.
        """
        with match_workers(self):
            self._on_demand_slots()

    def _on_demand_slots(self) -> None:
        workers = self.schedule.at_once
        waiting: dict[Any, dict[str, Any]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            try:
                while True:
                    due = self.schedule.due()
                    for pairing in due:
                        pairing['seed'] = self.seed
                        waiting[executor.submit(self._one, pairing)] = pairing
                    if due:
                        self._save()
                    if not waiting:
                        break
                    done, _ = concurrent.futures.wait(
                        waiting, return_when=concurrent.futures.FIRST_COMPLETED
                    )
                    for future in done:
                        pairing = waiting.pop(future)
                        try:
                            episode = future.result()
                            score = [episode['goals_for'], episode['goals_against']]
                            fields: dict[str, Any] = {}
                        except Exception as error:
                            opponent, setup_index, index, side = self._place(pairing)
                            shown = (
                                str(error)
                                if isinstance(error, (ArgumentError, RuntimeError))
                                else _messages.described(error)
                            )
                            episode = _episode(
                                pairing['number'],
                                pairing,
                                opponent,
                                setup_index,
                                index,
                                side,
                                self.seed,
                                None,
                                None,
                                error=shown,
                                **_messages.id_fields(error),
                                **self._snapshots(opponent, side),
                            )
                            score = None
                            fields = {'error': shown}
                        self.episodes.append(episode)
                        self.episodes.sort(key=lambda entry: entry['number'])
                        self._count(self.schedule.finish(pairing['number'], score, **fields))
                    self._save()
            except BaseException:
                with self.lock:
                    for process in list(self.processes):
                        process.kill()
                for future in waiting:
                    future.cancel()
                raise

    def _check_teams(self) -> None:
        """Runs the pre-check of the evaluation's format for each of its teams, at its start.

        Raises:
            ArgumentError: If the format refuses a team, which the evaluation then cannot play.
        """
        if self.choice is None or self.episodes:
            return
        teams = [self.made['policy'].get('team')]
        teams += [opponent.get('team') for opponent in self.made['opponents']]
        texts = list(dict.fromkeys(team for team in teams if team and team != 'builtin'))
        refused = _context.ensure(self.choice, texts, self.settings['scenario'])
        if refused:
            team, found = next(iter(refused.items()))
            reason = found['failed'] or '; '.join(item['message'] for item in found['problems'])
            raise MessageError('server.evaluate.refused', team=team, reason=reason)

    def run(self) -> None:
        """Plays every match that the schedule has left."""
        self._check_teams()
        if self.schedule.mode == 'batched':
            due = self.schedule.due()
            groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
            for pairing in due:
                opponent, setup_index, _, _ = self._place(pairing)
                groups.setdefault((opponent, setup_index), []).append(pairing)
            self._save()
            for group in groups.values():
                self._batch(group)
        else:
            self._on_demand()

    def keep(self) -> list[int]:
        """Marks the matches to keep, writes their extras, and returns their numbers."""
        import numpy as np

        record = self.settings['record']
        numbers = kept_episodes(self.episodes, record['keep'], int(record['limit']))
        for episode in self.episodes:
            episode['keep'] = episode['number'] in numbers
        extras = {number: self.extras[number] for number in numbers if self.extras.get(number)}
        if extras:
            empty = _runner.Played([], [], np.zeros((0, 0, 0), dtype=np.uint8))
            _runner.write_chunk(self.id, 'extras', [], empty, extras)
        self.extras = {}
        _runner.keep_episodes(self.id, self.episodes)
        return numbers


def _set_seconds(identifier: str, seconds: float, matches: int) -> None:
    """Adds the seconds of play of some matches to what the manifest counts."""
    document = _runs.get(identifier)
    if document is None:
        return
    aggregates = dict(document.get('aggregates') or {})
    aggregates['seconds'] = round(float(aggregates.get('seconds') or 0.0) + seconds, 3)
    aggregates['matches'] = int(aggregates.get('matches') or 0) + matches
    _runs.update(identifier, aggregates=aggregates)


def _checkpoint(
    settings: dict[str, Any], policy: dict[str, Any], snapshot: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Returns the checkpoint of an evaluation, that of the settings, else that of a team.

    A whole number is the step of the checkpoint and names it as well.
    """
    given = settings.get('checkpoint')
    if isinstance(given, int) and not isinstance(given, bool):
        return _runs.checkpoint({'id': str(given), 'step': given})
    if given is not None:
        return _runs.checkpoint(given)
    if policy['kind'] == 'team' and policy['team'] != _teams.RANDOM_ACTIONS:
        team = policy['team']
        if snapshot is None:
            return _runs.team_checkpoint(team)
        return {'id': snapshot['id'], 'label': team.partition('@')[0], 'digest': snapshot['digest']}
    return None


def _create(made: dict[str, Any], settings: dict[str, Any], job: str, state: str) -> str:
    """Writes the manifest of a new evaluation and returns its identifier."""
    policy = made['policy']
    team = policy['team']
    captured = _checkpoints.snapshot(team, with_digest=True) if team else None
    checkpoint = _checkpoint(settings, policy, captured)
    own = checkpoint if policy['kind'] == 'function' else captured
    config = _stored_config(made, settings, job)
    config['checkpoint_snapshots'] = {
        'policy': {key: own.get(key) for key in ('id', 'label', 'step')} if own else None,
        'opponents': {
            side['key']: _checkpoints.snapshot(side['team']) if side['team'] else None
            for side in made['opponents']
        },
    }
    identifier = _home.new_identifier('e')
    while _runs.get(identifier) is not None:
        identifier = _home.new_identifier('e')
    _runs.create(
        'evaluation',
        identifier,
        name=policy['name'],
        run=settings.get('run'),
        scenarios=[settings['scenario']],
        seeds=[int(settings['seed'])],
        opponents=copy.deepcopy(made['opponents']),
        checkpoints=[checkpoint] if checkpoint is not None else [],
        config=config,
        state=state,
    )
    return identifier


def _draw(identifier: str, background: bool) -> subprocess.Popen | None:
    """Has the kept matches of a run drawn, in a process of their own or in this one."""
    from . import replay as _replay

    if not _replay.wanted(identifier):
        _set_record(identifier, state='none')
        return None
    _set_record(identifier, state='waiting')
    if not background:
        _replay.run(identifier)
        return None
    return subprocess.Popen(
        _replay.command(identifier),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=_home.process_folder(),
        env=_home.process_environment(),
    )


def _execute(
    identifier: str,
    made: dict[str, Any],
    settings: dict[str, Any],
    progress: Callable[[dict[str, Any]], None] | None,
    background: bool,
) -> Evaluation:
    """Plays the matches of a stored evaluation, ends its run and has its recordings drawn."""
    from . import replay as _replay

    _runs.update(identifier, state='running')
    try:
        play = _Play(identifier, made, settings, progress)
        play.run()
        play.keep()
        document = _runs.get(identifier) or {}
        aggregates = dict(document.get('aggregates') or {})
        aggregates['summary'] = summarize(document)
        aggregates['extras_dropped'] = play.dropped
        # The kept matches wait to be drawn from the moment the run ends, so a reader of the ended
        # run never meets a record that has yet to start.
        aggregates['record'] = {
            **(aggregates.get('record') or {}),
            'state': 'waiting' if _replay.wanted(identifier) else 'none',
        }
        _runs.finish(identifier, aggregates)
    except BaseException as error:
        document = _runs.get(identifier) or {}
        aggregates = dict(document.get('aggregates') or {})
        cancelled = isinstance(error, KeyboardInterrupt)
        if not cancelled:
            aggregates.update(_messages.entry_fields(error, _messages.described(error)))
        with_state = 'cancelled' if cancelled else 'failed'
        if document.get('state') not in ENDED:
            _runs.finish(identifier, aggregates, with_state)
        raise
    process = _draw(identifier, background)
    found = Evaluation(identifier, process)
    if progress is not None:
        progress(found.progress)
    return found


def evaluate(
    policy: Any = None,
    config: Any = None,
    overrides: Any = (),
    *,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> Evaluation:
    """Plays a policy against each opponent of the settings and returns the evaluation.

    The table `[evaluate]` of the settings names the scenario, the opponents, the matches
    against each and their seed, and what to keep. Every match is one environment of a batch,
    played to its end once, and match `k` against any opponent is index `k` of the seed rule,
    so two policies evaluated with the same settings meet the same matches. The evaluation is
    written to the runs manifest as it goes, with the actions of every match beside it, and the
    matches that the settings keep become recordings afterwards.

    Args:
        policy: What plays. A function `policy(observation, batch)` receives the observation of
            every match and the batch, and returns the actions, `[matches, agents]`, or the
            actions and a mapping of extras, named as the keyword arguments of
            `cuju.arena.annotate`, which the kept recordings carry as annotations. The text
            `module:function` names a function that returns such a policy from the options of
            `policy_options`. `team:ID` names a team of the registry and `builtin` GRF's
            built-in AI. With `[evaluate] interface = "kaggle"` the function is a Kaggle agent,
            called for each match. `None` takes the key `policy` of the settings.
        config: The settings, a file, a mapping of tables, a `Config` or a list of them, laid
            over the shipped defaults as `cuju.arena.config` describes.
        overrides: Settings such as `evaluate.episodes=200`, laid over `config`.
        progress: A function called after every change with where the run stands, the mapping
            that `Evaluation.progress` returns.

    Returns:
        The evaluation, with its `summary`, its `episodes` and its `recordings`.

    Raises:
        ArgumentError: If the policy, an opponent or a setting of the run does not fit.
        ConfigurationError: If a setting is invalid.
        ChoiceError: If the settings name an unknown key.
    """
    settings = _layers.table('evaluate', config, overrides)
    made = plan(policy, settings)
    identifier = _create(made, settings, 'python', 'running')
    return _execute(identifier, made, settings, progress, bool(settings['record']['background']))


def resume(identifier: str, progress: Callable[[dict[str, Any]], None] | None = None) -> Evaluation:
    """Plays a stored evaluation on from its manifest, as the server's child process does.

    The policy is the one that the stored settings name, so the evaluation of a function given
    itself resumes only in the call that started it.

    Raises:
        ArgumentError: If the run is unknown, has ended, or names no policy to load.
    """
    document = _runs.get(identifier)
    if document is None or document['kind'] != 'evaluation':
        raise MessageError('server.evaluate.unknown', identifier=repr(identifier))
    if document['state'] in ENDED:
        raise MessageError('server.evaluate.ended', state=document['state'])
    settings = (document.get('config') or {}).get('evaluate') or {}
    made = plan(None, settings)
    return _execute(identifier, made, settings, progress, False)


def get(identifier: str) -> Evaluation:
    """Returns the evaluation or the benchmark of an identifier.

    Raises:
        ArgumentError: If the runs manifest holds none of that name.
    """
    return Evaluation(identifier)


def _listed(document: dict[str, Any]) -> dict[str, Any]:
    """Returns what a list shows of an evaluation, without its matches."""
    aggregates = document.get('aggregates') or {}
    config = document.get('config') or {}
    summary = aggregates.get('summary') or {}
    queue = document.get('queue') or {}
    return {
        'id': document['id'],
        'kind': document['kind'],
        'name': document.get('name'),
        'run': document.get('run'),
        'checkpoint': (document.get('checkpoints') or [None])[0],
        'state': document['state'],
        'created': document.get('created'),
        'ended': document.get('ended'),
        'scenario': (document.get('scenarios') or [None])[0],
        'seed': (document.get('seeds') or [None])[0],
        'policy': config.get('policy'),
        'opponents': document.get('opponents') or [],
        'mode': config.get('mode'),
        'episodes': len(document.get('episodes') or []),
        'played': queue.get('count') or 0,
        'total': queue.get('total'),
        'overall': summary.get('overall'),
        'recordings': sum(
            1 for episode in document.get('episodes') or [] if episode.get('recording')
        ),
    }


def evaluations(run: str | None = None) -> list[dict[str, Any]]:
    """Returns the evaluations of the runs manifest, newest first.

    Each entry holds the `id`, `name`, `run`, `checkpoint`, `state`, `created` and `ended` of an
    evaluation, its `scenario`, `seed`, `policy`, `opponents` and `mode`, the `played` matches
    of its `total`, the results over every match in `overall` once it has ended, and the number
    of its `recordings`. `cuju.arena.evaluate` returns the whole of one, and `Evaluation` opens
    one by its `id`.

    Args:
        run: Only the evaluations of this series, as `[evaluate] run` names it, or every one.
    """
    return [_listed(document) for document in _runs.list(kind='evaluation', run=run)]


def remove(identifier: str) -> bool:
    """Deletes an evaluation that has ended, its manifest and its kept actions.

    The recordings of the evaluation stay in the library, which keeps them as any other.

    Returns:
        Whether there was one.

    Raises:
        ArgumentError: If the evaluation still plays.
    """
    document = _runs.get(identifier)
    if document is None or document['kind'] not in KINDS:
        return False
    if document['state'] not in ENDED:
        raise MessageError('server.evaluate.running', state=document['state'])
    _runner.remove_actions(identifier)
    return _runs.remove(identifier)


# The evaluations of the server, played one at a time, each in a child process.


def request_settings(document: Any) -> dict[str, Any]:
    """Returns the table `[evaluate]` that the body of a request resolves to, each part checked.

    The body is the table itself, or a mapping that holds it under `evaluate`. A request names
    its policy as `builtin` or `team:ID`, since the server starts no code of a module for a
    page.

    Raises:
        ArgumentError: If the body is no mapping, names an unknown key, holds a value that does
            not fit, or names another kind of policy.
    """
    if not isinstance(document, dict):
        raise MessageError('server.json_object')
    table = document.get('evaluate') if set(document) == {'evaluate'} else document
    if not isinstance(table, dict):
        raise MessageError('server.json_object')
    known = _layers.table('evaluate')
    unknown = sorted(set(table) - set(known))
    if unknown:
        raise MessageError(
            'server.evaluate.unknown_key', keys=', '.join(sorted(known)), key=', '.join(unknown)
        )
    try:
        settings = _layers.table('evaluate', {'evaluate': table})
    except ConfigurationError as error:
        raise MessageError('server.evaluate.config', reason=str(error)) from error
    policy = settings.get('policy')
    if not (policy == 'builtin' or (isinstance(policy, str) and policy.startswith('team:'))):
        raise MessageError('server.evaluate.page_policy', policy=repr(policy))
    # The server draws the kept matches in the child process that plays the evaluation.
    settings['record']['background'] = False
    return settings


class _Evaluations:
    """The evaluations that a page or an agent asked the server for, played one at a time."""

    def __init__(self, starter: Callable[[str], subprocess.Popen] | None = None) -> None:
        """Takes up the evaluations of the server that a stop left waiting or under way.

        Args:
            starter: Starts the process that plays a stored evaluation, by default the child
                process of `runner.run_command`. A test passes a function of its own.
        """
        self._lock = threading.RLock()
        self._waiting: list[str] = []
        self._process: tuple[str, subprocess.Popen] | None = None
        self._wake = threading.Event()
        self._starter = starter or self._start
        for document in reversed(_runs.list(kind='evaluation')):
            job = (document.get('config') or {}).get('job')
            if job == 'server' and document['state'] in ('queued', 'running'):
                self._waiting.append(document['id'])
                self._wake.set()
        threading.Thread(target=self._run, daemon=True, name='cuju-evaluations').start()

    @staticmethod
    def _start(identifier: str) -> subprocess.Popen:
        return subprocess.Popen(
            _runner.run_command(identifier),
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
        """Queues the evaluation that a request names and returns it as it stands."""
        settings = request_settings(document)
        made = plan(None, settings)
        identifier = _create(made, settings, 'server', 'queued')
        with self._lock:
            self._waiting.append(identifier)
        self._wake.set()
        return Evaluation(identifier).to_dict()

    def cancel(self, identifier: str) -> bool:
        """Cancels a waiting or running evaluation of the server, and says whether it did."""
        with self._lock:
            document = _runs.get(identifier)
            if document is None or document['state'] in ENDED:
                return False
            if identifier in self._waiting:
                self._waiting.remove(identifier)
            _runs.finish(identifier, state='cancelled')
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
            document = _runs.get(identifier)
            stopped = document is None or document['state'] in ENDED
        if stopped:
            process.kill()
        tail: collections.deque[str] = collections.deque(maxlen=20)
        if process.stderr is not None:
            tail.extend(process.stderr)
        code = process.wait()
        with self._lock:
            self._process = None
        document = _runs.get(identifier)
        if document is not None and document['state'] not in ENDED:
            _fail(
                identifier,
                _messages.exit_fields('server.evaluate.exit', code, ''.join(tail).strip()),
            )


def _fail(identifier: str, fields: dict[str, Any]) -> None:
    """Ends a run as failed with the error fields of its failure."""
    document = _runs.get(identifier)
    if document is None:
        return
    aggregates = {**(document.get('aggregates') or {}), **fields}
    try:
        _runs.finish(identifier, aggregates, 'failed')
    except ArgumentError as error:
        _LOGGER.warning('cuju render could not end the evaluation %s: %s', identifier, error)


_EVALUATIONS: _Evaluations | None = None
_EVALUATIONS_LOCK = threading.Lock()


def jobs() -> _Evaluations:
    """Returns the server's queue of evaluations, started on first use."""
    global _EVALUATIONS
    with _EVALUATIONS_LOCK:
        if _EVALUATIONS is None:
            _EVALUATIONS = _Evaluations()
        return _EVALUATIONS
