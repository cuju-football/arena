# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Draws the kept matches of a run again from their seeds and actions, and checks a run.

A run of `evaluate` or `benchmark` keeps the actions of every match and no frame. `run` plays
each match that the run keeps once more, in a batch of one environment on the CPU with the same
seed, index and options, and feeds it the kept actions. A `Recorder` attached to that batch
writes the recording, with the lanes of the players, the actions and the state of the kick off,
and the extras that the policy handed back go into it as annotations, step by step, through
`cuju.arena.annotate`. The match plays as it did, bit for bit, on either backend, so the
recording shows what the evaluation counted, and a match whose replay gives another score is
marked instead of kept. The recordings of one run form one group of the library, and each match
of the manifest takes the identifier of its recording and its statistics.

`check` plays every match of a run again at the speed of a batch and confirms each score and
length, after holding the kept actions of each match to the digest that the manifest keeps.

`python -m cuju.arena._evaluate.replay ID` draws the kept matches of a run in a process of its
own, so the call that played the run returns at once.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable
from typing import Any

import numpy as np

from cuju.errors import ArgumentError

from .._registry import teams as _teams
from .._store import home as _home
from .._store import library as _library
from .._store import runs as _runs
from .._text import messages as _messages
from .._text.messages import MessageError
from . import runner as _runner

_LOGGER = logging.getLogger('cuju')
# What a match of the manifest keeps of the statistics of its recording.
STATS = (
    'score',
    'goals',
    'shots',
    'shots_on_target',
    'passes',
    'completed',
    'pass_accuracy',
    'possession',
    'actions',
)
# The words of what `check` finds wrong with a match.
PROBLEMS = ('missing', 'changed', 'score', 'length')


def command(identifier: str) -> list[str]:
    """Returns the command of the process that draws the kept matches of a run."""
    return [*_home.python_command(), '-m', 'cuju.arena._evaluate.replay', identifier]


def _known(identifier: str) -> dict[str, Any]:
    document = _runs.get(identifier)
    if document is None or document['kind'] not in ('evaluation', 'benchmark'):
        raise MessageError('server.evaluate.unknown', identifier=repr(identifier))
    return document


def _all_stats(document: dict[str, Any]) -> bool:
    settings = (document.get('config') or {}).get('evaluate') or {}
    return (settings.get('stats') or {}).get('episodes') == 'all'


def wanted(identifier: str) -> list[int]:
    """Returns the numbers of the matches of a run that wait to be drawn.

    A kept match waits until it has its recording, and with `[evaluate.stats] episodes = "all"`
    every match waits until it has its statistics.
    """
    document = _known(identifier)
    every = _all_stats(document)
    found = []
    for episode in document.get('episodes') or []:
        if episode.get('outcome') is None or episode.get('drawn'):
            continue
        if (episode.get('keep') and not episode.get('recording')) or (
            every and episode.get('stats') is None
        ):
            found.append(episode['number'])
    return found


def _label_of(holder: dict[str, Any]) -> Any:
    """Returns the name of a stored side, a label when it holds a valid one, else its text."""
    found = {
        'id': holder.get('name_id'),
        'text': holder.get('name'),
        'values': holder.get('name_values'),
    }
    return found if _messages.valid_label(found) else str(holder.get('name'))


def _names(document: dict[str, Any], episode: dict[str, Any]) -> tuple[Any, Any]:
    """Returns the name of the policy and of the opponent of a match."""
    config = document.get('config') or {}
    opponents = document.get('opponents') or []
    opponent = opponents[episode['opponent']] if episode['opponent'] < len(opponents) else {}
    return _label_of(config.get('policy') or {}), _label_of(opponent)


def _title(document: dict[str, Any], episode: dict[str, Any]) -> dict[str, Any]:
    policy, opponent = _names(document, episode)
    if document['kind'] == 'benchmark':
        return _messages.label(
            'server.names.benchmark_attempt',
            task=_messages.scenario(str(episode.get('scenario'))),
            attempt=episode['index'] + 1,
            competitor=policy,
        )
    return _messages.label(
        'server.names.evaluation_match',
        policy=policy,
        opponent=opponent,
        number=episode['index'] + 1,
    )


def _header_teams(
    document: dict[str, Any], episode: dict[str, Any], setup: dict[str, Any], batch: Any
) -> list[dict[str, Any]]:
    """Returns the two teams of a match as the header of its recording holds them."""
    policy, opponent = _names(document, episode)
    names = [policy, opponent] if setup['side'] == 'left' else [opponent, policy]
    sides = [setup.get('left') or _teams.BUILTIN, setup.get('right') or _teams.BUILTIN]
    try:
        teams = _teams.match_teams(*sides)
    except ArgumentError:
        # A team that left the registry since the match still has its name.
        teams = _teams.match_teams(_teams.BUILTIN, _teams.BUILTIN)
    played = [round(float(value), 4) for value in batch.difficulty[0].tolist()]
    return [
        {
            **{key: value for key, value in team.items() if key not in ('name_id', 'name_values')},
            **_messages.named('name', names[side]),
            # The page reads the difficulty that played under its earlier name.
            'ability': played[side],
            'difficulty': played[side],
        }
        for side, team in enumerate(teams)
    ]


