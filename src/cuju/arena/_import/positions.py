# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Recordings of positions, which an import writes from the observations of a match alone.

A GRF dump that cannot play again and a Kaggle episode tell where each player and the ball stood
at each step, and nothing of the joints. Such an import becomes a recording of version 3, whose
track frames hold the head of each frame and one position for each player, as the layout of
`TRKS` in the repository's `docs/recording-format.md` describes. The lanes of the players hold
what the observations give, and the actions those of the agents. The header says
`fidelity: positions`, and the provenance names the file and why it kept positions. The page shows
such a recording in its 2D view.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from typing import Any

import numpy as np

from .._format import actions as _actions
from .._format import codec as _codec
from .._format import players as _players
from .._format import recording as _recording
from .._format import tracks as _tracks
from . import grf as _grf

# The head of a frame, eight integers, the ball's seven floats and four integers of the referee.
_HEAD = struct.Struct('<8i7f4i')
# GRF: 90 minutes of play take 3000 steps, so a step is 1.8 seconds of the match clock.
CLOCK_PER_STEP = 1800
# The bits of a player in the closing bytes of a frame.
_ACTIVE, _YELLOW, _RED, _CONTROLLED = 1, 2, 4, 8


def _meters(raw: np.ndarray) -> np.ndarray:
    """Returns raw positions `[..., 2]` in meters from the center spot."""
    return raw * np.array(_grf.RAW_SCALE)


def lineup(first: _grf.Step) -> list[dict[str, Any]]:
    """Returns the header entry of each player, the left team first, from the first step."""
    players = []
    for team_index, team in enumerate(first.teams):
        controlled = set(team.controlled)
        for index in range(len(team.positions)):
            players.append(
                {
                    'team': team_index,
                    'index': index,
                    'goalkeeper': int(team.roles[index]) == 0,
                    'controlled': index in controlled,
                }
            )
    return players


def track_frame(step: _grf.Step, flags: int = 0) -> bytes:
    """Returns the track frame of a step, as version 3 stores it.

    The position of each player is its place on the pitch at the height of the ground, the ball
    keeps its height, and what the observations do not tell, the rotation of the ball, the
    animations, the touches and the calls of the referee, holds its value for none.
    """
    left, right = step.teams
    sizes = (len(left.positions), len(right.positions))
    count = sum(sizes)
    owner = -1
    if step.owned_team in (0, 1) and 0 <= step.owned_player < sizes[step.owned_team]:
        owner = step.owned_player + (sizes[0] if step.owned_team == 1 else 0)
    ball = np.array([*_meters(step.ball[:2]), step.ball[2]], np.float32)
    head = _HEAD.pack(
        step.step,
        step.game_mode,
        step.score[0],
        step.score[1],
        step.owned_team if owner >= 0 else -1,
        owner,
        step.step * CLOCK_PER_STEP,
        flags,
        *ball.tolist(),
        0.0,
        0.0,
        0.0,
        1.0,
        -1,
        -1,
        0,
        -1,
    )
    places = np.concatenate([_meters(left.positions), _meters(right.positions)])
    roots = np.zeros((count, 3), np.float64)
    roots[:, :2] = places
    quantized = np.clip(np.rint(roots * _recording.POSITION_SCALE), -32767, 32767).astype('<i2')
    padded = _codec.padded(count)
    marks = np.zeros(padded, np.uint8)
    offset = 0
    for team in (left, right):
        controlled = set(team.controlled)
        for index in range(len(team.positions)):
            marks[offset + index] = (
                (_ACTIVE if team.active[index] else _RED)
                | (_YELLOW if team.yellow[index] else 0)
                | (_CONTROLLED if index in controlled else 0)
            )
        offset += len(team.positions)
    roots_bytes = quantized.tobytes()
    room = _tracks.frame_bytes(count) - _codec.HEAD_BYTES - 3 * padded
    return b''.join(
        (
            head,
            roots_bytes,
            bytes(room - len(roots_bytes)),
            marks.tobytes(),
            bytes(padded),
            bytes(padded),
        )
    )


def _steps(numbers: Any, least: int = -32767) -> np.ndarray:
    return np.clip(np.rint(numbers), least, 32767)


