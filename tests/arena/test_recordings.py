# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the recording format, the recorder, its filters and annotations."""

import gc
import gzip
import hashlib
import io
import json
import os
import struct
import time

import numpy as np
import pytest
import torch

import cuju
import cuju.errors
import cuju.state
from conftest import actions_at
from cuju.arena._format import attachments as _attachments
from cuju.arena._format import recording as _recording
from cuju.arena._format import writer as _writer
from cuju.arena._store import library as _library
from support.common import (
    _SAMPLE_FOLDER,
    _SEVERAL,
    _frames,
    _names_lineup,
    _sample_head,
    _short_matches,
    _toy_team,
)


def test_pitch_frame_matches_raw(device):
    """The pitch frame turns every player into the raw observation's positions, bit for bit."""
    batch = cuju.BatchEnvironment(
        '5_vs_5_l4_r1', environment_count=2, device=device, with_raw=True, seed=3
    )
    batch.reset(reverse=[0, 1])
    reader = _recording.StateReader(10)
    for step in range(80):
        batch.step(actions_at(step, 2, 5, 19).to(device))
        raw = {name: tensor.cpu() for name, tensor in batch.raw().items()}
        for environment in range(2):
            state = batch.get_state(environment)
            order = reader._order(reader._values(state, 'pitch'))
            paths = [
                f'match.players[{player}].base.humanoid.spatialState.position'
                for player in range(10)
            ]
            positions = cuju.state.read(state, paths, frame='pitch')
            for player, (team, index) in enumerate(order):
                name = 'left_team' if team == 0 else 'right_team'
                x, y = positions[player][0], positions[player][1]
                expected = raw[name][environment, 0, index]
                # GRF: the raw observation divides by 54.4 and by -83.6 in double precision.
                assert np.float32(np.float64(x) / 54.4) == expected[0]
                assert np.float32(np.float64(y) / -83.6) == expected[1]
            frame = reader.frame(state)
            _, mode, left, right, owned_team, owned = struct.unpack_from('<6i', frame)
            assert [left, right] == raw['score'][environment, 0].tolist()
            assert mode == int(raw['game_mode'][environment, 0])
            assert owned_team == int(raw['ball_owned_team'][environment, 0])
            if owned >= 0:
                assert order[owned][1] == int(raw['ball_owned_player'][environment, 0])


def test_world_transforms_are_upright():
    batch = cuju.BatchEnvironment('5_vs_5_l1_r0', environment_count=1, device='cpu')
    batch.reset()
    reader = _recording.StateReader(10)
    for step in range(20):
        batch.step(actions_at(step, 1, 1, 19))
    frame = reader.frame(batch.get_state(0))
    head = _recording._FRAME_HEAD.size
    packed = np.frombuffer(frame, '<i2', 10 * 14 * 7, head).reshape(10, 14, 7).astype(np.float64)
    scale = [_recording.POSITION_SCALE] * 3 + [_recording.ROTATION_SCALE] * 4
    nodes = packed / np.array(scale)
    neck = _recording.NODES.index('neck')
    ankle = _recording.NODES.index('left_ankle')
    assert (nodes[:, neck, 2] > nodes[:, ankle, 2] + 1.0).all()
    assert np.allclose(np.linalg.norm(nodes[:, :, 3:], axis=2), 1, atol=2e-4)
    assert (np.abs(nodes[:, 0, 0]) < 60).all() and (np.abs(nodes[:, 0, 1]) < 45).all()


def test_recorder_writes_episodes(arena_home):
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu'
    )
    recorder = cuju.arena.Recorder(batch)
    batch.reset()
    recorder.capture()
    step = 0
    while recorder.episodes < 2:
        _, _, done, _ = batch.step(torch.tensor([[12 if step % 3 else 5]], dtype=torch.int32))
        recorder.capture(done)
        step += 1
    recorder.flush()
    first = recorder.saved[0]
    document = _recording.read_header(first)
    frames = list(_recording.frames(first))
    assert document['frame_count'] == len(frames)
    assert document['second_half'] is None
    assert document['duration'] > 0
    assert document['engine_scorer']
    assert document['offside_called']
    assert all(len(frame) == document['frame_bytes'] for frame in frames)
    assert [struct.unpack_from('<i', frame)[0] for frame in frames] == list(range(len(frames)))
    summary = {entry['id']: entry for entry in _library.matches()}[first.stem]
    assert summary['steps'] == len(frames)
    provenance = {
        'source': 'recorder',
        'environment': 0,
        'engine_seed': document['provenance']['engine_seed'],
        'action_set': 'default',
        'cuju_version': cuju.__version__,
    }
    assert document['provenance'] == provenance
    assert summary['provenance'] == provenance
    assert summary['scenario'] == 'academy_empty_goal_close_l1_r0'
    # The shots of the episode show as touches during shot animations of the agent's player.
    players = len(document['players'])
    padded = -(-players // 4) * 4
    start = _recording._FRAME_HEAD.size + players * 14 * 14 + padded
    actions = [frame[start : start + players] for frame in frames]
    touches = [frame[start + padded : start + padded + players] for frame in frames]
    shots = [
        index
        for index in range(len(frames))
        if any(t and a == 8 for a, t in zip(actions[index], touches[index], strict=True))
    ]
    assert shots


def test_recorder_keeps_the_final_goal_with_automatic_resets(arena_home):
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu'
    )
    recorder = cuju.arena.Recorder(batch)
    batch.reset()
    recorder.capture()
    step = 0
    while not recorder.episodes:
        _, reward, done, _ = batch.step(torch.tensor([[12 if step % 3 else 5]], dtype=torch.int32))
        recorder.capture(done)
        step += 1
    recorder.flush()
    scored = float(reward[0, 0]) > 0
    last = list(_recording.frames(recorder.saved[0]))[-1]
    assert struct.unpack_from('<2i', last, 8) == ((1, 0) if scored else (0, 0))
    assert struct.unpack_from('<i', last, 28)[0] & 1


