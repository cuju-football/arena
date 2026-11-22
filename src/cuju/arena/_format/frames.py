# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The frames of a recording as arrays, which an analysis reads without the page.

`read` opens a recording of any version once and gives a `Frames`, numpy arrays of every field of
the frames with one row for each frame, beside the sections that the file keeps and the
annotations and overlays of its attachments. The positions of
the players and the ball come in meters in the frame of the pitch, the center spot at the origin,
the left team defending negative x and the z axis pointing up, as the frames store them. The
joints of every player decode only when they are asked for, since they take most of the time and
memory of a read, and a recording of version 3 holds none. The layout of the file is in the
repository's `docs/recording-format.md`.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import numpy as np

from cuju.errors import StateError

from . import actions as _actions
from . import attachments as _attachments
from . import codec as _codec
from . import players as _players
from . import recording as _recording
from . import tracks as _tracks

# The head of a frame, as the format lists its words.
HEAD = np.dtype(
    [
        ('step', '<i4'),
        ('game_mode', '<i4'),
        ('score', '<i4', (2,)),
        ('owner_team', '<i4'),
        ('owner', '<i4'),
        ('clock', '<i4'),
        ('flags', '<i4'),
        ('ball', '<f4', (3,)),
        ('ball_rotation', '<f4', (4,)),
        ('referee', '<i4', (4,)),
    ]
)
assert HEAD.itemsize == _codec.HEAD_BYTES
# A position is stored in steps of 2 mm and a rotation in steps of 1 / 32767.
_METERS = np.float32(1 / _recording.POSITION_SCALE)
_ROTATION = np.float32(1 / _recording.ROTATION_SCALE)
# The bits of a frame that mark the end and the start of an episode.
EPISODE_END, EPISODE_START = 1, 2


