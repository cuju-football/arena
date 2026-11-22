# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Plays the matches of evaluations, benchmarks and seeding phases, and keeps their actions.

A match of an evaluation is cheap to play and dear to draw, so the runner records cheap and
leaves the drawing for later. `play_batch` plays one batch of matches without automatic resets,
one episode for each environment, and keeps the actions of every step, a byte for each agent,
with the goals and the length of each match. It reads no state and captures no frame, so the
batch plays at its own speed on either device. What a policy hands back beside its actions, its
extras, stays on the device of the batch in half precision, up to a number of bytes.

Environment `index` of a batch plays the match that the seed rule of cuju gives its seed and its
index, whatever the size of the batch, so a batch of one environment with the same seed and
`first_index` plays the same match again from the kept actions, bit for bit, on either backend.
`replay` of this package draws the chosen matches that way. A match that names a seed of its
own, as a seeding match of a tournament and an attempt of the benchmark do, plays in a batch
through the engine seed and the team order that a batch of one environment draws from that seed,
which `engine_seed` returns.

`play_teams` plays one match between teams of the registry, each policy called as Kaggle calls
it. The actions of an evaluation are kept in files beside the manifest of its run, in the folder
`runs/<id>.actions`, a file for each batch or match, which `write_chunk` writes and `episodes_of`
reads, with a digest of the actions of each match in the manifest, so a changed file is found.

`python -m cuju.arena._evaluate.runner` plays one match of teams in a process of its own, or a
whole stored evaluation, and reports one JSON line at a time. With `--worker` it is a match worker
of `_formats.worker`, which plays the matches of one slot of a run one after another, so a format
of `[process] scope = "run"` keeps each side's process across them.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import logging
import os
import random
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from .._registry import teams as _teams
from .._store import runs as _runs
from .._text import messages as _messages
from .._text.messages import MessageError

if TYPE_CHECKING:
    import numpy as np


_LOGGER = logging.getLogger('cuju')
# GRF: a full match of the shipped scenarios lasts 3000 steps, which the progress of a batch
# divides by.
_STEPS = 3001
# The names of what a policy may hand back beside its actions, as `annotate` takes them.
EXTRAS = ('values', 'probabilities', 'logits', 'targets', 'grids', 'arrows')
_NESTED = ('values', 'targets', 'grids', 'arrows')
# The folder of the actions of a run, beside its manifest.
_ACTIONS = '.actions'


def engine_seed(seed: int) -> tuple[int, int]:
    """Returns the engine seed and the team order of the match that a seed of its own plays.

    A batch of one environment created with `seed` plays, as its first episode, the fourth draw
    of the generator that `random.seed(seed * 2**32)` starts, with the team order of the parity
    of the second draw. A reset of any environment with that seed and that order plays the same
    match, so matches of several seeds play together in one batch.
    """
    generator = random.Random(int(seed) * 2**32)
    draws = [generator.randint(0, 2000000000) for _ in range(4)]
    return draws[3], draws[1] % 2


def asset_of(scenario: str, agents: tuple[int, int], length: int | None = None) -> str:
    """Returns what a batch is created with to play a scenario with agents, as a match does.

    A scenario of the renderer's own plays from its file, a match of a set length from the file
    that `length_scenario` of the tournaments writes, and a shipped scenario by its name with
    the agents of each side.
    """
    if length is not None:
        from .._tournaments.tournament_runner import length_scenario

        return length_scenario(scenario, length)
    if scenario in _teams.renderer_scenarios():
        return str(_teams.scenario_path(scenario))
    return f'{scenario}_l{agents[0]}_r{agents[1]}'


@dataclasses.dataclass
class Played:
    """What a batch of matches gave.

    Attributes:
        goals: The goals of the left and of the right team in each match.
        lengths: The steps of each match.
        actions: The actions of every step, `[steps, matches, agents]` of `uint8`, each counted
            from 0 within the action set.
        extras: The extras of the policy by name, each `[steps, matches, ...]` in half
            precision for the steps kept.
        dropped: The steps whose extras were left out, since the room for them was full.
        seconds: The seconds of play, without the creation of the batch.
        formats: What each side of a match of teams kept of its format, as `Policies.formats`
            gives it, or `None` for a side without one.
        forfeit: The forfeit of a match of teams that its format ended early, as
            `ForfeitError.rule` holds it, or `None`. A forfeit is never evidence.
    """

    goals: list[list[int]]
    lengths: list[int]
    actions: np.ndarray
    extras: dict[str, np.ndarray] = dataclasses.field(default_factory=dict)
    dropped: int = 0
    seconds: float = 0.0
    formats: list[Any] = dataclasses.field(default_factory=lambda: [None, None])
    forfeit: dict[str, Any] | None = None


