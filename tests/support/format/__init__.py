# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Small recordings with every section, and what they decode to, for both readers.

The folder holds recordings of a match of five a side that `generate.py` wrote from a batch, two
of version 2, the second with GRF's reverse team processing, which lists the right team first,
and one of version 3 with the tracks of the first. `expected.json` holds what each file decodes
to, written from the values that went into the files, so a reader that decodes a file to other
values fails. The Python tests read the files through `decoded`, and the tests of the page read
the same files and the same `expected.json`. `constants.json` holds the names, the scales and the
limits of the sections, which a test holds equal to those of the Python modules, for the reader
of the page to be held to the same file.
"""

import hashlib
import struct
from pathlib import Path

import numpy as np

from cuju.arena._format import attachments as _attachments
from cuju.arena._format import players as _players
from cuju.arena._format import recording as _recording
from cuju.arena._format import tracks as _tracks

FOLDER = Path(__file__).parent
JOINT_FILES = ('sections-5v5.cjr', 'sections-5v5-reverse.cjr')
TRACK_FILE = 'tracks-5v5.cjr'
# The frames whose values `expected.json` lists in full, around the keyframe at 100.
_HEAD = struct.Struct('<8i7f4i')
_HEADER_FIELDS = (
    'version',
    'frame_count',
    'frame_bytes',
    'keyframe_interval',
    'fidelity',
    'sections',
    'player_fields',
    'agents',
    'action_set',
    'run_key',
    'players',
)


def _digest(data):
    return hashlib.sha256(bytes(data)).hexdigest()


def sampled(count):
    """Returns the frames that the expected values list in full, for a file of `count` frames."""
    return sorted({index for index in (0, 1, 2, 50, 99, 100, 101, count - 1) if index < count})


def described(document, tracks, lanes, actions, state, overlays, notes, frames=None):
    """Returns what a recording holds as plain values of JSON, whole numbers and exact floats.

    Args:
        document: The header.
        tracks: The track frames.
        lanes: The lanes of the players by field, in their stored whole numbers.
        actions: The actions `[frames + 1, agents]`.
        state: The state of the kick off.
        overlays: The overlays, as `_overlays.decode` returns them.
        notes: The records of the annotations.
        frames: The frames, for a recording with joints.
    """
    players = len(document['players'])
    count = len(tracks)
    picked = sampled(count)
    found = {
        'header': {name: document[name] for name in _HEADER_FIELDS if name in document},
        'frames': {
            'count': count,
            'sampled': picked,
            'heads': [list(_HEAD.unpack_from(tracks[index])) for index in picked],
            'roots': [
                np.frombuffer(tracks[index], '<i2', 3 * players, _HEAD.size).tolist()
                for index in picked
            ],
            'closing': [list(tracks[index][-3 * (-(-players // 4) * 4) :]) for index in picked],
            'tracks_sha256': _digest(b''.join(tracks)),
        },
        'players': {
            name: {
                'sampled': [np.asarray(lanes[name][index]).tolist() for index in picked],
                # The bytes of the whole field, little endian, frame by frame.
                'sha256': _digest(np.ascontiguousarray(lanes[name]).tobytes()),
            }
            for name in _players.FIELDS
        },
        'actions': np.asarray(actions).tolist(),
        'state': {'bytes': len(state), 'sha256': _digest(state)},
        'overlays': [
            {
                **{key: value for key, value in overlay.items() if key != 'values'},
                'shape': list(overlay['values'].shape),
                'values': [float(value) for value in overlay['values'].reshape(-1)],
            }
            for overlay in overlays
        ],
        'annotations': notes,
    }
    if frames is not None:
        found['frames']['sha256'] = _digest(b''.join(frames))
    return found


def decoded(path):
    """Returns what the recording at `path` decodes to, as `described` lists it."""
    document = _recording.read_header(path)
    frames = list(_recording.frames(path)) if document['version'] != 3 else None
    return described(
        document,
        list(_recording.tracks(path)),
        _recording.players(path),
        _recording.actions(path),
        _recording.state(path),
        _attachments.overlays(path),
        _attachments.annotations(path),
        frames,
    )


def track_frames(frames, players):
    """Returns the track frames of frames."""
    return [_tracks.of_frame(frame, players) for frame in frames]
