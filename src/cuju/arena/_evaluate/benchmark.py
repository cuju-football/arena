# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The academy benchmark, registered teams that play the chosen academy tasks headless as one job.

A benchmark plays every pair of a model and a task as a full run of `academy`, in a child process
of its own, `python -m cuju.arena._evaluate.academy`. Each run therefore keeps the task's seeds
and scoring, and no model's run shares a process with another. A policy that fails ends its
attempt alone, which counts as failed, and a run that cannot play at all leaves its cell failed,
so the job always goes on to the next pair.

The results form a table of models by tasks. The score of a cell is its goal rate, the share of
its attempts in which the model scored, from 0 to 1. The score of a model is the mean of its goal
rates over the tasks of the benchmark, 0 for a cell without a result, times 100 and rounded to
one decimal, so 100 means a goal in every attempt of every task. Models place by score, and
models level on it share a place. `csv_text` writes the table as CSV, which the page writes the
same way.

The server plays one benchmark at a time, and within it several runs at once, `workers` of them,
one for every four logical processors of the machine by default, from 1 to 4. Each benchmark is a
run of the runs manifest, `runs/<id>.json` of kind `benchmark`, written as it goes, at its queueing,
after each attempt and at each end of a cell, so it survives a restart of the server. The
manifest keeps the request in `config` and the benchmark as the page reads it in `aggregates`. A
server that starts lists the newest 20 ended benchmarks with every waiting or running one, and
plays on a benchmark that a stop left unfinished, its ended cells as they were and the others
from their start.

