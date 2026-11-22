# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The objects that the renderer hands a format, the team, the match, each step and the result.

Within a major of the contract these only gain fields, so a format reads the fields it knows and
leaves the others alone. Each crosses to a format process as JSON through `as_dict`, with the raw
observations of a step as arrays of the fixed table.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

# GRF: the names of the roles, by the number that a raw observation gives.
ROLES = ('GK', 'CB', 'LB', 'RB', 'DM', 'CM', 'LM', 'RM', 'AM', 'CF')


def _known(cls: type, document: dict[str, Any]) -> dict[str, Any]:
    names = {field.name for field in dataclasses.fields(cls)}
    return {key: value for key, value in document.items() if key in names}


@dataclasses.dataclass(frozen=True)
class Control:
    """How a team controls its players, as its Control section sets it.

    Attributes:
        players: The players it controls, 1 to 11.
        assignment: `dynamic`, the active player as GRF moves it, or `fixed`, chosen players.
        selection: The chosen players of a fixed assignment, by role name or formation place.
        goalkeeper: Who plays the goalkeeper, `scenario` where the scenario's own setting decides,
            `agent` for the team's own policy, or `builtin` for the built-in AI.
        action_set: The action set it answers in, `default` or `v2`.
    """

    players: int = 1
    assignment: str = 'dynamic'
    selection: tuple[Any, ...] = ()
    goalkeeper: str = 'scenario'
    action_set: str = 'default'

    @classmethod
    def from_dict(cls, document: dict[str, Any] | None) -> Control:
        """Returns the control of a team entry's `control`, as the registry checks it."""
        found = _known(cls, document or {})
        found['selection'] = tuple(found.get('selection') or ())
        return cls(**found)

    def as_dict(self) -> dict[str, Any]:
        """Returns the control as JSON."""
        return {**dataclasses.asdict(self), 'selection': list(self.selection)}


@dataclasses.dataclass(frozen=True)
class Team:
    """The team that a pre-check judges.

    Attributes:
        name: The team's name.
        size: `5v5` or `11v11`.
        scenario: The context's scenario, or `None` where none is chosen yet, as in the team's
            own Submission section.
        size_default_scenario: The scenario of the size, `5_vs_5` or `11_vs_11_kaggle`, which a
            check plays where `scenario` is `None`.
        control: The team's `Control`.
        checkpoint: The identifier of the checkpoint judged.
        options: The format's options, resolved, those that reach only the check among them.
        full: Whether the context asks for the full check, such as a tournament's start.
    """

    name: str
    size: str
    scenario: str | None
    size_default_scenario: str
    control: Control
    checkpoint: str | None
    options: dict[str, Any]
    full: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Returns the team as JSON."""
        return {**dataclasses.asdict(self), 'control': self.control.as_dict()}

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> Team:
        """Returns the team that `as_dict` gave."""
        found = _known(cls, document)
        found['control'] = Control.from_dict(found.get('control'))
        return cls(**found)


@dataclasses.dataclass(frozen=True)
class Match:
    """The match that a side's player plays.

    Attributes:
        scenario: The scenario.
        steps: The steps of the scenario, its own length or the one the context set.
        seed: The engine's seed.
        policy_seed: A seed of the side, drawn from the seed and the side, for a submission's own
            randomness, so a match played again from its seed plays the same.
        side: `left` or `right`, the side as the engine places it. Each step is seen from the
            side's own point of view, its team as `left_team`, as gfootball gives a right agent.
        players: The players the side controls.
        indices: The engine index of each controlled player at kick off.
        roles: The role name of each controlled player, by its engine index.
        assignment: `dynamic` or `fixed`.
        team_action_set: The action set the team's control chose, which its answers are in.
        kind: `check`, `match`, `rating`, `tournament`, `evaluation` or `game`.
        options: The format's options that reach play, resolved.
        gpu: Whether the format process may see a GPU.
        threads: The threads the format process was given.
        folder: The submission's folder, its read-only copy where the format runs in a process.
        cache: A writable folder kept between runs for this format and checkpoint, or `None`.
    """

    scenario: str
    steps: int
    seed: int
    policy_seed: int
    side: str
    players: int
    indices: tuple[int, ...]
    roles: dict[int, str]
    assignment: str
    team_action_set: str
    kind: str
    options: dict[str, Any]
    gpu: bool
    threads: int
    folder: Path
    cache: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        """Returns the match as JSON."""
        found = dataclasses.asdict(self)
        found.update(
            indices=list(self.indices),
            roles={str(index): role for index, role in self.roles.items()},
            folder=str(self.folder),
            cache=None if self.cache is None else str(self.cache),
        )
        return found

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> Match:
        """Returns the match that `as_dict` gave."""
        found = _known(cls, document)
        found.update(
            indices=tuple(found.get('indices') or ()),
            roles={int(index): role for index, role in (found.get('roles') or {}).items()},
            folder=Path(found['folder']),
            cache=None if found.get('cache') is None else Path(found['cache']),
        )
        return cls(**found)


@dataclasses.dataclass(frozen=True)
class Step:
    """One step of the engine, sent to the side's player whether or not its answer is used.

    Attributes:
        number: The engine's step, from 0 at kick off.
        used: Whether the answer will be applied. It is false at a set piece of a fixed
            assignment, where GRF hands the players over, and while a live game hands the side to
            the built-in AI. A player may still update its state, and may return `None`.
        previous_applied: Whether the answer to the previous step was applied, which a live
            game can refuse after the fact. It is true at the first step.
        raw: One GRF raw observation per controlled player that plays this step, typed numpy
            arrays as gfootball gives them, from the side's own point of view.
        indices: The engine index of the player behind each entry of `raw`. An answer holds
            `len(raw)` actions, in this order.
        action_set: The side's action set, which its answers are checked against.
        renderer_seconds: The round trip of the previous step as the renderer measured it
            outside the format, or `None` at the first step.
        score: The goals of the side and of the other side so far.
    """

    number: int
    used: bool
    previous_applied: bool
    raw: list[dict[str, Any]]
    indices: tuple[int, ...]
    action_set: str
    renderer_seconds: float | None
    score: tuple[int, int]

    def as_dict(self) -> dict[str, Any]:
        """Returns the step as a value of a frame, its raw observations as they are."""
        return {
            'number': self.number,
            'used': self.used,
            'previous_applied': self.previous_applied,
            'raw': self.raw,
            'indices': list(self.indices),
            'action_set': self.action_set,
            'renderer_seconds': self.renderer_seconds,
            'score': list(self.score),
        }

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> Step:
        """Returns the step of a frame's value."""
        found = _known(cls, document)
        found.update(
            indices=tuple(found.get('indices') or ()),
            score=tuple(found.get('score') or (0, 0)),
        )
        return cls(**found)


@dataclasses.dataclass(frozen=True)
class Result:
    """The end of a match, which `end` reads.

    Attributes:
        goals: The goals of the side and of the other side.
        steps: The steps played.
        forfeit: The forfeit that ended the match, `{side, kind, message, detail}` with `side`
            `own`, `other` or `both`, or `None`.
    """

    goals: tuple[int, int]
    steps: int
    forfeit: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """Returns the result as JSON."""
        return {'goals': list(self.goals), 'steps': self.steps, 'forfeit': self.forfeit}

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> Result:
        """Returns the result that `as_dict` gave."""
        found = _known(cls, document)
        found['goals'] = tuple(found.get('goals') or (0, 0))
        return cls(**found)
