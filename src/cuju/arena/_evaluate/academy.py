# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The academy tasks of the benchmark, and the headless run of one model on one task.

Every academy scenario of GRF is a task, named after its scenario without the start `academy_`.
A run plays a fixed number of attempts of a task, and attempt `i` always plays the seed `seed_i`,
so every model faces the same episodes and every result compares. One function, `score_attempt`,
scores an attempt from the frames of its recording. An attempt counts when the left side scored
before its episode ended, the value of a run is the number of its attempts that count, and its
goal rate is that value over its attempts, from 0 to 1.

A policy that fails ends its attempt alone, which counts as failed with no goal, and the run goes
on, so a run always ends with a result unless its task or its team does not fit. A run that keeps
its recordings saves each attempt into the library, and the recordings of one run form a group,
which the Recordings page shows as one row.

The benchmark of `benchmark` plays each run in a child process of its own,
`python -m cuju.arena._evaluate.academy`, which reports one JSON line at a time as a requested
match does, so a submission never shares a process with another.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import struct
import subprocess
import sys
import threading
import zlib
from collections.abc import Callable
from typing import Any, TextIO

from .._format import recording as _recording
from .._registry import teams as _teams
from .._store import home as _home
from .._store import library as _library
from .._text import messages as _messages
from .._text.messages import MessageError
from . import ACADEMY_TASKS as TASKS

STEPS_PER_SECOND = 10
# Every task plays this many attempts, each on a seed of its own.
ATTEMPTS = 5
# A policy has this long to load and to answer its first observation, and this long for every
# later answer before its attempt ends as stuck.
LOAD_SECONDS = 120.0
FIRST_CALL_SECONDS = 120.0
HANG_SECONDS = 60.0
# A run waits this long for the thread of its policies to end, so that a child process never
# exits while that thread still runs.
_CLOSE_SECONDS = 1.0
# A run in a child process ends after this many seconds.
_RUN_SECONDS = 30 * 60
_SCORE = struct.Struct('<2i')
_FLAGS = struct.Struct('<i')
_ACADEMY = 'academy_'


def __getattr__(name: str) -> Any:
    """Returns `BatchEnvironment` on first use, so listing the tasks leaves PyTorch out."""
    if name == 'BatchEnvironment':
        from cuju import BatchEnvironment

        return BatchEnvironment
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def seeds(identifier: str, count: int) -> list[int]:
    """Returns the seeds of the first `count` attempts of a task.

    Attempt `i` plays `(crc32(identifier) + 7919 * i) % 2**31`, with the identifier in UTF 8.
    """
    base = zlib.crc32(identifier.encode())
    return [(base + 7919 * index) % 2**31 for index in range(count)]


def find(identifier: Any) -> dict[str, Any]:
    """Returns a task, its identifier, scenario, attempts and seeds.

    Raises:
        ArgumentError: If no task has that identifier.
    """
    if identifier not in TASKS:
        raise MessageError(
            'server.benchmark.unknown_task', tasks=', '.join(TASKS), identifier=repr(identifier)
        )
    return {
        'id': identifier,
        'scenario': _ACADEMY + identifier,
        'attempts': ATTEMPTS,
        'seeds': seeds(identifier, ATTEMPTS),
    }


def tasks() -> list[dict[str, Any]]:
    """Returns every task, in the order of `TASKS`."""
    return [find(identifier) for identifier in TASKS]


def score_attempt(header: dict[str, Any], frames: list[bytes], side: int = 0) -> dict[str, Any]:
    """Scores one attempt from the frames of its recording.

    The seconds of play run from the first frame to the frame of the goal, at ten steps a second,
    whatever the game clock shows. An attempt whose last frame lacks the episode end flag ended
    early, so it counts its goals against and no goals for.

    Args:
        header: The header of the recording.
        frames: Its frames, as `_recording.frames` yields them.
        side: The side that the model plays, the left one.

    Returns:
        Whether the episode finished, the goals for and against, whether the side scored and the
        seconds of play to its first goal, or `None` without a goal.
    """
    if not frames:
        return {'finished': False, 'goals': [0, 0], 'scored': False, 'seconds': None}
    steps = float(header.get('steps_per_second') or STEPS_PER_SECOND)
    first = _SCORE.unpack_from(frames[0], 8)
    last = _SCORE.unpack_from(frames[-1], 8)
    finished = bool(_FLAGS.unpack_from(frames[-1], 28)[0] & 1)
    goals_for = max(0, last[side] - first[side]) if finished else 0
    against = max(0, last[1 - side] - first[1 - side])
    seconds = None
    if goals_for:
        for index, frame in enumerate(frames):
            if _SCORE.unpack_from(frame, 8)[side] > first[side]:
                seconds = round(index / steps, 1)
                break
    return {
        'finished': finished,
        'goals': [goals_for, against],
        'scored': goals_for > 0,
        'seconds': seconds,
    }


