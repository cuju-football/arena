# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The tracks of a recording of positions, which version 3 keeps in the section `TRKS`.

A track frame is a frame without its joints. It holds the head of the frame, the position of the
root joint of each player and the three byte runs that close a frame, so it tells where every
player and the ball are, the score, the clock and the calls of the referee, at about a tenth of
the size. A recording of version 3 stores track frames in place of frames, and the tracks of a
recording of any version read the same way. The layout is in the repository's
`docs/recording-format.md`, under Version 3.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from . import codec as _codec

TAG = b'TRKS'
_HEAD = _codec.HEAD_BYTES
_JOINT_BYTES = 14


def _root_bytes(players: int) -> int:
    """Returns the bytes of the root positions of a track frame, padded to four bytes."""
    return -(-6 * players // 4) * 4


def frame_bytes(players: int) -> int:
    """Returns the bytes of one track frame of `players` players."""
    return _HEAD + _root_bytes(players) + 3 * _codec.padded(players)


def section_bytes(players: int, count: int) -> int:
    """Returns the bytes of the tracks section of `count` frames of `players` players."""
    return count * (_HEAD + 6 * players + 3 * _codec.padded(players))


def of_frame(frame: bytes, players: int) -> bytes:
    """Returns the track frame of a frame, its head, root positions and closing bytes.

    Args:
        frame: The frame, as version 1 stores it.
        players: The number of players.
    """
    nodes_end = _HEAD + players * _codec.JOINTS * _JOINT_BYTES
    joints = np.frombuffer(frame, '<i2', players * _codec.JOINTS * 7, _HEAD)
    roots = joints.reshape(players, _codec.JOINTS, 7)[:, 0, :3].tobytes()
    return frame[:_HEAD] + roots + bytes(_root_bytes(players) - len(roots)) + frame[nodes_end:]


def encode(
    frames: Sequence[bytes], players: int, interval: int = _codec.KEYFRAME_INTERVAL
) -> bytes:
    """Returns the tracks section of track frames, coded as the frames section codes its lanes.

    Args:
        frames: The track frames, each as `of_frame` returns it.
        players: The number of players.
        interval: The frames from one keyframe to the next.
    """
    count = len(frames)
    raw = np.frombuffer(b''.join(frames), np.uint8).reshape(count, frame_bytes(players))
    roots_end = _HEAD + 6 * players
    head = raw[:, :_HEAD].copy().view('<i4').T
    roots = raw[:, _HEAD:roots_end].copy().view('<i2').reshape(count, players, 3)
    tail = raw[:, _HEAD + _root_bytes(players) :].T
    data = b''.join(
        (
            _codec.pack(head, 4, _codec.DIFFERENCE, interval),
            # Component by component, then player by player, as the frames section has them.
            _codec.pack(
                roots.transpose(2, 1, 0).reshape(-1, count), 2, _codec.DIFFERENCE_TWICE, interval
            ),
            _codec.pack(tail, 1, _codec.EXCLUSIVE_OR, interval),
        )
    )
    assert len(data) == section_bytes(players, count)
    return data


def decode(
    data: bytes | memoryview,
    players: int,
    count: int,
    interval: int = _codec.KEYFRAME_INTERVAL,
    start: int = 0,
    stop: int | None = None,
) -> np.ndarray:
    """Returns frames `start` to `stop` of a tracks section, as `[frames, frame_bytes]` bytes.

    Only the frames from the keyframe before `start` decode.

    Args:
        data: The tracks section, at least `section_bytes(players, count)` bytes.
        players: The number of players.
        count: The number of frames of the section.
        interval: The frames from one keyframe to the next.
        start: The first frame to return.
        stop: The frame after the last to return, by default the end.
    """
    stop = count if stop is None else max(0, min(stop, count))
    start = max(0, min(start, stop))
    first = start // interval * interval
    span = stop - first
    out = np.zeros((span, frame_bytes(players)), np.uint8)
    if start == stop:
        return out
    buffer = np.frombuffer(data, np.uint8, section_bytes(players, count))
    padded = _codec.padded(players)
    head = _codec.unpack(buffer, 0, 19, count, 4, _codec.DIFFERENCE, interval, first, stop)
    at = _codec.run_bytes(19, count, 4)
    roots = _codec.unpack(
        buffer, at, 3 * players, count, 2, _codec.DIFFERENCE_TWICE, interval, first, stop
    )
    at += _codec.run_bytes(3 * players, count, 2)
    tail = _codec.unpack(
        buffer, at, 3 * padded, count, 1, _codec.EXCLUSIVE_OR, interval, first, stop
    )
    out[:, :_HEAD] = np.ascontiguousarray(head.T).astype('<i4').view(np.uint8)
    out[:, _HEAD : _HEAD + 6 * players] = (
        np.ascontiguousarray(roots.reshape(3, players, span).transpose(2, 1, 0))
        .astype('<i2')
        .reshape(span, -1)
        .view(np.uint8)
    )
    out[:, _HEAD + _root_bytes(players) :] = tail.T
    return out[start - first :]