`benchmark` plays one model on the tasks from Python and returns an `Evaluation`. A policy that
plays in a batch, a function or the built-in AI, plays every attempt of a task at once in one
batch, each attempt on the seed of the catalogue, so it faces the episodes that a team's run and
a person's attempt face. The runner keeps the actions alone, and a benchmark that keeps its
recordings has them drawn afterwards, as an evaluation does. A team of the registry plays its
runs in child processes, as on the server. The run goes to the runs manifest as it goes, in the
form that the list of benchmarks reads, so the page shows it beside its own.
"""

from __future__ import annotations

import contextlib
import copy
import csv
import io
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from cuju.errors import ArgumentError

from .._registry import teams as _teams
from .._store import home as _home
from .._store import runs as _runs
from .._text import messages as _messages
from .._text.messages import MessageError
from . import academy as _academy

_LOGGER = logging.getLogger('cuju')
# A benchmark plays at most this many runs at once.
MOST_WORKERS = 8
# The server keeps this many ended benchmarks in its list.
_KEPT = 20
# The states of a cell. A skipped cell names a pair that cannot play, such as a team of another
# format than its task's scenario.
CELL_STATES = ('queued', 'running', 'done', 'failed', 'skipped', 'cancelled')


def default_workers() -> int:
    """Returns the runs a benchmark plays at once by default, one for every four processors."""
    return max(1, min(4, (os.cpu_count() or 1) // 4))


def _distinct_texts(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) for item in value)
        and len(set(value)) == len(value)
    )


def _team_ids(value: Any) -> list[str]:
    if not _distinct_texts(value):
        raise MessageError('server.benchmark.teams', teams=repr(value))
    return list(value)


def _task_ids(value: Any) -> list[str]:
    if value is None:
        return list(_academy.TASKS)
    if not _distinct_texts(value):
        raise MessageError('server.benchmark.tasks', tasks=repr(value))
    for identifier in value:
        _academy.find(identifier)
    # The table lists the tasks in the order of the academy, whatever the order asked.
    return [identifier for identifier in _academy.TASKS if identifier in value]


def _workers(value: Any) -> int:
    if value is None:
        return default_workers()
    whole = isinstance(value, int) and not isinstance(value, bool)
    if not whole or not 1 <= value <= MOST_WORKERS:
        raise MessageError('server.benchmark.workers', most=MOST_WORKERS, workers=repr(value))
    return value


def plan(
    teams: Any,
    tasks: Any = None,
    keep_recordings: Any = False,
    workers: Any = None,
    device: str = 'cpu',
    submission_format: Any = None,
) -> dict[str, Any]:
    """Returns a new benchmark, its models, tasks and a queued cell for every pair.

    Args:
        teams: The models, a list of distinct team identifiers of the registry, `builtin` among
            them if wanted.
        tasks: The tasks, a list of distinct identifiers of `academy.TASKS`, or `None` for every
            one.
        keep_recordings: Whether the library keeps the attempts as recordings, false by default.
        workers: The runs played at once, from 1 to 8, or `None` for `default_workers`.
        device: `cpu` or `cuda`.
        submission_format: The format every team plays its attempts under, a format's
            identifier or a mapping `{id, preset, options}`, or `None` for Kaggle open.

    Raises:
        ArgumentError: If a team, a task, the keeping, the workers or the format do not fit.
    """
    from .._formats import context as _context

    identifiers = _team_ids(teams)
    choice = _context.check(submission_format)
    chosen = _task_ids(tasks)
    keep = _academy.keep_flag(keep_recordings)
    count = _workers(workers)
    models, cells = [], []
    for identifier in identifiers:
        entry = _academy.team_entry(identifier)
        models.append({'team': identifier, **_messages.named('name', _academy.entrant_name(entry))})
        for task in chosen:
            spec = _academy.find(task)
            cell = _cell(identifier, spec)
            try:
                _teams.task_fit(entry, spec['scenario'])
            except ArgumentError as error:
                cell.update(state='skipped', **_messages.entry_fields(error))
            cells.append(cell)
    document = {
        'teams': identifiers,
        'tasks': chosen,
        'models': models,
        'cells': cells,
        'playable': _playable(cells),
        'keep_recordings': keep,
        'workers': count,
        'device': device,
        'submission_format': choice,
    }
    document['rows'] = table(document)
    document['progress'] = progress_share(document)
    return document


def _cell(team: str, spec: dict[str, Any]) -> dict[str, Any]:
    return {
        'team': team,
        'task': spec['id'],
        'state': 'queued',
        'attempts': spec['attempts'],
        'played': 0,
        'value': None,
        'rate': None,
        'failed_attempts': 0,
        # The players that played and those the team controls, for a team with players out.
        'playing': None,
        'group': None,
        'results': [],
        **_messages.entry_fields(None),
    }


def rate(cell: dict[str, Any]) -> float:
    """Returns the goal rate of a cell, from 0 to 1, and 0 for a cell without a result."""
    found = cell.get('rate')
    numeric = isinstance(found, (int, float)) and not isinstance(found, bool)
    if cell['state'] != 'done' or not numeric:
        return 0.0
    return min(1.0, max(0.0, float(found)))


def table(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Returns the row of each model, its score and place, in the order of the models.

    The score is 100 times the mean goal rate over the benchmark's tasks, rounded to one decimal,
    and the place counts the models ahead by score, so models level on it share a place.
    """
    count = len(document['tasks'])
    rows = []
    for model in document['models']:
        cells = [cell for cell in document['cells'] if cell['team'] == model['team']]
        score = round(100 * sum(rate(cell) for cell in cells) / count, 1) if count else 0.0
        rows.append(
            {
                **model,
                'score': score,
                'done': sum(cell['state'] == 'done' for cell in cells),
                'place': None,
            }
        )
    for row in rows:
        row['place'] = 1 + sum(other['score'] > row['score'] for other in rows)
    return rows


def progress_share(document: dict[str, Any]) -> float:
    """Returns the share of the attempts played over the cells that play, from 0 to 1."""
    playing = [cell for cell in document['cells'] if cell['state'] != 'skipped']
    total = sum(cell['attempts'] for cell in playing)
    if not total:
        return 1.0
    played = sum(
        cell['attempts'] if cell['state'] in ('done', 'failed') else cell['played']
        for cell in playing
    )
    return round(played / total, 4)


def cell_fits(cell: dict[str, Any]) -> bool:
    """Returns whether the team and the format admit the task."""
    return cell['state'] != 'skipped' and cell.get('error_id') != 'server.benchmark.refused'


def _playable(cells: list[dict[str, Any]]) -> int:
    """Returns the number of team and task pairs eligible to play."""
    return sum(cell_fits(cell) for cell in cells)


