# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the sections that a recording keeps beside its frames, and of version 3."""

import gzip
import hashlib
import json
import os
import statistics
import struct
import time

import numpy as np
import pytest
import torch

import cuju
import cuju.errors
import cuju.raw
import cuju.state
from conftest import actions_at
from cuju.arena._format import actions as _actions
from cuju.arena._format import annotations as _annotations
from cuju.arena._format import attachments as _attachments
from cuju.arena._format import codec as _codec
from cuju.arena._format import overlays as _overlays
from cuju.arena._format import players as _players
from cuju.arena._format import recording as _recording
from cuju.arena._format import tracks as _tracks
from support import format as _fixtures
from support.common import _sample_head
from support.frozen import reader_010

# A timing test, which a busy machine fails, and a whole match run when this variable is set.
slow = pytest.mark.skipif(
    not os.environ.get('CUJU_RENDER_SLOW'), reason='a slow test, run with CUJU_RENDER_SLOW=1'
)


def _random_lanes(players, count, seed=3):
    """Returns lanes of noise, which reach every corner of their types."""
    generator = np.random.default_rng(seed)

    def noise(low, high, kind, *shape):
        return generator.integers(low, high, (count, *shape)).astype(kind)

    return {
        'direction': noise(-32768, 32768, np.int16, players, 2),
        'tired': noise(0, 32768, np.int16, players),
        'sticky': noise(0, 1 << 13, np.uint16, players),
        'agent': noise(0, 12, np.uint8, players),
        'role': noise(0, 10, np.uint8, players),
        'designated': noise(0, players + 1, np.uint8, 2),
        'ball_momentum': noise(-32768, 32768, np.int16, 3),
    }


def _rows(lanes, players):
    """Returns lanes by field as the rows of their steps, as a state reader returns them."""
    count = len(lanes['role'])
    records = np.zeros(count, _players.row_type(players))
    for name, values in lanes.items():
        records[name] = values
    return [records[index].tobytes() for index in range(count)]


def _same(first, second):
    assert list(first) == list(second)
    for name in first:
        assert first[name].dtype == second[name].dtype, name
        assert np.array_equal(first[name], second[name]), name


def _with(head, **fields):
    """Returns a header as `_recording.header` returns it, with more fields."""
    return _recording._encode_header({**_recording._document(head), **fields})


def _overlay_records():
    generator = np.random.default_rng(11)
    return [
        {
            'f': 3,
            'p': 2,
            'name': 'value',
            'kind': 'grid',
            'side': 1,
            'extent': [-1, -0.42, 1, 0.42],
            'values': generator.normal(size=(72, 96)),
        },
        {
            'f': 3,
            'p': 2,
            'name': 'reach',
            'kind': 'grid',
            'type': 'float16',
            'values': generator.random((16, 24)),
        },
        {'f': 3, 'p': 2, 'name': 'pass', 'kind': 'targets', 'values': generator.random(10)},
        {
            'f': 3,
            'p': 2,
            'name': 'policy',
            'kind': 'actions',
            'type': 'uint8',
            'values': generator.random(19),
        },
        {'f': 0, 'name': 'pressure', 'kind': 'grid', 'values': np.full((4, 6), 0.25)},
        {'f': 229, 'p': 9, 'name': 'pass', 'kind': 'targets', 'values': np.arange(10) / 8},
    ]


def _full_file(path, version=2, count=230):
    """Writes a recording of a sample with every section, and returns what went into it."""
    head, frames = _sample_head('sample-5v5', count)
    head = _with(head, agents=[2, 1], action_set='default')
    lanes = _random_lanes(10, count)
    generator = np.random.default_rng(5)
    actions = generator.integers(0, 19, (count + 1, 3)).astype(np.uint8)
    actions[0] = _actions.NONE
    state = generator.integers(0, 256, 5000, np.uint8).tobytes()
    notes = [{'f': 4, 'p': 1, 'v': {'value': 0.5}}, {'f': 9, 't': 'a note'}]
    carried = _recording.Frames(
        frames, players=lanes, actions=actions, state=state, overlays=_overlay_records()
    )
    _attachments.write_recording(path, head, carried, version, notes)
    return frames, lanes, actions, state, notes


def test_lane_runs_round_trip_in_every_coding():
    generator = np.random.default_rng(2)
    cases = [
        (1, _codec.EXCLUSIVE_OR, 0, 256),
        (2, _codec.DIFFERENCE, -32768, 32768),
        (2, _codec.DIFFERENCE_TWICE, -32768, 32768),
        (4, _codec.DIFFERENCE, -(2**31), 2**31),
    ]
    for width, coding, low, high in cases:
        values = generator.integers(low, high, (5, 230))
        data = np.frombuffer(b'head' + _codec.pack(values, width, coding), np.uint8)
        assert len(data) == 4 + _codec.run_bytes(5, 230, width)
        assert np.array_equal(_codec.unpack(data, 4, 5, 230, width, coding), values)
        part = _codec.unpack(data, 4, 5, 230, width, coding, first=100, stop=217)
        assert np.array_equal(part, values[:, 100:217])


