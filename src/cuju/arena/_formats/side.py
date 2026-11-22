# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""A side of a match played through a format, the bridge between a match's policies and a host.

A context chooses a format for a side as `submission_format`, the identifier of a format alone or
a mapping `{id, preset, options, per_entrant}`. `choose(value)` reads it into a `Choice`. A match
then builds the side's `v1.Match` from the batch at kick off with `match_of`, each step's
`v1.Step` with `step_of`, and plays it through a `Side`, which holds the host, the frozen copy of
the submission and the forfeit the side meets.

Each step every side's answer is asked at once, `send` for all of them first and `receive` after,
so the format processes think together and the engine steps in lockstep.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any

from cuju.raw import grf_observation, team_sizes

from .._text import messages as _messages
from .._text.messages import MessageError
from ..formats import v1 as _v1
from . import contract as _contract
from . import host as _host
from . import identity as _identity
from . import registry as _registry

# GRF: the role of each number of `left_team_roles`.
ROLES = _v1.ROLES
# The kinds of context a match is played for.
KINDS = ('check', 'match', 'rating', 'tournament', 'evaluation', 'game')


@dataclasses.dataclass(frozen=True)
class Choice:
    """The format a context chose for a side, with its options resolved.

    Attributes:
        format: The `registry.Format`.
        values: Every option, resolved, those that reach only the check among them.
        preset: The preset chosen, or `None`.
        per_entrant: The options set for this entrant alone.
    """

    format: _registry.Format
    values: dict[str, Any]
    preset: str | None = None
    per_entrant: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def playing(self) -> dict[str, Any]:
        """The options that reach play."""
        return _contract.playing(self.format.spec, self.values)

    def identity(self, revision: str | None = None) -> dict[str, Any]:
        """Returns the identity of the format as this side plays it."""
        return _identity.of(self.format, self.values, self.per_entrant, revision, self.preset)

    def as_dict(self) -> dict[str, Any]:
        """Returns the choice as a context keeps it."""
        found: dict[str, Any] = {'id': self.format.label, 'options': self.values}
        if self.preset is not None:
            found['preset'] = self.preset
        if self.per_entrant:
            found['per_entrant'] = self.per_entrant
        return found


def choose(value: Any) -> Choice | None:
    """Returns the `Choice` of a context's `submission_format`, or `None` for none.

    Raises:
        ArgumentError: If the format is unknown or broken, or an option or preset does not fit.
    """
    if value is None or value == '':
        return None
    if isinstance(value, Choice):
        return value
    if isinstance(value, str):
        value = {'id': value}
    if not isinstance(value, dict) or not isinstance(value.get('id'), str):
        raise MessageError('server.formats.choice', choice=repr(value))
    found = _registry.choosable(value['id'])
    per_entrant = dict(value.get('per_entrant') or {})
    try:
        values = _contract.options(
            found.spec, {**dict(value.get('options') or {}), **per_entrant}, value.get('preset')
        )
        for key in per_entrant:
            if not found.spec.options[key].per_entrant:
                raise _contract.ContractError(f'{key} is the same for every entrant')
    except _contract.ContractError as error:
        raise MessageError(
            'server.formats.options', format=found.label, reason=str(error)
        ) from None
    return Choice(found, values, value.get('preset'), per_entrant)


def submission_of(team: dict[str, Any]) -> tuple[Path, str | None]:
    """Returns the folder of a team's submission at its checkpoint, and the checkpoint's id.

    An entrant names the team's current files by the team's identifier, or an earlier checkpoint
    as `<team>@<id>`, which the resolver of checkpoints reads. The id is `files_digest[:16]`,
    the files alone.
    """
    from .._registry import checkpoints as _checkpoints

    found = _checkpoints.resolve(team.get('entrant') or team['id'])
    return Path(found['folder']), found['checkpoint']


def policy_seed(seed: int, side: int) -> int:
    """Returns the seed of a side's own randomness, drawn from the match's seed and the side."""
    digest = hashlib.sha256(f'{int(seed)}/format/{side}'.encode()).digest()
    return int.from_bytes(digest[:4], 'little') & 0x7FFFFFFF


