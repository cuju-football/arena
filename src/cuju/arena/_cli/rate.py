# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The command `cuju arena rating`, which gathers evidence for models and shows the ratings."""

from __future__ import annotations

import argparse
from typing import Any

from . import _references as references
from . import _words as words
from . import contract


def register(commands: Any) -> None:
    """Adds the rating commands."""
    actions = contract.group(commands, 'rating', 'gather and inspect rating evidence')
    run = contract.leaf(
        actions,
        'run',
        'play matches to gather rating evidence',
        'builtin:0.3 --scenario 11_vs_11_kaggle',
        'greens@baseline greens@latest --scenario 5_vs_5',
    )
    words.offer(
        run,
        'models',
        completion=words.TEAMS,
        nargs='+',
        type=contract.team_name,
        metavar='TEAM',
        help=references.ENTRANT,
    )
    contract.details(
        run,
        'Each model first plays the built-in anchor at difficulty 0.6 '
        'by default. Use --set ratings.run.most=N to bound the run. ' + references.CHECKPOINT,
    )
    words.offer(
        run,
        '--scenario',
        completion=words.scenarios(),
        required=True,
        metavar='SCENARIO',
        help='the scenario to rate on',
    )
    table = contract.leaf(actions, 'table', 'show ratings for a scenario', '--json')
    contract.details(
        table, 'A model row shows the checkpoint identifier, label and training step when known.'
    )
    words.offer(
        table,
        '--scenario',
        completion=words.scenarios(),
        metavar='SCENARIO',
        help='the scenario of the ratings',
    )
    words.offer(
        table,
        '--method',
        completion=('points', 'bradley_terry'),
        metavar='NAME',
        help='points, bradley_terry or a plugin method, points by default, requires --scenario',
    )
    contract.leaf(actions, 'list', 'list the rating runs', '--json')
    for verb, summary, sample in (
        ('show', 'show a rating run', 'rating-1'),
        ('check', 'replay and check a rating run', 'rating-1'),
        ('draw', 'save a rating match as a recording', 'rating-1 0'),
    ):
        parser = contract.leaf(actions, verb, summary, sample)
        parser.add_argument('identifier', metavar='ID', help='the identifier from rating list')
        if verb == 'show':
            contract.details(parser, 'Use --json to see matches and their zero-based numbers.')
        if verb == 'check':
            contract.details(parser, 'Replays the saved actions and compares match results.')
        if verb == 'draw':
            parser.add_argument(
                'number',
                type=int,
                metavar='N',
                help='the zero-based match number from rating show --json',
            )


def _bounds(entry: dict[str, Any]) -> str:
    return f'{entry["rating"]:.0f} from {entry["low"]:.0f} to {entry["high"]:.0f}'


def _heading(table: dict[str, Any]) -> str:
    anchor = table['anchor']
    return (
        f'{table["scenario"]}, {table["results"]} results, ranked by {table["method"]}, '
        f'anchor {anchor["name"]} at {anchor["rating"]:.0f}'
    )


def _name(row: dict[str, Any]) -> str:
    name = row['name']
    if row['checkpoint'] is not None:
        name += f' at {row["checkpoint"]}'
        if row.get('label') and row['label'] != row['key']:
            name += f', {row["label"]}'
        if row.get('step') is not None:
            name += f', step {row["step"]}'
    return name


def _evidence(row: dict[str, Any]) -> str:
    from .._text.messages import counted

    matches = counted(row['matches'], 'match', 'matches')
    text = f'{matches}, {row["anchor_matches"]} against the anchor'
    if row['anchor']:
        text += ', the anchor'
    elif row['lacking']:
        text += ', lacks evidence'
    if not row['latest']:
        text += ', an earlier checkpoint'
    return text


def table_lines(table: dict[str, Any]) -> list[str]:
    """Returns the table of a scenario in lines of English, as the command prints them."""
    lines = [_heading(table)]
    for row in table['models']:
        lines.append(
            f'{row["rank"]:>4}  {_name(row)}, points {_bounds(row["points"])}, Bradley and Terry '
            f'{_bounds(row["bradley_terry"])}, {_evidence(row)}'
        )
    return lines


