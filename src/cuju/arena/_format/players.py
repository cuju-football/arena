# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The lanes of the players, which a recording keeps in the section `PLYR` beside its frames.

A frame holds what a viewer draws. The lanes hold what an inspector shows of each player on each
step, the direction of its movement, its tiredness, its sticky actions, the agent that controls
it and its role, and for the whole step the designated player of each team and the momentum of
the ball. They are GRF's raw observation of a left agent, quantized, and read from the state that
gives the frame. The header names the fields of a file in `player_fields`, in the order of the
section, and a reader takes them up to the first name that it does not know. The layout is in the
repository's `docs/recording-format.md`, under Player lanes.

A row is the lanes of one step as bytes, as `StateReader.lanes` returns it, and `stack` joins the
rows of an episode into one array for each field.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from cuju.errors import StateError

from . import codec as _codec

TAG = b'PLYR'
# The fields, in the order of the section.
FIELDS = ('direction', 'tired', 'sticky', 'agent', 'role', 'designated', 'ball_momentum')
# GRF: a direction of the raw observation is a movement over one step, x divided by 54.4 and y by
# -83.6. A lane stores it times this scale, rounded to a whole number, so in steps of 0.00001, and
# the height of the ball's momentum, which the raw observation leaves in meters, in millimeters.
DIRECTION_SCALE = 100_000
HEIGHT_SCALE = 1000
# A lane stores tiredness, from 0 to 1, times this.
TIRED_SCALE = 32767
# GRF: the sticky actions of the action set `full`, whose first ten are those of the other sets.
# Bit `k` of a sticky lane is set while the action `k` of this list holds.
STICKY = (
    'left',
    'top_left',
    'top',
    'top_right',
    'right',
    'bottom_right',
    'bottom',
    'bottom_left',
    'sprint',
    'dribble',
    'keeper_rush',
    'pressure',
    'team_pressure',
)
# The lanes of each field for each player and for the whole step, the bytes of a value and the
# coding of its run.
_RUNS = {
    'direction': (2, 0, 2, _codec.DIFFERENCE),
    'tired': (1, 0, 2, _codec.DIFFERENCE),
    'sticky': (2, 0, 1, _codec.EXCLUSIVE_OR),
    'agent': (1, 0, 1, _codec.EXCLUSIVE_OR),
    'role': (1, 0, 1, _codec.EXCLUSIVE_OR),
    'designated': (0, 2, 1, _codec.EXCLUSIVE_OR),
    'ball_momentum': (0, 3, 2, _codec.DIFFERENCE),
}


def row_type(players: int) -> np.dtype:
    """Returns the type of the lanes of one step of `players` players, packed and little endian."""
    return np.dtype(
        [
            ('direction', '<i2', (players, 2)),
            ('tired', '<i2', (players,)),
            ('sticky', '<u2', (players,)),
            ('agent', 'u1', (players,)),
            ('role', 'u1', (players,)),
            ('designated', 'u1', (2,)),
            ('ball_momentum', '<i2', (3,)),
        ]
    )


def stack(rows: Sequence[bytes], players: int) -> dict[str, np.ndarray]:
    """Returns the lanes of an episode by field, each `[frames, ...]`, from the rows of its steps.

    Args:
        rows: The lanes of each step, as `StateReader.lanes` returns them.
        players: The number of players.
    """
    kind = row_type(players)
    records = np.frombuffer(b''.join(rows), kind, len(rows))
    return {name: np.ascontiguousarray(records[name]) for name in FIELDS}


def _lanes(name: str, values: np.ndarray) -> np.ndarray:
    """Returns the values of a field `[frames, ...]` as its lanes `[lanes, frames]`."""
    values = np.asarray(values)
    if name == 'direction':
        # Component by component, then player by player, as the root of a player in the frames.
        return values.transpose(2, 1, 0).reshape(-1, values.shape[0])
    if name == 'sticky':
        # The low bytes of every player, the eight directions, then the high bytes.
        wide = values.astype(np.uint16).T
        return np.concatenate((wide & 0xFF, wide >> 8))
    return values.T


def fields_of(lanes: Mapping[str, Any]) -> list[str]:
    """Returns the names of the fields that `lanes` holds, in the order of the section."""
    return [name for name in FIELDS if name in lanes]