def lanes(steps: Sequence[_grf.Step]) -> dict[str, np.ndarray]:
    """Returns the lanes of the players of the steps by field, as `PLYR` keeps them.

    The direction, tiredness, sticky actions, agent and role of each player, the designated player
    of each team and the momentum of the ball come from the observations, quantized as the format
    describes. A team whose designated player no observation tells holds 0 for none.
    """
    first = steps[0]
    sizes = [len(team.positions) for team in first.teams]
    count, frames = sum(sizes), len(steps)
    found = {
        'direction': np.zeros((frames, count, 2), np.int16),
        'tired': np.zeros((frames, count), np.int16),
        'sticky': np.zeros((frames, count), np.uint16),
        'agent': np.zeros((frames, count), np.uint8),
        'role': np.zeros((frames, count), np.uint8),
        'designated': np.zeros((frames, 2), np.uint8),
        'ball_momentum': np.zeros((frames, 3), np.int16),
    }
    for row, step in enumerate(steps):
        offset = 0
        for side, team in enumerate(step.teams):
            span = slice(offset, offset + len(team.positions))
            found['direction'][row, span] = _steps(team.directions * _players.DIRECTION_SCALE)
            found['tired'][row, span] = _steps(team.tired * _players.TIRED_SCALE, 0)
            found['role'][row, span] = np.clip(team.roles, 0, 255)
            for agent, player in enumerate(team.controlled):
                if 0 <= player < len(team.positions):
                    found['agent'][row, offset + player] = agent + 1
                    if agent < len(team.sticky):
                        found['sticky'][row, offset + player] = team.sticky[agent]
            if team.designated is not None and 0 <= team.designated < len(team.positions):
                found['designated'][row, side] = offset + team.designated + 1
            offset += len(team.positions)
        momentum = step.ball_direction
        found['ball_momentum'][row, :2] = _steps(momentum[:2] * _players.DIRECTION_SCALE)
        found['ball_momentum'][row, 2] = _steps(momentum[2] * _players.HEIGHT_SCALE)
    return found


def action_rows(
    steps: Sequence[_grf.Step], agents: int, action_set: str | None
) -> list[list[int]] | None:
    """Returns the actions of the agents, a row for each step and one more, or `None`.

    Row f holds the action that led to step f, in the numbering of the action set, and the last
    row holds none, as `ACTS` keeps them. An action that the set lacks, or a step that tells none,
    holds 255.
    """
    if not agents or action_set is None:
        return None
    names = _grf.ACTION_SETS[action_set]
    rows = []
    for step in steps:
        row = [_actions.NONE] * agents
        for agent, name in enumerate((step.actions or [])[:agents]):
            if name in names:
                row[agent] = names.index(name)
        rows.append(row)
    rows.append([_actions.NONE] * agents)
    if all(value == _actions.NONE for row in rows for value in row):
        return None
    return rows


def frames(
    steps: Sequence[_grf.Step],
    agents: tuple[int, int],
    action_set: str | None,
    ended: bool,
) -> _recording.Frames:
    """Returns the track frames of the steps with their lanes and actions, ready to write.

    Args:
        steps: The steps, in order.
        agents: The agents of the left and the right team.
        action_set: The name of the action set of the agents' actions, or `None`.
        ended: Whether the episode ended at the last step, which marks its frame as the end.
    """
    tracks = [track_frame(step) for step in steps]
    if ended and tracks:
        last = bytearray(tracks[-1])
        struct.pack_into('<i', last, 28, 1)
        tracks[-1] = bytes(last)
    return _recording.Frames(
        tracks,
        players=lanes(steps),
        actions=action_rows(steps, sum(agents), action_set),
    )


def header(
    title: Any,
    scenario: str,
    steps: Sequence[_grf.Step],
    teams: list[dict[str, Any]] | None,
    provenance: dict[str, Any],
    agents: tuple[int, int],
    action_set: str | None,
) -> bytes:
    """Returns the header of a recording of positions.

    The header leaves out the scorer of the engine, which no observation tells, so the page and
    the moments find the scorer from the positions, and it holds the duration of the episode that
    the first step's steps left give.
    """
    first = steps[0]
    head = _recording.header(
        title,
        scenario,
        lineup(first),
        teams,
        len(steps),
        {'duration': first.step + first.steps_left},
        provenance,
        agents if sum(agents) else None,
        action_set if sum(agents) else None,
    )
    document = _recording._document(head)
    document.pop('engine_scorer', None)
    return _recording._encode_header(document)