def score_run(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Returns the value and the goal rate of a run of attempts.

    The value counts the attempts in which the model scored, and the rate is the value over the
    attempts, from 0 to 1 with four decimals.
    """
    value = sum(bool(result['scored']) for result in results)
    return {'value': value, 'rate': round(value / len(results), 4) if results else 0.0}


def _attempt_result(
    task: dict[str, Any],
    index: int,
    match: str | None,
    scored: dict[str, Any],
    failure: BaseException | None = None,
) -> dict[str, Any]:
    """Returns the result of one attempt, its index, seed and recording with its score.

    An attempt whose policy failed says so in `failed` and carries the policy's error as `error`,
    `error_id` and `error_values`, and its score is that of an attempt that ended early.
    """
    result = {
        'index': index,
        'seed': task['seeds'][index],
        'match': match,
        **scored,
        'failed': failure is not None,
    }
    if failure is not None:
        result.update(_messages.entry_fields(failure))
    return result


def keep_flag(value: Any) -> bool:
    """Returns whether a run keeps its recordings, or raises `ArgumentError` for no boolean."""
    if not isinstance(value, bool):
        raise MessageError('server.benchmark.keep_recordings', value=repr(value))
    return value


# Groups of recordings, one for each run whose attempts the library keeps.


def _result_label(count: int, state: str, played: int, value: int | None) -> dict[str, Any]:
    """Returns the result of a run as a label, its value over its attempts, or how far it went."""
    if value is None:
        message = 'server.names.run_playing' if state == 'playing' else 'server.names.run_stopped'
        return _messages.label(message, played=played, attempts=count)
    return _messages.label('server.names.run_attempts', value=value, attempts=count)


def _group(
    task: dict[str, Any],
    identifier: str,
    competitor: dict[str, Any] | str,
    state: str,
    played: int,
    value: int | None = None,
) -> dict[str, Any]:
    """Returns the group of the recordings of a run, as the summaries of the library keep it.

    The state is `playing` while the run plays, `done` once it is scored and `stopped` for a run
    that ended on a failure of its own.
    """
    title = _messages.label(
        'server.names.benchmark_run',
        task=_messages.scenario(task['scenario']),
        competitor=competitor,
    )
    return {
        'id': identifier,
        'kind': 'benchmark',
        'task': task['id'],
        **_messages.named('title', title),
        **_messages.named('competitor', competitor),
        **_messages.named('result', _result_label(task['attempts'], state, played, value)),
        'state': state,
        'attempts': task['attempts'],
        'value': value,
    }


def entrant_name(entry: dict[str, Any]) -> dict[str, Any] | str:
    """Returns the name of a model of the benchmark, its checkpoint beside it for an entrant.

    An entrant `team@checkpoint` that names an earlier checkpoint shows the team's name and the
    checkpoint's label, or the first eight characters of its identifier when it has no label. A
    team at its current checkpoint keeps its name alone.
    """
    shown = _teams.shown_name(entry)
    if 'entrant' not in entry:
        return shown
    from .._registry import checkpoints as _checkpoints

    point = _checkpoints.get(entry['id'], entry['checkpoint'])
    text = shown['text'] if isinstance(shown, dict) else shown
    return f'{text} {point["label"] or point["id"][:8]}'


def team_entry(team: str) -> dict[str, Any]:
    """Returns the team that plays the academy task."""
    from .._cli.contract import team_value

    try:
        identifier, difficulty = team_value(team)
    except argparse.ArgumentTypeError as error:
        raise MessageError('server.benchmark.team', team=repr(team)) from error
    if identifier == 'random':
        identifier = _teams.RANDOM_ACTIONS
    side = _teams.side_entry(identifier)
    if difficulty is None:
        return side
    name = _messages.label('server.tournament.label.builtin_ability', ability=f'{difficulty:.2f}')
    return {**side, 'difficulty': difficulty, **_messages.named('name', name)}


def run(
    task: str,
    team: str,
    device: str = 'cpu',
    progress: Callable[[dict[str, Any]], None] | None = None,
    keep_recordings: bool = True,
    submission_format: Any = None,
) -> dict[str, Any]:
    """Plays a task headless with a team of the registry, and scores it.

    Each attempt plays in a batch of one environment, `{scenario}_l{agents}_r0` with the seed of
    the attempt, as fast as the policy answers. A policy that fails ends its attempt alone. The
    attempt then ends where the policy failed and counts as one that ended early, its goals
    against and no goals for, it says so in `failed` with the policy's error, and the next attempt
    loads the policy again. A policy that cannot load fails every attempt left.

    Args:
        task: The identifier of the task, such as `empty_goal_close`.
        team: A team of the registry, `builtin` for GRF's built-in AI on the whole side.
        device: `cpu` or `cuda`.
        progress: A function called with the result of each attempt as it ends.
        keep_recordings: Whether the library keeps the attempts as recordings. A run without
            them scores from the same frames, and its attempts name no recording.
        submission_format: The format the team plays under, a format's identifier or a mapping
            `{id, preset, options}`, or `None` for Kaggle open, which plays every attempt as
            the benchmark always did. A team must pass the format's pre-check first, and each
            attempt under a format starts the team afresh, so its forfeit ends that attempt alone.

    Returns:
        The run, its `task` and `team`, the team's `name`, the `value` and `rate` that
        `score_run` gives, the count of its `failed_attempts`, `playing`, the players that
        played and those the team controls for a team that played with players out, else `None`,
        the result and recording of each of its `attempts`, and the `group` of its recordings or
        `None`.

    Raises:
        ArgumentError: If the task or the team does not fit, or the format refuses the team.
    """
    from .._formats import context as _context
    from .._formats import launch as _launch
    from .._playing.play import Policies, PolicyRunner, chosen_formats

    spec = find(task)
    scenario = spec['scenario']
    keep = keep_flag(keep_recordings)
    if not isinstance(team, str):
        raise MessageError('server.benchmark.team', team=repr(team))
    side = team_entry(team)
    builtin = _teams.entry(_teams.BUILTIN)
    control = None if side['kind'] == 'builtin' else side.get('control')
    fit = _teams.task_fit(side, scenario)
    agents = fit['agents']
    # A team that controls more players than the side holds plays with the others out.
    playing = None if fit['slots'] is None else [fit['playing'], fit['players']]
    checked = None if side['kind'] == 'builtin' else _teams.check_control(control, side['format'])
    extended = checked is not None and (
        checked['assignment'] == 'fixed' or checked['action_set'] == 'v2'
    )

    choice = _context.check(submission_format) if side['kind'] == 'kaggle' else None
    if choice is not None:
        refused = _context.ensure(choice, [side.get('entrant', side['id'])], scenario)
        if refused:
            found = next(iter(refused.values()))
            reason = found['failed'] or '; '.join(item['message'] for item in found['problems'])
            name = _teams.shown_name(side)
            name = name['text'] if isinstance(name, dict) else name
            raise MessageError('server.benchmark.refused', team=name, reason=reason)
    formats = None if choice is None else chosen_formats([choice, None])

    def factory(batch: Any) -> Any:
        return Policies(
            batch,
            [side, builtin],
            scenario,
            [agents, 0],
            extended,
            [0, agents],
            fits=[fit, None],
            formats=formats,
            kind='evaluation',
        )

    runner = PolicyRunner(hang=HANG_SECONDS) if agents else None
    # The failure of policies that could not load, which every attempt left shares.
    unloadable: BaseException | None = None
    shown = _teams.shown_name(side)
    group_name = entrant_name(side) if keep else shown
    group = _home.new_identifier('g') if keep else None
    results: list[dict[str, Any]] = []

    def keep_group(state: str, value: int | None = None) -> None:
        if group is not None:
            members = {
                result['match']: index
                for index, result in enumerate(results)
                if result['match'] is not None
            }
            if members:
                entry = _group(spec, group, group_name, state, len(results), value)
                _library.set_group(entry, members)

    try:
        for index in range(spec['attempts']):
            failure = unloadable
            if failure is not None:
                match, header, frames = None, {}, []
            else:
                match, header, frames, failure = _play_attempt(
                    spec, index, side, agents, extended, device, runner, factory, keep
                )
                if failure is not None or formats is not None:
                    # Under a format every attempt starts the team afresh, at its own kick off.
                    if failure is not None and runner.policies is None:
                        unloadable = failure
                    # The next attempt loads the policies again on a thread of their own, since
                    # a call that never returned may still hold the old one.
                    wait = _CLOSE_SECONDS
                    if failure is None:
                        # A completed attempt gives the format worker time to exit and be reaped.
                        wait += _launch.CLOSE_SECONDS + _launch.REAP_SECONDS
                    runner.close(wait)
                    runner = PolicyRunner(hang=HANG_SECONDS)
            result = _attempt_result(spec, index, match, score_attempt(header, frames), failure)
            results.append(result)
            keep_group('playing')
            if progress is not None:
                progress(dict(result))
    except BaseException:
        keep_group('stopped')
        raise
    finally:
        if runner is not None:
            runner.close(_CLOSE_SECONDS)
    totals = score_run(results)
    keep_group('done', totals['value'])
    return {
        'task': task,
        'team': team,
        **_messages.named('name', shown),
        'attempts': results,
        **totals,
        'failed_attempts': sum(result['failed'] for result in results),
        'playing': playing,
        'group': group if any(result['match'] is not None for result in results) else None,
    }


def _play_attempt(
    spec: dict[str, Any],
    index: int,
    side: dict[str, Any],
    agents: int,
    extended: bool,
    device: str,
    runner: Any,
    factory: Callable[[Any], Any],
    keep: bool = True,
) -> tuple[str | None, dict[str, Any], list[bytes], BaseException | None]:
    """Plays one attempt and returns its recording's identifier, header, frames and failure.

    The recording goes to the library when `keep` is true, and its identifier is `None` otherwise.
    A policy that fails ends the attempt where it failed, which keeps the frames played so far
    and returns the failure, and an attempt that never stepped keeps no recording. The failure is
    `None` otherwise.
    """
    from .._playing.play import PolicyError, PolicyMessageError

    scenario, seed = spec['scenario'], spec['seeds'][index]
    # A test may put a batch of its own in the module's place of `BatchEnvironment`.
    make = globals().get('BatchEnvironment') or __getattr__('BatchEnvironment')
    difficulty = side.get('difficulty')
    options = {} if difficulty is None else {'difficulty': [(difficulty, None)]}
    batch = make(
        f'{scenario}_l{agents}_r0',
        agent_counts=[(agents, 0)],
        environment_count=1,
        representation='raw',
        seed=seed,
        auto_reset=False,
        device=device,
        action_set='v2' if extended else 'default',
        **options,
    )
    failure = None
    try:
        reader = _recording.StateReader(int(batch.left_players[0] + batch.right_players[0]))
        batch.reset()
        state = batch.get_state(0)
        lineup = reader.lineup(state)
        timing = _recording.scenario_timing(state)
        frames = [reader.frame(state)]
        try:
            first_call = runner is not None and runner.policies is None
            if runner is not None:
                if runner.policies is None:
                    runner.load(lambda: factory(batch), LOAD_SECONDS)
                else:
                    # The same policies play every attempt, and each sees its episode from step 0.
                    runner.policies.batch = batch
                    for player in runner.policies.players:
                        player.update(calls=0, held={})
            done = False
            while not done:
                if runner is not None:
                    runner.request()
                    limit = FIRST_CALL_SECONDS if first_call else HANG_SECONDS
                    first_call = False
                    if not runner.wait(limit):
                        raise PolicyMessageError('server.policy.silent', count=f'{limit:g}')
                    actions = runner.actions(None)
                else:
                    import torch

                    actions = torch.zeros((1, 0), dtype=torch.int32)
                _, _, finished, _ = batch.step(actions)
                done = bool(finished[0])
                frames.append(reader.frame(batch.get_state(0), 1 if done else 0))
        except PolicyError as error:
            # The attempt ends here, so its last frame lacks the end of the episode.
            failure = error
        played = [round(float(value), 4) for value in batch.difficulty[0].tolist()]
    finally:
        batch.close()
    teams = [
        {**team, 'ability': played[number]}
        for number, team in enumerate(
            _teams.match_teams(side.get('entrant', side['id']), _teams.BUILTIN)
        )
    ]
    provenance = {
        'source': 'benchmark',
        'task': spec['id'],
        'attempt': index,
        'seed': seed,
        'scenario': scenario,
        'teams': [side.get('entrant', side['id']), _teams.BUILTIN],
        'agent_counts': [agents, 0],
        'action_set': 'v2' if extended else 'default',
        'ability': played,
    }
    if failure is not None:
        provenance['failed'] = True
    title = _messages.label(
        'server.names.benchmark_attempt',
        task=_messages.scenario(scenario),
        attempt=index + 1,
        competitor=_teams.shown_name(side),
    )
    head = _recording.header(title, scenario, lineup, teams, len(frames), timing, provenance)
    stepped = failure is None or len(frames) > 1
    match = _library.save(head, frames).stem if keep and stepped else None
    return match, json.loads(head[len(_recording.MAGIC) + 4 :]), frames, failure


# The child process of a run.


def child_command(
    identifier: str,
    team: str,
    keep: bool = True,
    device: str = 'cpu',
    submission_format: Any = None,
) -> list[str]:
    """Returns the command of the child process that plays one run of a team on a task."""
    command = [
        *_home.python_command(),
        '-m',
        'cuju.arena._evaluate.academy',
        identifier,
        '--team',
        team,
    ]
    if device != 'cpu':
        command += ['--device', device]
    if not keep:
        command.append('--no-recordings')
    if submission_format is not None:
        command += ['--submission-format', json.dumps(submission_format)]
    return command


def play_child(
    command: list[str],
    report: Callable[[dict[str, Any]], None],
    started: Callable[[subprocess.Popen], None] | None = None,
    seconds: float = _RUN_SECONDS,
) -> tuple[int, str]:
    """Plays a run in a child process and hands each of its reports to `report`.

    The child runs in the renderer's folder, as a requested match does, and ends after `seconds`.
    Every run of a benchmark plays this way, on the server and on the command line alike, so a
    submission runs in a process of its own and no run sees another.

    Args:
        command: The command, as `child_command` returns it.
        report: A function called with each report of the child, a JSON object.
        started: A function called with the process once it has started, which may end it.
        seconds: The seconds after which the run ends.

    Returns:
        The exit code of the child and the last lines it wrote to standard error.
    """
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
        cwd=_home.process_folder(),
        env=_home.process_environment(),
    )
    if started is not None:
        started(process)
    stderr: collections.deque[str] = collections.deque(maxlen=20)
    reader = threading.Thread(target=lambda: stderr.extend(process.stderr), daemon=True)
    reader.start()
    timer = threading.Timer(seconds, process.kill)
    timer.daemon = True
    timer.start()
    try:
        for line in process.stdout:
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                report(parsed)
        code = process.wait()
    finally:
        timer.cancel()
    reader.join(1.0)
    return code, ''.join(stderr).strip()


# The channel of the reports, standard output until `main` moves everything else away.
_reports: TextIO = sys.stdout


def _report(**entries: Any) -> None:
    _reports.write(json.dumps(entries) + '\n')
    _reports.flush()


def main(arguments: list[str] | None = None) -> None:
    """Plays the run that the command line names and reports each attempt and the result."""
    global _reports
    parser = argparse.ArgumentParser(prog='python -m cuju.arena._evaluate.academy')
    parser.add_argument('task', metavar='TASK', help='the identifier of an academy task')
    parser.add_argument('--team', required=True, metavar='TEAM', help='a team of the registry')
    parser.add_argument('--device', default='cpu', metavar='DEVICE', help='cpu or cuda')
    parser.add_argument(
        '--no-recordings', action='store_true', help='keep no attempt as a recording'
    )
    parser.add_argument(
        '--submission-format',
        type=json.loads,
        default=None,
        metavar='JSON',
        help='the format the team plays under, Kaggle open by default',
    )
    options = parser.parse_args(arguments)
    sys.stdout.flush()
    _reports = os.fdopen(os.dup(1), 'w', encoding='utf-8')
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    try:
        outcome = run(
            options.task,
            options.team,
            device=options.device,
            progress=lambda result: _report(attempt=result),
            keep_recordings=not options.no_recordings,
            submission_format=options.submission_format,
        )
    except Exception as error:
        _report(error=_messages.described(error), **_messages.id_fields(error))
        sys.exit(1)
    _report(result=outcome)


if __name__ == '__main__':
    main()