@pytest.mark.parametrize('auto_reset', [True, False])
def test_recorder_captures_by_itself_as_by_hand(arena_home, auto_reset):
    recorded = []
    for by_hand in (False, True):
        batch = cuju.BatchEnvironment(
            'academy_empty_goal_close_l1_r0',
            environment_count=2,
            device='cpu',
            seed=3,
            auto_reset=auto_reset,
        )
        recorder = cuju.arena.Recorder(batch, environment=1)
        assert recorder in batch.observers
        batch.reset()
        if by_hand:
            recorder.capture()
        step = 0
        while recorder.episodes < 2:
            actions = torch.tensor([[12 if step % 3 else 5]] * 2, dtype=torch.int32)
            _, _, done, _ = batch.step(actions)
            if by_hand:
                recorder.capture(done)
            if not auto_reset and bool(done.any()):
                # The reset clears the done flags that `done` views.
                ended = bool(done[1])
                batch.reset_done()
                if by_hand and ended:
                    recorder.capture()
            step += 1
        # A reset of the other environment adds no frame to the recording of this one.
        frames = len(recorder._frames)
        batch.reset(mask=[True, False])
        assert len(recorder._frames) == frames
        recorder.flush()
        recorded.append([list(_recording.frames(path)) for path in recorder.saved])
        batch.close()
    assert recorded[0] == recorded[1]


def test_recorder_writes_one_recording_for_an_environment_that_idles_after_its_end(arena_home):
    """An ended environment of a batch without automatic resets captures nothing until its reset."""
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0',
        environment_count=2,
        device='cpu',
        seed=3,
        auto_reset=False,
    )
    recorder = cuju.arena.Recorder(batch, environment=0)
    by_hand = cuju.arena.Recorder(batch, environment=0)
    batch.reset()
    by_hand.capture()
    # Environment 0 shoots at once and then idles, while environment 1 runs to its limit.
    actions = torch.tensor([[12], [0]], dtype=torch.int32)
    finished = torch.zeros(2, dtype=torch.bool)
    handed = []
    while not bool(finished.all()):
        _, _, done, _ = batch.step(actions)
        handed.append(by_hand.capture(done))
        finished |= done.bool()
    recorder.flush()
    by_hand.flush()
    assert len(handed) > 100 and sum(path is not None for path in handed) == 1
    for found in (recorder, by_hand):
        assert (found.episodes, len(found.saved)) == (1, 1)
        frames = list(_recording.frames(found.saved[0]))
        assert len(frames) > 1 and struct.unpack_from('<i', frames[-1], 28)[0] & 1
        assert not found._frames
    assert list(_recording.frames(recorder.saved[0])) == list(_recording.frames(by_hand.saved[0]))
    # The reset of the environment starts the next recording, which ends once more.
    batch.reset_done()
    by_hand.capture()
    for _ in range(40):
        _, _, done, _ = batch.step(actions)
        by_hand.capture(done)
    recorder.flush()
    by_hand.flush()
    for found in (recorder, by_hand):
        assert (found.episodes, len(found.saved)) == (2, 2)
        assert len(list(_recording.frames(found.saved[1]))) > 1
    batch.close()


def test_recording_provenance_preserves_frame_format():
    head = _recording.header('Match', 'custom', [], provenance={'source': 'recorder'})
    document = _recording._parse_header(io.BytesIO(head))
    assert document['version'] == 1
    assert len(head) % 4 == 0
    assert document['provenance'] == {'source': 'recorder', 'cuju_version': cuju.__version__}
    document.pop('provenance')
    text = json.dumps(document).encode()
    old = _recording.MAGIC + struct.pack('<I', len(text)) + text
    assert 'provenance' not in _recording._parse_header(io.BytesIO(old))
    for seed, kept in ((2**53 - 1, 2**53 - 1), (2**53, str(2**53)), (2**64 - 1, str(2**64 - 1))):
        large = _recording.header('Match', 'custom', [], provenance={'seed': seed})
        assert _recording._parse_header(io.BytesIO(large))['provenance']['seed'] == kept


