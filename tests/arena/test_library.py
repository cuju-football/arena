# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the home folder, the library of recordings, its Trash and the samples."""

import json
import logging
import os
import struct
import threading
import time

import pytest
import torch

import cuju
import cuju.errors
import cuju.state
from cuju.arena._format import recording as _recording
from cuju.arena._store import home as _home
from cuju.arena._store import library as _library
from support.common import (
    _SINGLE,
    _filtered_episodes,
    _goals,
    _library_ids,
    _names_lineup,
    _one_second,
    _sample_head,
    _short_tasks,
    _toy_team,
)


def test_recording_details_and_trash(arena_home):
    _library.add_samples()
    path = _library.path_of('sample-5v5')
    original = path.read_bytes()
    entry = _library.update(
        path.stem, folder='League/Week 1', favorite=True, tags=['final', 'final '], notes='Goal'
    )
    assert (entry['tags'], entry['favorite'], entry['notes']) == (['final'], True, 'Goal')
    assert _library.details(path.stem) == entry
    # Only a recording of the Trash is deleted for good.
    assert not _library.purge(path.stem)
    assert not _library.trash(path.stem, restore=True)
    assert _library.trash(path.stem)
    assert not _library.trash(path.stem)
    assert _library.path_of(path.stem) is None
    assert _library.path_of(path.stem, include_trash=True) == path
    assert path.stem not in _library_ids()
    assert [item['id'] for item in _library.matches(trash=True)] == [path.stem]
    assert _library.matches(trash=True)[0]['notes'] == 'Goal'
    for refused in (_library.details, lambda name: _library.update(name, title='Other')):
        with pytest.raises(cuju.ArgumentError):
            refused(path.stem)
    assert _library.trash(path.stem, restore=True)
    assert _library.path_of(path.stem).read_bytes() == original
    restored = _library.details(path.stem)
    assert (restored['folder'], restored['tags']) == ('League/Week 1', ['final'])
    assert 'deleted_at' not in restored
    for changes in (
        {'favorite': 1},
        {'tags': 'final'},
        {'tags': [' ']},
        {'tags': ['x' * 41]},
        {'tags': [str(index) for index in range(21)]},
        {'notes': []},
        {'notes': 'x' * 10001},
        {'notes': 'kept', 'title': ' '},
    ):
        with pytest.raises(cuju.ArgumentError):
            _library.update(path.stem, **changes)
    assert _library.details(path.stem)['notes'] == 'Goal'
    assert path.read_bytes() == original
    assert _library.trash(path.stem)
    assert _library.purge(path.stem)
    assert not path.exists()
    assert not path.with_suffix('.json').exists()
    for unsafe in ('../sample-11v11', '..', 'Sample-11v11', ''):
        assert not _library.trash(unsafe)
        assert not _library.purge(unsafe)
    assert _library.path_of('sample-11v11') is not None


def test_recording_summary_fields_of_the_wrong_type_fall_back(arena_home):
    _library.add_samples(['sample-5v5'])
    summary = _home.folder('matches') / 'sample-5v5.json'
    stored = json.loads(summary.read_text(encoding='utf-8'))
    stored.update(favorite='yes', tags=['kept', 3], notes=None, provenance=[], folder=7)
    stored['deleted_at'] = 12
    summary.write_text(json.dumps(stored), encoding='utf-8')
    entry = _library.matches()[0]
    assert (entry['favorite'], entry['tags'], entry['notes']) == (False, ['kept'], '')
    assert (entry['provenance'], entry['folder']) == ({}, '')
    assert 'deleted_at' not in entry