def csv_text(document: dict[str, Any]) -> str:
    """Returns the table of a benchmark as CSV, one row for each model.

    The columns are the model's name and team, the goal rate of each task in percent with one
    decimal, empty for a cell without a result, and the model's score and place.
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(['model', 'team', *document['tasks'], 'score', 'place'])
    cells = {(cell['team'], cell['task']): cell for cell in document['cells']}
    for row in document['rows']:
        line = [row['name'], row['team']]
        for identifier in document['tasks']:
            cell = cells[(row['team'], identifier)]
            line.append(f'{100 * rate(cell):.1f}' if cell['state'] == 'done' else '')
        writer.writerow([*line, f'{row["score"]:.1f}', row['place']])
    return output.getvalue()


class CellError(Exception):
    """Raised for a run of a benchmark that ended without a result, with its error fields."""

    def __init__(self, fields: dict[str, Any]) -> None:
        super().__init__(fields.get('error'))
        self.fields = fields


def play_cell(
    document: dict[str, Any],
    cell: dict[str, Any],
    attempt: Callable[[dict[str, Any]], None],
    started: Callable[[subprocess.Popen], None] | None = None,
) -> dict[str, Any]:
    """Plays one cell of a benchmark in a child process and returns the result of its run.

    Raises:
        CellError: If the run ended without a result.
    """
    command = _academy.child_command(
        cell['task'],
        cell['team'],
        document['keep_recordings'],
        document['device'],
        document.get('submission_format'),
    )
    found: dict[str, Any] = {}

    def report(line: dict[str, Any]) -> None:
        if isinstance(line.get('attempt'), dict):
            attempt(line['attempt'])
        if isinstance(line.get('result'), dict):
            found['result'] = line['result']
        if isinstance(line.get('error'), str):
            found['error'] = _messages.report_fields(line)

    code, tail = _academy.play_child(command, report, started)
    if code == 0 and 'result' in found:
        return found['result']
    raise CellError(
        found.get('error') or _messages.exit_fields('server.benchmark.exit', code, tail)
    )


def run(
    document: dict[str, Any],
    play: Callable[..., dict[str, Any]] = play_cell,
    changed: Callable[[dict[str, Any] | None], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    started: Callable[[subprocess.Popen], None] | None = None,
    lock: Any = None,
) -> dict[str, Any]:
    """Plays every queued cell of a benchmark, `workers` at a time, and returns the benchmark.

    Each cell takes its value, goal rate, failed attempts and the results of its attempts from
    its run, and a run without a result leaves its cell failed with the error.

    Args:
        document: The benchmark, as `plan` returns it, which the function fills in.
        play: The function that plays a cell, as `play_cell` does, by default `play_cell`.
        changed: A function called with the cell after each attempt and each end of a cell.
        cancelled: A function that says whether the benchmark should stop, after which no cell
            starts.
        started: A function called with each child process once it has started.
        lock: The lock that guards the document, a new one by default.
    """
    lock = lock or threading.Lock()

    def attempt_of(cell: dict[str, Any]) -> Callable[[dict[str, Any]], None]:
        def seen(result: dict[str, Any]) -> None:
            with lock:
                cell['results'] = [*cell['results'], result]
                cell['played'] = len(cell['results'])
                document['progress'] = progress_share(document)
            if changed is not None:
                changed(cell)

        return seen

    def worker() -> None:
        while True:
            with lock:
                if cancelled is not None and cancelled():
                    return
                cell = next((item for item in document['cells'] if item['state'] == 'queued'), None)
                if cell is None:
                    return
                cell['state'] = 'running'
            try:
                result = play(document, cell, attempt_of(cell), started)
                ended = {
                    'state': 'done',
                    'value': result['value'],
                    'rate': result['rate'],
                    'group': result.get('group'),
                    'failed_attempts': result.get('failed_attempts', 0),
                    'playing': result.get('playing'),
                    'results': list(result['attempts']),
                    'played': len(result['attempts']),
                }
            except CellError as failure:
                ended = {'state': 'failed', **_messages.entry_fields(None), **failure.fields}
            except Exception as error:
                ended = {'state': 'failed', **_messages.entry_fields(error, str(error))}
            with lock:
                if cell['state'] == 'running':
                    cell.update(ended)
                document['playable'] = _playable(document['cells'])
                document['rows'] = table(document)
                document['progress'] = progress_share(document)
            if changed is not None:
                changed(cell)

    threads = [
        threading.Thread(target=worker, daemon=True, name=f'cuju-benchmark-{number}')
        for number in range(document['workers'])
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with lock:
        document['rows'] = table(document)
        document['progress'] = progress_share(document)
    return document


# The benchmarks of the server, played one at a time.


# The fields of a benchmark that the request gave, which the manifest keeps as its `config`.
_REQUEST = ('teams', 'tasks', 'keep_recordings', 'workers', 'device', 'submission_format')


def _stored(job: dict[str, Any]) -> dict[str, Any]:
    """Returns the fields of the manifest of a benchmark, a copy that the lock held."""
    state = job['state'] if job['state'] in _runs.STATES else 'failed'
    return {
        'scenarios': [_scenario_of(task) for task in job['tasks']],
        'opponents': list(job['teams']),
        'config': {key: job[key] for key in _REQUEST if key in job},
        'aggregates': {key: value for key, value in job.items() if key not in ('id', 'state')},
        'state': state,
    }


def _scenario_of(task: str) -> str | None:
    try:
        return _academy.find(task)['scenario']
    except ArgumentError:
        return None


def _save(identifier: str, fields: dict[str, Any]) -> None:
    """Writes the manifest of a benchmark, and logs a write that fails, which stops nothing."""
    try:
        if _runs.get(identifier) is None:
            _runs.create('benchmark', identifier, **fields)
        else:
            _runs.update(identifier, **fields)
    except Exception as error:
        _LOGGER.warning('cuju render could not keep the benchmark %s: %s', identifier, error)


def _restored(document: dict[str, Any]) -> dict[str, Any] | None:
    """Returns the benchmark that a manifest keeps, ready to play on, or `None` for one that does
    not fit.

    A benchmark that a stop left waiting or running waits again, and its cells that were
    running start again from their first attempt.
    """
    job = document.get('aggregates') or {}
    cells = job.get('cells')
    if (
        not isinstance(cells, list)
        or not all(isinstance(cell, dict) and cell.get('state') in CELL_STATES for cell in cells)
        or not isinstance(job.get('models'), list)
        or not isinstance(job.get('tasks'), list)
        or not isinstance(job.get('teams'), list)
        or not isinstance(job.get('workers'), int)
    ):
        return None
    job = {'id': document['id'], 'state': document['state'], **copy.deepcopy(job)}
    job['playable'] = _playable(cells)
    if job['state'] in ('queued', 'running'):
        job['state'] = 'queued'
        for cell in job['cells']:
            if cell['state'] == 'running':
                cell.update(state='queued', results=[], played=0)
    try:
        job['rows'] = table(job)
        job['progress'] = progress_share(job)
    except (KeyError, TypeError, ValueError):
        return None
    return job


class _Benchmarks:
    """The benchmarks of the page and of agents, queued and played one at a time."""

    def __init__(self, player: Callable[..., dict[str, Any]] | None = None) -> None:
        """Lists the benchmarks that the runs manifest keeps and plays on the unfinished ones.

        Args:
            player: The function that plays a cell, as `play_cell` does, by default `play_cell`.
        """
        self._lock = threading.RLock()
        self._entries: list[dict[str, Any]] = []
        self._processes: dict[str, set[subprocess.Popen]] = {}
        self._wake = threading.Event()
        self._player = player or play_cell
        self._load()
        threading.Thread(target=self._run, daemon=True, name='cuju-benchmarks').start()

    def _load(self) -> None:
        """Takes the benchmarks of the runs manifest, the newest ended ones and every unfinished."""
        try:
            documents = _runs.list(kind='benchmark')
        except OSError as error:
            _LOGGER.warning('cuju render could not read the kept benchmarks: %s', error)
            return
        jobs = [job for job in map(_restored, reversed(documents)) if job is not None]
        ended = [job for job in jobs if job['state'] != 'queued']
        kept = {id(job) for job in ended[max(0, len(ended) - _KEPT) :]}
        self._entries = [job for job in jobs if job['state'] == 'queued' or id(job) in kept]
        for job in self._entries:
            if job['state'] == 'queued':
                self._save(job)
                self._wake.set()

    def _save(self, job: dict[str, Any]) -> None:
        """Writes the manifest of a benchmark as it stands."""
        with self._lock:
            fields = copy.deepcopy(_stored(job))
            identifier = job['id']
        _save(identifier, fields)

    def add(self, document: dict[str, Any]) -> dict[str, Any]:
        """Queues a benchmark of `plan` and returns its entry."""
        job = {
            'id': _home.new_identifier('b'),
            'state': 'queued',
            **document,
            **_messages.entry_fields(None),
            'created': time.time(),
            'ended': None,
        }
        with self._lock:
            self._entries.append(job)
            ended = [item for item in self._entries if item['state'] in ('done', 'failed')]
            for item in ended[: max(0, len(ended) - _KEPT)]:
                self._entries.remove(item)
            copied = copy.deepcopy(job)
        self._save(job)
        self._wake.set()
        return copied

    def entries(self) -> list[dict[str, Any]]:
        """Returns a copy of every benchmark, newest first."""
        with self._lock:
            return [copy.deepcopy(job) for job in reversed(self._entries)]

    def get(self, identifier: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._find(identifier)
            return None if job is None else copy.deepcopy(job)

    def _find(self, identifier: str) -> dict[str, Any] | None:
        return next((item for item in self._entries if item['id'] == identifier), None)

    def cancel(self, identifier: str) -> bool:
        """Cancels a waiting or running benchmark, and returns whether there was one."""
        with self._lock:
            job = self._find(identifier)
            if job is None or job['state'] not in ('queued', 'running'):
                return False
            job.update(
                state='failed', ended=time.time(), **_messages.fields('server.requests.cancelled')
            )
            for cell in job['cells']:
                if cell['state'] in ('queued', 'running'):
                    cell['state'] = 'cancelled'
            job['rows'] = table(job)
            processes = list(self._processes.get(identifier, ()))
        for process in processes:
            process.kill()
        self._save(job)
        return True

    def _run(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            while True:
                with self._lock:
                    job = next((item for item in self._entries if item['state'] == 'queued'), None)
                    if job is not None:
                        job['state'] = 'running'
                        self._processes[job['id']] = set()
                if job is None:
                    break
                self._save(job)
                self._play(job)

    def _play(self, job: dict[str, Any]) -> None:
        identifier = job['id']

        def started(process: subprocess.Popen) -> None:
            with self._lock:
                self._processes[identifier].add(process)
                stopped = job['state'] != 'running'
            if stopped:
                process.kill()

        try:
            run(
                job,
                play=self._player,
                changed=lambda cell: self._save(job),
                cancelled=lambda: job['state'] != 'running',
                started=started,
                lock=self._lock,
            )
        except Exception as error:
            with self._lock:
                job.update(
                    state='failed', **_messages.entry_fields(error, _messages.described(error))
                )
        finally:
            with self._lock:
                self._processes.pop(identifier, None)
                if job['state'] == 'running':
                    job.update(state='done', ended=time.time())
                self._save(job)


_BENCHMARKS: _Benchmarks | None = None
_BENCHMARKS_LOCK = threading.Lock()


def benchmarks() -> _Benchmarks:
    """Returns the server's queue of benchmarks, started on first use."""
    global _BENCHMARKS
    with _BENCHMARKS_LOCK:
        if _BENCHMARKS is None:
            _BENCHMARKS = _Benchmarks()
        return _BENCHMARKS