class Frames:
    """The frames of one recording as numpy arrays, one row for each frame.

    Attributes:
        path: The file that the frames come from.
        header: The header of the recording, as the file holds it.
        version: The version of the file, 1, 2 or 3.
        frame_rate: The frames of a second of play, the header's steps per second, 10.
        lineup: The players of the header in their order, each with its `team`, `index`,
            `goalkeeper` and `controlled`. The axis of the players of every array follows it.
        step: `[T]` `int32`, the step of the episode, 0 at its reset.
        clock: `[T]` `int32`, the match time in milliseconds, 90 minutes over 3000 steps.
        game_mode: `[T]` `int32`, GRF's game mode, 0 in normal play and 1 to 6 for the restarts.
        score: `[T, 2]` `int32`, the goals of the left team and of the right team.
        ball: `[T, 3]` `float32`, the position of the engine's ball in meters.
        ball_rotation: `[T, 4]` `float32`, the rotation of the ball as a quaternion x, y, z, w.
        owner: `[T, 2]` `int32`, the team that owns the ball and the owner's index in the lineup,
            each -1 for none.
        frame_flags: `[T]` `int32`, the bits of the frame, 1 at the end of an episode, 2 at its
            start, 4 for a called offside and the scorer in bits 8 to 12.
        referee: `[T, 4]` `int32`, the foul offender, the victim, the kind of the foul and the
            offside player, as the frame holds them.
        positions: `[T, P, 3]` `float32`, the position of the root joint of each player in meters.
        flags: `[T, P]` `uint8`, 1 for a player on the pitch, 2 for a yellow card, 4 for a red
            card and 8 for a player that an agent controls on the step.
        animations: `[T, P]` `uint8`, the kind of animation that each player runs, GRF's
            `e_FunctionType`.
        touches: `[T, P]` `uint8`, 0 for no touch of the ball on the step, else 1 plus GRF's kind
            of the touch.
        players: The lanes of `PLYR` by field in the units of GRF's raw observation, as
            `players.scaled` gives them, or `None` for a file without.
        actions: `[T + 1, agents]` `uint8`, the actions of `ACTS`, or `None` for a file without.
        annotations: The records of the attachment `annotations`, or of the section `ANNO` of an
            older file, as `annotations.decode` gives them.
        fidelity: `joints` for frames with every joint, `positions` for a recording of version 3.
        provenance: The provenance of the header, empty when the file has none.
    """

    def __init__(
        self,
        path: Path,
        header: dict[str, Any],
        heads: np.ndarray,
        roots: np.ndarray,
        tails: np.ndarray,
        sections: dict[bytes, bytes],
        joints: Any,
    ) -> None:
        self.path = path
        self.header = header
        self.version = header.get('version', 1)
        self.frame_rate = header.get('steps_per_second') or 10
        self.lineup = [dict(player) for player in header['players']]
        count, players = len(heads), len(self.lineup)
        head = heads.view(HEAD).reshape(count)
        self.step = head['step'].copy()
        self.clock = head['clock'].copy()
        self.game_mode = head['game_mode'].copy()
        self.score = head['score'].copy()
        self.ball = head['ball'].copy()
        self.ball_rotation = head['ball_rotation'].copy()
        self.owner = np.stack((head['owner_team'], head['owner']), axis=1)
        self.frame_flags = head['flags'].copy()
        self.referee = head['referee'].copy()
        self.positions = roots.astype(np.float32) * _METERS
        padded = _codec.padded(players)
        runs = tails.reshape(count, 3, padded)[:, :, :players]
        self.flags = runs[:, 0].copy()
        self.animations = runs[:, 1].copy()
        self.touches = runs[:, 2].copy()
        self._roots = roots
        self._heads = heads
        self._tails = tails
        self._sections = sections
        self._joints = joints
        self.fidelity = header.get('fidelity') or (
            _recording.POSITIONS if self.version == 3 else _recording.JOINTS
        )
        self.provenance = dict(header.get('provenance') or {})
        self.players = _player_lanes(header, sections, count)
        self.actions = _agent_actions(header, sections, count)
        self.annotations = _attachments.annotations(path, inline=sections)

    def __len__(self) -> int:
        """Returns the number of frames."""
        return len(self.step)

    def __repr__(self) -> str:
        return (
            f'Frames({str(self.path)!r}, frames={len(self)}, players={len(self.lineup)}, '
            f'version={self.version}, fidelity={self.fidelity!r})'
        )

    @functools.cached_property
    def joints(self) -> np.ndarray | None:
        """`[T, P, 14, 7]` `float32`, the position in meters and the rotation of every joint.

        The joints come in the order of `recording.NODES`, each as x, y and z of its world
        position and x, y, z and w of its rotation. A recording of version 3 holds none, and
        gives `None`. The first read decodes every frame.
        """
        if self._joints is None:
            return None
        raw = self._joints()
        count, players = len(self), len(self.lineup)
        nodes = raw[:, _codec.HEAD_BYTES : _codec.HEAD_BYTES + players * _codec.JOINTS * 14]
        values = np.ascontiguousarray(nodes).view('<i2').reshape(count, players, _codec.JOINTS, 7)
        found = values.astype(np.float32)
        found[..., :3] *= _METERS
        found[..., 3:] *= _ROTATION
        return found

    @functools.cached_property
    def overlays(self) -> list[dict[str, Any]]:
        """The overlays of the attachment `overlays`, or of the section `GRID` of an older file.

        They come as `overlays.decode` gives them, decoded at the first read.
        """
        return _attachments.overlays(self.path, inline=self._sections)

    def rooted(self) -> list[bytes]:
        """Returns the frames as version 1 stores them, with the root joint of each player alone.

        The other joints are zero. The rules of the moments read the root joints, the head and
        the closing bytes of a frame alone, so these frames give them the moments of any version,
        a recording of positions among them, without decoding the joints.
        """
        count, players = len(self), len(self.lineup)
        out = np.zeros((count, _codec.frame_bytes(players)), np.uint8)
        out[:, : _codec.HEAD_BYTES] = self._heads
        nodes = np.zeros((count, players, _codec.JOINTS, 7), '<i2')
        nodes[:, :, 0, :3] = self._roots
        end = _codec.HEAD_BYTES + players * _codec.JOINTS * 14
        out[:, _codec.HEAD_BYTES : end] = nodes.reshape(count, -1).view(np.uint8)
        out[:, end:] = self._tails
        return [row.tobytes() for row in out]


def _player_lanes(
    header: dict[str, Any], sections: dict[bytes, bytes], count: int
) -> dict[str, np.ndarray] | None:
    """Returns the lanes of `PLYR` in GRF's units, or `None` for a file without."""
    payload = sections.get(_players.TAG)
    fields = header.get('player_fields')
    if payload is None or not fields or not _players.known(fields):
        return None
    lanes = _players.decode(payload, len(header['players']), count, fields)
    return _players.scaled(lanes)


def _agent_actions(
    header: dict[str, Any], sections: dict[bytes, bytes], count: int
) -> np.ndarray | None:
    """Returns the actions of `ACTS`, or `None` for a file without."""
    payload = sections.get(_actions.TAG)
    agents = header.get('agents')
    if payload is None:
        return None
    if not agents:
        raise StateError('expected the agents of the header beside the actions of a recording')
    return _actions.decode(payload, count, sum(agents))