def test_recording_identifiers_are_unique_within_a_second(arena_home, monkeypatch):
    head, frames = _sample_head('sample-5v5', 3)
    _one_second(monkeypatch)
    saved = []

    def save_many():
        for _ in range(10):
            saved.append(_library.save(head, frames).stem)

    workers = [threading.Thread(target=save_many) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(60)
    assert len(saved) == len(set(saved)) == 40
    assert sorted(saved) == _library_ids()
    for name in saved:
        assert list(_recording.frames(_library.path_of(name))) == frames
        assert _library.details(name)['steps'] == 3


def test_recorder_keeps_episodes_with_goals(arena_home):
    episodes, kept, _, recorder = _filtered_episodes(keep='goals')
    expected = [frames for frames in episodes if _goals(frames)]
    assert 0 < len(expected) < len(episodes)
    assert kept == expected
    assert recorder.dropped == len(episodes) - len(expected)
    # A dropped episode writes no file, so the library holds the saved ones alone.
    assert len(_library.matches()) == len(episodes) + len(kept)


def test_recorder_writes_on_a_thread_and_reports_failures(arena_home, monkeypatch, caplog):
    """The call after the step that ends an episode hands its frames over, and a failed write
    reports itself."""
    written = _library.save
    gate = threading.Event()

    def slow(*arguments):
        gate.wait(30)
        return written(*arguments)

    monkeypatch.setattr(_library, 'save', slow)
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu', seed=11
    )
    recorder = cuju.arena.Recorder(batch)
    batch.reset()
    step = 0
    while not recorder.episodes:
        batch.step(torch.tensor([[12 if step % 3 else 5]], dtype=torch.int32))
        step += 1
    # The ended episode waits for the next call of the batch, which hands it to the writer.
    assert recorder.saved == [] and recorder._pending == 0
    batch.step(torch.tensor([[12 if step % 3 else 5]], dtype=torch.int32))
    step += 1
    # The writer waits at the gate, so the episode ended and training stepped on without it.
    assert recorder.saved == [] and recorder._pending == 1
    gate.set()
    recorder.flush()
    assert len(recorder.saved) == 1 and recorder.saved[0].is_file()
    assert _library.details(recorder.saved[0].stem)['steps'] > 0

    def failing(*arguments):
        raise OSError('the disk is full')

    monkeypatch.setattr(_library, 'save', failing)
    with caplog.at_level(logging.WARNING, logger='cuju'):
        while recorder.episodes < 2:
            batch.step(torch.tensor([[12 if step % 3 else 5]], dtype=torch.int32))
            step += 1
        with pytest.raises(OSError, match='the disk is full'):
            recorder.flush()
    assert 'could not write' in caplog.text and 'the disk is full' in caplog.text
    assert len(recorder.saved) == 1 and len(_library.matches()) == 1
    assert not list((arena_home / 'matches').glob('*.part'))
    with pytest.raises(OSError, match='the disk is full'):
        recorder.save()
    recorder.flush()
    # Closing the batch waits for the recordings handed over.
    monkeypatch.setattr(_library, 'save', slow)
    gate.clear()
    while recorder.episodes < 3:
        batch.step(torch.tensor([[12 if step % 3 else 5]], dtype=torch.int32))
        step += 1
    threading.Timer(0.5, gate.set).start()
    batch.close()
    assert recorder._pending == 0 and len(recorder.saved) == 2


def _played(outcome):
    """Returns a run's result without what names its recordings."""
    attempts = [
        {key: value for key, value in item.items() if key != 'match'}
        for item in outcome['attempts']
    ]
    return {
        **{key: value for key, value in outcome.items() if key != 'group'},
        'attempts': attempts,
    }