def encode(
    lanes: Mapping[str, Any] | Sequence[bytes],
    players: int,
    interval: int = _codec.KEYFRAME_INTERVAL,
) -> bytes:
    """Returns the section of the lanes of an episode, the runs of its fields one after another.

    Args:
        lanes: The lanes by field, each `[frames, ...]` as `stack` returns them, or the rows of
            the steps. A mapping may leave fields out, and the section then holds the others.
        players: The number of players.
        interval: The frames from one keyframe to the next.

    Raises:
        StateError: If a field has another shape than its frames and players give.
    """
    if not isinstance(lanes, Mapping):
        lanes = stack(lanes, players)
    names = fields_of(lanes)
    count = len(lanes[names[0]]) if names else 0
    parts = []
    for name in names:
        each, whole, width, coding = _RUNS[name]
        values = _lanes(name, lanes[name])
        if values.shape != (each * players + whole, count):
            raise StateError(
                f'expected the lanes of {name} for {count} frames of {players} players, found '
                f'the shape {np.asarray(lanes[name]).shape}'
            )
        parts.append(_codec.pack(values, width, coding, interval))
    return b''.join(parts)


def section_bytes(players: int, count: int, fields: Sequence[str] = FIELDS) -> int:
    """Returns the bytes of the section of `count` frames of `players` players with `fields`."""
    return sum(
        _codec.run_bytes(_RUNS[name][0] * players + _RUNS[name][1], count, _RUNS[name][2])
        for name in fields
    )


def known(fields: Sequence[Any]) -> list[str]:
    """Returns the fields of a header that this reader reads, those before the first unknown."""
    found: list[str] = []
    for name in fields:
        if name not in _RUNS or name in found:
            break
        found.append(name)
    return found


def decode(
    data: bytes | memoryview,
    players: int,
    count: int,
    fields: Sequence[str] = FIELDS,
    interval: int = _codec.KEYFRAME_INTERVAL,
    start: int = 0,
    stop: int | None = None,
) -> dict[str, np.ndarray]:
    """Returns frames `start` to `stop` of the lanes of a section by field, each `[frames, ...]`.

    Only the frames from the keyframe before `start` decode, as with the frames of a recording.
    The direction comes as `[frames, players, 2]` and the tiredness as `[frames, players]`, both
    `int16`, the sticky actions as `uint16` and the agent and the role as `uint8`, each
    `[frames, players]`, the designated players as `[frames, 2]` `uint8` and the momentum of the
    ball as `[frames, 3]` `int16`.

    Args:
        data: The section.
        players: The number of players.
        count: The number of frames of the section.
        fields: The fields of the section, as the header names them in `player_fields`.
        interval: The frames from one keyframe to the next.
        start: The first frame to return.
        stop: The frame after the last to return, by default the end.

    Raises:
        StateError: If the section is shorter than its fields, frames and players give.
    """
    names = known(fields)
    if len(data) < section_bytes(players, count, names):
        raise StateError('expected the lanes of the players of a recording, found them cut short')
    stop = count if stop is None else max(0, min(stop, count))
    start = max(0, min(start, stop))
    first = start // interval * interval
    buffer = np.frombuffer(data, np.uint8)
    found, at = {}, 0
    for name in names:
        each, whole, width, coding = _RUNS[name]
        lanes = each * players + whole
        values = _codec.unpack(buffer, at, lanes, count, width, coding, interval, first, stop)
        at += _codec.run_bytes(lanes, count, width)
        values = values[:, start - first :]
        if name == 'direction':
            found[name] = np.ascontiguousarray(
                values.reshape(2, players, -1).transpose(2, 1, 0)
            ).astype(np.int16)
        elif name == 'sticky':
            wide = values[:players] | (values[players:] << 8)
            found[name] = np.ascontiguousarray(wide.T).astype(np.uint16)
        else:
            kind = np.int16 if width == 2 else np.uint8
            found[name] = np.ascontiguousarray(values.T).astype(kind)
    return found


def scaled(lanes: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Returns lanes with their quantized fields in the units of GRF's raw observation.

    The direction of each player and the x and y of the momentum of the ball come in GRF's raw
    units for one step, the height of the momentum in meters for one step, and the tiredness from
    0 to 1. The other fields come as they are.
    """
    found = dict(lanes)
    if 'direction' in found:
        found['direction'] = found['direction'] / DIRECTION_SCALE
    if 'tired' in found:
        found['tired'] = found['tired'] / TIRED_SCALE
    if 'ball_momentum' in found:
        found['ball_momentum'] = found['ball_momentum'] / np.array(
            [DIRECTION_SCALE, DIRECTION_SCALE, HEIGHT_SCALE]
        )
    return found
