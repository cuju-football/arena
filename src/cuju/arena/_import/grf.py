# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""What a GRF dump and a Kaggle episode hold, read into one view of each step of the match.

GRF names the actions of an agent by the lists of its action sets, and a dump stores each step's
observation in the frame of the pitch, as a left agent sees the match, with the actions of the
right agents turned to that frame. A Kaggle episode stores what each agent saw, so the right agent
sees the match turned by half a turn, with its own team as `left_team`. `Step` holds one step in
the frame of the pitch whatever the source, which the import of positions draws from, and
`observation` builds a dump's observation from a batch, which the exact import compares.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .._format import players as _players

# GRF: `football_action_set.action_set_dict`, the actions of each set by index.
_DEFAULT_SET = (
    'idle',
    'left',
    'top_left',
    'top',
    'top_right',
    'right',
    'bottom_right',
    'bottom',
    'bottom_left',
    'long_pass',
    'high_pass',
    'short_pass',
    'shot',
    'sprint',
    'release_direction',
    'release_sprint',
    'sliding',
    'dribble',
    'release_dribble',
)
ACTION_SETS: dict[str, tuple[str, ...]] = {
    'default': _DEFAULT_SET,
    'v2': (*_DEFAULT_SET, 'builtin_ai'),
    'full': (
        *_DEFAULT_SET,
        'builtin_ai',
        'keeper_rush',
        'pressure',
        'team_pressure',
        'switch',
        'release_long_pass',
        'release_high_pass',
        'release_short_pass',
        'release_shot',
        'release_keeper_rush',
        'release_sliding',
        'release_pressure',
        'release_team_pressure',
        'release_switch',
    ),
}
# GRF: the eight directions, which a turn by half a turn maps four places on.
DIRECTIONS = _players.STICKY[:8]
_TURNED = {name: DIRECTIONS[(index + 4) % 8] for index, name in enumerate(DIRECTIONS)}
# GRF: a raw position is x divided by 54.4 and y by -83.6, in meters from the center spot.
RAW_SCALE = (54.4, -83.6)


def turned(name: str) -> str:
    """Returns an action as an agent of the other side names it, a direction turned by half."""
    return _TURNED.get(name, name)


def action_set_of(names: Sequence[str]) -> str | None:
    """Returns the smallest action set that holds every action named, or `None`."""
    for set_name in ('default', 'v2', 'full'):
        if all(name in ACTION_SETS[set_name] for name in names):
            return set_name
    return None


def sticky_mask(states: Any) -> int:
    """Returns the sticky actions of an agent, GRF's array of 0 and 1, as the bits of a lane.

    GRF lists the sticky actions of every action set in the order of `_players.STICKY`, the sets
    other than `full` holding the first ten.
    """
    values = np.asarray(states).reshape(-1)[: len(_players.STICKY)]
    return int(sum(1 << index for index, value in enumerate(values.tolist()) if value))


def turned_sticky(states: Any) -> np.ndarray:
    """Returns sticky actions as an agent of the other side holds them, the directions turned."""
    values = np.asarray(states).copy()
    values[:8] = np.asarray(states)[[(index + 4) % 8 for index in range(8)]]
    return values


def agent_counts(players: Any) -> tuple[int, int] | None:
    """Returns the left and right agents of GRF's player definitions, or `None`.

    GRF: a definition such as `agent:left_players=2,right_players=1` names its players, and one
    that names no count controls one left player. Only definitions of agents play again, so a
    definition of another kind, or one that does not read, gives `None`.
    """
    if not isinstance(players, list):
        return None
    left = right = 0
    for definition in players:
        if not isinstance(definition, str):
            return None
        name, _, text = definition.partition(':')
        if name != 'agent':
            return None
        counts = {'left_players': 0, 'right_players': 0}
        for part in filter(None, text.split(',')):
            key, _, value = part.partition('=')
            if key not in counts or not value.isdigit():
                return None
            counts[key] = int(value)
        if not counts['left_players'] and not counts['right_players']:
            counts['left_players'] = 1
        left += counts['left_players']
        right += counts['right_players']
    return left, right