def test_benchmark_runs_group_their_recordings(arena_home, tmp_path, monkeypatch, capsys):
    from cuju.arena import __main__ as command
    from cuju.arena._evaluate import academy as _academy

    team = _toy_team(tmp_path, 'toy', _SINGLE, '11v11')
    _short_tasks(monkeypatch, 6)
    outcome = _academy.run('empty_goal_close', team['id'])
    group = outcome['group']
    members = cuju.arena.recordings(group=group)
    assert [member['id'] for member in members] == [r['match'] for r in outcome['attempts']]
    for index, member in enumerate(members):
        fields = member['group']
        assert (fields['id'], fields['attempt'], fields['attempts']) == (group, index, 5)
        assert fields['kind'] == 'benchmark' and fields['task'] == 'empty_goal_close'
        assert fields['state'] == 'done' and fields['title_id'] == 'server.names.benchmark_run'
        assert fields['title'] == 'academy_empty_goal_close, toy'
        assert fields['title_values']['task']['kind'] == 'scenario'
        assert fields['result_id'] == 'server.names.run_attempts'
        assert fields['result'] == f'{outcome["value"]} of 5'
        assert fields['value'] == outcome['value'] and fields['competitor'] == 'toy'
    # Every summary of the library names its group, and another run makes a group of its own.
    listed = {entry['id']: entry for entry in cuju.arena.recordings()}
    assert all(listed[member['id']]['group']['id'] == group for member in members)
    other = _academy.run('corner', 'builtin')
    assert other['group'] != group and len(cuju.arena.recordings(group=other['group'])) == 5
    other_fields = cuju.arena.recordings(group=other['group'])[0]['group']
    assert other_fields['competitor_id'] == 'server.names.builtin'
    # Deleting a group moves all its recordings to the Trash at once, each restorable.
    ids = [member['id'] for member in members]
    assert sorted(cuju.arena.delete_group(group)) == sorted(ids)
    assert cuju.arena.recordings(group=group) == []
    assert [entry['id'] for entry in cuju.arena.recordings(group=group, trash=True)] == ids
    assert cuju.arena.delete_group(group) == [] and cuju.arena.delete_group('g-none') == []
    assert _library.trash(ids[0], restore=True)
    assert cuju.arena.restore_group(group) == ids[1:]
    assert [entry['id'] for entry in cuju.arena.recordings(group=group)] == ids
    # The command line lists recordings of a group and moves the group to the Trash and back.
    assert command.main(['recording', 'list', '--group', group]) == 0
    rows = [line.split('  ') for line in capsys.readouterr().out.splitlines()]
    assert [row[0] for row in rows] == ids
    assert all(len(row) == 5 for row in rows)
    assert command.main(['recording', 'list', '--group', group, '--json']) == 0
    assert [entry['id'] for entry in json.loads(capsys.readouterr().out)['recordings']] == ids
    assert command.main(['recording', 'remove', '--group', group]) == 0
    assert '5 recordings moved' in capsys.readouterr().out
    assert command.main(['recording', 'restore', '--group', group, '--json']) == 0
    assert sorted(json.loads(capsys.readouterr().out)['restored']) == sorted(ids)
    assert command.main(['recording', 'list', '--group', 'g-none', '--json']) == 0
    empty = capsys.readouterr()
    assert json.loads(empty.out)['recordings'] == []
    assert 'cuju arena recording add PATH' in empty.err
    # A group of a hand edited summary that does not fit is left out, and the rest stays.
    path = arena_home / 'matches' / f'{ids[0]}.json'
    summary = json.loads(path.read_text(encoding='utf-8'))
    summary['group'] = {'id': '../evil', 'title': 'x'}
    path.write_text(json.dumps(summary), encoding='utf-8')
    assert 'group' not in _library.details(ids[0])
    summary['group'] = {'id': group, 'attempt': 'first', 'value': True, 'title': 7, 'extra': 1}
    path.write_text(json.dumps(summary), encoding='utf-8')
    assert _library.details(ids[0])['group'] == {
        'id': group,
        'title': group,
        'competitor': '',
        'result': '',
    }


def test_a_run_without_recordings_scores_the_same(arena_home, tmp_path, monkeypatch):
    from cuju.arena._evaluate import academy as _academy

    team = _toy_team(tmp_path, 'toy', _SINGLE, '11v11')
    _short_tasks(monkeypatch, 8)
    kept = _academy.run('corner', team['id'])
    before = {entry['id'] for entry in cuju.arena.recordings()}
    assert len(before) == 5 and kept['group']
    bare = _academy.run('corner', team['id'], keep_recordings=False)
    assert bare['group'] is None and all(r['match'] is None for r in bare['attempts'])
    assert _played(bare) == _played(kept)
    assert {entry['id'] for entry in cuju.arena.recordings()} == before
    assert cuju.arena.recordings(trash=True) == []
    with pytest.raises(cuju.errors.ArgumentError, match='keep_recordings'):
        _academy.run('corner', 'builtin', keep_recordings='no')