def test_plyr_round_trip_and_seek(tmp_path):
    lanes = _random_lanes(10, 250)
    payload = _players.encode(lanes, 10)
    assert len(payload) == _players.section_bytes(10, 250) == 250 * (10 * 10 + 8)
    _same(_players.decode(payload, 10, 250), lanes)
    for start, stop in [(0, 1), (57, 58), (99, 103), (100, 101), (249, None), (300, None)]:
        part = _players.decode(payload, 10, 250, start=start, stop=stop)
        _same(part, {name: values[start:stop] for name, values in lanes.items()})
    # The rows of the steps, as a state reader returns them, code to the same section.
    rows = _rows(lanes, 10)
    assert len(rows[0]) == _players.row_type(10).itemsize == 10 * 10 + 8
    _same(_players.stack(rows, 10), lanes)
    assert _players.encode(rows, 10) == payload
    # A section may leave fields out, and a reader takes those before the first unknown name.
    some = {'role': lanes['role'], 'direction': lanes['direction']}
    assert _players.fields_of(some) == ['direction', 'role']
    short = _players.encode(some, 10)
    assert len(short) == _players.section_bytes(10, 250, ['direction', 'role'])
    _same(
        _players.decode(short, 10, 250, ['direction', 'role']),
        {'direction': lanes['direction'], 'role': lanes['role']},
    )
    _same(
        _players.decode(short, 10, 250, ['direction', 'later', 'role']),
        {'direction': lanes['direction']},
    )
    with pytest.raises(cuju.StateError, match='cut short'):
        _players.decode(payload[:-1], 10, 250)
    with pytest.raises(cuju.StateError, match='lanes of tired'):
        _players.encode({**lanes, 'tired': lanes['tired'][:, :9]}, 10)
    scaled = _players.scaled(lanes)
    assert np.allclose(scaled['direction'], lanes['direction'] / 100000)
    assert scaled['tired'].max() <= 1 and np.array_equal(scaled['role'], lanes['role'])
    assert np.allclose(scaled['ball_momentum'][:, 2], lanes['ball_momentum'][:, 2] / 1000)
    # In a file the lanes lie in a section of their own beside the frames.
    head, frames = _sample_head('sample-5v5', 250)
    path = tmp_path / 'lanes.cjr'
    _recording.write(path, head, _recording.Frames(frames, players=rows))
    document = _recording.read_header(path)
    assert document['player_fields'] == list(_players.FIELDS)
    assert document['sections'] == ['PLYR'] and document['fidelity'] == 'joints'
    assert list(_recording.frames(path)) == frames
    _same(_recording.players(path), lanes)
    _same(
        _recording.players(path, 99, 103), {name: lanes[name][99:103] for name in _players.FIELDS}
    )
    plain = tmp_path / 'plain.cjr'
    _recording.write(plain, head, frames)
    assert _recording.players(plain) == {}
    assert _recording.read_header(plain)['sections'] == []
    with pytest.raises(cuju.StateError, match='lanes of 250 frames'):
        _recording.write(path, head, _recording.Frames(frames, players=rows[:-1]))


def test_every_section_reads_back_by_its_tag(tmp_path):
    path = tmp_path / 'full.cjr'
    frames, lanes, actions, state, notes = _full_file(path)
    document = _recording.read_header(path)
    assert document['version'] == 2 and document['frame_bytes'] == len(frames[0])
    assert document['sections'] == ['PLYR', 'ACTS', 'STAT']
    assert document['agents'] == [2, 1] and document['action_set'] == 'default'
    assert document['run_key']['state_digest'] == hashlib.sha256(state).hexdigest()
    found = _recording.sections(path)
    assert list(found) == ['FRMS', 'PLYR', 'ACTS', 'STAT']
    assert found['STAT'] == state
    assert len(found['ACTS']) == 3 * 231 and len(found['PLYR']) == 230 * 108
    assert list(_recording.frames(path)) == frames
    # The annotations and the overlays lie in attachments beside the file, the game alone.
    assert {'annotations', 'overlays'} <= set(_attachments.files(path))
    assert _attachments.annotations(path) == notes
    _same(_recording.players(path), lanes)
    assert np.array_equal(_recording.actions(path), actions)
    assert _recording.actions(path).dtype == np.uint8
    assert _recording.state(path) == state
    assert [record['name'] for record in _attachments.overlays(path)] == [
        'pressure',
        'pass',
        'policy',
        'reach',
        'value',
        'pass',
    ]
    # A file of version 1 has room for the frames alone, and one without sections reads as none.
    one = tmp_path / 'one.cjr'
    head, _ = _sample_head('sample-5v5', 230)
    _recording.write(one, head, _recording.Frames(frames, players=lanes, state=state), 1)
    assert _recording.sections(one) == {} and list(_recording.frames(one)) == frames
    assert _recording.players(one) == {} and _recording.actions(one) is None
    assert _recording.state(one) is None and _attachments.overlays(one) == []
    assert 'sections' not in _recording.read_header(one)


def test_parts_that_do_not_fit_the_header_are_refused(tmp_path):
    head, frames = _sample_head('sample-5v5', 20)
    path = tmp_path / 'bad.cjr'
    actions = np.zeros((21, 3), np.uint8)
    with pytest.raises(cuju.StateError, match='agents of the header'):
        _recording.write(path, head, _recording.Frames(frames, actions=actions))
    with pytest.raises(cuju.StateError, match='agents of the header'):
        _recording.write(
            path, _with(head, agents=[2, 2]), _recording.Frames(frames, actions=actions)
        )
    with pytest.raises(cuju.StateError, match='agents of the header'):
        _recording.write(
            path, _with(head, agents=[2, 1]), _recording.Frames(frames, actions=actions[:-1])
        )
    # Overlays past the limits leave their attachment out, and the recording, the game, stays.
    crowded = [{'f': 0, 'name': f'grid{k}', 'kind': 'grid', 'values': [[k]]} for k in range(5)]
    written = _attachments.write_recording(path, head, _recording.Frames(frames, overlays=crowded))
    assert 'overlays' not in written.kinds and 'overlays' not in _attachments.files(path)
    assert list(_recording.frames(path)) == frames
    with pytest.raises(cuju.StateError, match='version'):
        _recording.write(path, head, frames, 4)
    # A state that differs from the digest of the key is refused, and so are actions whose
    # header names no agents.
    _recording.write(path, head, _recording.Frames(frames, state=b'the kick off'))
    document = _recording.read_header(path)
    assert document['run_key'] == {
        'start': 'reset',
        'state_digest': hashlib.sha256(b'the kick off').hexdigest(),
    }
    assert _recording.state(path) == b'the kick off'
    found = _recording.sections(path)
    forged = tmp_path / 'forged.cjr'
    with gzip.open(forged, 'wb') as stream:
        stream.write(_recording._encode_header(document))
        stream.write(_recording._section(b'FRMS', found['FRMS']))
        stream.write(_recording._section(b'STAT', b'another start'))
        stream.write(_recording._section(b'ACTS', bytes(63)))
    with pytest.raises(cuju.StateError, match='key of the run'):
        _recording.state(forged)
    with pytest.raises(cuju.StateError, match='agents of the header'):
        _recording.actions(forged)