def _array(value: Any, kind: Any, shape: tuple[int, ...] | None = None) -> np.ndarray:
    """Returns a value of an observation as a numpy array, or raises `ValueError`."""
    found = np.asarray(value, dtype=kind)
    if shape is not None and found.shape != shape:
        raise ValueError(f'expected the shape {shape}, found {found.shape}')
    return found


def _whole(value: Any) -> int:
    """Returns a whole number of an observation, or raises `ValueError`."""
    if isinstance(value, np.ndarray) and value.size == 1:
        value = value.reshape(()).item()
    if isinstance(value, bool) or not isinstance(value, (int, np.integer, float)):
        raise ValueError(f'expected a whole number, found {value!r}')
    if float(value) != int(value):
        raise ValueError(f'expected a whole number, found {value!r}')
    return int(value)


@dataclass
class Team:
    """One team of a step, each array with one row for each player.

    Attributes:
        positions: `[n, 2]`, GRF's raw positions.
        directions: `[n, 2]`, the movement of each player over the step, in raw units.
        tired: `[n]`, GRF's tired factor from 0 to 1.
        active: `[n]`, whether each player is on the pitch.
        yellow: `[n]`, whether each player has a yellow card.
        roles: `[n]`, GRF's role of each player.
        designated: The index of the designated player, or `None` where the source tells none.
        controlled: The index of the player that each agent of the team controls.
        sticky: The sticky actions of each agent of the team, as the bits of a lane.
    """

    positions: np.ndarray
    directions: np.ndarray
    tired: np.ndarray
    active: np.ndarray
    yellow: np.ndarray
    roles: np.ndarray
    designated: int | None = None
    controlled: list[int] = field(default_factory=list)
    sticky: list[int] = field(default_factory=list)


@dataclass
class Step:
    """One step of the match in the frame of the pitch, as GRF's raw observation tells it.

    Attributes:
        step: The steps since the kick off, 1 for the first step played.
        teams: The left and the right `Team`.
        ball: `[3]`, the raw position of the ball, its height in meters.
        ball_direction: `[3]`, the movement of the ball over the step, its height in meters.
        owned_team: The team that owns the ball, 0 or 1, or -1 for none.
        owned_player: The index of the owner within its team, or -1.
        game_mode: GRF's game mode.
        score: The goals of the left and of the right team.
        steps_left: The steps left in the episode.
        actions: The name of each agent's action that led to the step, left agents first, as the
            agent chose it, or `None` where the source tells none.
    """

    step: int
    teams: tuple[Team, Team]
    ball: np.ndarray
    ball_direction: np.ndarray
    owned_team: int
    owned_player: int
    game_mode: int
    score: tuple[int, int]
    steps_left: int
    actions: list[str] | None = None


def _team(view: Mapping[str, Any], side: str, sign: float) -> Team:
    """Returns one team of a raw view, its positions times `sign`."""
    positions = _array(view[f'{side}_team'], np.float64)
    count = len(positions)
    if positions.shape != (count, 2):
        raise ValueError(f'expected the positions of {side}_team as pairs')
    return Team(
        positions=sign * positions,
        directions=sign * _array(view[f'{side}_team_direction'], np.float64, (count, 2)),
        tired=_array(view[f'{side}_team_tired_factor'], np.float64, (count,)),
        active=_array(view[f'{side}_team_active'], bool, (count,)),
        yellow=_array(view[f'{side}_team_yellow_card'], bool, (count,)),
        roles=_array(view[f'{side}_team_roles'], np.int64, (count,)),
    )


def _point(value: Any, sign: float) -> np.ndarray:
    found = _array(value, np.float64, (3,)).copy()
    found[:2] *= sign
    return found


