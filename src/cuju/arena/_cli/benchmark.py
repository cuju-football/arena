# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The command `cuju arena benchmark`, which plays models on the academy tasks."""

from __future__ import annotations

import argparse
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import _references as references
from . import _words as words
from . import contract

_NO_PLAYABLE = 'No task fits this team and nothing was played.'


def register(commands: Any) -> None:
    """Adds the benchmark commands."""
    from .._evaluate import ACADEMY_TASKS

    actions = contract.group(commands, 'benchmark', 'run benchmarks on academy tasks')
    tasks = contract.leaf(actions, 'tasks', 'list the academy tasks', '--json')
    contract.details(tasks, 'Each task has five fixed seeds. The same seeds serve every model.')
    parser = contract.leaf(
        actions,
        'run',
        'play teams on academy tasks',
        'builtin:0.3 --task empty_goal_close',
        'greens@baseline builtin --task corner --json',
    )
    contract.details(
        parser,
        'Use benchmark tasks to find the eleven tasks. Each task uses '
        'five fixed seeds. Use --set benchmark.format=NAME to choose a format.',
    )
    contract.details(
        parser,
        'The score is the mean share of attempts with a goal across '
        'the tasks. Use --json to see each cell, its attempts and any failure. '
        + references.CHECKPOINT,
    )
    words.offer(
        parser,
        'team',
        completion=words.TEAMS,
        nargs='*',
        type=contract.team_name,
        metavar='TEAM',
        help=references.ENTRANT + ', the policy of the settings if omitted',
    )
    words.offer(
        parser,
        '--task',
        completion=ACADEMY_TASKS,
        action='append',
        default=[],
        metavar='NAME',
        help='an academy task, all eleven by default, may repeat',
    )
    contract.device(parser, default=None)
    parser.add_argument(
        '--keep-recordings', action='store_true', help='keep attempts as recordings'
    )
    parser.add_argument(
        '--workers',
        type=int,
        metavar='N',
        help='model and task pairs played at once, from 1 to 8, automatic by default',
    )
    parser.add_argument('--csv', action='store_true', help='print the table as CSV')


def run(namespace: argparse.Namespace) -> int:
    """Runs `cuju arena benchmark` and returns the exit status.

    Each run plays in a child process as on the server, and its end prints one line on standard
    error. The table goes to standard output, as text, as JSON under `--json` or as CSV under
    `--csv`.
    """
    from .._evaluate import academy as _academy
    from .._evaluate import benchmark as _benchmark

    if namespace.action == 'tasks':
        found = _academy.tasks()
        contract.list_answer(
            'tasks',
            found,
            ((e['id'], e['scenario'], f'{e["attempts"]} attempts') for e in found),
            'Run cuju arena config show to inspect the academy settings.',
        )
        return 0
    from .._config import layers as _layers

    named = _layers.table('evaluate', namespace.config or None, namespace.overrides)['policy']
    if not namespace.team and named is not None:
        return _one_model(namespace)

    # The options of the command line come last, over the settings of the files and of --set.
    settings = _layers.table('benchmark', namespace.config or None, namespace.overrides)
    tasks = namespace.task or (None if settings['tasks'] in (None, 'all') else settings['tasks'])
    workers = namespace.workers
    if workers is None and settings['workers'] not in (None, 'auto'):
        workers = settings['workers']
    device = namespace.device
    if device is None:
        device = _layers.table('evaluate', namespace.config or None, namespace.overrides)['device']
    from .._formats import context as _context

    document = _benchmark.plan(
        [references.team(team) for team in namespace.team],
        tasks,
        namespace.keep_recordings or settings['keep_recordings'],
        workers,
        device,
        submission_format=_context.from_settings(settings),
    )
    processes: set[Any] = set()
    stop = threading.Event()

    with contract.Progress(namespace.quiet) as progress, ThreadPoolExecutor(1) as executor:

        def changed(cell: dict[str, Any] | None) -> None:
            if (
                cell is not None
                and cell['state'] in ('done', 'failed')
                and _benchmark.cell_fits(cell)
            ):
                progress(_benchmark.cell_line(document, cell))

        worker = executor.submit(
            _benchmark.run,
            document=document,
            changed=changed,
            cancelled=stop.is_set,
            started=processes.add,
        )
        try:
            worker.result()
        except KeyboardInterrupt:
            stop.set()
            for process in list(processes):
                process.kill()
            raise
        if document['playable']:
            for cell in document['cells']:
                if not _benchmark.cell_fits(cell):
                    progress(_benchmark.cell_line(document, cell))
    if not document['playable']:
        contract.note(_NO_PLAYABLE)
    if namespace.json:
        contract.emit(document)
    elif namespace.csv:
        print(_benchmark.csv_text(document), end='')
    else:
        for row in sorted(document['rows'], key=lambda item: item['place']):
            print(f'place {row["place"]}, {row["name"]}, score {row["score"]:.1f}')
            for cell in document['cells']:
                if cell['team'] == row['team']:
                    print(f'    {_benchmark.cell_line(document, cell, named=False)}')
    return 0


def _one_model(namespace: argparse.Namespace) -> int:
    """Plays the policy that the settings name on the tasks and prints its benchmark."""
    from .._evaluate import benchmark as _benchmark

    given: dict[str, Any] = {}
    if namespace.task:
        given.setdefault('benchmark', {})['tasks'] = list(namespace.task)
    if namespace.keep_recordings:
        given.setdefault('benchmark', {})['keep_recordings'] = True
    if namespace.workers is not None:
        given.setdefault('benchmark', {})['workers'] = namespace.workers
    if namespace.device is not None:
        given.setdefault('evaluate', {})['device'] = namespace.device

    layers = [*namespace.config, *([given] if given else [])]
    with contract.Progress(namespace.quiet) as progress:

        def shown(event: dict[str, Any]) -> None:
            if event['playable_ended']:
                progress(f'{event["played"]} of {event["total"]} tasks played')

        found = _benchmark.benchmark(None, layers or None, namespace.overrides, progress=shown)
        found.wait()
    document = found.to_dict()
    if not document['playable']:
        contract.note(_NO_PLAYABLE)
    if namespace.json:
        contract.emit(document)
    elif namespace.csv:
        print(_benchmark.csv_text(document['benchmark']), end='')
    else:
        from .evaluate import summary_lines

        for line in summary_lines(document):
            print(line)
    return 0