def test_moment_index_follows_the_library(arena_home, monkeypatch):
    from cuju.arena._moments import search as _search

    _library.add_samples()
    # A recording is indexed in the background as it is saved, so the pass finds both read.
    _search.wait()
    assert _search.sync() == {'recordings': 2, 'built': 0, 'removed': 0, 'failed': 0}
    _search._CACHES.clear()
    for path in _home.folder('index').iterdir():
        path.unlink()
    assert _search.sync() == {'recordings': 2, 'built': 2, 'removed': 0, 'failed': 0}
    assert _search.sync()['built'] == 0
    assert _search.search('goal')['total'] == 4
    index = _home.folder('index')
    assert sorted(path.name for path in index.iterdir()) == ['sample-11v11.json', 'sample-5v5.json']
    # A recording in the Trash leaves the results and keeps its index, a purge removes both.
    assert _library.trash('sample-5v5')
    found = _search.search('goal')
    assert found['total'] == 2 and found['recordings'] == 1
    assert _search.sync()['built'] == 0 and (index / 'sample-5v5.json').is_file()
    assert _library.trash('sample-5v5', restore=True)
    assert _search.search('goal')['total'] == 4
    assert _library.trash('sample-5v5') and _library.purge('sample-5v5')
    assert _search.sync()['removed'] == 1
    assert not (index / 'sample-5v5.json').exists()
    # A recording that arrives is read once, a broken one keeps an empty entry.
    assert _library.add_samples(['sample-5v5'])
    _search.wait()
    broken = _home.folder('matches') / 'm-broken.cjr'
    broken.write_bytes(b'not a recording')
    done = _search.sync()
    assert done['built'] == 1 and done['failed'] == 1
    assert _search.sync()['built'] == 0
    assert _search.search('goal')['total'] == 4
    # A new version of the moments rebuilds every file, from disk as from memory.
    _search._CACHES.clear()
    assert _search.sync()['built'] == 0
    monkeypatch.setattr(_search, 'VERSION', 'other')
    assert _search.sync()['built'] == 3


def test_samples_date_from_their_files_and_list_five_a_side_first(arena_home):
    assert _library.samples() == ['sample-5v5', 'sample-11v11']
    _library.add_samples()
    dates = {entry['id']: entry['created'] for entry in _library.matches()}
    shipped = time.localtime((_library._SAMPLES / 'sample-5v5.cjr').stat().st_mtime)
    assert dates['sample-5v5'] == time.strftime('%Y-%m-%dT%H:%M:%S', shipped)


def test_names_reach_the_library_summary_and_a_new_title_drops_them(arena_home):
    from cuju.arena._text import messages as _messages

    title = _messages.label('server.names.demo')
    head = _recording.header(title, '5_vs_5', _names_lineup())
    frame = bytes(_recording.frame_bytes(2))
    path = _library.save(head, [frame])
    summary = _library.details(path.stem)
    assert summary['title'] == 'Demo, the built-in AI against itself'
    assert summary['title_id'] == 'server.names.demo' and summary['title_values'] == {}
    assert summary['teams'] == ['Left', 'Right']
    assert [label['id'] for label in summary['team_labels']] == [
        'server.names.left',
        'server.names.right',
    ]
    # The stored summary keeps the label, so a list read from the files shows it again.
    stored = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
    assert stored['title_id'] == 'server.names.demo'
    renamed = _library.update(path.stem, title='Our demo')
    assert renamed['title'] == 'Our demo' and 'title_id' not in renamed
    assert 'title_id' not in _library.details(path.stem)
    # A summary made again from the header keeps the labels too.
    path.with_suffix('.json').unlink()
    assert _library.details(path.stem)['title_id'] == 'server.names.demo'


def test_names_of_the_samples_come_by_their_name(arena_home):
    _library.add_samples(['sample-5v5'])
    summary = _library.details('sample-5v5')
    assert summary['title'] == 'Sample match, 5 vs 5'
    assert summary['title_id'] == 'server.names.sample_5v5'
    # The teams of a sample have names of their own, which stay text.
    assert summary['team_labels'] == [None, None]
    _library.update('sample-5v5', title='Five')
    assert 'title_id' not in _library.details('sample-5v5')