def _home_side(config: dict[str, Any], setup: dict[str, Any], batch: Any) -> Any:
    """Returns the side at home of a match played again, its left side, for the header.

    A side that the setup names as an entrant is that entrant at the difficulty it played. The
    evaluated policy at home is its team, or for a function its key in the colors of the built-in
    AI. A built-in opponent at home is the built-in AI at its level. `None` leaves the side to the
    header, which shows the neutral ground for a policy without a key.
    """
    played = [round(float(value), 4) for value in batch.difficulty[0].tolist()]
    left = setup.get('left')
    if left:
        return {'entrant': left, 'ability': played[0]}
    if setup.get('side') != 'left':
        return {'entrant': _teams.BUILTIN, 'ability': played[0]}
    policy = config.get('policy') or {}
    if policy.get('kind') == 'team' and policy.get('team'):
        return {'entrant': policy['team']}
    key = policy.get('key')
    if not key:
        return None
    return {'entrant': _teams.BUILTIN, 'key': key}


def _draw(
    document: dict[str, Any], episode: dict[str, Any], entry: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Plays one match again with a recorder and returns its recording and what it gave.

    Raises:
        ArgumentError: If the replay ends with another score or length than the match had.
    """
    import torch

    from .._watch.annotate import annotate
    from .._watch.recorder import Recorder

    config = document.get('config') or {}
    setup = config['setups'][episode['setup']]
    settings = config.get('evaluate') or {}
    actions = np.ascontiguousarray(entry['actions'], dtype=np.uint8)
    width = actions.shape[1] if actions.ndim == 2 else 0
    annotations = (settings.get('record') or {}).get('annotations', True)
    extras = {name: values for name, values in (entry.get('extras') or {}).items() if annotations}
    checkpoint = (document.get('checkpoints') or [None])[0]
    scenario = episode.get('scenario') or (document.get('scenarios') or ['match'])[0]
    recorders: list[Any] = []
    failed: list[str] = []

    def attach(batch: Any) -> None:
        recorders.append(
            Recorder(
                batch,
                0,
                title=_title(document, episode),
                teams=_header_teams(document, episode, setup, batch),
                scenario=scenario,
                version=2,
                keep='all',
                every=1,
                min_reward=None,
                run={
                    'id': document['id'],
                    'seed': int(episode['seed']),
                    'sides': episode.get('sides'),
                },
                checkpoint=checkpoint,
                home=_home_side(config, setup, batch),
            )
        )

    def before_step(batch: Any, step: int) -> None:
        if not width or failed:
            return
        parts = {
            name: torch.as_tensor(np.asarray(values[step], dtype=np.float32))[None]
            for name, values in extras.items()
            if step < len(values)
        }
        chosen = torch.as_tensor(actions[step].astype(np.int32))[None]
        try:
            annotate(batch, actions=chosen, **_runner.nested_extras(parts))
        except ArgumentError as error:
            # The annotations of a match are an extra, so one that does not fit ends them alone.
            failed.append(str(error))
            _LOGGER.warning(
                'cuju render left the annotations out of a match of %s from step %d, %s',
                document['id'],
                step,
                error,
            )

    own = episode.get('own_seed')
    played = _runner.play_batch(
        setup['asset'],
        tuple(setup['agents']),
        setup['difficulty'],
        1,
        int(episode['seed']),
        actions=actions.reshape(len(actions), 1, width),
        options=setup.get('options') or {},
        device='cpu',
        threads=1,
        first_index=0 if own is not None else int(episode['index']),
        seeds=None if own is None else [int(own)],
        attach=attach,
        before_step=before_step,
    )
    recorder = recorders[0]
    recorder.flush()
    goals = played.goals[0] if setup['side'] == 'left' else played.goals[0][::-1]
    if not recorder.saved:
        raise MessageError('server.evaluate.replay_empty', number=episode['number'])
    recording = recorder.saved[-1].stem
    if (
        goals != [episode['goals_for'], episode['goals_against']]
        or played.lengths[0] != (episode['length'])
    ):
        _library.remove(recording)
        raise MessageError(
            'server.evaluate.replay_differs',
            number=episode['number'],
            expected=repr([episode['goals_for'], episode['goals_against'], episode['length']]),
            found=repr([*goals, played.lengths[0]]),
        )
    return recording, {'annotations_error': failed[0] if failed else None}


def _statistics(recording: str) -> dict[str, Any] | None:
    """Returns what a match keeps of the statistics of its recording, `None` when none read."""
    from .._store import stats as _stats

    try:
        found = _stats.of(recording)
    except (ArgumentError, OSError, ValueError) as error:
        _LOGGER.warning('cuju render found no statistics of %s, %s', recording, error)
        return None
    kept = {key: found.get(key) for key in STATS}
    actions = kept.get('actions')
    if isinstance(actions, dict):
        kept['actions'] = {key: actions.get(key) for key in ('set', 'counts')}
    return kept


def _update(identifier: str, number: int, **fields: Any) -> None:
    """Changes fields of one match of the manifest of a run."""
    document = _runs.get(identifier)
    if document is None:
        return
    episodes = [dict(episode) for episode in document.get('episodes') or []]
    for episode in episodes:
        if episode.get('number') == number:
            episode.update(fields)
    _runner.keep_episodes(identifier, episodes)


def _group(document: dict[str, Any], identifier: str, members: dict[str, int]) -> None:
    """Writes the group of the recordings of a run into the library."""
    from . import evaluate as _evaluate

    config = document.get('config') or {}
    policy = _label_of(config.get('policy') or {})
    if document['kind'] == 'benchmark':
        title = _messages.label('server.names.benchmark_of', competitor=policy)
        summary = (document.get('aggregates') or {}).get('summary') or {}
        result = _messages.label(
            'server.names.benchmark_score', score=f'{summary.get("score", 0):.1f}'
        )
        value = summary.get('score')
    else:
        title = _messages.label('server.names.evaluation_of', policy=policy)
        overall = _evaluate.summarize(document)['overall']
        result = _messages.label(
            'server.names.evaluation_result',
            wins=overall['wins'],
            draws=overall['draws'],
            losses=overall['losses'],
        )
        value = overall['wins']
    group = {
        'id': identifier,
        'kind': document['kind'],
        **_messages.named('title', title),
        **_messages.named('competitor', policy),
        **_messages.named('result', result),
        'state': 'done',
        'attempts': len(members),
        'value': value,
    }
    _library.set_group(group, members)


def run(
    identifier: str, progress: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    """Draws the matches of a run that wait for it and returns what the run keeps of that.

    Each kept match becomes a recording of the group of the run, and each drawn match takes its
    statistics. A match that is drawn only for its statistics leaves no recording behind. A
    match whose actions are missing or changed, or whose replay gives another result, is marked
    with the reason in `draw_error` and the others go on.

    Args:
        identifier: The run.
        progress: A function called after each match with the mapping that the call returns.

    Returns:
        A mapping of the `state` of the drawing, `done` or `failed`, the `group` of the
        recordings, the matches `drawn` and those that `failed`.

    Raises:
        ArgumentError: If the runs manifest holds no such run.
    """
    from . import evaluate as _evaluate

    document = _known(identifier)
    numbers = wanted(identifier)
    record = dict((document.get('aggregates') or {}).get('record') or {})
    group = record.get('group') or _home.new_identifier('g')
    state = {'state': 'running', 'group': group, 'drawn': 0, 'failed': 0, 'pid': os.getpid()}
    _evaluate._set_record(identifier, **state)
    try:
        found = _runner.episodes_of(identifier)
        members: dict[str, int] = {
            episode['recording']: episode['number']
            for episode in document.get('episodes') or []
            if episode.get('recording')
        }
        for number in numbers:
            document = _known(identifier)
            episode = next(e for e in document['episodes'] if e['number'] == number)
            entry = found.get(number)
            try:
                if entry is None:
                    raise MessageError('server.evaluate.actions_missing', number=number)
                if _runner.episode_digest(entry['actions']) != episode.get('digest'):
                    raise MessageError('server.evaluate.actions_changed', number=number)
                recording, notes = _draw(document, episode, entry)
                stats = _statistics(recording)
                if episode.get('keep'):
                    members[recording] = number
                    _update(identifier, number, recording=recording, stats=stats, drawn=True)
                else:
                    _library.remove(recording)
                    _update(identifier, number, stats=stats, drawn=True)
                if notes['annotations_error']:
                    _update(identifier, number, annotations_error=notes['annotations_error'])
                state['drawn'] += 1
            except Exception as error:
                shown = (
                    str(error) if isinstance(error, ArgumentError) else _messages.described(error)
                )
                _update(identifier, number, draw_error=shown, drawn=True)
                state['failed'] += 1
            if members:
                _group(_known(identifier), group, members)
            _evaluate._set_record(identifier, **state)
            if progress is not None:
                progress(dict(state))
        state['state'] = 'done'
    except BaseException as error:
        state.update(state='failed', error=_messages.described(error))
        raise
    finally:
        state.pop('pid', None)
        summary = None
        try:
            document = _runs.get(identifier)
            if document is not None and document['kind'] == 'evaluation':
                summary = _evaluate.summarize(document)
        finally:
            _evaluate._set_record(identifier, summary=summary, **state, pid=None)
    return state


def check(
    identifier: str,
    device: str = 'cpu',
    threads: int = 0,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Plays every match of a run again from its kept actions and confirms every result.

    The kept actions of each match are first held to the digest that the manifest keeps, so a
    file that changed is found even where the change leaves the score as it was. The matches of
    each opponent and side then play at once in one batch, with the kept actions in place of the
    policy, and each score and length is held to those of the manifest.

    Args:
        identifier: The run.
        device: `cpu` or `cuda`. Either gives the same matches.
        threads: The CPU threads, 0 for one for each processor.
        progress: A function called with the share of the matches checked.

    Returns:
        A mapping of the `id` of the run, the matches `checked`, whether every one is the
        `same`, and `problems`, each with the `number` of its match, the `problem`, one of
        `missing` for kept actions that are gone, `changed` for actions that differ from their
        digest, `score` and `length`, and what was `expected` and `found`.

    Raises:
        ArgumentError: If the runs manifest holds no such run.
    """
    document = _known(identifier)
    config = document.get('config') or {}
    setups = config.get('setups') or []
    found = _runner.episodes_of(identifier)
    problems: list[dict[str, Any]] = []
    groups: dict[int, list[dict[str, Any]]] = {}
    # A match that kept no actions, as an attempt of a team's run of the benchmark, has nothing
    # to play again.
    episodes = [
        e
        for e in document.get('episodes') or []
        if e.get('outcome') is not None and e.get('digest') is not None
    ]
    for episode in episodes:
        entry = found.get(episode['number'])
        if entry is None:
            problems.append({'number': episode['number'], 'problem': 'missing'})
        elif _runner.episode_digest(entry['actions']) != episode.get('digest'):
            problems.append(
                {
                    'number': episode['number'],
                    'problem': 'changed',
                    'expected': episode.get('digest'),
                    'found': _runner.episode_digest(entry['actions']),
                }
            )
        else:
            groups.setdefault(episode['setup'], []).append(episode)
    done = 0
    for setup_index, group in sorted(groups.items()):
        setup = setups[setup_index]
        own = group[0].get('own_seed') is not None
        if own:
            columns = list(range(len(group)))
            first, count = 0, len(group)
        else:
            first = min(episode['index'] for episode in group)
            columns = [episode['index'] - first for episode in group]
            count = max(columns) + 1
        longest = max(len(found[episode['number']]['actions']) for episode in group)
        width = sum(setup['agents'])
        actions = np.zeros((longest, count, width), dtype=np.uint8)
        for column, episode in zip(columns, group, strict=True):
            rows = found[episode['number']]['actions']
            actions[: len(rows), column] = rows.reshape(len(rows), width)
        played = _runner.play_batch(
            setup['asset'],
            tuple(setup['agents']),
            setup['difficulty'],
            count,
            int(group[0]['seed']),
            actions=actions,
            options=setup.get('options') or {},
            device=device,
            threads=threads,
            first_index=first,
            seeds=[int(episode['own_seed']) for episode in group] if own else None,
        )
        for column, episode in zip(columns, group, strict=True):
            goals = played.goals[column] if setup['side'] == 'left' else played.goals[column][::-1]
            expected = [episode['goals_for'], episode['goals_against']]
            if goals != expected:
                problems.append(
                    {
                        'number': episode['number'],
                        'problem': 'score',
                        'expected': expected,
                        'found': goals,
                    }
                )
            elif played.lengths[column] != episode['length']:
                problems.append(
                    {
                        'number': episode['number'],
                        'problem': 'length',
                        'expected': episode['length'],
                        'found': played.lengths[column],
                    }
                )
        done += len(group)
        if progress is not None:
            progress(done / max(1, len(episodes)))
    problems.sort(key=lambda problem: problem['number'])
    return {
        'id': identifier,
        'checked': len(episodes),
        'same': not problems,
        'problems': problems,
    }


def main(arguments: list[str] | None = None) -> None:
    """Draws the kept matches of the run that the command line names."""
    parser = argparse.ArgumentParser(prog='python -m cuju.arena._evaluate.replay')
    parser.add_argument('run', help='the identifier of a run')
    options = parser.parse_args(arguments)
    try:
        run(options.run)
    except Exception as error:
        print(_messages.described(error), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