def _split(rows: np.ndarray, players: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns the heads, root positions and closing bytes of track frames `[T, bytes]`."""
    count = len(rows)
    heads = np.ascontiguousarray(rows[:, : _codec.HEAD_BYTES])
    end = _codec.HEAD_BYTES + 6 * players
    roots = np.ascontiguousarray(rows[:, _codec.HEAD_BYTES : end]).view('<i2')
    tails = np.ascontiguousarray(
        rows[:, _tracks.frame_bytes(players) - 3 * _codec.padded(players) :]
    )
    return heads, roots.reshape(count, players, 3), tails


def _coded_parts(
    data: bytes, players: int, count: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns the heads, root positions and closing bytes of a frames section of version 2.

    These are the first two runs of the section and its last, so they decode without the joints,
    to the same values as the frames that the whole section decodes to.
    """
    buffer = np.frombuffer(data, np.uint8, _codec.section_bytes(players, count))
    interval = _codec.KEYFRAME_INTERVAL
    head = _codec.unpack(buffer, 0, _codec.HEAD_WORDS, count, 4, _codec.DIFFERENCE, interval)
    at = _codec.run_bytes(_codec.HEAD_WORDS, count, 4)
    roots = _codec.unpack(buffer, at, 3 * players, count, 2, _codec.DIFFERENCE_TWICE, interval)
    padded = 3 * _codec.padded(players)
    at = len(buffer) - _codec.run_bytes(padded, count, 1)
    tails = _codec.unpack(buffer, at, padded, count, 1, _codec.EXCLUSIVE_OR, interval)
    heads = np.ascontiguousarray(head.T).astype('<i4').view(np.uint8)
    roots = np.ascontiguousarray(roots.reshape(3, players, count).transpose(2, 1, 0)).astype('<i2')
    return heads, roots, np.ascontiguousarray(tails.T).astype(np.uint8)


def read(path: str | Path) -> Frames:
    """Returns the frames of a recording file of version 1, 2 or 3 as arrays.

    The file is read once. The joints decode at the first read of `Frames.joints`, and the
    overlays at the first read of `Frames.overlays`.

    Raises:
        StateError: If the file is not a recording, reaches past the limits of one, or holds
            sections that do not fit its header.
    """
    path = Path(path)
    header, sections = _recording._read(path)
    players = len(header['players'])
    version = header.get('version', 1)
    if version == 1:
        size = header['frame_bytes']
        rows = np.frombuffer(b''.join(_recording.frames(path)), np.uint8).reshape(-1, size)
        end = _codec.HEAD_BYTES + players * _codec.JOINTS * 14
        nodes = np.ascontiguousarray(rows[:, _codec.HEAD_BYTES : end]).view('<i2')
        roots = nodes.reshape(len(rows), players, _codec.JOINTS, 7)[:, :, 0, :3].copy()
        heads = np.ascontiguousarray(rows[:, : _codec.HEAD_BYTES])
        tails = np.ascontiguousarray(rows[:, end:])
        return Frames(path, header, heads, roots, tails, sections, lambda: rows)
    count = header['frame_count']
    if version == 3:
        data, players = _recording._coded_tracks(header, sections)
        heads, roots, tails = _split(_tracks.decode(data, players, count), players)
        return Frames(path, header, heads, roots, tails, sections, None)
    data, players = _recording._coded_frames(header, sections)
    heads, roots, tails = _coded_parts(data, players, count)

    def joints() -> np.ndarray:
        return _codec.decode(data, players, count)

    return Frames(path, header, heads, roots, tails, sections, joints)


def locate(recording: Any) -> tuple[str, Path]:
    """Returns the name and path of a recording given by identifier, path or an object with one.

    Raises:
        MessageError: If it names no recording of the library and no recording file.
    """
    from .._store import library as _library
    from .._text.messages import MessageError

    for attribute in ('path', 'id'):
        inner = getattr(recording, attribute, None)
        if isinstance(inner, str | Path) and not isinstance(recording, str | Path):
            recording = inner
            break
    if isinstance(recording, str):
        found = _library.path_of(recording)
        if found is not None:
            return recording, found
    if isinstance(recording, str | Path) and Path(recording).is_file():
        return Path(recording).stem, Path(recording)
    raise MessageError('server.export.recording', recording=repr(str(recording)))


def frames(recording: Any) -> Frames:
    """Returns the frames of a recording as numpy arrays, a `Frames` with one row for each frame.

    Args:
        recording: An identifier of the library, a path of a recording file, or an object with
            `path` or `id`.

    Raises:
        MessageError: If it names no recording.
        StateError: If the file is no recording.
    """
    return read(locate(recording)[1])