def _header_file(path, **fields):
    """Writes a recording of version 1 of one frame and two players, with fields of its header."""
    import gzip
    import struct

    size = _recording.frame_bytes(2)
    document = {
        'version': 1,
        'title': 'fine',
        'scenario': 's',
        'teams': [{'name': 'A'}, {'name': 'B'}],
        'players': [{'team': side, 'index': 0, 'goalkeeper': True} for side in (0, 1)],
        'frame_bytes': size,
        **fields,
    }
    text = json.dumps(document).encode()
    with gzip.open(path, 'wb') as stream:
        stream.write(_recording.MAGIC + struct.pack('<I', len(text)) + text + bytes(size))
    return path


@pytest.mark.parametrize(
    'fields',
    [
        {'title': 5},
        {'title': [1]},
        {'scenario': {'x': 1}},
        {'teams': [{'name': {'a': 1}}, {'name': 7}]},
        {'teams': [{'name': 'A', 'id': [1]}, {}]},
        {'teams': 'AB'},
        {'players': [{'team': 0}, {'team': 1}]},
        {'players': [{'team': 2, 'index': 0}, {'team': True, 'index': 0}]},
        {'players': [{'team': 0, 'index': 0, 'goalkeeper': 1}, {'team': 1, 'index': 0}]},
        {'players': ['a', 'b']},
        {'provenance': [1]},
        {'steps_per_second': 0},
        {'steps_per_second': True},
        {'second_half': 'x'},
    ],
)
def test_header_fields_of_the_wrong_type_are_refused(arena_home, tmp_path, fields):
    from cuju.arena._moments import search as _search

    path = _header_file(tmp_path / 'bad.cjr', **fields)
    with pytest.raises(cuju.errors.StateError, match='expected'):
        _recording.read_header(path)
    # An import says what is wrong, without a traceback of the reader.
    with pytest.raises(cuju.ArgumentError, match='other bytes'):
        _library.import_file(path)
    assert _library.matches() == []
    # The same file copied into the folder lists under its identifier and searches as none.
    copied = _home.folder('matches') / 'm-copied.cjr'
    copied.write_bytes(path.read_bytes())
    (entry,) = _library.matches()
    assert entry['title'] == 'm-copied' and entry['teams'] == ['', '']
    found = _search.search('')
    assert found['recordings'] == 1 and found['total'] == 0


def test_a_header_without_its_optional_fields_imports(arena_home, tmp_path):
    path = _header_file(tmp_path / 'bare.cjr', title=None, scenario=None, teams=None)
    identifier = _library.import_file(path)
    entry = _library.details(identifier)
    assert (entry['title'], entry['scenario'], entry['teams']) == (identifier, '', [None, None])
    assert entry['steps'] == 1


def test_readers_take_fields_of_the_wrong_type_of_an_older_install(arena_home):
    from cuju.arena._moments import captions as _captions
    from cuju.arena._moments import search as _search

    (path,) = _library.add_samples(['sample-5v5'])
    _search.sync()
    summary = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
    summary.update(title={'text': 1}, scenario={'a': 1}, teams=[{}, 7], created=5)
    path.with_suffix('.json').write_text(json.dumps(summary), encoding='utf-8')
    index = _home.folder('index') / 'sample-5v5.json'
    entry = json.loads(index.read_text(encoding='utf-8'))
    entry['header'].update(
        title=5,
        scenario={'a': 1},
        teams=[{'name': {}, 'id': [1], 'style': 3}, 'x'],
        players=[{'team': 0}, 'x', {'team': [1], 'index': None}],
    )
    index.write_text(json.dumps(entry), encoding='utf-8')
    _search._CACHES.clear()
    (listed,) = _library.matches()
    assert (listed['title'], listed['scenario'], listed['teams']) == ('sample-5v5', '', ['', ''])
    found = _search.search('goal')
    assert found['total'] > 0
    assert {result['title'] for result in found['results']} == {'sample-5v5'}
    assert _search.moments_of('sample-5v5')['moments']
    context = _captions.context(entry['header'], [])
    assert context['teams'] == [None, None] and len(context['players']) == 3


