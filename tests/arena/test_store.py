# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the runs manifest, checkpoints, persistent benchmarks and what recordings keep."""

import json
import threading
import time

import pytest
import torch

import cuju
import cuju.errors
from cuju.arena._format import recording as _recording
from cuju.arena._store import home as _home
from cuju.arena._store import library as _library
from cuju.arena._store import runs as _runs


def _wait(test, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if test():
            return True
        time.sleep(0.02)
    return False


def test_run_manifest_round_trip_and_skip_of_bad_fields(arena_home):
    made = _runs.create(
        'evaluation',
        name='Wings',
        run='wings-training',
        scenarios=['5_vs_5'],
        seeds=[1, 2],
        opponents=[{'kind': 'builtin', 'difficulty': 0.6}],
        checkpoints=[{'id': 'c1', 'label': 'first', 'step': 100}],
        config={'episodes': 2},
    )
    identifier = made['id']
    assert identifier.startswith('r-') and made['state'] == 'running' and made['episodes'] == []
    episode = {'recording': 'm-1', 'checkpoint': 'c1', 'seed': 1, 'score': [2, 1], 'outcome': 'win'}
    _runs.append_episode(identifier, episode)
    _runs.append_episode(identifier, {**episode, 'seed': 2, 'score': [0, 0], 'outcome': 'draw'})
    done = _runs.finish(identifier, {'wins': 1, 'draws': 1})
    assert done['state'] == 'done' and done['ended']
    assert done['aggregates'] == {'wins': 1, 'draws': 1}
    read = _runs.get(identifier)
    assert read == done
    assert [e['seed'] for e in read['episodes']] == [1, 2]
    assert read['checkpoints'] == [{'id': 'c1', 'label': 'first', 'step': 100}]
    # The file passes through a temporary file, which leaves nothing behind.
    assert sorted(path.name for path in _runs.folder().iterdir()) == [f'{identifier}.json']
    other = _runs.create('benchmark', 'b-test-1', run='other')
    assert [run['id'] for run in _runs.list(kind='evaluation')] == [identifier]
    assert [run['id'] for run in _runs.list(run='other')] == [other['id']]
    assert {run['id'] for run in _runs.list()} == {identifier, other['id']}
    # Refusals name what does not fit.
    for call in (
        lambda: _runs.create('nothing'),
        lambda: _runs.create('import', colour='red'),
        lambda: _runs.create('import', '../out'),
        lambda: _runs.create('benchmark', 'b-test-1'),
        lambda: _runs.create('import', seeds='many'),
        lambda: _runs.append_episode(identifier, {'score': float('nan')}),
        lambda: _runs.append_episode('r-none', episode),
        lambda: _runs.finish(identifier, state='running'),
        lambda: _runs.checkpoint(True),
        lambda: _runs.checkpoint({'label': 'no id'}),
        lambda: _runs.checkpoint({'id': 'c', 'step': 1.5}),
    ):
        with pytest.raises(cuju.errors.ArgumentError):
            call()
    # A damaged file keeps every field that fits and leaves the rest at their defaults.
    path = _runs.folder() / f'{identifier}.json'
    damaged = json.loads(path.read_text(encoding='utf-8'))
    damaged.update(
        name=5,
        seeds='x',
        state='lost',
        aggregates=[1],
        checkpoints=[{'id': 'c2'}, 'bad', {'label': 'no id'}],
        episodes=[episode, 7, {'nan': float('nan')}],
        extra={'kept': False},
    )
    path.write_text(json.dumps(damaged), encoding='utf-8')
    kept = _runs.get(identifier)
    assert kept['name'] is None and kept['seeds'] == [] and kept['state'] == 'running'
    assert kept['aggregates'] == {} and kept['checkpoints'] == [{'id': 'c2'}]
    assert kept['episodes'] == [episode] and 'extra' not in kept
    assert kept['scenarios'] == ['5_vs_5'] and kept['run'] == 'wings-training'
    path.write_text('{"kind": "unknown"}', encoding='utf-8')
    assert _runs.get(identifier) is None
    path.write_text('not json', encoding='utf-8')
    assert _runs.get(identifier) is None
    assert [run['id'] for run in _runs.list()] == [other['id']]
    assert _runs.remove(other['id']) and not _runs.remove(other['id'])
    assert not _runs.remove('../escape')


def test_checkpoint_digest_stable_and_changes_with_files(arena_home, tmp_path):
    model = tmp_path / 'model'
    (model / 'weights').mkdir(parents=True)
    (model / 'main.py').write_text('def agent(obs):\n    return [0]\n', encoding='utf-8')
    (model / 'weights' / 'net.pt').write_bytes(bytes(range(256)))
    first = _runs.checkpoint(model)
    assert first['id'] == first['digest'][:16] and first['label'] == 'model'
    assert _runs.checkpoint(model) == first
    # The digest reads the content, so a copy elsewhere names the same checkpoint.
    moved = tmp_path / 'elsewhere' / 'model'
    moved.parent.mkdir()
    model.rename(moved)
    assert _runs.checkpoint(moved)['digest'] == first['digest']
    (moved / 'weights' / 'net.pt').write_bytes(bytes(range(255)) + b'\x00')
    changed = _runs.checkpoint(moved)['digest']
    assert changed != first['digest']
    (moved / 'weights' / 'net.pt').rename(moved / 'weights' / 'other.pt')
    assert _runs.checkpoint(moved)['digest'] not in (first['digest'], changed)
    assert _runs.checkpoint('step-100') == {'id': 'step-100'}
    assert _runs.checkpoint(7) == {'id': '7'}
    # A team of the registry is named by its model files, and its name, kit and sounds stay out.
    team = _home.folder('teams') / 't-test'
    (team / 'cuju-team').mkdir(parents=True)
    (team / 'main.py').write_text('def agent(obs):\n    return [0]\n', encoding='utf-8')
    (team / 'cuju-team' / 'team.json').write_text('{"name": "One"}', encoding='utf-8')
    named = _runs.team_checkpoint('t-test')
    (team / 'cuju-team' / 'team.json').write_text('{"name": "Two"}', encoding='utf-8')
    assert _runs.team_checkpoint('t-test') == named
    (team / 'main.py').write_text('def agent(obs):\n    return [1]\n', encoding='utf-8')
    assert _runs.team_checkpoint('t-test')['id'] != named['id']
    with pytest.raises(cuju.errors.ArgumentError):
        _runs.team_checkpoint('t-none')


def test_the_episodes_given_to_create_and_update_reach_the_manifest(arena_home):
    episodes = [{'number': 0, 'seed': 1, 'outcome': 'win'}, {'number': 1, 'seed': 2}]
    made = _runs.create('evaluation', name='Wings', episodes=episodes)
    assert made['episodes'] == episodes and _runs.get(made['id'])['episodes'] == episodes
    changed = _runs.update(made['id'], episodes=episodes[1:])
    assert changed['episodes'] == episodes[1:] and _runs.get(made['id'])['episodes'] == episodes[1:]
    with pytest.raises(cuju.errors.ArgumentError):
        _runs.update(made['id'], episodes=[3])


def test_recorder_keeps_its_run_checkpoint_and_sections(arena_home):
    from cuju.arena._moments import search as _search

    batch = cuju.BatchEnvironment('5_vs_5', environment_count=1, device='cpu')
    try:
        with pytest.raises(cuju.errors.ArgumentError):
            cuju.arena.Recorder(batch, checkpoint=[1])
        with pytest.raises(cuju.errors.ArgumentError):
            cuju.arena.Recorder(batch, run={'id': 'r', 'seed': -1})
        recorder = cuju.arena.Recorder(
            batch,
            run={'id': 'wings', 'seed': 7},
            checkpoint={'id': 'c1', 'label': 'first', 'step': 100},
        )
        batch.reset()
        agents = int(batch.left_agents[0] + batch.right_agents[0])
        for _ in range(30):
            batch.step(torch.zeros((1, agents), dtype=torch.int32))
        recorder.save()
    finally:
        batch.close()
    (path,) = recorder.saved
    provenance = _recording.read_header(path)['provenance']
    assert provenance['run'] == {'id': 'wings', 'episode': None}
    assert provenance['batch_seed'] == 7
    assert provenance['checkpoint'] == {'id': 'c1', 'label': 'first', 'step': 100}
    summary = _library.details(path.stem)
    assert summary['checkpoint'] == provenance['checkpoint'] and summary['run']['id'] == 'wings'
    # The summary reads what the writer completed in the header, after the file is written.
    assert {'PLYR', 'ACTS', 'STAT'} <= set(summary['has'])
    assert summary['fidelity'] == 'joints'
    assert (
        summary['run_key']['state_digest']
        == _recording.read_header(path)['run_key']['state_digest']
    )
    assert summary['engine_seed'] == provenance['engine_seed']
    _search.wait()
    entry = _search.entry(path.stem)
    assert entry['run'] == provenance['run'] and entry['checkpoint'] == provenance['checkpoint']
    assert entry['run_key'] == summary['run_key'] and entry['has'] == summary['has']


def test_benchmark_survives_server_restart(arena_home):
    from cuju.arena._evaluate import benchmark as _benchmark

    gate = threading.Event()
    first_calls = []

    def first_player(document, cell, attempt, started=None):
        first_calls.append(cell['task'])
        if len(first_calls) == 1:
            attempt({'attempt': 1, 'scored': True})
            return {'value': 3, 'rate': 0.6, 'attempts': [{'attempt': 1}] * 5}
        # The server stops while the second cell plays.
        gate.wait(30)
        raise RuntimeError('stopped')

    document = _benchmark.plan(['builtin'], ['empty_goal_close', 'corner'], workers=1)
    before = _benchmark._Benchmarks(player=first_player)
    job = before.add(document)
    assert _wait(lambda: len(first_calls) == 2)
    kept = _runs.get(job['id'])
    assert kept['kind'] == 'benchmark' and kept['state'] == 'running'
    cells = kept['aggregates']['cells']
    assert cells[0]['state'] == 'done' and cells[1]['state'] in ('queued', 'running')
    assert kept['config']['tasks'] == ['empty_goal_close', 'corner']
    # A new server reads the benchmark back and plays the cell that the stop cut short.
    second_calls = []

    def second_player(document, cell, attempt, started=None):
        second_calls.append(cell['task'])
        return {'value': 1, 'rate': 0.2, 'attempts': [{'attempt': 1}] * 5}

    after = _benchmark._Benchmarks(player=second_player)
    assert _wait(lambda: after.get(job['id'])['state'] == 'done')
    ended = after.get(job['id'])
    assert second_calls == ['corner']
    assert [cell['value'] for cell in ended['cells']] == [3, 1]
    assert ended['rows'][0]['score'] == round(100 * (0.6 + 0.2) / 2, 1)
    assert ended['progress'] == 1.0
    stored = _runs.get(job['id'])
    assert stored['state'] == 'done' and stored['aggregates']['rows'] == ended['rows']
    gate.set()


def test_a_benchmark_keeps_its_result_before_reporting_done(arena_home, monkeypatch):
    from cuju.arena._evaluate import benchmark as _benchmark

    saving = threading.Event()
    release_save = threading.Event()
    update = _runs.update

    def slow_update(identifier, **fields):
        if fields.get('state') == 'done':
            saving.set()
            release_save.wait(2)
        return update(identifier, **fields)

    def play(document, cell, attempt, started=None):
        return {'value': 1, 'rate': 0.2, 'attempts': [{'attempt': 1}] * 5}

    monkeypatch.setattr(_runs, 'update', slow_update)
    manager = _benchmark._Benchmarks(player=play)
    document = _benchmark.plan(['builtin'], ['empty_goal_close'], workers=1)
    identifier = manager.add(document)['id']
    try:
        assert saving.wait(10)
        assert _wait(lambda: manager.get(identifier)['state'] == 'done')
        assert _runs.get(identifier)['state'] == 'done'
    finally:
        release_save.set()
        assert _wait(lambda: _runs.get(identifier)['state'] == 'done')


def test_tournament_matches_keep_actions(arena_home, tmp_path):
    from cuju.arena._registry import teams as _teams
    from cuju.arena._tournaments import tournament_runner as runner

    folder = tmp_path / 'rex'
    folder.mkdir()
    (folder / 'main.py').write_text('def agent(obs):\n    return [5]\n', encoding='utf-8')
    team = _teams.add_folder(folder, 'Rex', '5v5')
    cups = runner.Tournaments(start=False, interval=0.1)
    config = {
        'name': 'Short cup',
        'seed': 3,
        'seeding': 'manual',
        'entrants': [{'kind': 'team', 'team': team['id']}, {'kind': 'builtin'}],
        'stages': [{'format': 'league', 'scenario': '5_vs_5', 'length': 50, 'cycles': 1}],
    }
    entry = cups.create(config)
    assert cups.run(entry['id'], report=lambda line: None) == 'done'
    done = cups.get(entry['id'])
    cups.close()
    (match,) = [m for m in done['matches'] if m['result']['match']]
    path = _library.path_of(match['result']['match'])
    header = _recording.read_header(path)
    assert {'PLYR', 'ACTS', 'STAT'} <= set(header['sections'])
    actions = _recording.actions(path)
    # Rex is the one agent, and every step holds its action, the kick off none.
    assert actions.shape[1] == sum(header['agents']) == 1
    assert actions[0, 0] == 255 and set(actions[1:-1, 0].tolist()) == {5}
    assert _recording.state(path) is not None
