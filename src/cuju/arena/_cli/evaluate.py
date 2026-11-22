# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Runs, checks and lists evaluations."""

from __future__ import annotations

import argparse
from typing import Any

from . import _references as references
from . import _words as words
from . import contract


def register(commands: Any) -> None:
    """Adds the evaluation commands."""
    actions = contract.group(commands, 'evaluation', 'run and inspect evaluations')
    run = contract.leaf(
        actions,
        'run',
        'run an evaluation',
        'builtin',
        'builtin --episodes 1 --series baseline',
    )
    words.offer(
        run,
        'team',
        completion=words.TEAMS,
        nargs='?',
        type=contract.team_name,
        metavar='TEAM',
        help=(references.ENTRANT + ', the policy of the settings by default'),
    )
    contract.details(
        run,
        'The default plays 100 episodes per opponent on 11_vs_11_kaggle '
        'against builtin:0.05, builtin:0.6 and builtin:1. '
        'The seed repeats the match setup. The recording rule keeps losses by default.',
    )
    contract.details(run, references.CHECKPOINT)
    contract.details(
        run, 'Use --episodes 1 for one episode per opponent, as with --set evaluate.episodes=1.'
    )
    run.add_argument(
        '--episodes',
        type=int,
        metavar='N',
        help='the episodes per opponent, evaluate.episodes by default',
    )
    run.add_argument(
        '--series',
        metavar='NAME',
        help='the series name as text, or none to unset it, evaluate.run by default',
    )
    listing = contract.leaf(actions, 'list', 'list evaluations', '--series baseline')
    listing.add_argument(
        '--series', metavar='NAME', help='the series name set by evaluation run or evaluate.run'
    )
    for verb, summary in (
        ('show', 'show an evaluation'),
        ('remove', 'remove an evaluation'),
        ('check', 'replay and check an evaluation'),
    ):
        parser = contract.leaf(actions, verb, summary, 'last')
        references.kept_argument(parser, 'ID')
        if verb == 'show':
            contract.details(parser, 'Shows the summary and gives the individual matches in JSON.')
        if verb == 'check':
            contract.details(
                parser,
                'Replays kept actions from the saved setup and seeds and compares the results.',
            )
            contract.device(parser)


def _percent(value: Any) -> str:
    return 'none' if value is None else f'{100 * value:.1f}%'


def summary_lines(document: dict[str, Any]) -> list[str]:
    """Returns the results of an evaluation in lines of English, as the command prints them."""
    from .._text.messages import counted

    summary = document['summary']
    lines = [
        f'{document["id"]}, {document["name"]}, {document["state"]}, '
        f'{counted(len(document["episodes"]), "match", "matches")}, '
        f'{counted(len(document["recordings"]), "recording")}'
    ]
    if document['kind'] == 'benchmark':
        for task in summary.get('tasks') or []:
            shown = (
                f'{task["value"]} of {task["attempts"]}'
                if task['state'] == 'done'
                else task['state']
            )
            lines.append(f'    {task["task"]}, {shown}')
        lines.append(f'score {summary.get("score", 0.0):.1f}')
        return lines

    def line(name: str, entry: dict[str, Any]) -> str:
        low, high = entry['win_rate_interval']
        difference = entry['goal_difference']
        matches = counted(entry['episodes'], 'match', 'matches')
        text = (
            f'{name}, {matches}, {entry["wins"]} won, {entry["draws"]} drawn, '
            f'{entry["losses"]} lost, win rate {_percent(entry["win_rate"])} from '
            f'{_percent(low)} to {_percent(high)}'
        )
        if difference['mean'] is not None:
            text += (
                f', goal difference {difference["mean"]:+.2f} from {difference["low"]:+.2f} '
                f'to {difference["high"]:+.2f}'
            )
        if entry.get('failed'):
            text += f', {entry["failed"]} without a result'
        return text

    for opponent in summary['opponents']:
        lines.append('    ' + line(f'against {opponent.get("name")}', opponent))
    lines.append(line('overall', summary['overall']))
    return lines


def run(namespace: argparse.Namespace) -> int:
    """Runs an evaluation command."""
    from .._evaluate import evaluate
    from .._registry import teams
    from .._store import runs
    from .._text.messages import counted

    action = namespace.action
    if action in ('show', 'check', 'remove') and runs.get(namespace.target) is None:
        namespace.target = references.kept(namespace.target, evaluate.evaluations(), 'e-')
    if action == 'list':
        found = evaluate.evaluations(namespace.series)
        hint = 'Run cuju arena evaluation run TEAM to start an evaluation.'
        if namespace.series is not None and not found:
            known = sorted({entry['run'] for entry in evaluate.evaluations() if entry['run']})
            if known:
                hint = f'Known series are {", ".join(repr(name) for name in known)}.'
        contract.list_answer(
            'evaluations',
            found,
            (
                (
                    e['id'],
                    e['name'],
                    e['state'],
                    counted(e['played'], 'match', 'matches'),
                    f'win rate {_percent((e.get("overall") or {}).get("win_rate"))}',
                )
                for e in found
            ),
            hint,
        )
        return 0
    if action == 'remove':
        if not evaluate.remove(namespace.target):
            raise contract.RefusalError(
                f'expected an evaluation, found {namespace.target!r}',
                hint='run cuju arena evaluation list',
            )
        if namespace.json:
            contract.emit({'removed': namespace.target})
        else:
            print(f'removed {namespace.target}')
        return 0
    if action == 'check':
        from .._evaluate import replay

        found = replay.check(namespace.target, namespace.device)
        if namespace.json:
            contract.emit(found)
        else:
            for problem in found['problems']:
                print(
                    f'match {problem["number"]}, {problem["problem"]}, expected '
                    f'{problem.get("expected")}, found {problem.get("found")}'
                )
            word = 'the same' if found['same'] else 'another result'
            print(f'{counted(found["checked"], "match", "matches")} played again, {word}')
        return 0 if found['same'] else 1
    if action == 'show':
        evaluation = evaluate.get(namespace.target)
    else:
        team, difficulty = contract.team_value(namespace.team) if namespace.team else (None, None)
        if team == 'random':
            team = teams.RANDOM_ACTIONS
        policy = team if team in (None, 'builtin') else f'team:{references.team(team)}'
        with contract.Progress(namespace.quiet) as progress:

            def shown(event: dict[str, Any]) -> None:
                progress(f'{event["id"]}  {event["played"]} of {event["total"]} matches played')

            overrides = []
            if difficulty is not None:
                overrides.append(f'evaluate.difficulty={difficulty}')
            if namespace.episodes is not None:
                overrides.append(f'evaluate.episodes={namespace.episodes}')
            if namespace.series is not None:
                from .._config.files import value_text

                overrides.append(f'evaluate.run={value_text(namespace.series)}')
            evaluation = evaluate.evaluate(policy, progress=shown, overrides=overrides)
            evaluation.wait()
    document = evaluation.to_dict()
    if namespace.json:
        contract.emit(document)
    else:
        print('\n'.join(summary_lines(document)))
    return 0 if document['state'] == 'done' or action == 'show' else 1
