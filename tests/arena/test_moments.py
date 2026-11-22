# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the moments of a match, their search and their captions."""

import json
import os
import threading

import pytest
import torch

import cuju
import cuju.errors
import cuju.state
from cuju.arena._format import recording as _recording
from cuju.arena._store import home as _home
from cuju.arena._store import library as _library
from support.common import _REPOSITORY


def test_search_query_language(arena_home):
    from cuju.arena._moments import search as _search

    _library.add_samples()

    def total(query):
        return _search.search(query)['total']

    assert total('goal') == 4
    assert total('goals') == total('kind:goal') == 4
    assert total('goal team:Harbour') == 1
    assert total('goal team:left') == 2
    assert total('goal team:"harbour rovers"') == 1
    assert total('player:10 goal') == 1
    assert total('penalty') == 1
    assert total('kind:penalty,save') == 2
    assert total('set-piece') == 9
    assert total('goal score:level') == 2
    assert total('goal score:1-1') == 2
    # Both equalizers come from behind, and neither goal of the samples puts a leader further on.
    assert total('goal score:trailing') == 2
    assert total('goal score:leading') == 0
    assert total('free-kick scenario:5 vs 5') == 1
    assert total('shot after:45') == 1
    assert total('pass outcome:intercepted') == 16
    assert total('goal source:unknown') == 4
    assert total('goal source:tournament') == 0
    assert total('goal match:sample-5v5') == 2
    # A word matches the recording, its title, scenario and teams, so both goals of its match.
    assert total('goal Harbour') == 2
    assert total('goal -team:left') == 2
    assert total('goal date:2000-01-01') == 0
    assert total('goal since:2000-01-01') == 4
    everything = total('')
    assert everything == total('-zzz') and everything > 100
    assert total('-pass -possession') == everything - total('pass') - total('possession')
    found = _search.search('goal limit:1')
    assert len(found['results']) == 1 and found['total'] == 4
    first = found['results'][0]
    assert first['seek'] == max(0.0, round(first['t'] - 4, 1))
    assert first['url'] == f'#match={first["match"]}&t={first["seek"]:g}'
    assert first['text'].startswith('Goal, number ')
    assert first['minute'] == f'{first["clock"] // 60000}:{first["clock"] // 1000 % 60:02d}'
    for bad in ('foo:1', 'after:x', 'kind:zzz', 'score:huge', 'limit:0', 'source:moon', 'date:x'):
        with pytest.raises(cuju.errors.ArgumentError):
            _search.search(bad)


def test_a_name_never_adds_or_styles_a_cue_of_the_captions_file():
    from cuju.arena._moments import captions as _captions

    name = 'Reds\n\n00:00:00.000 --> 99:00:00.000\r\n<b>Fake</b> & co\u2028end'
    header = {
        'teams': [{'name': name}, {'name': 'Blues'}],
        'players': [{'team': 0, 'index': 0, 'goalkeeper': False}],
    }
    moments = [{'id': 'g', 'kind': 'goal', 'step': 10, 't': 1.0, 'team': 0, 'data': {}}]
    found = _captions.cues(moments, _captions.context(header, []))
    text = _captions.to_vtt(found)
    lines = text.split('\n')
    assert lines[:3] == ['WEBVTT', '', '1'] and lines[3].count('-->') == 1
    assert len(lines) == 6 and lines[5] == ''
    assert '<' not in lines[4] and '-->' not in lines[4] and '\r' not in text
    assert '&lt;b&gt;Fake&lt;/b&gt; &amp; co end' in lines[4]
    assert '00:00:00.000 --&gt; 99:00:00.000' in lines[4]
    # The cues themselves keep the name as the page shows it.
    assert name in found[0]['text']


# The index of version 2, its signals, heat and statistics.