def dump_step(entry: Mapping[str, Any], left: int, right: int) -> Step:
    """Returns a step of a GRF dump in the frame of the pitch.

    Args:
        entry: The step, as the reader of dumps reads it.
        left, right: The agents of each team, which order the actions of the step.

    Raises:
        ValueError, KeyError, TypeError: If the step lacks what a dump holds.
    """
    view = entry['observation']
    debug = entry.get('debug') or {}
    teams = (_team(view, 'left', 1.0), _team(view, 'right', 1.0))
    for team, side in zip(teams, ('left', 'right'), strict=True):
        designated = view.get(f'{side}_team_designated_player')
        team.designated = None if designated is None else _whole(designated)
        team.controlled = [
            _whole(value) for value in view.get(f'{side}_agent_controlled_player', [])
        ]
        team.sticky = [sticky_mask(value) for value in view.get(f'{side}_agent_sticky_actions', [])]
    names = debug.get('action')
    actions = None
    if isinstance(names, list) and all(isinstance(name, str) for name in names) and names:
        # GRF: a dump stores the actions of the right agents turned to the frame of the pitch.
        actions = [name if agent < left else turned(name) for agent, name in enumerate(names)]
    score = view['score']
    return Step(
        step=_whole(debug.get('frame_cnt', 0)) + 1,
        teams=teams,
        ball=_point(view['ball'], 1.0),
        ball_direction=_point(view['ball_direction'], 1.0),
        owned_team=_whole(view['ball_owned_team']),
        owned_player=_whole(view['ball_owned_player']),
        game_mode=_whole(view['game_mode']),
        score=(_whole(score[0]), _whole(score[1])),
        steps_left=_whole(view['steps_left']),
        actions=actions,
    )


def agent_view(raw: Mapping[str, Any], right: bool) -> Step:
    """Returns a Kaggle agent's raw observation in the frame of the pitch.

    A right agent sees the match turned by half a turn, its own team as `left_team`, so its view
    turns back, with the teams, the owner of the ball and the score swapped, and its sticky
    actions turned.

    Args:
        raw: The agent's raw observation, an entry of `players_raw`.
        right: Whether the agent plays on the right.

    Raises:
        ValueError, KeyError, TypeError: If the observation lacks what GRF's raw one holds.
    """
    sign = -1.0 if right else 1.0
    own, other = _team(raw, 'left', sign), _team(raw, 'right', sign)
    teams = (other, own) if right else (own, other)
    owned = _whole(raw['ball_owned_team'])
    score = raw['score']
    goals = (_whole(score[0]), _whole(score[1]))
    sticky = raw.get('sticky_actions')
    if sticky is not None:
        states = np.asarray(sticky)
        own.sticky = [sticky_mask(turned_sticky(states) if right else states)]
    if raw.get('active') is not None:
        own.controlled = [_whole(raw['active'])]
    if raw.get('designated') is not None:
        own.designated = _whole(raw['designated'])
    return Step(
        step=0,
        teams=teams,
        ball=_point(raw['ball'], sign),
        ball_direction=_point(raw['ball_direction'], sign),
        owned_team=(1 - owned if owned >= 0 else -1) if right else owned,
        owned_player=_whole(raw['ball_owned_player']),
        game_mode=_whole(raw['game_mode']),
        score=(goals[1], goals[0]) if right else goals,
        steps_left=_whole(raw['steps_left']),
    )