def _moved(document, frames, team, steps):
    """Returns the change of y of each player of `team` over `steps` steps, by index."""
    players = len(document['players'])
    start, end = frames[0], frames[steps]
    head = _recording._FRAME_HEAD.size
    moves = {}
    for k, entry in enumerate(document['players']):
        if entry['team'] != team:
            continue
        y0 = struct.unpack_from('<h', start, head + k * 196 + 2)[0]
        y1 = struct.unpack_from('<h', end, head + k * 196 + 2)[0]
        moves[entry['index']] = (y1 - y0) / _recording.POSITION_SCALE
    assert len(moves) == (5 if players == 10 else 11)
    return moves


@pytest.mark.parametrize(
    ('format', 'scenario', 'control', 'moving'),
    [
        ('5v5', '5_vs_5', {'players': 4, 'goalkeeper': 'agent'}, range(1, 5)),
        ('11v11', '11_vs_11_kaggle', {'players': 10, 'goalkeeper': 'builtin'}, range(1, 11)),
    ],
)
def test_team_controls_several_players(
    arena_home, tmp_path, monkeypatch, format, scenario, control, moving
):
    """Each controlled player follows its own action, and the goalkeeper stays with the AI."""
    _short_matches(monkeypatch, 60)
    team = _toy_team(tmp_path, 'several', _SEVERAL, format, control)
    path = cuju.arena.play(team['id'], 'builtin', scenario, seed=2)
    document, frames = _frames(path)
    moves = _moved(document, frames, 0, 60)
    # Odd players run up the pitch, towards positive y, and even players down.
    obeyed = [index for index in moving if (moves[index] > 1) == bool(index % 2)]
    assert len(obeyed) >= len(moving) - 1, moves
    assert all(abs(moves[index]) > 1 for index in obeyed)
    # Every frame marks the controlled players of the left team, and none of the right.
    head = _recording._FRAME_HEAD.size + len(document['players']) * 196
    marked = {
        k for frame in frames[1:] for k in range(len(document['players'])) if frame[head + k] & 8
    }
    assert {document['players'][k]['team'] for k in marked} == {0}


@pytest.mark.parametrize('reverse', [0, 1])
def test_recorded_engine_scorer_and_called_offside(monkeypatch, reverse):
    batch = cuju.BatchEnvironment('5_vs_5_l1_r0', environment_count=1, device='cpu')
    try:
        batch.reset(reverse=[reverse])
        reader = _recording.StateReader(10)
        state = batch.get_state(0)
        values = reader._values(state, 'pitch')
        values = {name: values[name].copy() for name in values.dtype.names}
        values.update(
            buffer_active=True,
            set_piece=3,
            foul_type=0,
            offside_count=2,
            offside_first=1,
            touch_team=0,
            scorer=7,
        )
        first = reader._order(values)[0][0]
        values['touch_first' if first == 0 else 'touch_second'] = 3
        monkeypatch.setattr(reader, '_values', lambda state, frame: values)
        frame = reader.frame(state)
        flags = struct.unpack_from('<i', frame, 28)[0]
        assert flags & 4
        assert (flags >> 8) & 31 == 8
        assert struct.unpack_from('<i', frame, 72)[0] == 3
    finally:
        batch.close()


@pytest.mark.parametrize(
    'duration,second,expected',
    [(3000, 1500, 1500), (400, 999999999, None), (100, 1500, None)],
)
def test_recording_scenario_timing(monkeypatch, duration, second, expected):
    monkeypatch.setattr(
        _recording._state, 'read', lambda state, fields: [duration, second, False, False, False]
    )
    timing = _recording.scenario_timing(b'')
    document = _recording._parse_header(
        io.BytesIO(_recording.header('Match', 'custom', [], timing=timing))
    )
    assert document['second_half'] == expected
    assert document['duration'] == duration
    assert document['full_match'] == (duration >= 3000)


@pytest.mark.parametrize('name,players', [('sample-11v11', 22), ('sample-5v5', 10)])
def test_bundled_samples_are_full_recordings(name, players):
    path = _library._SAMPLES / f'{name}.cjr'
    document = _recording.read_header(path)
    frames = list(_recording.frames(path))
    assert len(document['players']) == players
    assert len(frames) == document['frame_count'] == 3002
    assert all(len(frame) == document['frame_bytes'] for frame in frames)
    assert struct.unpack_from('<i', frames[-1], 28)[0] & 1
    assert document['title'].startswith('Sample match')
    left, right = document['teams']
    assert left['name'] != right['name'] and left['color'] != right['color']
    assert sum(struct.unpack_from('<2i', frames[-1], 8)) >= 2