def _short_recording(steps=60, annotate=None, overrides=()):
    """Records one short 5v5 episode of random actions and returns its path."""
    generator = torch.Generator().manual_seed(11)
    batch = cuju.BatchEnvironment('5_vs_5', environment_count=1, device='cpu')
    try:
        recorder = cuju.arena.Recorder(batch, overrides=overrides)
        batch.reset()
        agents = int(batch.left_agents[0] + batch.right_agents[0])
        for step in range(1, steps + 1):
            actions = torch.randint(0, 19, (1, agents), generator=generator, dtype=torch.int32)
            batch.step(actions)
            if annotate is not None:
                annotate(recorder, step, int(actions[0, 0]))
        recorder.save()
    finally:
        batch.close()
    (path,) = recorder.saved
    return path


def test_index_version_3_rebuilds_old_entries(arena_home):
    from cuju.arena._moments import search as _search
    from cuju.arena._store import stats as _stats

    _library.add_samples()
    _search.wait()
    index = _home.folder('index')
    old = {}
    for path in sorted(index.glob('*.json')):
        entry = json.loads(path.read_text(encoding='utf-8'))
        assert entry['version'] == _search.VERSION and entry['version'].startswith('3.')
        fields = {'signals', 'heat', 'run', 'checkpoint', 'run_key', 'has', 'unavailable'}
        assert fields <= set(entry)
        # An entry as cuju-render 0.1.0 wrote it, with the moments alone.
        written = {key: entry[key] for key in ('id', 'signature', 'header', 'moments')}
        written['version'] = '1.1'
        path.write_text(json.dumps(written), encoding='utf-8')
        old[path.stem] = entry
    (_home.folder('stats') / 'sample-5v5.json').unlink()
    _search._CACHES.clear()
    assert _search.sync()['built'] == 2
    for identifier, entry in old.items():
        rebuilt = json.loads((index / f'{identifier}.json').read_text(encoding='utf-8'))
        assert rebuilt == entry
    assert _stats.read('sample-5v5')['id'] == 'sample-5v5'
    # Statistics that go missing are found again on request.
    (_home.folder('stats') / 'sample-11v11.json').unlink()
    assert _stats.of('sample-11v11')['id'] == 'sample-11v11'
    assert _stats.read('sample-11v11') is not None


def test_signals_hold_drops_and_unlikely_frames(arena_home):
    from cuju.arena._moments import search as _search
    from cuju.arena._store import stats as _stats

    def annotate(recorder, step, chosen):
        value = {20: -0.4, 25: 0.7}.get(step, 0.5 if step < 20 else 0.6)
        held = 0.02 if step == 30 else 0.5
        rest = (1 - held) / 18
        probabilities = [held if action == chosen else rest for action in range(19)]
        recorder.annotate({'value': value}, player=0, probabilities=probabilities, action=chosen)

    path = _short_recording(40, annotate, ['annotations.probabilities=all'])
    _search.wait()
    signals = _search.entry(path.stem)['signals']
    ((key, series),) = signals['series'].items()
    who = key.split(':')[0]
    assert key.endswith(':value') and series['count'] == 40
    assert (series['min'], series['max']) == (-0.4, 0.7)
    assert series['drops'][0] == [20, -0.9] and series['drops'][1] == [26, -0.1]
    assert series['rises'][0] == [21, 1.0] and series['rises'][1] == [25, 0.1]
    assert len(series['drops']) <= _stats.SIGNAL_CHANGES
    assert signals['unlikely'] == {who: [[30, 0.02]]}
    entropy = signals['entropy'][who]
    assert entropy['count'] == 40 and entropy['max'][0] == 30
    assert entropy['min'][1] < entropy['mean'] < entropy['max'][1]
    # The query part reads only what the index keeps, so a file without annotations has none.
    assert _stats.signals([]) == {'series': {}, 'unlikely': {}, 'entropy': {}}
    # A chosen action outside the three kept probabilities counts at the most it can be.
    kept = [{'f': 3, 'p': 1, 'a': 7, 'k': [[1, 0.9], [2, 0.05], [3, 0.03]]}]
    assert _stats.signals(kept)['unlikely'] == {'1': [[3, 0.02]]}