# The fields that a request for a benchmark may hold.
FIELDS = frozenset({'teams', 'tasks', 'keep_recordings', 'workers', 'submission_format'})


def queue(document: dict[str, Any]) -> dict[str, Any]:
    """Queues the benchmark that a request names on the server, and returns its entry.

    Raises:
        ArgumentError: If the request holds an unknown field, no teams, or a value that does not
            fit, as `plan` tells.
    """
    unknown = set(document) - FIELDS
    if unknown or 'teams' not in document:
        raise MessageError('server.benchmark.fields', unknown=repr(sorted(unknown)))
    planned = plan(
        document['teams'],
        document.get('tasks'),
        document.get('keep_recordings', False),
        document.get('workers'),
        submission_format=document.get('submission_format'),
    )
    return benchmarks().add(planned)


def csv_of(identifier: str) -> str | None:
    """Returns the table of a benchmark of the server as CSV, or `None` for no such benchmark."""
    job = benchmarks().get(identifier)
    return None if job is None else csv_text(job)


def cell_line(document: dict[str, Any], cell: dict[str, Any], named: bool = True) -> str:
    """Returns one cell in a line of English, as the command prints it.

    The line names the model first, unless `named` is false, as under the row of its model.
    """
    start = ''
    if named:
        name = next(model['name'] for model in document['models'] if model['team'] == cell['team'])
        start = f'{name}, '
    if cell['state'] == 'done':
        shown = f'{cell["value"]} of {cell["attempts"]}'
        failed = cell['failed_attempts']
        if failed:
            shown += f', {_messages.counted(failed, "failed attempt")}'
        return f'{start}{cell["task"]}, {shown}'
    return f'{start}{cell["task"]}, {cell["state"]}, {cell["error"]}'