# The SHA-256 of the frames of each shipped sample as version 1 stores them, which the samples of
# version 2 decode to bit for bit. tests/web/format.mjs checks the same digests on the page.
_SAMPLE_DIGESTS = {
    'sample-11v11': '518c377d7b32648e94e9fb9c33baa70fcf5ea9232be7d8c4bf368638fdd94076',
    'sample-5v5': 'b41e32efb5decbece8ea3a1ab949e2f1e62c8052e4929f7a1f429a938e6b0d27',
}


@pytest.mark.parametrize('name', sorted(_SAMPLE_DIGESTS))
def test_samples_of_version_2_decode_to_their_frames(name):
    path = _library._SAMPLES / f'{name}.cjr'
    document = _recording.read_header(path)
    assert document['version'] == 2
    assert document['keyframe_interval'] == 100
    frames = list(_recording.frames(path))
    assert hashlib.sha256(b''.join(frames)).hexdigest() == _SAMPLE_DIGESTS[name]
    head, _ = _sample_head(name, 0)
    one = len(gzip.compress(_recording.encode(head, frames, 1), 6))
    two = path.stat().st_size
    print(f'{name}: version 2 takes {two} bytes, version 1 {one}, {two / one:.0%}')
    assert two < 0.42 * one


def test_recording_versions_round_trip_and_seek(tmp_path):
    head, frames = _sample_head('sample-5v5', 250)
    for version in (1, 2):
        path = tmp_path / f'version{version}.cjr'
        _recording.write(path, head, frames, version)
        document = _recording.read_header(path)
        assert document['version'] == version
        assert document['frame_count'] == 250
        assert list(_recording.frames(path)) == frames
        assert list(_recording.frames(path, 99, 103)) == frames[99:103]
        assert list(_recording.frames(path, 249)) == frames[249:]
        assert list(_recording.frames(path, 300)) == []
    with pytest.raises(cuju.StateError):
        _recording.write(tmp_path / 'bad.cjr', head, frames, 4)


def test_recording_version_2_keeps_any_frame_bit_for_bit(tmp_path):
    """Frames of any bytes, such as joints of no length, decode exactly, wrapping where needed."""
    generator = np.random.default_rng(7)
    players = 10
    size = _recording.frame_bytes(players)
    frames = [generator.integers(0, 256, size, np.uint8).tobytes() for _ in range(230)]
    extremes = np.frombuffer(frames[5], np.uint8).copy()
    nodes = extremes[76 : 76 + players * 196].view('<i2')
    nodes[::2], nodes[1::2] = 32767, -32768
    frames[5] = extremes.tobytes()
    frames[6] = bytes(size)
    lineup = [
        {'team': k // 5, 'index': k % 5, 'goalkeeper': False, 'controlled': False}
        for k in range(players)
    ]
    head = _recording.header('Noise', 'custom', lineup, frame_count=len(frames))
    path = tmp_path / 'noise.cjr'
    _recording.write(path, head, frames)
    assert list(_recording.frames(path)) == frames
    assert list(_recording.frames(path, 150, 160)) == frames[150:160]


def test_recorder_writes_version_2_or_1_with_the_same_frames(arena_home):
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu', seed=5
    )
    two = cuju.arena.Recorder(batch)
    one = cuju.arena.Recorder(batch, version=1)
    with pytest.raises(cuju.errors.ArgumentError):
        cuju.arena.Recorder(batch, version=3)
    batch.reset()
    step = 0
    while not one.episodes:
        batch.step(torch.tensor([[12 if step % 3 else 5]], dtype=torch.int32))
        step += 1
    one.flush()
    two.flush()
    assert (two.version, one.version) == (2, 1)
    assert _recording.read_header(two.saved[0])['version'] == 2
    assert _recording.read_header(one.saved[0])['version'] == 1
    assert list(_recording.frames(two.saved[0])) == list(_recording.frames(one.saved[0]))
    summaries = {entry['id']: entry for entry in _library.matches()}
    assert summaries[two.saved[0].stem]['score'] == summaries[one.saved[0].stem]['score']
    assert summaries[two.saved[0].stem]['steps'] == summaries[one.saved[0].stem]['steps']
    batch.close()


def _shooting_episode(recorder_options=(), annotate=None):
    """Plays one scoring episode under a recorder, calling `annotate(recorder, step)` each step.

    Returns the recorder after its first save.
    """
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu', seed=11
    )
    recorder = cuju.arena.Recorder(batch, **dict(recorder_options))
    batch.reset()
    step = 0
    while not recorder.episodes:
        if annotate:
            annotate(recorder, step)
        batch.step(torch.tensor([[12 if step % 3 else 5]], dtype=torch.int32))
        step += 1
    batch.close()
    return recorder