@pytest.mark.parametrize(
    'fields',
    [
        {'sections': 'PLYR'},
        {'sections': ['PLAYERS']},
        {'sections': ['PLYR', 4]},
        {'sections': ['PLYR'] * 65},
        {'player_fields': 'direction'},
        {'player_fields': ['direction', 2]},
        {'agents': [1]},
        {'agents': [1, -1]},
        {'agents': [True, 1]},
        {'agents': [1, 33]},
        {'action_set': 2},
        {'run_key': ['reset']},
        {'fidelity': 1},
    ],
)
def test_reader_refuses_new_header_fields_of_the_wrong_type(fields):
    head, _ = _sample_head('sample-5v5', 1)
    document = {**_recording._document(head), 'version': 2, 'keyframe_interval': 100}
    assert _recording.validate_header(dict(document))['version'] == 2
    later = {'sections': ['PLYR', 'ZZZZ'], 'fidelity': 'later', 'player_fields': ['later']}
    assert _recording.validate_header({**document, **later})['fidelity'] == 'later'
    with pytest.raises(cuju.StateError):
        _recording.validate_header({**document, **fields})


def test_version_3_keeps_the_tracks_of_the_frames(tmp_path):
    path = tmp_path / 'tracks.cjr'
    frames, lanes, actions, state, notes = _full_file(path, version=3)
    tracked = [_tracks.of_frame(frame, 10) for frame in frames]
    assert len(tracked[0]) == _tracks.frame_bytes(10) == 76 + 60 + 3 * 12
    document = _recording.read_header(path)
    assert document['version'] == 3 and document['fidelity'] == 'positions'
    assert 'frame_bytes' not in document and document['frame_count'] == 230
    assert document['sections'] == ['PLYR', 'ACTS', 'STAT']
    assert next(iter(_recording.sections(path))) == 'TRKS'
    assert list(_recording.tracks(path)) == tracked
    assert list(_recording.tracks(path, 99, 103)) == tracked[99:103]
    assert list(_recording.tracks(path, 229)) == tracked[229:]
    assert list(_recording.tracks(path, 300)) == []
    # A track frame opens with the head of its frame and closes with its three byte runs.
    for frame, track in zip(frames[:3], tracked[:3], strict=True):
        assert track[:76] == frame[:76] and track[-36:] == frame[-36:]
        roots = np.frombuffer(frame, '<i2', 10 * 14 * 7, 76).reshape(10, 14, 7)[:, 0, :3]
        assert np.array_equal(np.frombuffer(track, '<i2', 30, 76).reshape(10, 3), roots)
    with pytest.raises(cuju.StateError, match='recording of positions'):
        list(_recording.frames(path))
    assert _recording.ending(path) == (230, tracked[-1])
    assert _attachments.annotations(path) == notes
    _same(_recording.players(path), lanes)
    assert np.array_equal(_recording.actions(path), actions)
    assert _recording.state(path) == state
    assert len(_attachments.overlays(path)) == 6
    # The file is a small part of the same frames with their joints.
    full = tmp_path / 'full.cjr'
    _full_file(full)
    assert list(_recording.tracks(full)) == tracked
    assert list(_recording.tracks(full, 99, 103)) == tracked[99:103]
    assert path.stat().st_size < full.stat().st_size / 4
    # Track frames go in as they are, and frames of any bytes keep theirs bit for bit.
    head, _ = _sample_head('sample-5v5', 230)
    again = tmp_path / 'again.cjr'
    _recording.write(again, head, tracked, 3)
    assert list(_recording.tracks(again)) == tracked
    generator = np.random.default_rng(7)
    noise = [
        _tracks.of_frame(
            generator.integers(0, 256, _recording.frame_bytes(3), np.uint8).tobytes(), 3
        )
        for _ in range(230)
    ]
    assert len(noise[0]) == _tracks.frame_bytes(3) == 76 + 20 + 3 * 4
    section = _tracks.encode(noise, 3)
    assert len(section) == _tracks.section_bytes(3, 230)
    assert [row.tobytes() for row in _tracks.decode(section, 3, 230)] == noise
    assert [row.tobytes() for row in _tracks.decode(section, 3, 230, start=150, stop=160)] == (
        noise[150:160]
    )
    with pytest.raises(cuju.StateError, match='track frames of 10 players'):
        _recording.write(again, head, [frame[:-4] for frame in tracked], 3)