def test_heat_counts_sum_to_frames(arena_home):
    from cuju.arena._moments import search as _search
    from cuju.arena._store import stats as _stats

    path = _short_recording(80)
    header = _recording.read_header(path)
    heat = _search.entry(path.stem)['heat']
    cells = _stats.HEAT_COLUMNS * _stats.HEAT_ROWS
    assert (heat['columns'], heat['rows']) == (12, 8) and heat['extent'] == [-55, 55, -36, 36]
    assert len(heat['players']) == len(header['players'])
    for counts in [*heat['players'], heat['ball']]:
        assert len(counts) == cells and sum(counts) == header['frame_count']
    # The kick off puts the ball on the centre spot, in the middle of the pitch.
    first = _stats.heat(header, [next(_recording.frames(path))])
    (spot,) = [index for index, count in enumerate(first['ball']) if count]
    assert spot // 12 in (3, 4) and spot % 12 in (5, 6)


def test_one_recording_indexes_without_the_library(arena_home):
    import shutil
    import time

    from cuju.arena._moments import search as _search

    path = _short_recording(60)
    _search.wait()
    matches = _home.folder('matches')
    for number in range(200):
        shutil.copyfile(path, matches / f'm-copy-{number:03d}.cjr')
    shutil.rmtree(_home.folder('index'))
    _search._CACHES.clear()
    assert _search.progress() == {'indexed': 0, 'total': 201, 'running': False}
    start = time.perf_counter()
    found = _search.moments_of('m-copy-123')
    took = time.perf_counter() - start
    # B17: the moments of one recording come within two seconds, whatever the library holds.
    assert took < 2.0 and found['match'] == 'm-copy-123'
    assert [entry.name for entry in _home.folder('index').iterdir()] == ['m-copy-123.json']
    assert _search.progress()['indexed'] == 1
    # A pass over the library lets one recording through while it reads the others.
    passing = threading.Thread(target=_search.sync)
    passing.start()
    try:
        assert _wait_for(lambda: _search.progress()['running'])
        start = time.perf_counter()
        assert _search.entry('m-copy-199')['id'] == 'm-copy-199'
        assert time.perf_counter() - start < 2.0
    finally:
        passing.join()
    assert _search.progress() == {'indexed': 201, 'total': 201, 'running': False}


def _wait_for(test, seconds=30):
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if test():
            return True
        time.sleep(0.005)
    return False