def test_recorder_annotations_round_trip(arena_home):
    players = {}

    def annotate(recorder, step):
        lineup = recorder._lineup
        players['agent'] = next(k for k, entry in enumerate(lineup) if entry['controlled'])
        players['other'] = next(k for k, entry in enumerate(lineup) if not entry['controlled'])
        other = lineup[players['other']]
        # A number counts the left team first, and the file keeps the index of the header.
        order = sorted(range(len(lineup)), key=lambda k: (lineup[k]['team'], lineup[k]['index']))
        number = order.index(players['agent'])
        players['other_number'] = order.index(players['other'])
        recorder.annotate(
            values={'value': 0.5 + step / 100, 'reward': torch.tensor(0.0)},
            player=number,
            probabilities=torch.tensor([0.05] * 11 + [0.3, 0.15] + [0.05] * 6),
            action=12 if step % 3 else 5,
        )
        if step == 2:
            recorder.annotate(text='first shot', player=(other['team'], other['index']))
            recorder.annotate(values={'pressure': 1}, step=1, player=number)
            recorder.annotate(values={'reward': 0.25}, step=1, player=number)

    seen = []
    recorder = _shooting_episode(
        {'keep': lambda summary: seen.append(summary['annotations']) or True}, annotate
    )
    path = recorder.saved[0]
    records = _attachments.annotations(path)
    frames = list(_recording.frames(path))
    by_key = {(record['f'], record.get('p', -1)): record for record in records}
    agent = players['agent']
    assert by_key[(0, agent)]['v'] == {'value': 0.5, 'reward': 0}
    assert by_key[(0, agent)]['a'] == 5
    # The three most likely actions, the most likely first.
    assert by_key[(0, agent)]['k'] == [[11, 0.3], [12, 0.15], [0, 0.05]]
    # Annotations of one step and player join their numbers.
    assert by_key[(1, agent)]['v'] == {'value': 0.51, 'reward': 0.25, 'pressure': 1}
    assert by_key[(2, players['other'])]['t'] == 'first shot'
    assert [record['f'] for record in records] == sorted(record['f'] for record in records)
    # The filter read the same annotations with long keys and the step of each frame.
    (summary,) = seen
    assert len(summary) == len(records)
    note = next(item for item in summary if item['text'] == 'first shot')
    assert note['frame'] == 2 and note['step'] == struct.unpack_from('<i', frames[2])[0]
    # The summary numbers the player as annotate takes it, the left team first.
    assert note['player'] == players['other_number'] and note['values'] == {}
    assert note['probabilities'] == [] and note['action'] is None
    # A file without annotations reads none, and a file of version 1, which has no room for
    # sections, keeps them in its attachment as well.
    assert _attachments.annotations(_library._SAMPLES / 'sample-5v5.cjr') == []
    plain = _shooting_episode({'version': 1}, lambda recorder, step: recorder.annotate(text='x'))
    assert _recording.read_header(plain.saved[0])['version'] == 1
    assert {note['t'] for note in _attachments.annotations(plain.saved[0])} == {'x'}


@pytest.mark.parametrize('auto_reset', [True, False])
def test_annotation_of_the_step_just_played_reaches_the_ended_episode(arena_home, auto_reset):
    """The reward of the step that ends an episode lands in its file and in its summary."""
    seen = []
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0',
        environment_count=1,
        device='cpu',
        seed=11,
        auto_reset=auto_reset,
    )
    recorder = cuju.arena.Recorder(batch, keep=lambda summary: seen.append(summary) or True)
    batch.reset()
    step = 0
    while not recorder.episodes:
        _, reward, _, _ = batch.step(torch.tensor([[12 if step % 3 else 5]], dtype=torch.int32))
        recorder.annotate(values={'reward': reward[0, 0], f'step {step}': 1}, played=True)
        step += 1
    assert float(reward[0, 0]) == 1.0
    # The ended episode waits for the next call of the batch, and nothing has judged it yet.
    assert seen == [] and recorder.saved == [] and recorder.dropped == 0
    if auto_reset:
        # Without the word, an annotation after the ending step describes the new kick off.
        recorder.annotate(text='kick off')
        batch.step(torch.tensor([[0]], dtype=torch.int32))
    else:
        with pytest.raises(cuju.errors.ArgumentError, match='played=True'):
            recorder.annotate(text='the episode has no step yet')
        batch.reset_done()
    (summary,) = seen
    recorder.flush()
    frames = list(_recording.frames(recorder.saved[0]))
    last = len(frames) - 1
    records = {record['f']: record for record in _attachments.annotations(recorder.saved[0])}
    assert records[last]['v']['reward'] == 1 and f'step {step - 1}' in records[last]['v']
    assert all('t' not in record for record in records.values())
    assert summary['annotations'][-1]['frame'] == last
    assert summary['annotations'][-1]['values']['reward'] == 1
    assert summary['reward'] == 1 and summary['episode'] == 0
    # A step by its number names a frame of the ended episode as well, until it is handed over.
    assert struct.unpack_from('<i', frames[last])[0] == (step - 1 if auto_reset else step)
    path = recorder.save()
    assert _attachments.annotations(path) == ([{'f': 0, 't': 'kick off'}] if auto_reset else [])
    for options in ({'played': 1}, {'played': None}):
        with pytest.raises(cuju.errors.ArgumentError):
            recorder.annotate(text='x', **options)
    batch.close()