def test_old_reader_skips_new_sections(tmp_path):
    """The reader of cuju-render 0.1.0 opens a file with every new section, frame for frame."""
    old = reader_010()
    assert old.VERSIONS == (1, 2) and old is not _recording
    path = tmp_path / 'full.cjr'
    frames, _, _, _, notes = _full_file(path)
    document = old.read_header(path)
    assert document['version'] == 2 and document['frame_count'] == 230
    assert document['sections'] == ['PLYR', 'ACTS', 'STAT']
    assert list(old.frames(path)) == frames
    assert list(old.frames(path, 150, 160)) == frames[150:160]
    assert old.ending(path) == (230, frames[-1])
    # The annotations lie in their attachment, which the reader of 0.1.0 passes over.
    assert old.annotations(path) == [] and _attachments.annotations(path) == notes
    # The shipped fixtures, which a recorder wrote with every section, open the same way.
    for name in _fixtures.JOINT_FILES:
        fixture = _fixtures.FOLDER / name
        assert list(old.frames(fixture)) == list(_recording.frames(fixture))
        assert old.read_header(fixture) == _recording.read_header(fixture)


def test_version_3_refused_by_old_readers_with_the_version_message(tmp_path):
    old = reader_010()
    path = tmp_path / 'tracks.cjr'
    _full_file(path, version=3)
    for read in (old.read_header, lambda file: list(old.frames(file)), old.annotations):
        with pytest.raises(cuju.StateError) as refusal:
            read(path)
        assert str(refusal.value) == 'expected a recording version from (1, 2), found 3'
    with pytest.raises(cuju.StateError, match=r'version from \(1, 2\), found 3'):
        old.read_header(_fixtures.FOLDER / _fixtures.TRACK_FILE)


def test_grid_limits_and_round_trip(tmp_path, monkeypatch):
    records = _overlay_records()
    payload = _overlays.encode(records)
    (length,) = struct.unpack_from('<I', payload)
    assert (4 + length) % 4 == 0
    index = json.loads(payload[4 : 4 + length])['records']
    assert [(entry['f'], entry.get('p'), entry['name']) for entry in index] == [
        (0, None, 'pressure'),
        (3, 2, 'pass'),
        (3, 2, 'policy'),
        (3, 2, 'reach'),
        (3, 2, 'value'),
        (229, 9, 'pass'),
    ]
    assert [entry['type'] for entry in index] == [
        'uint8',
        'float16',
        'uint8',
        'float16',
        'uint8',
        'float16',
    ]
    assert all(entry['at'] % 2 == 0 for entry in index if entry['type'] == 'float16')
    decoded = _overlays.decode(payload)
    by_name = {(record['f'], record['name']): record for record in records}
    for found in decoded:
        given = by_name[(found['f'], found['name'])]
        values = np.asarray(given['values'], np.float64)
        assert found['values'].shape == values.shape and found['values'].dtype == np.float32
        assert found['kind'] == given['kind'] and found.get('p') == given.get('p')
        assert found.get('side') == given.get('side')
        assert found.get('extent') == (
            [float(item) for item in given['extent']] if 'extent' in given else None
        )
        if found['type'] == 'uint8':
            # A byte holds one of 256 steps between the least and the greatest value.
            step = (values.max() - values.min()) / 255
            assert np.abs(found['values'] - values).max() <= step / 2 + 1e-6
        else:
            assert np.array_equal(found['values'], values.astype('<f2').astype(np.float32))
    assert np.array_equal(decoded[0]['values'], np.full((4, 6), 0.25, np.float32))
    # The limits of a step and player, of a grid, of a vector and of the whole section.
    grid = {'f': 1, 'p': 0, 'kind': 'grid', 'values': np.zeros((2, 2))}
    vector = {'f': 1, 'p': 0, 'kind': 'targets', 'values': np.zeros(3)}
    most = [{**grid, 'name': f'grid{k}'} for k in range(4)]
    most += [
        {**vector, 'name': f'vector{k}', 'kind': ('targets', 'actions')[k % 2]} for k in range(4)
    ]
    assert len(_overlays.decode(_overlays.encode(most))) == 8
    refused = [
        [*most, {**grid, 'name': 'one more'}],
        [*most, {**vector, 'name': 'one more'}],
        [{**grid, 'name': 'tall', 'values': np.zeros((73, 96))}],
        [{**grid, 'name': 'wide', 'values': np.zeros((72, 97))}],
        [{**grid, 'name': 'flat', 'values': np.zeros(5)}],
        [{**vector, 'name': 'long', 'values': np.zeros(65)}],
        [{**vector, 'name': 'empty', 'values': []}],
        [{**grid, 'name': 'hole', 'values': [[np.nan]]}],
        [{**grid, 'name': 'huge', 'type': 'float16', 'values': [[1e6]]}],
        [{**grid, 'name': 'kind', 'kind': 'heat'}],
        [{**grid, 'name': ''}],
        [{**grid, 'name': 'n' * 41}],
        [{**grid, 'name': 'twice'}, {**grid, 'name': 'twice'}],
        [{**grid, 'name': 'side', 'side': 2}],
        [{**grid, 'name': 'extent', 'extent': [0, 0, 1]}],
        [{**vector, 'name': 'extent', 'extent': [0, 0, 1, 1]}],
        [{**grid, 'name': 'type', 'type': 'float32'}],
        [{**grid, 'name': 'frame', 'f': -1}],
        [{**grid, 'name': 'player', 'p': 32}],
    ]
    for overlays in refused:
        with pytest.raises(cuju.errors.ArgumentError):
            _overlays.encode(overlays)
    # A reader leaves out what is malformed or past a limit, and keeps the rest.
    crowded = {'records': [{**index[0], 'name': f'grid{k}'} for k in range(5)]}
    text = json.dumps(crowded).encode()
    text += b' ' * (-(4 + len(text)) % 4)
    assert len(_overlays.decode(struct.pack('<I', len(text)) + text + bytes(24))) == 4
    past = json.dumps({'records': [{**index[0], 'at': 1}, index[0], 7]}).encode()
    assert len(_overlays.decode(struct.pack('<I', len(past)) + past + bytes(24))) == 1
    for junk in (b'', b'abc', struct.pack('<I', 99) + b'{}', struct.pack('<I', 2) + b'[]'):
        assert _overlays.decode(junk) == []
    assert len(_overlays.decode(payload[:-1])) == 5
    monkeypatch.setattr(_overlays, 'MAX_BYTES', 4000)
    with pytest.raises(cuju.errors.ArgumentError, match='at most 0 MiB'):
        _overlays.encode(records)
    monkeypatch.undo()
    # Beside a file the overlays lie in an attachment, whose body keeps the layout of GRID.
    head, frames = _sample_head('sample-5v5', 230)
    path = tmp_path / 'overlays.cjr'
    _attachments.write_recording(path, head, _recording.Frames(frames, overlays=records))
    assert _recording.read_header(path)['sections'] == []
    assert _attachments.read(_attachments.path_of(path, 'overlays')).body == payload
    found = _attachments.overlays(path)
    assert [record['name'] for record in found] == [record['name'] for record in decoded]
    assert all(
        np.array_equal(a['values'], b['values']) for a, b in zip(found, decoded, strict=True)
    )


