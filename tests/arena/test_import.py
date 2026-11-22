# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the import of GRF dumps, Kaggle episodes, action logs and recordings."""

import builtins
import json
import pickle

import numpy as np
import pytest

import cuju.dumps
from cuju.arena._format import frames as _frames
from cuju.arena._format import recording as _recording
from cuju.arena._import import core as _core
from cuju.arena._import import pickles as _pickles
from cuju.arena._store import library as _library
from cuju.arena._store import runs as _runs
from cuju.errors import ArgumentError
from support import imports as _files


def _refused(error_info, message_id):
    assert getattr(error_info.value, 'message_id', None) == message_id, str(error_info.value)


def _lineup_order(imported, native):
    """Returns the index in the native lineup of each player of the imported lineup."""
    return [
        next(
            index
            for index, other in enumerate(native.lineup)
            if (other['team'], other['index']) == (player['team'], player['index'])
        )
        for player in imported.lineup
    ]


# Exact imports.


@pytest.mark.parametrize('reverse', [0, 1])
def test_import_full_dump_resimulates_and_matches_every_step(arena_home, tmp_path, reverse):
    dump, native = _files.full_dump(tmp_path, steps=40, reverse=reverse)
    (entry,) = _cuju_import(dump)
    assert entry['source'] == 'grf_dump' and entry['fidelity'] == 'exact'
    assert entry['verified_steps'] == 40 and entry['first_difference'] is None
    assert entry['warnings'] == [] and entry['reason'] is None
    path = _library.path_of(entry['id'])
    # The frames, the lanes, the actions and the state of the kick off equal the native ones.
    assert list(_recording.frames(path)) == list(_recording.frames(native))
    found, expected = _recording.sections(path), _recording.sections(native)
    for tag in ('PLYR', 'ACTS', 'STAT'):
        assert found[tag] == expected[tag], tag
    header = _recording.read_header(path)
    assert header['fidelity'] == 'joints' and header['version'] == 2
    imported = header['provenance']['imported_from']
    assert imported['fidelity'] == 'exact' and imported['verified_steps'] == 40
    assert imported['source'] == 'grf_dump' and imported['file'] == dump.name
    assert len(imported['sha256']) == 64 and header['provenance']['source'] == 'import'
    assert (
        header['run_key']['state_digest']
        == _recording.read_header(native)['run_key']['state_digest']
    )
    assert _library.details(entry['id'])['folder'] == 'Imported'
    run = _runs.get(entry['run'])
    assert run['kind'] == 'import' and run['state'] == 'done'
    assert run['episodes'][0]['recording'] == entry['id']
    assert run['aggregates'] == {'recordings': 1, 'exact': 1, 'positions': 0}


def _cuju_import(*paths, **options):
    return _core.add_recordings(*paths, **options)


def test_import_goal_dump_is_track_only(arena_home, tmp_path):
    dump = _files.goal_dump(tmp_path)
    steps = _pickles.steps(dump.read_bytes())
    assert steps[0]['debug']['frame_cnt'] > 0 and 'config' in steps[0]['debug']
    (entry,) = _cuju_import(dump)
    assert entry['fidelity'] == 'positions'
    assert entry['reason']['id'] == 'server.import.reason.mid_episode'
    path = _library.path_of(entry['id'])
    header = _recording.read_header(path)
    assert header['version'] == 3 and header['fidelity'] == 'positions'
    assert header['provenance']['imported_from']['fidelity'] == 'positions'
    assert header['provenance']['imported_from']['reason']['id'] == entry['reason']['id']
    assert 'engine_scorer' not in header
    with pytest.raises(cuju.errors.StateError):
        next(_recording.frames(path))
    found = _frames.read(path)
    assert len(found) == len(steps) and found.joints is None
    assert found.step[0] == steps[0]['debug']['frame_cnt'] + 1
    # The last step scored, and the frame of the pitch holds the ball and the players in meters.
    assert tuple(found.score[-1]) == (1, 0)
    raw = steps[-1]['observation']
    assert np.allclose(found.ball[-1][:2], raw['ball'][:2] * np.array([54.4, -83.6]), atol=1e-4)
    assert np.allclose(
        found.positions[-1][:, :2],
        np.concatenate([raw['left_team'], raw['right_team']]) * np.array([54.4, -83.6]),
        atol=0.002,
    )
    assert (found.touches == 0).all() and found.players is not None
    assert found.actions is not None and found.actions.shape == (len(steps) + 1, 1)