def test_an_ended_episode_reaches_the_writer_without_another_call(arena_home):
    """An episode that ended at the last step of a loop is saved by flush, by close and at the
    end of the recorder."""
    for finish in ('flush', 'close', 'drop', 'batch'):
        batch = cuju.BatchEnvironment(
            'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu', seed=11
        )
        recorder = cuju.arena.Recorder(batch)
        saved = recorder.saved
        batch.reset()
        step = 0
        while not recorder.episodes:
            batch.step(torch.tensor([[12 if step % 3 else 5]], dtype=torch.int32))
            step += 1
        assert saved == []
        if finish == 'flush':
            recorder.flush()
        elif finish == 'close':
            recorder.close()
        elif finish == 'drop':
            batch.remove_observer(recorder)
            del recorder
            gc.collect()
            _writer.WRITER.join()
        else:
            batch.close()
        assert len(saved) == (2 if finish == 'close' else 1), finish
        assert len(list(_recording.frames(saved[0]))) == step
        batch.close()


def test_moments_of_the_samples():
    from cuju.arena._moments import events as _events

    file = os.path.join(_SAMPLE_FOLDER, 'sample-11v11.cjr')
    header = _recording.read_header(file)
    found = _events.moments(header, _recording.frames(file), timing=True)
    goals = [moment for moment in found if moment['kind'] == 'goal']
    assert [goal['score'] for goal in goals] == [[1, 0], [1, 1]]
    assert [goal['team'] for goal in goals] == [0, 1]
    assert all(goal['data']['scorer'] == goal['player'] for goal in goals)
    assert all(goal['id'].startswith(f'goal:{goal["step"]}:') for goal in goals)
    penalty = next(m for m in found if m['kind'] == 'restart' and m['data']['restart'] == 'penalty')
    assert penalty['step'] < goals[0]['step']
    assert [moment['step'] for moment in found] == sorted(moment['step'] for moment in found)
    assert {moment['kind'] for moment in found} <= set(_events.KINDS)
    assert _events.event_id('goal', goals[0]['step'], ['team0']) == goals[0]['id']


def test_annotation_numbers_count_the_left_team_first():
    from cuju.arena._format import annotations as _annotations

    # The header of a match may list the right team first.
    lineup = [{'team': 1, 'index': i} for i in range(3)] + [
        {'team': 0, 'index': i} for i in range(3)
    ]
    assert [_annotations.player_index(lineup, number) for number in range(6)] == [3, 4, 5, 0, 1, 2]
    assert _annotations.player_index(lineup, (1, 2)) == 2
    with pytest.raises(cuju.errors.ArgumentError, match='from 0 to 5'):
        _annotations.player_index(lineup, 6)


def test_names_travel_as_labels_in_a_header(tmp_path):
    from cuju.arena._text import messages as _messages

    title = _messages.label(
        'server.names.against',
        left=_messages.label('server.names.you'),
        right=_messages.label('server.names.builtin'),
    )
    head = _recording.header(title, '5_vs_5', _names_lineup(), [{'name': 'Rule bot'}])
    document = json.loads(head[len(_recording.MAGIC) + 4 :])
    # The command line and every reader of before see the English text.
    assert document['title'] == "You against GRF's built-in AI"
    assert document['title_id'] == 'server.names.against'
    assert document['title_values']['left'] == _messages.label('server.names.you')
    left, right = document['teams']
    # A name that a person gave stays text, a side without a name takes the label of its side.
    assert left['name'] == 'Rule bot' and 'name_id' not in left
    assert right['name'] == 'Right' and right['name_id'] == 'server.names.right'
    assert _recording.label_of(document, 'title')['id'] == 'server.names.against'
    assert _recording.label_of(left, 'name') == 'Rule bot'
    # A title that a person gave stays text, and a header of before reads as its text.
    plain_head = _recording.header('My match', '5_vs_5', _names_lineup())
    plain = json.loads(plain_head[len(_recording.MAGIC) + 4 :])
    assert plain['title'] == 'My match' and 'title_id' not in plain
    assert _recording.label_of({'title': 'Left against Right'}, 'title') == 'Left against Right'
    unknown = {'title': 'x', 'title_id': 'no.such', 'title_values': {}}
    assert _recording.label_of(unknown, 'title') == 'x'