def test_a_summary_left_empty_by_a_hard_stop_is_made_once(arena_home, monkeypatch):
    (path,) = _library.add_samples(['sample-5v5'])
    summary = path.with_suffix('.json')
    summary.write_text('', encoding='utf-8')
    # A summary that a save may still be writing stays as it is.
    assert [entry['id'] for entry in _library.matches()] == ['sample-5v5']
    assert summary.read_text(encoding='utf-8') == ''
    old = time.time() - 120
    for item in (path, summary):
        os.utime(item, (old, old))
    (entry,) = _library.matches()
    kept = json.loads(summary.read_text(encoding='utf-8'))
    assert kept['id'] == 'sample-5v5' and kept['title'] == entry['title']

    def again(path):
        raise AssertionError('the summary was made again')

    made = _library._summarize
    monkeypatch.setattr(_library, '_summarize', again)
    assert [entry['id'] for entry in _library.matches()] == ['sample-5v5']
    # A summary that is missing is made and kept the same way.
    summary.unlink()
    monkeypatch.setattr(_library, '_summarize', made)
    _library.matches()
    assert json.loads(summary.read_text(encoding='utf-8'))['id'] == 'sample-5v5'


def test_summary_keeps_returns_and_outcome(arena_home):
    """The summary of a recording keeps the results of its episode, which the frames lack."""
    summaries = {}
    for auto_reset in (True, False):
        batch = cuju.BatchEnvironment(
            'academy_empty_goal_close_l1_r0',
            environment_count=1,
            device='cpu',
            seed=11,
            auto_reset=auto_reset,
        )
        seen = []
        recorder = cuju.arena.Recorder(batch, keep=seen.append)
        kept = cuju.arena.Recorder(batch)
        batch.reset()
        episode = step = 0
        while recorder.episodes < 2:
            # The first episode shoots at the goal and scores, the second over the touchline.
            action = (12 if step % 3 else 5) if episode == 0 else (3 if step % 2 else 12)
            _, _, done, _ = batch.step(torch.tensor([[action]], dtype=torch.int32))
            step += 1
            if bool(done[0]):
                episode, step = episode + 1, 0
                if not auto_reset:
                    batch.reset_done()
        recorder.flush()
        kept.flush()
        batch.close()
        # The filter returned nothing, so it dropped both episodes, which a second recorder kept.
        assert [summary['outcome'] for summary in seen] == ['win', 'draw']
        assert recorder.dropped == 2 and len(kept.saved) == 2
        for saved, expected, returns in zip(
            kept.saved, ('win', 'draw'), ([1.0], [0.0]), strict=True
        ):
            summary = _library.details(saved.stem)
            frames = list(_recording.frames(saved))
            last = struct.unpack_from('<i', frames[-1])[0]
            assert summary['outcome'] == expected and summary['returns'] == returns
            assert summary['score'] == list(struct.unpack_from('<2i', frames[-1], 8))
            # An automatic reset leaves the last step out of the frames, and the length counts it.
            assert summary['length'] == (last + 1 if auto_reset else last)
            assert summary['length'] == (len(frames) if auto_reset else len(frames) - 1)
            summaries[auto_reset, expected] = summary
    # The episodes of the two batches are the same ones, with or without automatic resets.
    for expected in ('win', 'draw'):
        assert summaries[True, expected]['length'] == summaries[False, expected]['length']
    # A recording saved before its episode ends keeps the returns and the standing so far.
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu', seed=11
    )
    with cuju.arena.record(batch) as recorder:
        batch.reset()
        for _ in range(3):
            batch.step(torch.tensor([[0]], dtype=torch.int32))
    batch.close()
    summary = _library.details(recorder.saved[0].stem)
    assert (summary['outcome'], summary['length'], summary['returns']) == ('draw', 3, [0.0])