# The benchmark of one model from Python.


def _summary(document: dict[str, Any]) -> dict[str, Any]:
    """Returns the summary of a benchmark of one model, its score and the result of each task."""
    row = document['rows'][0] if document['rows'] else {'score': 0.0, 'place': 1}
    tasks = [
        {
            'task': cell['task'],
            'scenario': _scenario_of(cell['task']),
            'state': cell['state'],
            'attempts': cell['attempts'],
            'value': cell['value'],
            'rate': cell['rate'],
            'failed_attempts': cell['failed_attempts'],
        }
        for cell in document['cells']
    ]
    return {
        'score': row['score'],
        'tasks': tasks,
        'attempts': sum(task['attempts'] for task in tasks if task['state'] == 'done'),
        'value': sum(task['value'] or 0 for task in tasks),
    }


def _queue_state(document: dict[str, Any], playing: str | None) -> dict[str, Any]:
    """Returns the state of the queue of a benchmark of one model, task by task."""
    played, waiting = [], []
    for cell in document['cells']:
        entry = {'home': cell['team'], 'away': cell['task'], 'number': len(played) + len(waiting)}
        if cell['state'] in ('done', 'failed', 'skipped', 'cancelled'):
            played.append({**entry, 'score': [cell['value'] or 0, cell['attempts']]})
        elif cell['task'] != playing:
            waiting.append(entry)
    return {
        'mode': 'batched',
        'played': played,
        'playing': [
            {'home': cell['team'], 'away': cell['task']}
            for cell in document['cells']
            if cell['task'] == playing
        ],
        'next': waiting,
        'count': len(played),
        'most': len(document['cells']),
        'total': len(document['cells']),
        'settled': False,
    }