def test_version_3_moments_from_tracks(arena_home):
    from cuju.arena._moments import search as _search

    path = _short_recording(500)
    _search.wait()
    document = _recording.read_header(path)
    frames = list(_recording.frames(path))
    head = _recording._encode_header(document)
    tracks = _home.folder('matches') / 'm-tracks.cjr'
    _recording.write(tracks, head, frames, 3)
    players = len(document['players'])
    padded = -(-players // 4) * 4
    at = len(frames[0]) - padded
    blind = [frame[:at] + bytes(players) + frame[at + players :] for frame in frames]
    untouched = _home.folder('matches') / 'm-blind.cjr'
    _recording.write(untouched, head, blind, 3)
    assert _recording.read_header(tracks)['fidelity'] == 'positions'
    full = _search.entry(path.stem)
    found = _search.entry('m-tracks')
    # The rules read the root positions, the heads and the closing bytes, which tracks keep.
    assert found['moments'] == full['moments'] and found['unavailable'] == []
    assert found['heat'] == full['heat']
    kinds = {moment['kind'] for moment in full['moments']}
    assert {'pass', 'shot'} <= kinds
    blinded = _search.entry('m-blind')
    assert blinded['unavailable'] == [*_search.TOUCH_KINDS, *_search.REFEREE_KINDS]
    assert not {moment['kind'] for moment in blinded['moments']} & set(blinded['unavailable'])
    assert _search.statistics('m-blind')['passes'] == [0, 0]
    assert _search.moments_of('m-blind')['unavailable'] == blinded['unavailable']
    # Captions read a file of version 3 by its path as well.
    assert _search.captions(tracks) == _search.captions(path)


def test_stored_statistics_agree_with_the_moments_of_a_sample(arena_home):
    from cuju.arena._moments import search as _search
    from cuju.arena._store import stats as _stats

    _library.add_samples()
    for name in ('sample-5v5', 'sample-11v11'):
        found = _stats.of(name)
        assert (_home.folder('stats') / f'{name}.json').is_file()
        moments = _search.moments_of(name)['moments']
        summary = _library.details(name)

        def count(kind, side, test=lambda moment: True, moments=moments):
            return sum(
                moment['kind'] == kind and moment['team'] == side and test(moment)
                for moment in moments
            )

        assert found['score'] == summary['score'] == found['goals']
        assert found['outcome'] == summary['outcome'] and found['length'] == summary['length']
        for side in (0, 1):
            assert found['shots'][side] == count('shot', side)
            assert found['shots_on_target'][side] == count(
                'shot', side, lambda m: m['data'].get('onTarget') is True
            )
            assert found['passes'][side] == count('pass', side)
            assert found['completed'][side] == count(
                'pass', side, lambda m: m['data'].get('outcome') == 'complete'
            )
        assert abs(sum(found['possession']) - 1) < 1e-3
        assert sum(found['owned']) <= found['frames'] == summary['steps']
        assert found['actions'] is None
        assert all(sum(counts) == found['frames'] for counts in found['heat']['players'])
    assert set(_stats.many(['sample-5v5', 'nothing'])) == {'sample-5v5', 'nothing'}
    assert _stats.many(['nothing'])['nothing'] is None


def test_statistics_count_the_actions_of_each_team(arena_home):
    from cuju.arena._store import stats as _stats

    path = _short_recording(50)
    found = _stats.of(path.stem)
    actions = _recording.actions(path)
    agents = _recording.read_header(path)['agents']
    assert found['actions']['set'] == 'default'
    left = actions[:, : agents[0]]
    left = left[left != 255]
    assert sum(found['actions']['counts'][0].values()) == len(left)
    assert abs(sum(found['actions']['share'][0].values()) - 1) < 1e-3
    assert found['actions']['counts'][0] == {
        str(number): int((left == number).sum()) for number in set(left.tolist())
    }


def test_the_source_filter_finds_imported_recordings():
    from cuju.arena._moments import search as _search

    def moment(source):
        return _search._Moment({'kind': 'goal'}, {'provenance': {'source': source}})

    for word in ('import', 'imported'):
        test = _search._filter('source', word)
        assert test(moment('import')) and not test(moment('recorder'))


# The query language, its cases shared with the page and the policy signals.


def _query_cases():
    path = os.path.join(_REPOSITORY, 'tests', 'support', 'query_cases.json')
    with open(path, encoding='utf-8') as file:
        return json.load(file)


def test_query_cases_find_the_signals_of_the_fixture():
    from cuju.arena._moments import search as _search

    cases = _query_cases()
    fixture = cases['fixture']
    shipped = _search.thresholds()
    assert shipped == {'value_drop': 0.3, 'window': 2.0, 'surprise': 0.1}
    for case in cases['find']:
        terms = _search.parse(case['query'])
        settings = case.get('settings') or shipped
        found = _search.signal_moments(terms, fixture['signals'], 10, settings)
        assert found == case['found'], case['query']


def test_signal_moments_take_their_clock_team_and_filters():
    from cuju.arena._moments import search as _search

    fixture = _query_cases()['fixture']
    header = fixture['header']
    record = _search._record(
        {'id': 'fixture', 'header': header}, {'id': 'fixture', 'created': '2026-11-17'}, []
    )

    def find(query, settings=None):
        reading = _search.Query(query)
        found = reading.find(
            record, fixture['moments'], fixture['signals'], header, settings or _search.thresholds()
        )
        return [(item.moment['kind'], item.moment['step']) for item in found]

    drop = _search.Query('value-drop').find(
        record, fixture['moments'], fixture['signals'], header, _search.thresholds()
    )[0]
    # Halfway between the kick off at 0 and the goal at frame 100, at 180 seconds of the clock.
    assert drop.moment['clock'] == 90000 and drop.moment['t'] == 5.0
    assert drop.moment['team'] == 0 and drop.moment['score'] == [0, 0]
    result = _search._result(drop)
    assert result['minute'] == '1:30' and result['team_name'] == 'Greens'
    assert result['text'] == 'Value drop, number 2 of Greens, value fell by 0.6'
    assert result['text_id'] == 'describe.signal.drop'
    assert find('value-drop team:left') == [('value-drop', 50), ('value-drop', 121)]
    assert find('value-drop team:right') == []
    assert find('surprise team:right') == [('surprise', 30)]
    # Number 2 of each team, the second player of either side.
    assert find('surprise player:2') == [('surprise', s) for s in (30, 55, 90, 140)]
    assert find('surprise after:3') == [('surprise', 140)]
    # Words of kinds and signal terms each add moments, and filters narrow them all.
    assert find('goal surprise:0.01') == [('surprise', 55), ('goal', 100)]
    assert find('reward:>0.5') == [('annotation', 100)]
    reward = _search.Query('reward:>0.5').find(
        record, fixture['moments'], fixture['signals'], header, _search.thresholds()
    )[0]
    assert _search._result(reward)['text'] == 'reward, > 0.5'
    # A recording without annotations has no signals to find.
    assert _search.Query('value-drop').find(record, fixture['moments'], None, header, {}) == []


def test_signals_find_value_drops(arena_home):
    from cuju.arena._moments import search as _search

    def annotate(recorder, step, chosen):
        value = {20: -0.4, 25: 0.7}.get(step, 0.5 if step < 20 else 0.6)
        held = 0.02 if step == 30 else 0.5
        rest = (1 - held) / 18
        probabilities = [held if action == chosen else rest for action in range(19)]
        recorder.annotate({'value': value}, player=0, probabilities=probabilities, action=chosen)

    path = _short_recording(40, annotate, ['annotations.probabilities=all'])
    _search.wait()
    found = _search.search('value-drop')
    assert found['total'] == 1
    (drop,) = found['results']
    assert (drop['match'], drop['kind'], drop['step']) == (path.stem, 'value-drop', 20)
    assert drop['data'] == {'name': 'value', 'change': -0.9}
    assert drop['team'] == 0 and drop['text_id'] == 'describe.signal.drop'
    assert drop['url'] == f'#match={path.stem}&t={drop["seek"]:g}'
    assert [r['step'] for r in _search.search('surprise')['results']] == [30]
    assert [r['step'] for r in _search.search('entropy:>1')['results']] == [30]
    assert [r['step'] for r in _search.search('ann:value<0')['results']] == [20]
    assert _search.search('value-drop:other')['total'] == 0
    assert _search.search('value-drop team:right')['total'] == 0
    # A changed threshold applies at once, and the index reads no recording again.
    harder = _search.search('value-drop', overrides=['search.value_drop=1.0'])
    assert harder['total'] == 0 and harder['index']['built'] == 0
    softer = _search.search('value-drop', overrides=['search.value_drop=0.05', 'search.window=0.1'])
    assert softer['total'] == 2 and softer['index']['built'] == 0
    assert _search.search('surprise:0.01')['total'] == 0
    assert _search.search('goal')['total'] == _search.search('goal value-drop')['total'] - 1
    for bad in ('-surprise', 'surprise:3', 'entropy:2', 'ann:value'):
        with pytest.raises(cuju.errors.ArgumentError):
            _search.search(bad)