def test_fixtures_decode_to_their_expected_values():
    """The files that the page's reader is tested on decode here to the values beside them."""
    expected = json.loads((_fixtures.FOLDER / 'expected.json').read_text(encoding='utf-8'))
    assert sorted(expected) == sorted([*_fixtures.JOINT_FILES, _fixtures.TRACK_FILE])
    for name, values in expected.items():
        assert _fixtures.decoded(_fixtures.FOLDER / name) == values, name
        assert values['header']['sections'][-1] == 'GRID'
        assert {'PLYR', 'ACTS', 'STAT', 'GRID'} <= set(values['header']['sections'])
    assert expected[_fixtures.TRACK_FILE]['header']['version'] == 3


def test_constants_of_the_sections_stand_in_one_file():
    """The names, scales and limits of the sections stand in one file, which the tests of the
    page read as well, so the two readers stay equal."""
    text = (_fixtures.FOLDER / 'constants.json').read_text(encoding='utf-8')
    constants = json.loads(text)
    tags = (
        _recording.FRAMES_TAG,
        _tracks.TAG,
        _annotations.TAG,
        _players.TAG,
        _actions.TAG,
        _recording.STATE_TAG,
        _overlays.TAG,
    )
    assert constants == {
        'sections': {
            'limit': _recording.MAX_SECTIONS,
            'tags': [tag.decode('ascii') for tag in tags],
        },
        'fidelity': [_recording.JOINTS, _recording.POSITIONS],
        'start': [_recording.START_RESET, _recording.START_MID],
        'player_fields': {'limit': _recording.MAX_SECTIONS, 'names': list(_players.FIELDS)},
        'scales': {
            'direction': _players.DIRECTION_SCALE,
            'height': _players.HEIGHT_SCALE,
            'tired': _players.TIRED_SCALE,
        },
        'sticky': list(_players.STICKY),
        'agents_a_side': _recording.MAX_PLAYERS,
        'no_action': _actions.NONE,
        'overlays': {
            'kinds': list(_overlays.KINDS),
            'types': list(_overlays.TYPES),
            'grids': _overlays.MAX_GRIDS,
            'vectors': _overlays.MAX_VECTORS,
            'rows': _overlays.MAX_ROWS,
            'columns': _overlays.MAX_COLUMNS,
            'vector': _overlays.MAX_VECTOR,
            'name': _overlays.NAME_LENGTH,
            'bytes': _overlays.MAX_BYTES,
        },
    }


def _turned(sticky):
    """Returns sticky actions with their eight directions turned by half a turn."""
    return [*sticky[4:8], *sticky[:4], *sticky[8:]]