# The command line, the Python surface and HTTP answers.


def test_filter_summary_numbers_players_as_annotate_takes_them():
    """A header that lists the right team first still gives the filter the number given."""
    from cuju.arena._format import annotations as _annotations

    lineup = [{'team': team, 'index': index} for team in (1, 0) for index in range(5)]
    frames = [struct.pack('<i', 0) + bytes(60)]
    for number in (0, 4, 5, 9):
        record = _annotations.note(lineup, text='x', player=number)
        assert lineup[record['p']] == {'team': number // 5, 'index': number % 5}
        record['f'] = 0
        assert _annotations.public(record, frames, lineup)['player'] == number
    # Without the header the summary keeps the index of the file, as before.
    record = {**_annotations.note(lineup, text='x', player=0), 'f': 0}
    assert _annotations.public(record, frames)['player'] == 5


# The limits of the readers, which hold a hostile file to little memory and time.


def _crafted(path, document, *parts):
    """Writes a recording of a header and the bytes after it, as a hostile file may hold them."""
    text = json.dumps(document).encode()
    with gzip.open(path, 'wb', compresslevel=1) as stream:
        stream.write(_recording.MAGIC + struct.pack('<I', len(text)) + text)
        for part in parts:
            stream.write(part)
    return path


def _lineup(players):
    return [
        {'team': k % 2, 'index': k // 2, 'goalkeeper': False, 'controlled': False}
        for k in range(players)
    ]


def _coded(players, count, **fields):
    """Returns a header of version 2 of zero frames, and its frames section."""
    document = {
        'version': 2,
        'players': _lineup(players),
        'frame_bytes': _recording.frame_bytes(players),
        'frame_count': count,
        'keyframe_interval': 100,
        **fields,
    }
    size = _recording._codec.section_bytes(players, count)
    return document, _recording._section(_recording.FRAMES_TAG, bytes(size))


def test_reader_refuses_a_large_header_and_skips_unknown_sections(tmp_path, monkeypatch):
    path = tmp_path / 'header.cjr'
    with gzip.open(path, 'wb') as stream:
        stream.write(_recording.MAGIC + struct.pack('<I', 2 << 20) + b'[' + bytes(2 << 20))
    with pytest.raises(cuju.StateError, match='header of at most'):
        _recording.read_header(path)
    document, frames = _coded(2, 3)
    junk = struct.pack('<4sI', b'JUNK', 3 << 20) + bytes(3 << 20)
    path = _crafted(tmp_path / 'junk.cjr', document, junk, frames, junk)
    assert list(_recording.frames(path)) == [bytes(_recording.frame_bytes(2))] * 3
    # The bytes that a file decompresses to count against one budget, unknown sections too.
    monkeypatch.setattr(_recording, 'MAX_BYTES', 2 << 20)
    with pytest.raises(cuju.StateError, match='at most 2 MiB'):
        list(_recording.frames(path))
    with pytest.raises(cuju.StateError, match='at most 2 MiB'):
        _attachments.annotations(path)


@pytest.mark.parametrize(
    'fields',
    [
        {'frame_count': _recording.MAX_FRAMES + 1},
        {'keyframe_interval': 1},
        {'keyframe_interval': 10**30},
        {
            'players': _lineup(_recording.MAX_PLAYERS + 2),
            'frame_bytes': _recording.frame_bytes(_recording.MAX_PLAYERS + 2),
        },
        {'version': True},
        {'frame_bytes': 1},
    ],
)
def test_reader_refuses_frames_past_its_limits(tmp_path, fields):
    document, frames = _coded(2, 1)
    path = _crafted(tmp_path / 'bad.cjr', {**document, **fields}, frames)
    with pytest.raises(cuju.StateError):
        list(_recording.frames(path))


def test_reader_refuses_a_file_by_its_size_before_it_reads_it(tmp_path, monkeypatch):
    """A file larger than a recording may decompress to is refused without a read."""
    document, frames = _coded(2, 3)
    path = _crafted(tmp_path / 'fine.cjr', document, frames)
    assert len(list(_recording.frames(path))) == 3
    monkeypatch.setattr(_recording, 'MAX_BYTES', path.stat().st_size - 1)
    monkeypatch.setattr(_recording.gzip, 'open', lambda *_: pytest.fail('the file was opened'))
    for read in (
        _recording.read_header,
        _attachments.annotations,
        _recording.ending,
        lambda found: list(_recording.frames(found)),
    ):
        with pytest.raises(cuju.StateError, match='a recording file of at most 0 MiB, found one'):
            read(path)
    monkeypatch.undo()
    # A file of holes past the real limit takes no time to refuse, and names both sizes.
    huge = tmp_path / 'huge.cjr'
    with huge.open('wb') as stream:
        stream.truncate(_recording.MAX_BYTES + 1)
    started = time.monotonic()
    with pytest.raises(cuju.StateError, match='at most 256 MiB, found one of 257 MiB'):
        _recording.read_header(huge)
    assert time.monotonic() - started < 1
    # The library lists such a file by its name, as it lists any file it cannot read.
    assert _library._summarize(huge)['title'] == 'huge'


def test_reader_of_version_1_checks_the_frame_size(tmp_path, monkeypatch):
    size = _recording.frame_bytes(2)
    for value in (-1, None, True, 'x', 1.5, size + 4):
        document = {'version': 1, 'players': _lineup(2), 'frame_bytes': value}
        path = _crafted(tmp_path / 'size.cjr', document, bytes(5000))
        with pytest.raises(cuju.StateError, match='expected frames of'):
            _recording.read_header(path)
    # A last frame cut short is left out, as the page leaves it out.
    document = {'version': 1, 'players': _lineup(2), 'frame_bytes': size}
    path = _crafted(tmp_path / 'short.cjr', document, bytes(2 * size + 10))
    assert list(_recording.frames(path)) == [bytes(size)] * 2
    assert _recording.ending(path) == (2, bytes(size))
    monkeypatch.setattr(_recording, 'MAX_FRAMES', 1)
    with pytest.raises(cuju.StateError, match='at most 1 frames'):
        _recording.ending(path)


def test_version_2_decodes_in_chunks_to_the_same_frames():
    from cuju.arena._moments import events as _events

    path = _library._SAMPLES / 'sample-5v5.cjr'
    document = _recording.read_header(path)
    count = document['frame_count']
    with gzip.open(path, 'rb') as raw:
        stream = _recording._Budget(raw)
        _recording._parse_header(stream)
        data = _recording._sections(stream, (_recording.FRAMES_TAG,))[_recording.FRAMES_TAG]
    whole = [row.tobytes() for row in _recording._codec.decode(data, 10, count)]
    assert count > 2 * _recording._DECODE_FRAMES
    assert list(_recording.frames(path)) == whole
    assert list(_recording.frames(path, 950, 2050)) == whole[950:2050]
    assert _recording.ending(path) == (count, whole[-1])
    parsed = _events._frames(document, whole)
    single = _events._parse(document, whole)
    assert [(f.step, f.roots, f.touches) for f in parsed] == [
        (f.step, f.roots, f.touches) for f in single
    ]


def test_a_header_names_its_title_and_teams_by_the_names_of_cuju_alone():
    from cuju.arena._text import messages as _messages

    title = _messages.label(
        'server.names.against',
        left=_messages.label('server.names.you'),
        right=_messages.label('server.names.builtin'),
    )
    head = _recording.header(title, '5_vs_5', _names_lineup())
    document = _recording._parse_header(io.BytesIO(head))
    assert _recording.label_of(document, 'title') == title
    assert document['teams'][1]['name_id'] == 'server.names.right'
    # A label of any other message of the page, or with such a label among its values, is text.
    body_size = {
        'id': 'server.body_size',
        'text': 'expected a request body of at most 110 MB',
        'values': {},
    }
    for found in (
        {'title_id': 'server.body_size', 'title_values': {}},
        {
            'title_id': 'server.names.against',
            'title_values': {'left': body_size, 'right': 'x'},
        },
    ):
        crafted = json.loads(head[len(_recording.MAGIC) + 4 :])
        crafted.update(found)
        crafted['teams'][0].update(name='A', name_id='server.game.busy', name_values={})
        parsed = _recording.validate_header(crafted)
        assert 'title_id' not in parsed and 'title_values' not in parsed
        assert parsed['title'] == "You against GRF's built-in AI"
        assert 'name_id' not in parsed['teams'][0] and parsed['teams'][0]['name'] == 'A'
        assert parsed['teams'][1]['name_id'] == 'server.names.right'


def test_annotations_leave_out_records_of_no_frame():
    from cuju.arena._format import annotations as _annotations

    deep = b'{"records":' + b'[' * 100000 + b']' * 100000 + b'}'
    assert _annotations.decode(deep) == []
    assert _annotations.decode(b'\xff\xfe') == []
    records = _annotations.decode(
        b'{"records":[{"f":-3,"v":{}},{"f":true},{"f":1.0},{"f":"1"},5,{"f":1,"t":"kept"}]}'
    )
    assert records == [{'f': 1, 't': 'kept'}]
    frames = [struct.pack('<i', 77) + bytes(76)]
    assert _annotations.public({'f': -1, 'v': {}}, frames)['step'] is None
    assert _annotations.public({'f': 0, 'v': {}}, frames)['step'] == 77