def test_positions_of_a_dump_keep_the_native_lanes(arena_home, tmp_path):
    dump, native = _files.full_dump(tmp_path, steps=30, reverse=1)
    (entry,) = _cuju_import(dump, overrides=['import.fidelity=positions'])
    assert entry['reason']['id'] == 'server.import.reason.chosen'
    found, expected = _frames.read(entry['path']), _frames.read(native)
    order = _lineup_order(found, expected)
    for name in ('direction', 'tired', 'sticky', 'agent', 'role'):
        assert np.array_equal(found.players[name], expected.players[name][1:, order]), name
    assert np.array_equal(found.players['ball_momentum'], expected.players['ball_momentum'][1:])
    # The designated player of each team names the same player in each lineup.
    for row, other in zip(
        found.players['designated'], expected.players['designated'][1:], strict=True
    ):
        for side in range(2):
            mine = found.lineup[row[side] - 1]
            theirs = expected.lineup[other[side] - 1]
            assert (mine['team'], mine['index']) == (theirs['team'], theirs['index'])
    assert np.array_equal(found.actions[:-1], expected.actions[1:-1])
    assert np.array_equal(found.score, expected.score[1:])
    assert np.allclose(found.ball, expected.ball[1:], atol=1e-5)
    assert np.allclose(found.positions[..., :2], expected.positions[1:, order, :2], atol=0.2)


def test_tampered_step_is_named_as_the_first_difference(arena_home, tmp_path):
    dump, _ = _files.full_dump(tmp_path, steps=30)
    steps = _pickles.steps(dump.read_bytes())
    steps[12]['observation']['ball'][0] += 1e-6
    tampered = _files.write_dump(tmp_path / 'tampered.dump', steps)
    (entry,) = _cuju_import(tampered)
    assert entry['fidelity'] == 'positions' and entry['verified_steps'] == 12
    difference = entry['first_difference']
    assert difference['step'] == 12 and difference['field'] == 'observation.ball'
    assert entry['reason'] == {
        'id': 'server.import.reason.difference',
        'text': 'the match played again differs from the file at step 12, in observation.ball',
        'values': {'step': '12', 'field': 'observation.ball'},
    }
    assert _recording.read_header(entry['path'])['version'] == 3
    # The setting exact refuses such a file in place of keeping its positions.
    with pytest.raises(ArgumentError) as refused:
        _cuju_import(tampered, overrides=['import.fidelity=exact'])
    _refused(refused, 'server.import.not_exact')


def test_dump_without_observations_is_never_exact(arena_home, tmp_path):
    dump, _ = _files.full_dump(tmp_path, steps=15)
    steps = _pickles.steps(dump.read_bytes())
    del steps[4]['observation']
    (entry,) = _cuju_import(_files.write_dump(tmp_path / 'gap.dump', steps))
    assert entry['fidelity'] == 'positions' and entry['verified_steps'] == 0
    assert entry['reason']['id'] == 'server.import.reason.unobserved'
    assert len(_frames.read(entry['path'])) == 14
    for step in steps:
        step.pop('observation', None)
    with pytest.raises(ArgumentError) as refused:
        _cuju_import(_files.write_dump(tmp_path / 'blind.dump', steps))
    _refused(refused, 'server.import.no_observations')


def test_rewritten_dump_still_imports_exact(arena_home, tmp_path):
    dump, native = _files.full_dump(tmp_path, steps=20)
    again = _files.write_dump(tmp_path / 'again.dump', _pickles.steps(dump.read_bytes()))
    (entry,) = _cuju_import(again)
    assert entry['fidelity'] == 'exact'
    assert list(_recording.frames(entry['path'])) == list(_recording.frames(native))