def _flat_extras(extras: Any) -> dict[str, Any]:
    """Returns the extras of a policy by one name each, such as `values/value`.

    Raises:
        ArgumentError: If the extras are no mapping of the names that `annotate` takes.
    """
    if not isinstance(extras, dict):
        raise MessageError('server.evaluate.extras', names=', '.join(EXTRAS), found=repr(extras))
    found: dict[str, Any] = {}
    for name, value in extras.items():
        if name not in EXTRAS or (name in _NESTED and not isinstance(value, dict)):
            raise MessageError('server.evaluate.extras', names=', '.join(EXTRAS), found=repr(name))
        if name in _NESTED:
            for inner, tensor in value.items():
                found[f'{name}/{inner}'] = tensor
        elif value is not None:
            found[name] = value
    return found


def nested_extras(flat: dict[str, Any]) -> dict[str, Any]:
    """Returns extras by one name each as the keyword arguments of `annotate`."""
    found: dict[str, Any] = {}
    for name, value in flat.items():
        outer, _, inner = name.partition('/')
        if inner:
            found.setdefault(outer, {})[inner] = value
        else:
            found[outer] = value
    return found


def play_batch(
    asset: str,
    agents: tuple[int, int],
    difficulty: Any,
    count: int,
    seed: int,
    *,
    policy: Callable[..., Any] | None = None,
    actions: np.ndarray | None = None,
    options: dict[str, Any] | None = None,
    device: str = 'cpu',
    threads: int = 0,
    first_index: int = 0,
    seeds: list[int] | None = None,
    extras_bytes: int = 0,
    attach: Callable[[Any], None] | None = None,
    before_step: Callable[[Any, int], None] | None = None,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Played:
    """Plays one batch of matches, one episode for each environment, and keeps their actions.

    Args:
        asset: What the batch is created with, as `asset_of` returns it.
        agents: The agents of the left and of the right side.
        difficulty: The difficulty of the built-in AI of the left and of the right side, each
            from 0 to 1 or `None` for the scenario's.
        count: The matches, one for each environment.
        seed: The seed of the batch.
        policy: A function called with the observation, the batch and the flags of the matches
            that have ended, which returns the actions of the step, `[matches, agents]`, or the
            actions and the extras. `None` plays the built-in AI alone, or the given actions.
        actions: The actions of every step, as a `Played` keeps them, to play matches again. A
            match that lasts longer than they do idles from their end.
        options: Other options of `BatchEnvironment`, such as the representation.
        device: `cpu` or `cuda`.
        threads: The CPU threads, 0 for one for each processor.
        first_index: The index of the first environment in the seed rule.
        seeds: A seed of its own for each match, which plays as `engine_seed` tells, or `None`
            for the seed rule of the batch.
        extras_bytes: The bytes of extras kept at most, 0 to keep none.
        attach: A function called with the batch before its reset, such as one that attaches a
            recorder.
        before_step: A function called with the batch and the number of the step before each
            step, once the actions of the step are known.
        progress: A function called with the share of a full match played, every 50 steps.
        cancelled: A function that says whether to stop, after which the call raises.

    Raises:
        ArgumentError: If the policy answers no actions of the shape of the batch, or its
            extras do not fit.
        KeyboardInterrupt: If `cancelled` says to stop.
    """
    import time

    import numpy as np
    import torch

    import cuju

    pair = None if difficulty is None or list(difficulty) == [None, None] else [tuple(difficulty)]
    batch = cuju.BatchEnvironment(
        asset,
        agent_counts=[tuple(agents)],
        environment_count=count,
        seed=seed,
        first_index=first_index,
        auto_reset=False,
        device=device,
        threads=threads,
        difficulty=pair,
        **(options or {}),
    )
    try:
        if attach is not None:
            attach(batch)
        if seeds is not None:
            drawn = [engine_seed(value) for value in seeds]
            observation = batch.reset(
                seeds=[value[0] for value in drawn], reverse=[value[1] for value in drawn]
            )
        else:
            observation = batch.reset()
        where = (
            observation.device if isinstance(observation, torch.Tensor) else torch.device(device)
        )
        width = batch.agents
        finished = torch.zeros(count, dtype=torch.bool, device=where)
        goals = torch.zeros((count, 2), dtype=torch.int64, device=where)
        lengths = torch.zeros(count, dtype=torch.int64, device=where)
        idle = torch.zeros((count, width), dtype=torch.int32, device=where)
        kept: list[Any] = []
        extras: dict[str, list[Any]] = {}
        room, dropped, step = int(extras_bytes), 0, 0
        started = time.perf_counter()
        while not bool(finished.all()):
            if cancelled is not None and cancelled():
                raise KeyboardInterrupt
            found = None
            if actions is not None and step < len(actions):
                chosen = torch.as_tensor(np.ascontiguousarray(actions[step]), dtype=torch.int32)
                chosen = chosen.to(where)
            elif actions is not None:
                # A match that outlasts its kept actions idles on, and its length tells of it.
                chosen = idle
            elif policy is None or width == 0:
                chosen = idle
            else:
                answer = policy(observation, batch, finished)
                if isinstance(answer, tuple):
                    answer, found = answer
                chosen = torch.as_tensor(answer, device=where).to(torch.int32)
                if chosen.shape == (count,) and width == 1:
                    chosen = chosen.reshape(count, 1)
                if chosen.shape != (count, width):
                    raise MessageError(
                        'server.evaluate.actions_shape',
                        shape=repr(tuple(chosen.shape)),
                        expected=repr((count, width)),
                    )
                chosen = chosen.contiguous()
            if before_step is not None:
                before_step(batch, step)
            kept.append((chosen % max(1, batch.action_count)).to(torch.uint8).clone())
            if found:
                flat = _flat_extras(found)
                size = 0
                halves = {}
                for name, tensor in flat.items():
                    half = torch.as_tensor(tensor).detach()
                    half = half.to(torch.float16) if half.is_floating_point() else half.clone()
                    halves[name] = half
                    size += half.numel() * half.element_size()
                if size <= room and dropped == 0:
                    room -= size
                    for name, half in halves.items():
                        extras.setdefault(name, []).append(half)
                else:
                    dropped += 1
            observation, _, done, information = batch.step(chosen)
            live = ~finished
            score = information['score_reward']
            goals[:, 0] += (score == 1) & live
            goals[:, 1] += (score == -1) & live
            lengths += live
            finished |= done.bool()
            step += 1
            if progress is not None and step % 50 == 0:
                progress(min(0.99, step / _STEPS))
        seconds = time.perf_counter() - started
        stacked = (
            torch.stack(kept).cpu().numpy() if kept else np.zeros((0, count, width), dtype=np.uint8)
        )
        result = Played(
            goals=[[int(left), int(right)] for left, right in goals.cpu().tolist()],
            lengths=[int(value) for value in lengths.cpu().tolist()],
            actions=stacked,
            extras={name: torch.stack(values).cpu().numpy() for name, values in extras.items()},
            dropped=dropped,
            seconds=seconds,
        )
    finally:
        batch.close()
    if dropped:
        _LOGGER.warning(
            'cuju render kept the extras of the first %d steps of a batch of %d matches and '
            'left out those of %d steps, since they pass the %d bytes of '
            '[evaluate.record] extras_mb',
            step - dropped,
            count,
            dropped,
            extras_bytes,
        )
    return result


def team_setup(left: str, right: str, scenario: str) -> dict[str, Any]:
    """Returns the agents and the action set of a match between two sides of the registry.

    Raises:
        ArgumentError: If a team is unknown or does not fit the scenario.
    """
    sides = [_teams.side_entry(left), _teams.side_entry(right)]
    _teams.check_scenario(sides, scenario)
    agents = [_teams.agent_count(team, scenario, side) for side, team in enumerate(sides)]
    controls = [_teams.check_control(team.get('control'), team['format']) for team in sides]
    extended = any(
        team['kind'] != 'builtin' and (c['assignment'] == 'fixed' or c['action_set'] == 'v2')
        for team, c in zip(sides, controls, strict=True)
    )
    return {'agents': agents, 'action_set': 'v2' if extended else 'default'}


def play_teams(
    left: str,
    right: str,
    scenario: str,
    seed: int,
    index: int = 0,
    difficulty: Any = None,
    device: str = 'cpu',
    progress: Callable[[float], None] | None = None,
    submission_format: Any = None,
    kind: str = 'rating',
    pool: Any = None,
) -> Played:
    """Plays one match between two sides of the registry and keeps its actions alone.

    Each team answers under its submission format, Kaggle open where the context chose none, as
    in a requested match, and the built-in AI plays the players that a team leaves to it. The
    match is environment `index` of the seed rule of `seed`, and the call reads no state and
    writes no recording. A side that its format forfeits ends the match where it forfeited, and
    the result holds the forfeit in `forfeit` and the steps played so far.

    Args:
        left: The team of the left side, a registry identifier or `builtin`.
        right: The team of the right side.
        scenario: A scenario, without agent counts, which the teams decide.
        seed: The seed of the batch.
        index: The index of the match in the seed rule.
        difficulty: The difficulty of the built-in AI of the two sides, or `None`.
        device: `cpu` or `cuda`.
        progress: A function called with the share of a full match played.
        submission_format: The format of each side, a pair or one choice for both, as
            `play.chosen_formats` reads it, Kaggle open for a side without one.
        kind: What the match is played for, `rating` or `evaluation`, which a format reads as
            `Match.kind`.
        pool: The `_formats.pool.Pool` that keeps the processes of a format of `[process] scope
            = "run"` between the matches of one process, or `None`.

    Raises:
        ArgumentError: If a team is unknown or does not fit the scenario, or a format is unknown.
        PolicyError: If a format's own code fails.
    """
    import time

    import numpy as np

    import cuju

    from .._playing.play import ForfeitError, Policies, RuleError, chosen_formats

    setup = team_setup(left, right, scenario)
    agents = setup['agents']
    sides = [_teams.side_entry(left), _teams.side_entry(right)]
    extended = setup['action_set'] == 'v2'
    pair = None if difficulty is None or list(difficulty) == [None, None] else [tuple(difficulty)]
    batch = cuju.BatchEnvironment(
        asset_of(scenario, tuple(agents)),
        agent_counts=[tuple(agents)],
        environment_count=1,
        representation='raw',
        seed=seed,
        first_index=index,
        auto_reset=False,
        device=device,
        difficulty=pair,
        action_set=setup['action_set'],
    )
    formats = chosen_formats(submission_format)
    policies = None
    rows: list[np.ndarray] = []
    goals = [0, 0]
    forfeit = None
    played: list[Any] = [None, None]
    started = time.perf_counter()
    try:
        batch.reset()
        try:
            policies = Policies(
                batch,
                sides,
                scenario,
                agents,
                extended,
                [0, agents[0]],
                seed=seed,
                formats=formats,
                kind=kind,
                length=_STEPS - 1,
                pool=pool,
            )
            done = False
            started = time.perf_counter()
            while not done:
                parts = policies.observe()
                answers = policies.decide(parts)
                actions = policies.actions(parts, answers)
                rows.append(
                    (actions[0] % max(1, batch.action_count)).to('cpu').numpy().astype(np.uint8)
                )
                _, _, finished, information = batch.step(actions)
                score = int(information['score_reward'][0])
                if score:
                    goals[0 if score > 0 else 1] += 1
                done = bool(finished[0])
                if progress is not None and len(rows) % 50 == 0:
                    progress(min(0.99, len(rows) / _STEPS))
            policies.finish()
        except RuleError as broken:
            # The format decided a forfeit, which ends the match and is never evidence.
            found = policies.stats() if policies is not None else broken.stats
            forfeit = ForfeitError(broken, list(goals), len(rows), found).rule
        except BaseException:
            if policies is not None:
                policies.clean = False
            raise
        seconds = time.perf_counter() - started
    finally:
        if policies is not None:
            played = policies.formats()
            policies.close()
        batch.close()
    width = sum(agents)
    stacked = np.stack(rows).reshape(len(rows), 1, width) if rows else np.zeros((0, 1, width))
    return Played(
        [goals],
        [len(rows)],
        stacked.astype(np.uint8),
        seconds=seconds,
        formats=played,
        forfeit=forfeit,
    )


# The kept actions of a run.


def actions_folder(run: str, create: bool = False) -> Path:
    """Returns the folder of the actions of a run, beside its manifest."""
    path = _runs.folder() / f'{run}{_ACTIONS}'
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def episode_digest(actions: np.ndarray) -> str:
    """Returns the SHA-256 of the actions of one match, `[steps, agents]`, with their shape."""
    import numpy as np

    rows = np.ascontiguousarray(actions, dtype=np.uint8)
    digest = hashlib.sha256()
    digest.update(len(rows).to_bytes(8, 'little'))
    digest.update((rows.shape[1] if rows.ndim == 2 else 0).to_bytes(8, 'little'))
    digest.update(rows.tobytes())
    return digest.hexdigest()


def write_chunk(
    run: str,
    name: str,
    numbers: list[int],
    played: Played,
    extras: dict[int, dict[str, np.ndarray]] | None = None,
) -> list[str]:
    """Writes the actions of a batch or of a match of a run, and returns the digest of each.

    Args:
        run: The run.
        name: The name of the file within the folder of the run's actions, without its end.
        numbers: The number of each match of the batch within the run, in the order of the
            environments.
        played: What the batch gave.
        extras: The extras to keep, by the number of the match and by name, each `[steps, ...]`
            of the steps of that match.
    """
    import numpy as np

    arrays: dict[str, np.ndarray] = {
        'numbers': np.asarray(numbers, dtype=np.int64),
        'lengths': np.asarray(played.lengths, dtype=np.int64),
        'actions': np.ascontiguousarray(played.actions, dtype=np.uint8),
    }
    for number, found in (extras or {}).items():
        for key, values in found.items():
            arrays[f'x{number}|{key}'] = values
    folder = actions_folder(run, create=True)
    target = folder / f'{name}.npz'
    temporary = folder / f'{name}.part.npz'
    np.savez_compressed(temporary, **arrays)
    os.replace(temporary, target)
    return [
        episode_digest(played.actions[: played.lengths[column], column])
        for column in range(len(numbers))
    ]


def episodes_of(run: str) -> dict[int, dict[str, Any]]:
    """Returns the kept actions of each match of a run, by the number of the match.

    Each entry holds the `actions` of the match, `[steps, agents]`, the `extras` kept of it by
    name and the `file` that holds them. A file that does not read is left out, so its matches
    are missing.
    """
    import numpy as np

    found: dict[int, dict[str, Any]] = {}
    extras: dict[int, dict[str, np.ndarray]] = {}
    folder = actions_folder(run)
    if not folder.is_dir():
        return found
    for path in sorted(folder.glob('*.npz')):
        if path.name.endswith('.part.npz'):
            continue
        try:
            with np.load(path, allow_pickle=False) as data:
                numbers = data['numbers'].tolist()
                lengths = data['lengths'].tolist()
                actions = data['actions']
                for key in data.files:
                    if key.startswith('x') and '|' in key:
                        number, _, name = key[1:].partition('|')
                        extras.setdefault(int(number), {})[name] = data[key]
                for column, number in enumerate(numbers):
                    found[int(number)] = {
                        'actions': np.array(actions[: lengths[column], column]),
                        'extras': {},
                        'file': path.name,
                    }
        except (OSError, ValueError, KeyError, IndexError):
            continue
    # The extras of the kept matches lie in a file of their own, written once play has ended.
    for number, kept in extras.items():
        if number in found:
            found[number]['extras'] = kept
    return found


def keep_episodes(run: str, episodes: list[dict[str, Any]]) -> None:
    """Writes the matches of a run into its manifest, in place of those it holds.

    Raises:
        ArgumentError: If the run is unknown or a match is no mapping of plain JSON.
    """
    _runs.update(run, episodes=episodes)


def remove_actions(run: str) -> None:
    """Deletes the kept actions of a run."""
    import shutil

    shutil.rmtree(actions_folder(run), ignore_errors=True)


# The child process of a match of teams, or of a whole stored evaluation.

_reports: TextIO = sys.stdout


def _report(**entries: Any) -> None:
    _reports.write(json.dumps(entries) + '\n')
    _reports.flush()


def child_command(spec: dict[str, Any]) -> list[str]:
    """Returns the command of the child process that plays one match of teams."""
    from .._store import home as _home

    return [
        *_home.python_command(),
        '-m',
        'cuju.arena._evaluate.runner',
        '--match',
        json.dumps(spec),
    ]


def run_command(run: str) -> list[str]:
    """Returns the command of the child process that plays a stored evaluation to its end."""
    from .._store import home as _home

    return [*_home.python_command(), '-m', 'cuju.arena._evaluate.runner', '--run', run]


def worker_command() -> list[str]:
    """Returns the command of a match worker, which plays matches of teams as they come.

    Each line on its standard input is one match as `child_command` takes it, and its reports
    are those of `child_command` with the line that `_formats.worker` ends each match with.
    """
    from .._store import home as _home

    return [*_home.python_command(), '-m', 'cuju.arena._evaluate.runner', '--worker']


def _play_spec(spec: dict[str, Any], pool: Any = None) -> bool:
    """Plays one match of teams that a child names and reports its result or its failure.

    Returns:
        Whether the match gave a result.
    """
    from .._formats import terminals as _terminals

    try:
        name = spec.get('terminal')
        with (
            _terminals.run(name)
            if isinstance(name, str) and _terminals.valid(name)
            else contextlib.nullcontext()
        ):
            played = play_teams(
                spec['left'],
                spec['right'],
                spec['scenario'],
                spec['seed'],
                spec.get('index', 0),
                spec.get('difficulty'),
                spec.get('device', 'cpu'),
                progress=lambda share: _report(progress=share),
                submission_format=spec.get('submission_format'),
                kind=spec.get('kind', 'rating'),
                pool=pool,
            )
    except Exception as error:
        _report_error(error)
        return False
    _report(
        result={
            'goals': played.goals[0],
            'length': played.lengths[0],
            'actions': played.actions[:, 0].tolist(),
            'seconds': played.seconds,
            'formats': played.formats,
            'forfeit': played.forfeit,
        }
    )
    return True


def _report_error(error: BaseException) -> None:
    """Reports the failure of a match or a run, a policy's in its own words.

    A report that cannot be written, since the parent has gone, raises `OSError`.
    """
    shown = None
    with contextlib.suppress(Exception):
        from .._playing.play import PolicyError

        if isinstance(error, PolicyError):
            shown = str(error)
    _report(
        error=shown if shown is not None else _messages.described(error),
        **_messages.id_fields(error),
    )


def main(arguments: list[str] | None = None) -> None:
    """Plays the match or the evaluation that the command line names and reports as it goes.

    With `--worker` the process is a match worker of `_formats.worker`, which plays the matches
    that arrive on its standard input under one pool until that input ends.
    """
    global _reports
    parser = argparse.ArgumentParser(prog='python -m cuju.arena._evaluate.runner')
    parser.add_argument('--match', type=json.loads, default=None, help='a match of teams, as JSON')
    parser.add_argument('--run', default=None, help='the identifier of a stored evaluation')
    parser.add_argument('--worker', action='store_true', help='play matches as they arrive')
    options = parser.parse_args(arguments)
    # The reports keep a copy of standard output, and whatever a policy prints goes to standard
    # error, so no policy can forge a report.
    sys.stdout.flush()
    _reports = os.fdopen(os.dup(1), 'w', encoding='utf-8')
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    if options.worker:
        from .._formats import worker as _worker

        _worker.serve(_play_spec, _reports)
        return
    try:
        if options.run is not None:
            from . import evaluate as _evaluate

            document = _evaluate.resume(
                options.run, progress=lambda found: _report(progress=found.get('played'))
            )
            _report(result=document.id)
        elif not _play_spec(options.match or {}):
            sys.exit(1)
    except Exception as error:
        _report_error(error)
        sys.exit(1)


if __name__ == '__main__':
    main()