def _roles(raw: dict[str, Any], agent: int, indices: list[int]) -> dict[int, str]:
    found = {}
    # GRF: an agent sees its own team as the left team, so its roles are `left_team_roles`.
    numbers = raw['left_team_roles'][0, agent].tolist()
    for index in indices:
        if 0 <= index < len(numbers) and 0 <= numbers[index] < len(ROLES):
            found[index] = ROLES[int(numbers[index])]
    return found


def match_of(
    batch: Any,
    raw: dict[str, Any],
    side: int,
    columns: list[int],
    control: dict[str, Any],
    choice: Choice,
    seed: int,
    scenario: str,
    steps: int,
    kind: str,
    folder: Path,
    cache: Path | None = None,
) -> _v1.Match:
    """Returns the `v1.Match` of a side at kick off.

    Args:
        batch: The batch of one environment, reset.
        raw: Its raw tensors on the host, as `batch.raw()` gives them.
        side: 0 for the left, 1 for the right.
        columns: The agents of the side, in order.
        control: The team's control, as the registry checks it.
        choice: The side's `Choice`.
        seed: The engine's seed.
        scenario: The scenario.
        steps: The steps of the scenario.
        kind: One of `KINDS`.
        folder: The submission's folder as the side reads it.
        cache: The side's cache folder, or `None`.
    """
    indices = [int(raw['active'][0, column]) for column in columns]
    # Every player of the side has its role, since a dynamic assignment may hand any of them over.
    own = team_sizes(batch, 0, columns[0])[0] if columns else 0
    roles = _roles(raw, columns[0], list(range(own))) if columns else {}
    spec = choice.format.spec
    return _v1.Match(
        scenario=scenario,
        steps=int(steps),
        seed=int(seed),
        policy_seed=policy_seed(seed, side),
        side=('left', 'right')[side],
        players=len(columns),
        indices=tuple(indices),
        roles=roles,
        assignment=control.get('assignment', 'dynamic'),
        team_action_set=control.get('action_set', 'default'),
        kind=kind,
        options=choice.playing,
        gpu=spec.process.gpu == 'allowed',
        threads=spec.process.threads,
        folder=folder,
        cache=cache,
    )


def step_of(
    batch: Any,
    raw: dict[str, Any],
    number: int,
    seen: list[int],
    used: bool,
    action_set: str,
    renderer_seconds: float | None,
    previous_applied: bool = True,
) -> _v1.Step:
    """Returns the `v1.Step` of a side, its raw observations those of the agents `seen`."""
    observations = [grf_observation(raw, 0, agent, *team_sizes(batch, 0, agent)) for agent in seen]
    indices = tuple(int(raw['active'][0, agent]) for agent in seen)
    score = tuple(observations[0]['score']) if observations else (0, 0)
    return _v1.Step(
        number=int(number),
        used=bool(used),
        previous_applied=bool(previous_applied),
        raw=observations,
        indices=indices,
        action_set=action_set,
        renderer_seconds=renderer_seconds,
        score=score,
    )


def frozen(folder: Path) -> Path:
    """Returns a read-only copy of a submission, without the renderer's own `cuju-team` folder.

    The copy lives in a temporary folder that `thaw` removes.
    """
    target = Path(tempfile.mkdtemp(prefix='cuju-submission-')) / 'submission'

    def ignore(where: str, names: list[str]) -> set[str]:
        skipped = {name for name in names if name == '__pycache__' or name.endswith('.part')}
        if Path(where).resolve() == Path(folder).resolve():
            skipped.update(
                name for name in names if name.startswith('cuju-') and (Path(where) / name).is_dir()
            )
            skipped.add('cuju-team')
        return skipped

    shutil.copytree(folder, target, symlinks=False, ignore=ignore)
    for root, folders, files in os.walk(target):
        for name in files:
            path = Path(root) / name
            path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        del folders
    return target


def thaw(copy: Path) -> None:
    """Removes a copy that `frozen` made."""

    def writable(function: Any, path: str, _: Any) -> None:
        os.chmod(path, stat.S_IWUSR | stat.S_IRUSR)
        function(path)

    shutil.rmtree(copy.parent, onerror=writable)