@pytest.mark.parametrize('scenario', ['5_vs_5_l2_r3', '11_vs_11_stochastic_l3_r2'])
@pytest.mark.parametrize('reverse', [0, 1])
def test_player_lanes_match_raw(device, scenario, reverse):
    """The lanes of every step equal GRF's raw observation of every agent, quantized."""
    batch = cuju.BatchEnvironment(
        scenario, environment_count=2, device=device, with_raw=True, seed=5
    )
    batch.reset(reverse=[1 - reverse, reverse])
    players = int(batch.left_players[1] + batch.right_players[1])
    left_agents = int(batch.left_agents[1])
    reader = _recording.StateReader(players)
    controlled = set()
    for step in range(60):
        batch.step(actions_at(step, 2, batch.agents, batch.action_count).to(batch.torch_device))
        state = batch.get_state(1)
        lineup = reader.lineup(state)
        reader.frame(state)
        assert reader.reverse == bool(reverse)
        assert reader.agents == (left_agents, batch.agents - left_agents)
        lanes = _players.stack([reader.lanes(batch.roles(1))], players)
        lanes = {name: values[0] for name, values in lanes.items()}
        place = {(entry['team'], entry['index']): at for at, entry in enumerate(lineup)}
        held = 0
        for agent, raw in enumerate(cuju.raw.grf_observations(batch, 1)):
            right = agent >= left_agents
            # GRF: a right agent sees its own team as the left team, turned by half a turn.
            sign = -1 if right else 1
            for own, name in ((True, 'left_team'), (False, 'right_team')):
                team = int(right) if own else 1 - int(right)
                for index in range(len(raw[name])):
                    at = place[(team, index)]
                    direction = np.float32(raw[f'{name}_direction'][index]) * sign
                    expected = np.rint(direction.astype(np.float64) * 100000)
                    assert lanes['direction'][at].tolist() == expected.tolist()
                    tired = np.float64(np.float32(raw[f'{name}_tired_factor'][index]))
                    assert lanes['tired'][at] == np.rint(tired * 32767)
                    assert lanes['role'][at] == raw[f'{name}_roles'][index]
            momentum = np.float32(raw['ball_direction']) * np.float32([sign, sign, 1])
            scale = np.array([100000, 100000, 1000])
            assert lanes['ball_momentum'].tolist() == np.rint(momentum * scale).tolist()
            team = int(right)
            assert lanes['designated'][team] == place[(team, raw['designated'])] + 1
            if raw['active'] < 0:
                continue
            held += 1
            at = place[(team, raw['active'])]
            controlled.add(at)
            assert lanes['agent'][at] == (agent - left_agents if right else agent) + 1
            sticky = [int(lanes['sticky'][at]) >> bit & 1 for bit in range(13)]
            mine = _turned(sticky) if right else sticky
            assert mine[: len(raw['sticky_actions'])] == raw['sticky_actions'].tolist()
            assert not any(sticky[len(raw['sticky_actions']) :])
        assert int((lanes['agent'] > 0).sum()) == held
        assert not lanes['sticky'][lanes['agent'] == 0].any()
    # The agents moved between players of both teams.
    assert len(controlled) > batch.agents
    batch.close()


def test_player_lanes_keep_the_sticky_actions_of_the_full_set():
    batch = cuju.BatchEnvironment(
        '5_vs_5_l1_r1', environment_count=1, device='cpu', with_raw=True, action_set='full'
    )
    batch.reset()
    reader = _recording.StateReader(10)
    seen = set()
    # The actions 20 to 22 of the full set are the keeper rush and the two pressures.
    for step in range(40):
        batch.step(torch.tensor([[(13, 20, 21, 22, 17, 3)[step % 6]] * 2], dtype=torch.int32))
        state = batch.get_state(0)
        lineup = reader.lineup(state)
        reader.frame(state)
        lanes = _players.stack([reader.lanes()], 10)
        for agent, raw in enumerate(cuju.raw.grf_observations(batch, 0)):
            at = next(
                k
                for k, entry in enumerate(lineup)
                if (entry['team'], entry['index']) == (agent, raw['active'])
            )
            sticky = [int(lanes['sticky'][0, at]) >> bit & 1 for bit in range(13)]
            assert (_turned(sticky) if agent else sticky) == raw['sticky_actions'].tolist()
            seen.update(bit for bit in range(13) if sticky[bit])
        # Without the roles of the batch the lanes hold those of the scenario.
        roles = batch.roles(0)
        expected = [
            int(roles[('left_team_roles', 'right_team_roles')[entry['team']]][entry['index']])
            for entry in lineup
        ]
        assert lanes['role'][0].tolist() == expected
    assert {8, 9, 10, 11, 12} <= seen
    with pytest.raises(cuju.StateError, match='before its lanes'):
        _recording.StateReader(10).lanes()
    batch.close()


def test_player_lane_paths_exist_in_layout():
    """Every field that the state reader gathers is a field of the layout of cuju."""
    layout = cuju.state.layout()
    reader = _recording.StateReader(22)
    blocks = {'nodes': 'nodes[0].position', 'buttonsPressed': 'buttonsPressed[0]'}
    for field in reader._fields:
        if field.path in layout:
            assert field == layout[field.path], field.path
            continue
        # One field covers the fourteen nodes of a player, and one the buttons of a keyboard.
        stem, _, name = field.path.rpartition('.')
        first = layout[f'{stem}.{blocks[name]}']
        assert field.offset == first.offset and field.size > first.size
        last = max(
            (known for path, known in layout.items() if path.startswith(f'{field.path}[')),
            key=lambda known: known.offset,
        )
        assert field.offset + field.size >= last.offset + last.size
    paths = {field.path for field in reader._fields}
    for player in (0, 21):
        base = f'match.players[{player}].base'
        assert f'{base}.humanoid.spatialState.movement' in paths
        assert f'{base}.fatigueFactorInverse' in paths
        assert f'{base}.externalController' in paths
    for team in (0, 1):
        assert f'match.teams[{team}].mainSelectedPlayer' in paths
        assert f'match.teams[{team}].humanGamers.count' in paths
        for gamer in (0, 10):
            keyboard = f'match.teams[{team}].humanGamers[{gamer}]'
            assert f'{keyboard}.keyboardIndex' in paths
            assert f'{keyboard}.controller.keyboard.mirror' in paths
            assert f'{keyboard}.controller.keyboard.direction' in paths
            assert f'{keyboard}.controller.keyboard.buttonsPressed' in paths
    for side in ('left', 'right'):
        assert f'environment.scenario.{side}Team[10].role' in paths
        assert f'environment.scenario.{side}Agents' in paths
    assert 'match.ball.momentum' in paths
    # The types are those that the lanes read them as.
    assert layout['match.ball.momentum'].size == 12
    assert layout['match.players[0].base.fatigueFactorInverse'].type == 'f'
    assert layout['match.teams[0].humanGamers[0].controller.keyboard.buttonsPressed[10]'].size == 1