def test_truncated_dump_falls_back_to_positions(arena_home, tmp_path):
    dump, _ = _files.full_dump(tmp_path, steps=25)
    data = dump.read_bytes()
    cut = tmp_path / 'cut.dump'
    cut.write_bytes(data[: len(data) - 300])
    (entry,) = _cuju_import(cut)
    assert entry['fidelity'] == 'positions'
    assert entry['reason']['id'] == 'server.import.reason.truncated'
    assert [warning['id'] for warning in entry['warnings']] == ['server.import.warning.truncated']
    whole = int(entry['reason']['values']['steps'])
    assert 0 < whole < 25
    assert len(_frames.read(entry['path'])) == whole


# The reader of dumps.


class _Hostile:
    def __reduce__(self):
        return builtins.exec, ("open('ran.txt', 'w').write('ran')",)


def test_import_refuses_foreign_pickles(arena_home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    hostile = tmp_path / 'hostile.dump'
    hostile.write_bytes(pickle.dumps({'debug': {'frame_cnt': 0}, 'x': _Hostile()}, protocol=4))
    assert _core.kind_of(hostile) == 'grf_dump'
    with pytest.raises(ArgumentError) as refused:
        _cuju_import(hostile)
    _refused(refused, 'server.import.refused')
    assert 'builtins.exec' in str(refused.value)
    assert not (tmp_path / 'ran.txt').exists()
    # cuju's own reader refuses it under the same rule.
    with pytest.raises(pickle.UnpicklingError):
        cuju.dumps.read_dump(str(hostile))
    assert not (tmp_path / 'ran.txt').exists()
    # A pickle that repeats one list into an enormous value is refused by its count of values.
    repeated = pickle.dumps(_nested(40), protocol=4)
    with pytest.raises(_pickles.RefusedError):
        _pickles.steps(repeated)
    # Python 2 pickles of protocols 0 and 1 name their classes through copyreg.
    for protocol in (0, 1):
        with pytest.raises(_pickles.RefusedError):
            _pickles.steps(pickle.dumps({'x': _Hostile()}, protocol=protocol))
    assert _list_of_records(arena_home) == []


def _nested(depth):
    value = [0] * 8
    for _ in range(depth):
        value = [value, value]
    return {'debug': value}


def _list_of_records(arena_home):
    return sorted(path.name for path in (arena_home / 'matches').glob('*.cjr'))


def test_reader_reads_numpy_pickles_of_either_version(tmp_path):
    entry = {
        'debug': {'frame_cnt': 0, 'action': []},
        'observation': {'ball': np.array([0.5, -0.0, 0.1]), 'roles': np.arange(3)},
        'reward': np.float64(1.0),
    }
    for protocol in (2, 4, 5):
        (found,) = _pickles.steps(pickle.dumps(entry, protocol=protocol))
        ball = found['observation']['ball']
        assert ball.dtype == np.float64 and ball.tolist() == [0.5, -0.0, 0.1]
        assert np.signbit(ball[1]) and found['reward'] == 1.0
        assert found['observation']['roles'].tolist() == [0, 1, 2]


# Kaggle episodes.


def test_kaggle_right_agent_flips_back(arena_home, tmp_path):
    episode, native = _files.kaggle_episode(tmp_path / 'episode.json', steps=20)
    (entry,) = _cuju_import(episode)
    assert entry['source'] == 'kaggle' and entry['fidelity'] == 'positions'
    assert entry['reason']['id'] == 'server.import.reason.kaggle' and entry['warnings'] == []
    found, expected = _frames.read(entry['path']), _frames.read(native)
    assert len(found) == len(expected) == 21
    assert found.header['teams'][0]['name'] == 'Greens'
    order = _lineup_order(found, expected)
    assert np.array_equal(found.score, expected.score)
    assert np.allclose(found.ball, expected.ball, atol=1e-5)
    assert np.array_equal(found.players['agent'], expected.players['agent'][:, order])
    assert np.array_equal(found.players['sticky'], expected.players['sticky'][:, order])
    assert np.array_equal(found.actions[1:-1], expected.actions[1:-1])
    # Without the left agent's view, the right agent's view turned back gives the same frames.
    document = json.loads(episode.read_text(encoding='utf-8'))
    for step in document['steps']:
        step[0]['observation']['players_raw'] = []
    alone = tmp_path / 'right-alone.json'
    alone.write_text(json.dumps(document), encoding='utf-8')
    (turned,) = _cuju_import(alone)
    assert [warning['id'] for warning in turned['warnings']] == [
        'server.import.warning.turned_back'
    ]
    again = _frames.read(turned['path'])
    assert np.array_equal(again.positions, found.positions)
    assert np.array_equal(again.ball, found.ball)
    assert np.array_equal(again.owner, found.owner) and np.array_equal(again.score, found.score)
    right = [index for index, player in enumerate(found.lineup) if player['team'] == 1]
    for name in ('direction', 'tired', 'role'):
        assert np.array_equal(again.players[name], found.players[name]), name
    assert np.array_equal(again.players['sticky'][:, right], found.players['sticky'][:, right])
    # A Kaggle episode cannot play again, so the setting exact refuses it.
    with pytest.raises(ArgumentError) as refused:
        _cuju_import(episode, overrides=['import.fidelity=exact'])
    _refused(refused, 'server.import.not_exact')


def test_kaggle_files_that_do_not_fit_are_refused(arena_home, tmp_path):
    for content in ('{"steps": 3}', '{"steps": [[{"observation": {}}]]}', '{"steps": [[]]}'):
        path = tmp_path / 'bad.json'
        path.write_text(content, encoding='utf-8')
        with pytest.raises(ArgumentError) as refused:
            _cuju_import(path)
        _refused(refused, 'server.import.kaggle')


# Action logs and recordings.


def test_action_log_round_trip(arena_home, tmp_path):
    log, native = _files.action_log(tmp_path / 'run.zip', steps=30)
    assert _core.kind_of(log) == 'action_log'
    (entry,) = _cuju_import(log)
    assert entry['source'] == 'action_log' and entry['fidelity'] == 'exact'
    assert entry['first_difference'] is None and entry['verified_steps'] == 30
    assert list(_recording.frames(entry['path'])) == list(_recording.frames(native))
    # A log whose digests do not fit its outputs imports nothing.
    import zipfile

    tampered = tmp_path / 'tampered.zip'
    with zipfile.ZipFile(log) as source, zipfile.ZipFile(tampered, 'w') as target:
        for name in source.namelist():
            data = source.read(name)
            if name == 'digests.bin':
                data = data[:64] + bytes([data[64] ^ 1]) + data[65:]
            target.writestr(name, data)
    with pytest.raises(ArgumentError) as refused:
        _cuju_import(tampered)
    _refused(refused, 'server.import.log_differs')


def test_recording_file_imports_as_it_is(arena_home, tmp_path):
    _, native = _files.full_dump(tmp_path, steps=10)
    copy = tmp_path / 'copy.cjr'
    copy.write_bytes(native.read_bytes())
    (entry,) = _cuju_import(copy)
    assert entry['source'] == 'cjr' and entry['fidelity'] == 'exact'
    assert entry['verified_steps'] is None and entry['id'] == native.stem


def test_import_caps_and_kinds(arena_home, tmp_path):
    dump, native = _files.full_dump(tmp_path, steps=5)
    with pytest.raises(ArgumentError) as refused:
        _cuju_import(dump, overrides=['import.max_bytes=100'])
    _refused(refused, 'server.import.size')
    with pytest.raises(ArgumentError) as refused:
        _cuju_import(dump, dump, overrides=['import.max_files=1'])
    _refused(refused, 'server.import.count')
    other = tmp_path / 'notes.txt'
    other.write_text('nothing to import', encoding='utf-8')
    with pytest.raises(ArgumentError) as refused:
        _cuju_import(other)
    _refused(refused, 'server.import.kind')
    with pytest.raises(ArgumentError) as refused:
        _cuju_import(tmp_path / 'missing.dump')
    _refused(refused, 'server.import.missing')
    assert _list_of_records(arena_home) == [native.name]