def test_summary_results_of_other_recordings_and_of_earlier_versions(arena_home):
    """Every saved recording names its outcome and length, and an older summary its outcome."""
    head, frames = _sample_head('sample-5v5', 12)
    lost = bytearray(frames[-1])
    struct.pack_into('<2i', lost, 8, 1, 3)
    path = _library.save(head, [*frames[:-1], bytes(lost)])
    summary = _library.details(path.stem)
    assert (summary['outcome'], summary['length']) == ('loss', 11) and 'returns' not in summary
    # A summary of an earlier version holds the score alone, which gives the outcome.
    stored = path.with_suffix('.json')
    document = json.loads(stored.read_text(encoding='utf-8'))
    del document['outcome'], document['length']
    stored.write_text(json.dumps(document), encoding='utf-8')
    summary = _library.details(path.stem)
    assert summary['outcome'] == 'loss' and 'length' not in summary
    # A result of the wrong type, from a hand edit, is left out.
    for edit in (
        {'outcome': 'victory', 'score': None},
        {'length': -1},
        {'length': True},
        {'length': 2.5},
        {'returns': [1.0, 'high']},
        {'returns': 3},
        {'returns': [float('nan')]},
    ):
        stored.write_text(json.dumps({**document, **edit}), encoding='utf-8')
        summary = _library.details(path.stem)
        assert all(key not in summary for key in edit if key != 'score'), edit
    stored.write_text(json.dumps({**document, 'returns': [1, -0.5], 'length': 7}), encoding='utf-8')
    summary = _library.details(path.stem)
    assert summary['returns'] == [1, -0.5] and summary['length'] == 7


def test_cached_list_answers_within_budget(arena_home):
    (sample,) = _library.add_samples(['sample-5v5'])
    summary = json.loads(sample.with_suffix('.json').read_text(encoding='utf-8'))
    folder = _home.folder('matches')
    data = sample.read_bytes()
    for number in range(1000):
        identifier = f'm-20261117-000000-{number:04d}'
        (folder / f'{identifier}.cjr').write_bytes(data)
        entry = {**summary, 'id': identifier, 'title': f'Match {number}', 'created': identifier}
        entry.pop('title_id', None)
        (folder / f'{identifier}.json').write_text(json.dumps(entry), encoding='utf-8')
    cold = _library.matches()
    assert len(cold) == 1001
    times = []
    for _ in range(5):
        start = time.perf_counter()
        warm = _library.matches()
        times.append(time.perf_counter() - start)
    # B16: a warm list of 1,000 recordings answers within 60 milliseconds.
    assert min(times) < 0.060, times
    assert warm == cold
    # A change through the library, by hand or by another process shows at once.
    first = 'm-20261117-000000-0000'
    _library.update(first, favorite=True)
    assert next(e for e in _library.matches() if e['id'] == first)['favorite']
    path = folder / 'm-20261117-000000-0001.json'
    edited = json.loads(path.read_text(encoding='utf-8'))
    path.write_text(json.dumps({**edited, 'notes': 'by hand, a longer note'}), encoding='utf-8')
    found = {entry['id']: entry for entry in _library.matches()}
    assert found['m-20261117-000000-0001']['notes'] == 'by hand, a longer note'
    assert _library.trash(first) and first not in {e['id'] for e in _library.matches()}
    assert _library.purge(first) and len(_library.matches()) == 1000
    assert not _library.matches(trash=True)
    (folder / 'm-20261117-000000-0002.cjr').unlink()
    assert len(_library.matches()) == 999
    # A caller that changes what it was given leaves the memory as it was.
    listed = _library.matches()
    listed[0]['title'] = 'changed'
    assert _library.matches()[0]['title'] != 'changed'


def test_benchmark_recording_groups_name_explicit_checkpoints(arena_home, tmp_path):
    from cuju.arena._evaluate import academy
    from cuju.arena._registry import checkpoints, teams

    folder = tmp_path / 'versions'
    folder.mkdir()
    (folder / 'main.py').write_text('def agent(obs): return [0]')
    identifier = teams.add_folder(folder, 'Harbour')['id']
    version = checkpoints.add(identifier, 'main.py', b'def agent(obs): return [12]', 'Early', 10)
    entrant = f'{identifier}@{version["current"]}'
    outcome = academy.run('empty_goal_close', entrant, keep_recordings=True)
    found = _library.matches(group=outcome['group'])
    assert len(found) == 5
    assert {entry['group']['competitor'] for entry in found} == {'Harbour Early'}