def _recorded(scenario, device='cpu', steps=130, reverse=0, environments=2, **options):
    """Records the start of an episode of the last environment of a batch.

    Returns the path of the recording and the actions that the environment played.
    """
    batch = cuju.BatchEnvironment(
        scenario, environment_count=environments, device=device, seed=9, auto_reset=False, **options
    )
    recorder = cuju.arena.Recorder(batch, environment=environments - 1)
    batch.reset(reverse=[reverse] * environments)
    played = []
    for step in range(steps):
        actions = actions_at(step, environments, batch.agents, batch.action_count)
        played.append(actions[environments - 1].tolist())
        actions = actions.to(batch.torch_device)
        batch.step(actions)
        # The caller may write its tensor after the step, which the recording does not follow.
        actions.zero_()
    path = recorder.close()
    batch.close()
    return path, played


def _rerun(path):
    """Plays a recording again from its state and its actions, in a batch that its key gives.

    Returns the frames and the rows of lanes of every step, the step that an automatic reset cut
    included, and whether the last step ended the episode.
    """
    document = _recording.read_header(path)
    key = document['run_key']
    assert key['start'] == 'reset'
    state = _recording.state(path)
    assert key['state_digest'] == hashlib.sha256(state).hexdigest()
    actions = _recording.actions(path)
    batch = cuju.BatchEnvironment(
        key['scenario'],
        environment_count=1,
        device='cpu',
        auto_reset=False,
        action_set=key['action_set'],
    )
    assert batch.difficulty[0].tolist() == pytest.approx(key['difficulty'])
    batch.set_state(0, state)
    reader = _recording.StateReader(len(document['players']))
    frames = [reader.frame(batch.get_state(0))]
    rows = [reader.lanes(batch.roles(0))]
    done = False
    for row in actions[1:]:
        if (row == _actions.NONE).any():
            break
        _, _, ended, _ = batch.step(torch.tensor(row[None].astype(np.int32)))
        done = bool(ended[0])
        frames.append(reader.frame(batch.get_state(0), 1 if done else 0))
        rows.append(reader.lanes(batch.roles(0)))
    batch.close()
    return frames, rows, done


@pytest.mark.parametrize(
    'scenario,reverse', [('5_vs_5_l4_r1', 0), ('5_vs_5_l2_r3', 1), ('11_vs_11_stochastic_l3_r2', 1)]
)
def test_stat_and_acts_rerun_bit_for_bit(arena_home, device, scenario, reverse):
    """The state of the kick off and the actions play the episode again, frame for frame."""
    path, played = _recorded(scenario, device, reverse=reverse)
    document = _recording.read_header(path)
    assert document['sections'] == ['PLYR', 'ACTS', 'STAT']
    assert document['run_key']['reverse'] == bool(reverse)
    assert document['run_key']['scenario'] == scenario
    assert document['run_key']['engine_seed'] == document['provenance']['engine_seed']
    actions = _recording.actions(path)
    assert actions[1:-1].tolist() == played
    assert (actions[0] == 255).all() and (actions[-1] == 255).all()
    recorded = list(_recording.frames(path))
    frames, rows, done = _rerun(path)
    assert len(recorded) == 131 and not done
    assert frames == recorded
    _same(_players.stack(rows, len(document['players'])), _recording.players(path))
    # The reader of cuju-render 0.1.0 opens what the recorder writes, frame for frame.
    old = reader_010()
    assert list(old.frames(path)) == recorded
    assert old.read_header(path)['run_key'] == document['run_key']


def test_recorder_actions_come_from_the_observer(arena_home):
    """The actions of a recording are those that the batch hands its step observers."""
    batch = cuju.BatchEnvironment('5_vs_5_l2_r1', environment_count=3, device='cpu', seed=4)
    recorder = cuju.arena.Recorder(batch, environment=1)
    seen = []
    batch.add_observer(
        lambda batch, done, information: seen.append(
            None if done is None else information['actions'][1].tolist()
        )
    )
    batch.reset()
    played = []
    for step in range(12):
        actions = actions_at(step, 3, 3, 19)
        if step == 3:
            # GRF: a negative action counts from the end of the action set.
            actions[1, 2] = -1
        played.append(actions[1].tolist())
        # The batch takes its actions as a tensor or as a list.
        batch.step(actions if step % 2 else actions.tolist())
    path = recorder.close()
    assert seen == [None, *played]
    played[3][2] = 18
    document = _recording.read_header(path)
    assert document['agents'] == [2, 1] and document['action_set'] == 'default'
    assert document['sections'] == ['PLYR', 'ACTS', 'STAT']
    assert _recording.actions(path).tolist() == [[255] * 3, *played, [255] * 3]
    # A capture by hand that leaves a step out marks the actions that it did not see.
    by_hand = cuju.arena.Recorder(batch, environment=1)
    batch.reset()
    by_hand.capture()
    kept = []
    for step in range(9):
        actions = actions_at(step, 3, 3, 19)
        _, _, done, _ = batch.step(actions)
        if step % 3 == 1:
            continue
        kept.append([255] * 3 if step % 3 == 2 else actions[1].tolist())
        by_hand.capture(done)
    path = by_hand.close()
    assert _recording.actions(path).tolist() == [[255] * 3, *kept, [255] * 3]
    assert len(list(_recording.frames(path))) == 7
    assert len(_recording.players(path)['role']) == 7
    # A recording of version 1 has no room for any of it.
    one = cuju.arena.Recorder(batch, environment=1, version=1)
    batch.reset()
    batch.step(actions_at(0, 3, 3, 19))
    path = one.close()
    document = _recording.read_header(path)
    assert not {'agents', 'action_set', 'run_key', 'sections'} & set(document)
    batch.close()


