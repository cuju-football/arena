# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The moments of a recording as an analysis reads them, each with its outcome, receiver and the
positions of the ball and its player."""

from __future__ import annotations

from typing import Any

import numpy as np

from .._format import frames as _frames
from .._store import library as _library
from . import search as _search


def _outcome(kind: str, data: dict[str, Any]) -> Any:
    """Returns the outcome of a moment as one value, or `None` for a kind without one."""
    if kind == 'goal':
        return 'goal'
    if kind in ('shot', 'pass'):
        return data.get('outcome')
    if kind == 'save':
        saved = data.get('saved')
        return saved if isinstance(saved, str) else ('saved' if saved else 'conceded')
    if kind == 'tackle':
        won = data.get('won')
        return won if isinstance(won, str) else ('won' if won else 'lost')
    if kind == 'card':
        return data.get('colour')
    if kind == 'foul':
        return data.get('type')
    if kind == 'restart':
        return data.get('restart')
    if kind == 'woodwork':
        return 'woodwork'
    if kind == 'nearMiss':
        return 'miss'
    return None


def _reception(found: _frames.Frames, start: int, player: int) -> int | None:
    """Returns the first frame after `start` in its episode where `player` has the ball."""
    for frame in range(start + 1, min(len(found), start + 10 * found.frame_rate + 1)):
        started = found.frame_flags[frame] & _frames.EPISODE_START
        if started or found.step[frame] <= found.step[frame - 1]:
            return None
        if found.touches[frame, player] or found.owner[frame, 1] == player:
            return frame
    return None


def _point(values: np.ndarray) -> list[float]:
    return [round(float(value), 3) for value in values]


def _period(found: _frames.Frames, frame: int) -> int:
    half = found.header.get('second_half')
    return 2 if isinstance(half, int) and found.step[frame] >= half else 1


def moments(recording: Any) -> list[dict[str, Any]]:
    """Returns the moments of a recording, each with its outcome, receiver and positions.

    A recording of the library gives the moments of the index, those that a search finds, and a
    file gives the same moments found from its frames. Each moment holds the `id`, `kind`, `t`,
    `team`, `player` and `data` of the index, its `frame` in the recording and its `step` in the
    episode, the `clock` in milliseconds, the `period`, the `score`, the `outcome`, the `receiver`
    of a pass, the `ball` and the `position` of its player in meters in the frame of the pitch,
    and for a pass that reached a player the `end_frame` and the `end` of the ball there.

    Args:
        recording: An identifier of the library, a path of a recording file, or an object with
            `path` or `id`.

    Raises:
        MessageError: If it names no recording.
        StateError: If the file is no recording.
    """
    name, path = _frames.locate(recording)
    return of_frames(_frames.read(path), name)


def of_frames(found: _frames.Frames, name: str) -> list[dict[str, Any]]:
    """Returns the moments of the frames of a recording, as `moments` describes them."""
    if _library.path_of(found.path.stem) == found.path:
        listed = _search.entry(found.path.stem)['moments']
    else:
        listed = _search.file_catalog(found.path, found.header, found.rooted(), rooted=True)
    out = []
    players = len(found.lineup)
    for moment in listed:
        frame = int(moment['step'])
        if not 0 <= frame < len(found):
            continue
        data = dict(moment.get('data') or {})
        player = moment.get('player')
        receiver = data.get('receiver') if moment['kind'] == 'pass' else None
        receiver = receiver if isinstance(receiver, int) and 0 <= receiver < players else None
        item = {
            'recording': name,
            'id': moment['id'],
            'kind': moment['kind'],
            'frame': frame,
            'step': int(found.step[frame]),
            't': moment['t'],
            'clock': int(found.clock[frame]),
            'period': _period(found, frame),
            'score': found.score[frame].tolist(),
            'team': moment.get('team'),
            'player': player,
            'outcome': _outcome(moment['kind'], data),
            'receiver': receiver,
            'ball': _point(found.ball[frame]),
            'position': (
                _point(found.positions[frame, player])
                if isinstance(player, int) and 0 <= player < players
                else None
            ),
            'end_frame': None,
            'end': None,
            'data': data,
        }
        if receiver is not None and item['outcome'] == 'complete':
            end = _reception(found, frame, receiver)
            if end is not None:
                item['end_frame'], item['end'] = end, _point(found.ball[end])
        out.append(item)
    return out