class Side:
    """One side of a match played through a format.

    Attributes:
        index: 0 for the left, 1 for the right.
        name: The side's name in a failure, text or a label.
        choice: The `Choice`.
        host: The `host.Host`, once `start` has run.
        forfeit: The forfeit the side met, as `host.SideForfeit`, or `None`.
    """

    def __init__(
        self,
        index: int,
        name: Any,
        choice: Choice,
        team_folder: Path,
        pool: Any = None,
        key: str | None = None,
    ) -> None:
        """Holds a side, and takes a kept host of the run where its format and the pool allow.

        Args:
            index: 0 for the left, 1 for the right.
            name: The side's name in a failure.
            choice: The `Choice`.
            team_folder: The submission's folder at its checkpoint.
            pool: The `pool.Pool` of the run, or `None`. Only a format of `[process] scope =
                "run"` that plays in a process of its own uses it.
            key: The side's key in the pool, as `pool.key_of` gives it.
        """
        self.index = index
        self.name = name
        self.choice = choice
        self.team_folder = Path(team_folder)
        self.host: _host.Host | None = None
        self.forfeit: _host.SideForfeit | None = None
        self.failed = False
        self._started = False
        self._copy: Path | None = None
        process = choice.format.spec.process
        self._pool = pool if process.scope == 'run' and process.host != 'match' else None
        self._key = key
        self._lease = None if self._pool is None else self._pool.take(key)
        if self._lease is not None:
            self.host = self._lease.host
            self._copy = self._lease.copy

    @property
    def in_process(self) -> bool:
        """Whether the side plays in the match process."""
        return self.choice.format.spec.process.host == 'match'

    @property
    def kept(self) -> bool:
        """Whether the side plays in a process that an earlier match of the run started."""
        return self._lease is not None and self._lease.matches > 0

    def folder(self) -> Path:
        """Returns the submission's folder as the side reads it, a frozen copy for a process."""
        if self.in_process:
            return self.team_folder
        if self._copy is None:
            self._copy = frozen(self.team_folder)
        return self._copy

    def start(self, match: _v1.Match, terminal: Any = None) -> None:
        """Starts the side's host, or the kept one's next match, and resets its player."""
        self._started = True
        if self._lease is not None:
            self.host.again(match, terminal)
        else:
            self.host = _host.host_for(self.choice.format, match, terminal)
            if self._pool is not None:
                from . import pool as _pool

                self._lease = _pool.Lease(self._key, self.host, self._copy)
                self._pool.adopt(self._lease)
            self.host.start()
        self.host.reset()

    def close(self) -> None:
        """Ends the side's match, keeping its host for the run where nothing went wrong.

        A host of the pool goes back to it after a clean match. A forfeit, a ceiling, a failure
        or a process that ended retires it, and the next match of the run starts a fresh one.
        Without a pool the host closes and the copy leaves.
        """
        self._note_end()
        if self._lease is not None:
            lease, self._lease = self._lease, None
            self.host, self._copy = None, None
            clean = self.forfeit is None and not self.failed and lease.host.alive()
            if clean and self._started:
                try:
                    lease.host.finish_match()
                except (_host.SideForfeit, _host.FormatFailure):
                    clean = False
            if clean:
                self._pool.give(lease)
            else:
                self._pool.retire(lease)
            return
        if self.host is not None:
            self.host.close()
        if self._copy is not None:
            thaw(self._copy)
            self._copy = None

    def _note_end(self) -> None:
        """Writes the renderer's closing lines of the side to its terminal."""
        host = self.host
        if host is None or host.terminal is None:
            return
        if host.load_seconds is not None:
            host.note('load', seconds=round(host.load_seconds, 2))
        if host.seconds:
            host.note('time', seconds=round(host.seconds, 2))
        if self.forfeit is not None:
            host.note(
                'forfeit',
                name=self.name,
                kind=self.forfeit.kind,
                message=self.forfeit.message,
            )
        elif self.failed:
            host.note('failure', name=self.name)

    def rule(self, forfeit: _host.SideForfeit) -> tuple[int, str, dict[str, Any], str | None]:
        """Returns the forfeit as a break of `RuleError`, `(side, kind, reason, detail)`."""
        reason = _messages.label('server.formats.forfeit', player=self.name, reason=forfeit.message)
        return (self.index, forfeit.kind, reason, forfeit.detail)