def run_lines(document: dict[str, Any]) -> list[str]:
    """Returns a rating run in lines of English, as the command prints them."""
    from .._text.messages import counted

    progress = document['progress']
    lines = [
        f'{document["id"]}, {document["scenario"]}, {document["state"]}, '
        f'{counted(progress["played"], "match", "matches")} played'
    ]
    if document.get('error'):
        lines.append(document['error'])
    after = document.get('after') or {}
    for key in document['models']:
        row = after.get(key)
        entrant = progress['entrants'].get(key) or {}
        name = entrant.get('name') or key
        if entrant.get('checkpoint') is not None:
            name = _name({**entrant, 'key': key.partition('@')[0]})
        if row is None:
            lines.append(f'    {name}, no result')
            continue
        text = (
            f'    {name}, points {_bounds(row["points"])}, Bradley and Terry '
            f'{_bounds(row["bradley_terry"])}, '
            f'{counted(row["matches"], "match", "matches")}, '
            f'{row["anchor_matches"]} against the anchor'
        )
        if row['lacking']:
            text += ', lacks evidence'
        lines.append(text)
    return lines


def run(namespace: argparse.Namespace) -> int:
    """Runs `cuju arena rating` and returns the exit status.

    A new rating run prints one line of progress for each change on standard error and its
    ratings on standard output, as text or as JSON under `--json`. `--check` returns 1 when a
    match plays another result than the run kept.
    """
    from .._evaluate import ratings as _ratings
    from .._text.messages import counted

    def show(found: Any, lines: list[str]) -> None:
        if namespace.json:
            contract.emit(found)
        elif lines:
            print('\n'.join(lines))

    action = namespace.action
    if action == 'list':
        found = _ratings.runs()
        contract.list_answer(
            'runs',
            found,
            (
                (e['id'], e['scenario'], e['state'], counted(e['played'], 'match', 'matches'))
                for e in found
            ),
            'Run cuju arena rating run TEAM --scenario SCENARIO to gather evidence.',
        )
        return 0
    if action == 'show':
        document = _ratings.get(namespace.identifier)
        show({'run': document}, run_lines(document))
        return 0
    if action == 'check':
        found = _ratings.check(namespace.identifier)
        word = 'the same' if found['same'] else 'another result'
        show(
            found,
            [
                *(
                    f'match {problem["number"]}, {problem["problem"]}, expected '
                    f'{problem.get("expected")}, found {problem.get("found")}'
                    for problem in found['problems']
                ),
                f'{counted(found["checked"], "match", "matches")} played again, {word}',
            ],
        )
        return 0 if found['same'] else 1
    if action == 'draw':
        from pathlib import Path

        from .._store import library

        found = _ratings.draw(namespace.identifier, [namespace.number])
        if found['failed']:
            reason = found['failed'][str(namespace.number)]
            raise contract.RefusalError(
                f'expected a recording for match {namespace.number}, {reason}'
            )
        paths: list[Path] = []
        for identifier in found['drawn'].values():
            path = library.path_of(identifier, include_trash=True)
            if path is None:
                raise contract.RefusalError(
                    f'expected a recording for match {namespace.number}, '
                    f'found no file for {identifier!r}',
                    hint='run cuju arena recording list',
                )
            paths.append(path)
        contract.files(paths, namespace.json)
        return 0
    if action == 'table':
        if not namespace.scenario:
            if namespace.method:
                namespace._parser.error('expected --scenario with --method, found no scenario')
            found = _ratings.overview()
            entries = found['scenarios']
            if namespace.json:
                contract.emit({'table': None, 'overview': found})
            else:
                contract.listing(
                    (e['scenario'], f'{e["models"]} models', f'{e["results"]} results')
                    for e in entries
                )
            if not entries:
                contract.note(
                    'Run cuju arena rating run TEAM --scenario SCENARIO to gather evidence.'
                )
            return 0
        table = _ratings.table(namespace.scenario, method=namespace.method)
        contract.note(_heading(table))
        if namespace.json:
            contract.emit({'table': table, 'overview': None})
        else:
            contract.listing(
                (
                    row['id'],
                    row['rank'],
                    _name(row),
                    f'points {_bounds(row["points"])}',
                    f'Bradley and Terry {_bounds(row["bradley_terry"])}',
                    _evidence(row),
                )
                for row in table['models']
            )
        return 0
    with contract.Progress(namespace.quiet) as progress:

        def shown(event: dict[str, Any]) -> None:
            progress(f'{event["played"]} of {event["total"]} matches played')

        models = [references.team(value.removeprefix('team:')) for value in namespace.models]
        document = _ratings.rate(models, namespace.scenario, progress=shown)
    show(document, run_lines(document))
    return 0 if document['state'] == 'done' else 1