def test_auto_reset_last_action_reaches_the_true_end(arena_home):
    """The last row of the actions plays the step whose frame an automatic reset replaced."""
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu', seed=11
    )
    recorder = cuju.arena.Recorder(batch)
    batch.reset()
    step, scores = 0, []
    while recorder.episodes < 2:
        _, reward, done, _ = batch.step(torch.tensor([[12 if step % 3 else 5]], dtype=torch.int32))
        step += 1
        if bool(done[0]):
            scores.append((1, 0) if float(reward[0, 0]) > 0 else (0, 0))
    recorder.flush()
    batch.close()
    assert len(recorder.saved) == 2
    for path, score in zip(recorder.saved, scores, strict=True):
        recorded = list(_recording.frames(path))
        actions = _recording.actions(path)
        assert len(actions) == len(recorded) + 1 and actions[-1, 0] in (5, 12)
        assert _recording.read_header(path)['run_key']['start'] == 'reset'
        frames, _, done = _rerun(path)
        # The run plays one step more than the recording holds, the step that ended it.
        assert done and len(frames) == len(recorded) + 1
        assert frames[: len(recorded) - 1] == recorded[:-1]
        # The recorder marked its last frame as the end and gave it the goal of the lost step.
        last, marked = bytearray(frames[len(recorded) - 1]), bytearray(recorded[-1])
        for offset in (8, 12, 28):
            last[offset : offset + 4] = marked[offset : offset + 4] = bytes(4)
        assert last == marked
        # The step that the recording lacks ends the episode at the score that it reports.
        assert struct.unpack_from('<2i', frames[-1], 8) == score
        assert struct.unpack_from('<2i', recorded[-1], 8) == score
        assert struct.unpack_from('<i', frames[-1], 28)[0] & 1


@slow
def test_stat_and_acts_rerun_a_whole_match(arena_home, device):
    """A whole match under automatic resets plays again to its true end, frame for frame."""
    batch = cuju.BatchEnvironment('5_vs_5_l2_r3', environment_count=1, device=device, seed=13)
    recorder = cuju.arena.Recorder(batch)
    batch.reset(reverse=[1])
    step = 0
    while recorder.episodes < 1:
        # Each agent keeps an action for a few steps, so the players run, pass and shoot.
        actions = actions_at(step // 5, 1, batch.agents, batch.action_count)
        batch.step(actions.to(batch.torch_device))
        step += 1
    recorder.flush()
    batch.close()
    path = recorder.saved[0]
    recorded = list(_recording.frames(path))
    frames, rows, done = _rerun(path)
    assert done and len(recorded) == step > 3000 and len(frames) == step + 1
    assert frames[: step - 1] == recorded[:-1]
    # The recorder marked its last frame as the end, and the lanes of that step stay as read.
    last, marked = bytearray(frames[step - 1]), bytearray(recorded[-1])
    for offset in (8, 12, 28):
        last[offset : offset + 4] = marked[offset : offset + 4] = bytes(4)
    assert last == marked
    _same(_players.stack(rows[:-1], 10), _recording.players(path))
    assert list(reader_010().frames(path)) == recorded


def test_mid_episode_capture_says_start_mid(arena_home):
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu', seed=11
    )
    batch.reset()
    for _ in range(3):
        batch.step(torch.tensor([[5]], dtype=torch.int32))
    recorder = cuju.arena.Recorder(batch)
    step = 0
    while recorder.episodes < 2:
        batch.step(torch.tensor([[12 if step % 3 else 5]], dtype=torch.int32))
        step += 1
    recorder.flush()
    batch.close()
    first, second = (_recording.read_header(path) for path in recorder.saved)
    # The capture began after the kick off, so the recording keeps no state to start from.
    assert first['run_key']['start'] == 'mid' and 'state_digest' not in first['run_key']
    assert first['sections'] == ['PLYR', 'ACTS']
    assert _recording.state(recorder.saved[0]) is None
    assert struct.unpack_from('<i', next(_recording.frames(recorder.saved[0])))[0] == 4
    assert (_recording.actions(recorder.saved[0])[1:] != 255).all()
    # The next episode of the same recorder starts at its reset.
    assert second['run_key']['start'] == 'reset'
    assert second['sections'] == ['PLYR', 'ACTS', 'STAT']
    assert len(second['run_key']['state_digest']) == 64
    for name in ('scenario', 'agents', 'action_set', 'reverse', 'difficulty'):
        assert first['run_key'][name] == second['run_key'][name]


@slow
def test_recorder_capture_stays_within_budget(arena_home):
    """A capture with the lanes, the actions and the roles takes at most 1.3 times one without."""
    batch = cuju.BatchEnvironment(
        '11_vs_11_stochastic_l11_r0', environment_count=1, device='cpu', seed=3, threads=1
    )
    spent = {}

    def timed(recorder, name):
        batch.remove_observer(recorder)
        spent[name] = []

        def observer(batch, done, information):
            start = time.perf_counter()
            recorder(batch, done, information)
            spent[name].append(time.perf_counter() - start)

        batch.add_observer(observer)

    # Version 1 captures the frames alone, as every version did before the sections.
    timed(cuju.arena.Recorder(batch, version=1, keep=lambda summary: False), 'frames')
    timed(cuju.arena.Recorder(batch, keep=lambda summary: False), 'sections')
    batch.reset()
    for step in range(400):
        batch.step(actions_at(step, 1, 11, 19))
    batch.close()
    before = statistics.median(spent['frames'][1:])
    after = statistics.median(spent['sections'][1:])
    print(f'capture, median ms per step: frames {before * 1e3:.3f}, sections {after * 1e3:.3f}')
    assert after <= 1.3 * before
