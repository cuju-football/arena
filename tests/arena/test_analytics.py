# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the analytics export, `frames`, `moments`, `export`, its command and its route."""

import gzip
import json
import struct
from pathlib import Path

import numpy as np
import pytest

from cuju.arena._format import attachments as _attachments
from cuju.arena._format import codec as _codec
from cuju.arena._format import frames as _frames
from cuju.arena._format import recording as _recording
from cuju.arena._format import tracks as _tracks
from cuju.arena._moments import events as _events
from cuju.arena._moments import listing as _listing
from cuju.arena._store import library as _library
from support import format as _fixtures

SAMPLES = Path(_recording.__file__).parents[1] / 'samples'


def _version_1(source, target):
    """Writes the frames of a recording of version 2 as a recording of version 1."""
    document = _recording.read_header(source)
    for name in ('frame_count', 'keyframe_interval', 'sections', 'fidelity', 'player_fields'):
        document.pop(name, None)
    document['version'] = 1
    frames = list(_recording.frames(source))
    with gzip.open(target, 'wb') as stream:
        stream.write(_recording._encode_header(document) + b''.join(frames))
    return frames


def test_frames_read_versions_1_2_and_3_to_the_same_values(tmp_path):
    joints = _fixtures.FOLDER / 'sections-5v5.cjr'
    frames = _version_1(joints, tmp_path / 'one.cjr')
    one = _frames.read(tmp_path / 'one.cjr')
    two = _frames.read(joints)
    three = _frames.read(_fixtures.FOLDER / _fixtures.TRACK_FILE)
    assert (one.version, two.version, three.version) == (1, 2, 3)
    assert (one.fidelity, two.fidelity, three.fidelity) == ('joints', 'joints', 'positions')
    players = len(two.lineup)
    for found in (one, three):
        for name in ('step', 'clock', 'game_mode', 'score', 'ball', 'ball_rotation', 'owner'):
            assert np.array_equal(getattr(found, name), getattr(two, name)), name
        for name in ('frame_flags', 'referee', 'positions', 'flags', 'animations', 'touches'):
            assert np.array_equal(getattr(found, name), getattr(two, name)), name
    # The joints of both versions are the frames, in meters and parts of a turn.
    stored = np.stack(
        [
            np.frombuffer(frame, '<i2', players * 98, _codec.HEAD_BYTES).reshape(players, 14, 7)
            for frame in frames
        ]
    )
    for found in (one, two):
        assert found.joints.shape == (len(frames), players, 14, 7)
        assert np.allclose(found.joints[..., :3] * _recording.POSITION_SCALE, stored[..., :3])
        assert np.allclose(found.joints[..., 3:] * _recording.ROTATION_SCALE, stored[..., 3:])
        assert np.array_equal(found.joints[:, :, 0, :3], found.positions)
    assert three.joints is None
    # Version 1 keeps no sections, and versions 2 and 3 give theirs as the reader decodes them.
    assert one.players is None and one.actions is None and one.annotations == []
    for found, name in ((two, 'sections-5v5.cjr'), (three, _fixtures.TRACK_FILE)):
        path = _fixtures.FOLDER / name
        lanes = _recording.players(path)
        assert set(found.players) == set(lanes)
        assert np.allclose(found.players['tired'], lanes['tired'] / 32767)
        assert np.array_equal(found.actions, _recording.actions(path))
        assert found.annotations == _attachments.annotations(path)
        assert len(found.overlays) == len(_attachments.overlays(path))
        assert found.provenance['engine_seed'] == 1126786459


def test_frames_hold_the_values_of_the_fixtures():
    expected = json.loads((_fixtures.FOLDER / 'expected.json').read_text(encoding='utf-8'))
    for name, values in expected.items():
        found = _frames.read(_fixtures.FOLDER / name)
        heads = values['frames']['heads']
        for row, index in enumerate(values['frames']['sampled']):
            head = heads[row]
            assert found.step[index] == head[0] and found.game_mode[index] == head[1]
            assert found.score[index].tolist() == head[2:4]
            assert found.owner[index].tolist() == head[4:6]
            assert found.clock[index] == head[6] and found.frame_flags[index] == head[7]
            assert found.ball[index].tolist() == pytest.approx(head[8:11])
            assert found.referee[index].tolist() == head[15:19]
            roots = np.array(values['frames']['roots'][row]).reshape(-1, 3)
            assert np.allclose(found.positions[index] * 500, roots)
        assert found.actions.tolist() == values['actions']
        assert len(found) == values['frames']['count']


@pytest.mark.parametrize('name', ['sample-5v5', 'sample-11v11'])
def test_frames_of_the_samples_equal_their_tracks_and_moments(name):
    path = SAMPLES / f'{name}.cjr'
    found = _frames.read(path)
    players = len(found.lineup)
    tracks = list(_recording.tracks(path))
    assert len(found) == len(tracks)
    for index in (0, 1, 1500, len(tracks) - 1):
        track = tracks[index]
        assert found.step[index] == struct.unpack_from('<i', track)[0]
        roots = np.frombuffer(track, '<i2', 3 * players, _codec.HEAD_BYTES).reshape(players, 3)
        assert np.allclose(found.positions[index] * 500, roots)
        closing = np.frombuffer(
            track, np.uint8, offset=_tracks.frame_bytes(players) - 3 * _codec.padded(players)
        )
        assert found.flags[index].tolist() == closing[:players].tolist()
    # The frames with the root joints alone give the moments of the whole frames.
    header = _recording.read_header(path)
    assert _events.moments(header, found.rooted(), timing=True) == _events.moments(
        header, _recording.frames(path), timing=True
    )


def test_moments_carry_outcome_receiver_and_positions(arena_home):
    path = _fixtures.FOLDER / 'sections-5v5.cjr'
    listed = _listing.moments(path)
    found = _frames.read(path)
    passes = [moment for moment in listed if moment['kind'] == 'pass']
    assert passes and all(moment['outcome'] for moment in passes)
    complete = [moment for moment in passes if moment['outcome'] == 'complete']
    assert complete and all(moment['receiver'] is not None for moment in complete)
    for moment in listed:
        frame = moment['frame']
        assert moment['step'] == found.step[frame] and moment['clock'] == found.clock[frame]
        assert moment['ball'] == pytest.approx(found.ball[frame].tolist(), abs=1e-3)
        if moment['player'] is not None:
            assert moment['position'] == pytest.approx(
                found.positions[frame, moment['player']].tolist(), abs=1e-3
            )
        if moment['end_frame'] is not None:
            assert moment['end_frame'] > frame
            receiver = moment['receiver']
            end = moment['end_frame']
            assert found.touches[end, receiver] or found.owner[end, 1] == receiver
    # A recording of the library gives the moments of its index, the same as its file.
    added = _library.add_samples(['sample-5v5'])[0]
    by_index = _listing.moments(added.stem)
    by_file = _listing.moments(SAMPLES / 'sample-5v5.cjr')
    for moment in by_file:
        moment['recording'] = added.stem
    assert by_index == by_file