def benchmark(
    policy: Any = None,
    config: Any = None,
    overrides: Any = (),
    *,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> Any:
    """Plays a policy on the academy tasks and returns the benchmark as an `Evaluation`.

    The table `[benchmark]` of the settings names the tasks and whether to keep the recordings,
    and the table `[evaluate]` names the model, its `name`, `agents`, `interface`, `difficulty`,
    `device`, `threads` and the options of its batches. Every task plays its five attempts on
    the seeds of the catalogue, and the score is 100 times the mean goal rate over the tasks.

    Args:
        policy: What plays, as `cuju.arena.evaluate` takes it, a function, `module:function`,
            `team:ID` or `builtin`, or `None` for the key `policy` of `[evaluate]`.
        config: The settings, a file, a mapping of tables, a `Config` or a list of them.
        overrides: Settings such as `benchmark.keep_recordings=true`, laid over `config`.
        progress: A function called after each task with where the run stands, the mapping that
            `Evaluation.progress` returns.

    Returns:
        The benchmark, whose `summary` holds its `score` and, for each task, its `value` and
        its goal `rate`, and whose `episodes` hold every attempt.

    Raises:
        ArgumentError: If the policy or a task does not fit.
        ConfigurationError: If a setting is invalid.
    """
    from .._config import layers as _layers
    from .._store import queue as _queue
    from . import evaluate as _evaluate
    from . import runner as _runner

    settings = _layers.table('evaluate', config, overrides)
    bench = _layers.table('benchmark', config, overrides)
    found = _evaluate.resolve_policy(policy, settings)
    chosen = _task_ids(
        None if bench['tasks'] is None or bench['tasks'] == 'all' else bench['tasks']
    )
    keep = _academy.keep_flag(bench['keep_recordings'])
    workers = _workers(None if bench['workers'] in (None, 'auto') else bench['workers'])
    key = found['key'] or 'policy'
    named = {name: found[name] for name in ('name', 'name_id', 'name_values') if name in found}
    if found['kind'] == 'team':
        from .._formats import context as _context

        # `[benchmark] format` names the format the team plays its attempts under.
        document = plan(
            [found['team']],
            chosen,
            keep,
            workers,
            settings['device'],
            submission_format=_context.from_settings(bench),
        )
    else:
        cells = [_cell(key, _academy.find(task)) for task in chosen]
        document = {
            'teams': [key],
            'tasks': chosen,
            'models': [{'team': key, **named}],
            'cells': cells,
            'playable': _playable(cells),
            'keep_recordings': keep,
            'workers': workers,
            'device': settings['device'],
        }
        document['rows'] = table(document)
        document['progress'] = progress_share(document)
    agents = int(settings['agents']) if found['kind'] == 'function' else 0
    options = dict(settings.get('batch') or {})
    if (
        found['kind'] == 'function'
        and settings['interface'] == 'kaggle'
        and options.get('representation') != 'raw'
    ):
        options['with_raw'] = True
    setups = [
        {
            'side': 'left',
            'opponent': 'builtin:scenario',
            'asset': _runner.asset_of(_academy.find(task)['scenario'], (agents, 0)),
            'agents': [agents, 0],
            'difficulty': [settings.get('difficulty'), None],
            'options': options,
            'left': None,
            'right': None,
        }
        for task in chosen
    ]
    stored = copy.deepcopy(settings)
    stored['policy'] = found['text']
    identifier = _home.new_identifier('b')
    while _runs.get(identifier) is not None:
        identifier = _home.new_identifier('b')
    checkpoint = _evaluate._checkpoint(settings, found)
    job = {
        **document,
        **_messages.entry_fields(None),
        'created': time.time(),
        'ended': None,
    }
    opponent = {'kind': 'builtin', 'difficulty': None, 'team': None, 'key': 'builtin:scenario'}
    opponent.update(_messages.named('name', _messages.label('server.tournament.label.builtin')))
    _runs.create(
        'benchmark',
        identifier,
        name=found['name'],
        run=settings.get('run'),
        scenarios=[_academy.find(task)['scenario'] for task in chosen],
        opponents=[opponent],
        checkpoints=[checkpoint] if checkpoint is not None else [],
        config={
            'evaluate': stored,
            'benchmark': copy.deepcopy(bench),
            'policy': {
                name: found.get(name)
                for name in ('kind', 'text', 'team', 'key', 'name', 'name_id', 'name_values')
                if name in found
            },
            'entrants': {},
            'mode': 'batched',
            'setups': setups if found['kind'] != 'team' else [],
            'job': 'python',
        },
        aggregates=copy.deepcopy(job),
        state='running',
    )
    episodes: list[dict[str, Any]] = []

    def save(playing: str | None = None) -> None:
        document['rows'] = table(document)
        document['progress'] = progress_share(document)
        cells = document['cells']
        job.update(
            rows=document['rows'],
            progress=document['progress'],
            cells=cells,
            playable=_playable(cells),
        )
        aggregates = copy.deepcopy({**job, 'summary': _summary(document)})
        held = (_runs.get(identifier) or {}).get('aggregates') or {}
        for name in ('record', 'seconds', 'matches'):
            if name in held:
                aggregates[name] = held[name]
        _runs.update(identifier, aggregates=aggregates)
        _runner.keep_episodes(identifier, episodes)
        _queue.save_queue(f'run:{identifier}', _queue_state(document, playing))
        if progress is not None:
            event = _evaluate.progress_of(_runs.get(identifier) or {})
            event['playable_ended'] = any(
                cell['state'] in ('done', 'failed') and cell_fits(cell) for cell in cells
            )
            progress(event)

    def attempts_of(cell: dict[str, Any], results: list[dict[str, Any]], **fields: Any) -> None:
        """Adds the attempts of a task to the matches of the manifest."""
        task = chosen.index(cell['task'])
        for result in results:
            goals = result.get('goals') or [0, 0]
            episodes.append(
                {
                    'number': len(episodes),
                    'opponent': 0,
                    'opponent_key': 'builtin:scenario',
                    'task': cell['task'],
                    'scenario': _academy.find(cell['task'])['scenario'],
                    'setup': task,
                    'index': result['index'],
                    'environment': result['index'],
                    'side': 'left',
                    'seed': 0,
                    'own_seed': result['seed'],
                    'goals_for': goals[0],
                    'goals_against': goals[1],
                    'outcome': 'win' if result['scored'] else 'loss',
                    'length': result.get('length'),
                    'recording': result.get('match'),
                    'keep': keep and result.get('match') is None,
                    'stats': None,
                    'digest': result.get('digest'),
                    'actions': fields.get('actions'),
                }
            )

    try:
        if found['kind'] == 'team':

            def changed(cell: dict[str, Any] | None) -> None:
                if cell is not None and cell['state'] in ('done', 'failed'):
                    attempts_of(cell, [dict(result, digest=None) for result in cell['results']])
                    for episode in episodes:
                        episode['keep'] = False
                save()

            save()
            run(document, changed=changed)
        else:
            function = _evaluate._played_by(found, settings)
            room = int(settings['record']['extras_mb']) * 1024 * 1024 if keep else 0
            extras: dict[int, dict[str, Any]] = {}
            for index, cell in enumerate(document['cells']):
                spec = _academy.find(cell['task'])
                cell['state'] = 'running'
                save(cell['task'])
                setup = setups[index]
                try:
                    played = _runner.play_batch(
                        setup['asset'],
                        tuple(setup['agents']),
                        setup['difficulty'],
                        spec['attempts'],
                        0,
                        policy=function,
                        options=options,
                        device=settings['device'],
                        threads=int(settings['threads']),
                        seeds=spec['seeds'],
                        extras_bytes=room,
                    )
                except ArgumentError as error:
                    cell.update(state='failed', **_messages.entry_fields(error, str(error)))
                    save()
                    continue
                room = max(0, room - sum(value.nbytes for value in played.extras.values()))
                numbers = [len(episodes) + attempt for attempt in range(spec['attempts'])]
                name = f't{index}-{cell["task"]}'
                digests = _runner.write_chunk(identifier, name, numbers, played)
                results = [
                    {
                        'index': attempt,
                        'seed': spec['seeds'][attempt],
                        'match': None,
                        'finished': True,
                        'goals': played.goals[attempt],
                        'scored': played.goals[attempt][0] > 0,
                        'seconds': None,
                        'failed': False,
                        'length': played.lengths[attempt],
                        'digest': digests[attempt],
                    }
                    for attempt in range(spec['attempts'])
                ]
                for attempt, number in enumerate(numbers):
                    extras[number] = {
                        key_: values[: played.lengths[attempt], attempt]
                        for key_, values in played.extras.items()
                    }
                attempts_of(cell, results, actions=f'{name}.npz')
                totals = _academy.score_run(results)
                cell.update(
                    state='done',
                    **totals,
                    results=[
                        {k: v for k, v in result.items() if k not in ('length', 'digest')}
                        for result in results
                    ],
                    played=len(results),
                )
                _evaluate._set_seconds(identifier, played.seconds, len(results))
                save()
            if keep and any(extras.values()):
                import numpy as np

                empty = _runner.Played([], [], np.zeros((0, 0, 0), dtype=np.uint8))
                _runner.write_chunk(identifier, 'extras', [], empty, extras)
        job.update(ended=time.time())
        save()
        _runs.finish(identifier)
    except BaseException as error:
        cancelled = isinstance(error, KeyboardInterrupt)
        if not cancelled:
            job.update(_messages.entry_fields(error, _messages.described(error)))
        for cell in document['cells']:
            if cell['state'] in ('queued', 'running'):
                cell['state'] = 'cancelled'
        job.update(ended=time.time())
        with contextlib.suppress(Exception):
            save()
            _runs.finish(identifier, state='cancelled' if cancelled else 'failed')
        raise
    process = _evaluate._draw(identifier, bool(settings['record']['background']))
    return _evaluate.Evaluation(identifier, process)