def observation(views: Sequence[Mapping[str, Any]], left: int, right: int) -> dict[str, Any]:
    """Returns GRF's observation of the match as a dump stores it, from each agent's raw view.

    It is the observation of GRF's `FootballEnvCore`, built as cuju's `DumpWriter` builds it. The
    designated player of a team whose agents are absent is left out, since only an agent of the
    team sees it.

    Args:
        views: The raw observation of each agent, as `cuju.raw.grf_observations` gives them.
        left, right: The agents of each team.
    """
    base = views[0]
    # GRF: a right agent alone sees the match turned, with the teams swapped.
    flip = not left
    sign = -1.0 if flip else 1.0

    def point(value: Any) -> np.ndarray:
        found = np.asarray(value, np.float64).copy()
        found[:2] = sign * found[:2]
        return found

    found: dict[str, Any] = {
        'ball': point(base['ball']),
        'ball_direction': point(base['ball_direction']),
        'ball_rotation': np.asarray(base['ball_rotation'], np.float64),
    }
    for side in ('left', 'right'):
        source = side if not flip else ('right' if side == 'left' else 'left')
        found[f'{side}_team'] = sign * np.asarray(base[f'{source}_team'], np.float64)
        found[f'{side}_team_direction'] = sign * np.asarray(
            base[f'{source}_team_direction'], np.float64
        )
        for name in ('tired_factor', 'active', 'yellow_card', 'roles'):
            found[f'{side}_team_{name}'] = np.asarray(base[f'{source}_team_{name}'])
    if left:
        found['left_team_designated_player'] = int(views[0]['designated'])
    if right:
        found['right_team_designated_player'] = int(views[left]['designated'])
    found['left_agent_sticky_actions'] = [
        np.asarray(views[agent]['sticky_actions'], np.uint8) for agent in range(left)
    ]
    found['left_agent_controlled_player'] = [int(views[agent]['active']) for agent in range(left)]
    found['right_agent_sticky_actions'] = [
        turned_sticky(np.asarray(views[agent]['sticky_actions'], np.uint8))
        for agent in range(left, left + right)
    ]
    found['right_agent_controlled_player'] = [
        int(views[agent]['active']) for agent in range(left, left + right)
    ]
    owned = int(base['ball_owned_team'])
    score = [int(goals) for goals in base['score']]
    found['game_mode'] = int(base['game_mode'])
    found['score'] = [score[1], score[0]] if flip else score
    found['ball_owned_team'] = (1 - owned if owned > -1 else -1) if flip else owned
    found['ball_owned_player'] = int(base['ball_owned_player'])
    found['steps_left'] = int(base['steps_left'])
    return found


def _shown(value: Any) -> str:
    """Returns a short text of a value of a step."""
    if isinstance(value, np.ndarray):
        if value.size <= 6:
            return repr(value.tolist())
        return f'an array of the shape {tuple(value.shape)}'
    return repr(value)[:80]


def _equal_arrays(first: np.ndarray, second: np.ndarray) -> bool:
    if first.shape != second.shape:
        return False
    numeric = (first.dtype.kind in 'biuf') and (second.dtype.kind in 'biuf')
    if not numeric:
        return False
    if not np.array_equal(first, second):
        return False
    # A turned zero keeps its sign, as GRF's own turn does, so the signs count too.
    if first.dtype.kind == 'f' or second.dtype.kind == 'f':
        return bool(np.array_equal(np.signbit(first), np.signbit(second)))
    return True


def difference(mine: Any, stored: Any, where: str, skipped: set[str]) -> tuple[str, str] | None:
    """Returns where a step played again differs from the stored one, and how, or `None`.

    Only the stored step's keys count. A key that `mine` lacks is left out and named in
    `skipped`, since it is a part of the observation that the batch does not give.

    Returns:
        The field, such as `observation.left_team`, and a text of both values.
    """
    if isinstance(stored, np.ndarray) or isinstance(mine, np.ndarray):
        first, second = np.asarray(mine), np.asarray(stored)
        if not _equal_arrays(first, second):
            return where, f'the replay gives {_shown(first)}, the file holds {_shown(second)}'
        return None
    if isinstance(stored, Mapping):
        if not isinstance(mine, Mapping):
            return where, f'the replay gives {_shown(mine)}, the file holds a mapping'
        for key, value in stored.items():
            if key not in mine:
                skipped.add(f'{where}.{key}')
                continue
            found = difference(mine[key], value, f'{where}.{key}', skipped)
            if found is not None:
                return found
        return None
    if isinstance(stored, (list, tuple)):
        if not isinstance(mine, (list, tuple)) or len(mine) != len(stored):
            return where, f'the replay gives {_shown(mine)}, the file holds {_shown(stored)}'
        for index, (first, second) in enumerate(zip(mine, stored, strict=True)):
            found = difference(first, second, f'{where}[{index}]', skipped)
            if found is not None:
                return found
        return None
    numbers = (int, float, np.integer, np.floating)
    if isinstance(stored, bool) or isinstance(mine, bool):
        same = type(stored) is type(mine) and stored == mine
    elif isinstance(stored, numbers) and isinstance(mine, numbers):
        same = float(stored) == float(mine)
    else:
        same = type(stored) is type(mine) and stored == mine
    if not same:
        return where, f'the replay gives {_shown(mine)}, the file holds {_shown(stored)}'
    return None
