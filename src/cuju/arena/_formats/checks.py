# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The pre-check of a team under a format, and the check matches that `test_match` plays.

`check(choice, folder, team)` runs the format's `accept` in a checker process of its own, or in
this process for a built-in format that plays in the match process, and returns its `Verdict`.
An exception of the submission's code refuses the team as `crash`, one of the format's own code
is a `FormatFailure`, and a missing package is an environment refusal, which is never kept.

Inside `accept`, `test_match` plays a short match through `play_side`, the team in a fresh format
process under the same format and options, against the built-in AI or the random entrant.
The host watches the match process tree and sends its ceiling verdict to the checker over the
same frames that carry the result of `accept`.
"""

from __future__ import annotations

import collections
import contextlib
import contextvars
import hashlib
import random
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..formats import v1 as _v1
from ..formats.v1 import _context
from . import ceilings as _ceilings
from . import check_trees as _check_trees
from . import host as _host
from . import launch as _launch
from . import terminals as _terminals

# The size of each team, by its size, where a check names no scenario.
SIZE_SCENARIOS = {'5v5': '5_vs_5', '11v11': '11_vs_11_kaggle'}
# GRF: the actions of the default set, which the random entrant draws from.
_DEFAULT_ACTIONS = 19


class Cap:
    """A ceiling on the steps of every check match, and a file that notes a match it cut.

    The file lets a checker process tell this one that a match met the ceiling before it ended.
    """

    def __init__(self, steps: int, path: Path) -> None:
        self.steps = int(steps)
        self.path = Path(path)
        self.cut = False

    def mark(self) -> None:
        """Notes that a check match was cut by the ceiling."""
        self.cut = True
        self.path.write_text('cut', encoding='ascii')


_CAP: contextvars.ContextVar[Cap | None] = contextvars.ContextVar('check_cap', default=None)


@contextlib.contextmanager
def capped(steps: int | None) -> Iterator[Cap | None]:
    """Holds every check match of the block to at most `steps` steps, `None` for no ceiling.

    The block yields the `Cap`, whose `cut` tells afterwards whether a match met the ceiling
    before its end, which a pre-check then reports instead of a pass.
    """
    if steps is None:
        yield None
        return
    folder = Path(tempfile.mkdtemp(prefix='cuju-cut-'))
    cap = Cap(steps, folder / 'cut')
    token = _CAP.set(cap)
    try:
        yield cap
    finally:
        _CAP.reset(token)
        cap.cut = cap.cut or cap.path.exists()
        shutil.rmtree(folder, ignore_errors=True)


def _refusal(found: Any) -> _v1.Verdict:
    if isinstance(found, _host.FormatFailure):
        raise found
    kind = found.kind if found.kind in _v1.KINDS else 'crash'
    detail = found.detail if found.kind in _v1.KINDS else found.kind.replace('ceiling.', 'ceiling_')
    return _v1.refuse(kind, found.message, detail=detail, environment=found.detail == 'environment')


def check(
    format: Any,
    values: dict[str, Any],
    folder: Path,
    team: _v1.Team,
    terminal: Any = None,
) -> _v1.Verdict:
    """Runs a format's pre-check of a team and returns its verdict.

    Args:
        format: The `registry.Format`.
        values: Every option of the format, resolved, those that reach only the check among them.
        folder: The submission's folder.
        team: The `v1.Team`, with `options` the resolved values.
        terminal: Where the checker's output goes, or `None`.

    Raises:
        FormatFailure: If the format's own code fails.
    """
    del values
    inside = format.spec.process.host == 'match'
    if terminal is None and _terminals.current() is not None:
        terminal = _terminals.current().open('check', pipe=not inside)
    try:
        verdict = _check(format, folder, team, terminal)
    except BaseException:
        if terminal is not None:
            terminal.note('failure', name=team.name)
            terminal.close()
        raise
    if terminal is not None:
        if not verdict.accepted:
            found = next((one for one in verdict.problems if one.level == 'error'), None)
            terminal.note(
                'refused',
                name=team.name,
                kind=getattr(found, 'kind', None),
                message=getattr(found, 'message', ''),
            )
        terminal.close()
    return verdict


def _check(format: Any, folder: Path, team: _v1.Team, terminal: Any) -> _v1.Verdict:
    seconds = float(format.spec.process.check_minutes) * 60.0
    if format.spec.process.host == 'match':
        host = _host.MatchHost(format, _check_match(folder), terminal)
        host.note('start', format=format.label)
        host.context.team = team
        host.context.play_check = _play_check(format, Path(folder), team)
        try:
            verdict = host.call(host.module.accept, [Path(folder), team], seconds, True)
        except _host.SideForfeit as found:
            return _refusal(found)
        if not isinstance(verdict, _v1.Verdict):
            raise _host.FormatFailure(f'accept returned {type(verdict).__name__}, not a verdict')
        return verdict
    # The checker plays under the same ceilings as a side, its tree watched and pinned.
    guard = _host.SideGuard(format.spec.process)
    process = guard.start(Path(folder), terminal, names=format.spec.process.env)
    trees = _check_trees.CheckTrees(process, _ceilings.limits(format.spec.process))
    if terminal is not None:
        terminal.note('start', format=format.label, pid=process.pid)

    def ceiling() -> _v1.Verdict | None:
        found = guard.settle()
        if found is None:
            return None
        # A ceiling belongs to the machine as much as to the team, so the verdict is never kept.
        detail = found.kind.replace('ceiling.', 'ceiling_')
        return _v1.refuse('crash', found.message, detail=detail, environment=True)

    try:
        process.send(
            'hello',
            {
                'role': 'format',
                'folder': str(folder),
                'script': str(format.folder / format.spec.script),
                'format_folder': None if format.builtin else str(format.folder),
                'builtin': format.builtin,
                'module': format.module,
                'check': {
                    'format': str(format.folder),
                    'id': format.id,
                    'cap': None
                    if _CAP.get() is None
                    else {'steps': _CAP.get().steps, 'path': str(_CAP.get().path)},
                },
            },
        )
        try:
            answer = process.receive(seconds)
            if answer['kind'] == 'error':
                return _refusal(_host._forfeit_of(answer['value'], True))
            process.send('call', {'method': 'accept', 'arguments': [team.as_dict()]})
            answer = trees.receive(seconds)
        except _launch.Timeout:
            return _v1.refuse('timeout', f'the check took longer than {seconds / 60:g} minutes')
        except _launch.Ended as error:
            return ceiling() or _v1.refuse(
                'crash', f'the checker ended, {error}', detail='ceiling_crash'
            )
        if answer['kind'] == 'error':
            return ceiling() or _refusal(_host._forfeit_of(answer['value'], True))
        return _v1.Verdict.from_dict(answer['value'])
    finally:
        try:
            trees.close()
        finally:
            code = process.close()
            guard.close()
        if terminal is not None:
            terminal.note('exit', code=code)


def _check_match(folder: Path) -> _v1.Match:
    """Returns the match a pre-check in the match process stands in, which names its folder."""
    return _v1.Match(
        scenario='',
        steps=0,
        seed=0,
        policy_seed=0,
        side='left',
        players=0,
        indices=(),
        roles={},
        assignment='dynamic',
        team_action_set='default',
        kind='check',
        options={},
        gpu=False,
        threads=1,
        folder=Path(folder),
    )


def _play_check(format: Any, folder: Path, team: _v1.Team) -> Any:
    """Returns what `test_match` calls in a pre-check of `team` under `format`."""

    def play(opponent: str, side: str, steps: int | None, seed: int) -> dict[str, Any]:
        return play_side(format, folder, team, opponent, side, steps, seed, _CAP.get())

    return play


def child_play_check(hello: dict[str, Any], request: Any) -> Any:
    """Returns the `play_check` of a checker process, from its `hello`."""
    from . import registry as _registry

    found = hello.get('check') or {}
    folder = Path(found['format'])
    format = _registry._read(found['id'], folder, bool(hello.get('builtin')))
    submission = Path(hello['folder'])
    held = found.get('cap')
    cap = None if not held else Cap(held['steps'], Path(held['path']))

    def monitor(_limits: Any, cpus: Any, release: Any) -> _check_trees.RemoteTree:
        return _check_trees.RemoteTree(request, cpus, release)

    def play(opponent: str, side: str, steps: int | None, seed: int) -> dict[str, Any]:
        team = _context.current().team
        return play_side(format, submission, team, opponent, side, steps, seed, cap, monitor)

    return play


def play_side(
    format: Any,
    folder: Path,
    team: _v1.Team,
    opponent: str,
    side: str,
    steps: int | None,
    seed: int,
    cap: Cap | None = None,
    monitor: Any = None,
) -> dict[str, Any]:
    """Plays a check match of a team under a format, and returns what `test_match` reads.

    The team plays in a fresh host of the format, under the options of the check that reach play.
    A `cap` holds the match to its steps, and a match that it cuts before the end is noted.
    """
    import torch

    from cuju import BatchEnvironment

    from .._playing.play import _player_columns
    from .._registry import teams as _teams
    from . import contract as _contract
    from . import side as _side

    scenario = team.scenario or team.size_default_scenario
    own = team.control.players
    selected = None
    held: dict[int, int] = {}
    if team.control.assignment == 'fixed':
        # A fixed assignment has an agent for every controllable player of the side, and the
        # team's selection decides which of them it plays, as it does in a match.
        control = _teams.check_control(team.control.as_dict(), team.size)
        players = _teams.scenario_players(scenario, 0 if side == 'left' else 1)
        selected = _teams.selected_players(control, players)
        own = sum(player.controllable for player in players)
    other = 1 if opponent == 'random' else 0
    left = side == 'left'
    counts = (own, other) if left else (other, own)
    extended = team.control.action_set == 'v2' or team.control.assignment == 'fixed'
    batch = BatchEnvironment(
        f'{scenario}_l{counts[0]}_r{counts[1]}',
        agent_counts=[counts],
        environment_count=1,
        threads=1,
        representation='raw',
        seed=int(seed),
        auto_reset=False,
        device='cpu',
        action_set='v2' if extended else 'default',
    )
    rolls = random.Random(hashlib.sha256(f'{seed}/check/random'.encode()).digest())
    columns = list(range(own)) if left else list(range(other, other + own))
    others = list(range(own, own + other)) if left else list(range(other))
    choice = _side.Choice(format, _contract.options(format.spec, team.options))
    limit = steps
    if cap is not None and (limit is None or cap.steps < limit):
        limit = cap.steps
    else:
        cap = None
    found: dict[str, Any] = {
        'goals': [0, 0],
        'steps': 0,
        'forfeit': None,
        'seconds': [],
        'actions': collections.Counter(),
        'sequence': [],
    }
    host = None
    try:
        batch.reset()
        raw = {name: tensor.cpu() for name, tensor in batch.raw().items()}
        timing = int(raw['steps_left'][0, columns[0]])
        match = _side.match_of(
            batch,
            raw,
            0 if left else 1,
            columns,
            team.control.as_dict(),
            choice,
            seed,
            scenario,
            timing,
            'check',
            folder,
        )
        terminal = None
        current = _terminals.current()
        if current is not None:
            terminal = current.open('play', pipe=format.spec.process.host != 'match')
        host = _host.host_for(format, match, terminal)
        if monitor is not None:
            host.guard.monitor = monitor
        try:
            host.start()
            host.reset()
            done = False
            while not done and (limit is None or found['steps'] < limit):
                raw = {name: tensor.cpu() for name, tensor in batch.raw().items()}
                seen, used = columns, True
                if selected is not None:
                    # GRF hands the players over at a set piece, and the answer is not used.
                    picked = _player_columns(raw, columns, selected, held)
                    seen, used = (columns, False) if picked is None else (picked[0], True)
                step = _side.step_of(
                    batch,
                    raw,
                    found['steps'],
                    seen,
                    used,
                    'v2' if extended else 'default',
                    host.last,
                )
                host.send(step)
                answer = host.receive()
                found['seconds'].append(host.last)
                found['sequence'].append(list(answer or []))
                found['actions'].update(answer or [])
                actions = torch.zeros((1, sum(counts)), dtype=torch.int32)
                if selected is not None:
                    actions[0, columns] = _DEFAULT_ACTIONS
                if answer:
                    actions[0, seen] = torch.tensor(answer, dtype=torch.int32)
                for column in others:
                    actions[0, column] = rolls.randrange(_DEFAULT_ACTIONS)
                _, _, finished, _ = batch.step(actions)
                done = bool(finished[0])
                found['steps'] += 1
            if cap is not None and not done:
                cap.mark()
            score = batch.raw()['score'][0, columns[0]].tolist()
            found['goals'] = [int(score[0]), int(score[1])]
            forfeit = host.end(_v1.Result(goals=tuple(found['goals']), steps=found['steps']))
            if forfeit is not None:
                found['forfeit'] = forfeit.as_dict()
        except _host.SideForfeit as forfeit:
            found['forfeit'] = forfeit.as_dict()
    finally:
        if host is not None:
            host.close()
        batch.close()
    found['actions'] = {str(action): count for action, count in found['actions'].items()}
    return found
