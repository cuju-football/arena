# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the store of the ratings, its rows, its earlier ratings and saved queues."""

import json

import pytest

import cuju.errors
from cuju.arena._evaluate import ratings as _evidence
from cuju.arena._store import home as _home
from cuju.arena._store import queue as _queue
from cuju.arena._store import ratings as _ratings
from cuju.arena._store import runs as _runs
from cuju.arena._tournaments import tournament_runner as runner


def _side(key, name=None, **fields):
    return {'key': key, 'name': name or key, **fields}


def test_the_store_reads_the_ratings_that_tournaments_kept(arena_home):
    earlier = {
        'builtin:0.80': {
            'name': 'Built-in AI 0.80',
            'name_id': 'server.tournament.label.builtin_ability',
            'name_values': {'ability': '0.80'},
            'rating': 1512.37,
            'matches': 3,
        },
        't-rex': {'name': 'Rex', 'rating': 1487.63, 'matches': 3},
        'broken': {'name': 'No rating', 'rating': 'high'},
        'odd': 5,
    }
    path = _home.folder('tournaments') / 'ratings.json'
    path.write_text(json.dumps(earlier), encoding='utf-8')
    table = _ratings.earlier()
    assert set(table) == {'builtin:0.80', 't-rex'}
    for key in table:
        for field in ('name', 'rating', 'matches'):
            assert table[key][field] == earlier[key][field]
        assert table[key]['sources'] == {'tournament': 3}
    assert table['builtin:0.80']['name_id'] == earlier['builtin:0.80']['name_id']
    # The store keeps its own file from then on, and the earlier file stays as it was.
    assert (arena_home / 'ratings' / 'ratings.json').is_file()
    assert json.loads(path.read_text(encoding='utf-8')) == earlier
    assert _ratings.earlier_history('t-rex') == []
    # The earlier ratings name no scenario, so they stay a table of their own, on their own
    # scale from 1500, and enter the table of no scenario. Before the tables of Evaluate the
    # tournaments read them as the ratings of every draw.
    shown = _evidence.earlier()
    assert [entry['key'] for entry in shown['teams']] == ['builtin:0.80', 't-rex']
    assert shown['scale'] == 'earlier' and shown['start'] == 1500.0 and shown['method'] == 'points'
    assert _ratings.results() == [] and _ratings.scenarios() == {}
    assert runner.ratings() == {} and _evidence.overview()['earlier'] == 2
    # A file of the store of before the rows, with its history, reads the same way.
    lines = [
        {
            'key': 't-rex',
            'time': 5.0,
            'opponent': 'x',
            'score': [1, 0],
            'before': 1500,
            'after': 1510,
        },
        {
            'key': 'x',
            'time': 5.0,
            'opponent': 't-rex',
            'score': [0, 1],
            'before': 1500,
            'after': 1490,
        },
    ]
    with (arena_home / 'ratings' / 'history.jsonl').open('w', encoding='utf-8') as stream:
        stream.write(''.join(json.dumps(line) + '\n' for line in lines) + '{"key": "t-rex", "ti')
    assert _ratings.earlier_history('t-rex') == [
        {'time': 5.0, 'opponent': 'x', 'score': [1, 0], 'before': 1500, 'after': 1510}
    ]
    assert _ratings.reset('t-rex') and not _ratings.reset('t-rex')
    assert set(_ratings.earlier()) == {'builtin:0.80'} and _ratings.earlier_history('t-rex') == []
    assert len(_ratings.earlier_history('x')) == 1


def test_a_result_is_kept_once_as_a_small_row(arena_home):
    rex = _side('rex', 'Rex', checkpoint='3fa9c1d200a1b2c3', kind='team', label='rex', step=4)
    anchor = _side('builtin:0.60', 'Built-in AI 0.60', kind='builtin')
    row = _ratings.record(
        {
            'scenario': '5_vs_5',
            'home': rex,
            'away': anchor,
            'score': [2, 1],
            'source': 'rating',
            'phase': 'seeding',
            'ref': 'rate-1/0',
            'time': 1000.0,
            'seed': '7/0/left',
            'actions': {'run': 'rate-1', 'number': 0},
        }
    )
    # A row holds the keys and the checkpoints of its sides, and no name.
    assert row == {
        'time': 1000.0,
        'scenario': '5_vs_5',
        'home': {'key': 'rex', 'checkpoint': '3fa9c1d200a1b2c3'},
        'away': {'key': 'builtin:0.60', 'checkpoint': None},
        'score': [2, 1],
        'source': 'rating',
        'ref': 'rate-1/0',
        'phase': 'seeding',
        'seed': '7/0/left',
        'actions': {'run': 'rate-1', 'number': 0},
    }
    path = arena_home / 'ratings' / 'results.jsonl'
    assert json.loads(path.read_text(encoding='utf-8')) == row
    assert path.stat().st_size < 400
    assert _ratings.results('5_vs_5') == [row] == _ratings.results()
    assert _ratings.results('11_vs_11_kaggle') == [] and _ratings.results(None) == []
    assert _ratings.scenarios() == {'5_vs_5': 1}
    assert _ratings.model('rex', '3fa9c1d200a1b2c3') == 'rex@3fa9c1d200a1b2c3'
    assert _ratings.model('builtin:0.60') == 'builtin:0.60'
    # The name, the kind and the checkpoint's label lie beside the rows, once for each model.
    assert _ratings.models() == {
        'rex@3fa9c1d200a1b2c3': {
            'key': 'rex',
            'checkpoint': '3fa9c1d200a1b2c3',
            'name': 'Rex',
            'kind': 'team',
            'label': 'rex',
            'step': 4,
        },
        'builtin:0.60': {
            'key': 'builtin:0.60',
            'checkpoint': None,
            'name': 'Built-in AI 0.60',
            'kind': 'builtin',
        },
    }
    before = _ratings.signature()
    # The same match again counts for nothing, by its reference, its recording or its play.
    again = {'scenario': '5_vs_5', 'home': rex, 'away': anchor, 'score': [2, 1]}
    assert _ratings.record({**again, 'source': 'rating', 'ref': 'rate-1/0'}) is None
    assert _ratings.record({**again, 'source': 'match', 'seed': '7/0/left'}) is None
    assert _ratings.record({**again, 'source': 'match', 'home': rex, 'away': rex}) is None
    assert _ratings.signature() == before
    second = _ratings.record({**again, 'source': 'match', 'ref': 'm-1', 'recording': 'm-1'})
    assert second['recording'] == 'm-1' and 'actions' not in second
    assert _ratings.record({**again, 'source': 'tournament', 'recording': 'm-1'}) is None
    assert _ratings.signature() != before
    # A match drawn later takes its recording, and the row then names it.
    assert _ratings.point('rating', 'rate-1/0', 'm-7') and not _ratings.point('rating', 'no', 'm-8')
    assert _ratings.results()[0]['recording'] == 'm-7'
    assert _ratings.results()[0]['actions'] == {'run': 'rate-1', 'number': 0}
    # A result without a scenario is kept under none.
    assert _ratings.record({**again, 'scenario': None, 'source': 'match'})['scenario'] is None
    assert _ratings.scenarios() == {'5_vs_5': 2, None: 1}
    for wrong in (
        None,
        {**again, 'source': 'match', 'away': {}},
        {**again, 'source': 'match', 'away': _side('x', checkpoint=4)},
        {**again, 'source': 'match', 'score': [1]},
        {**again, 'source': 'match', 'score': [1, -1]},
        {**again, 'source': 'moon'},
        {**again, 'source': 'match', 'phase': 3},
        {**again, 'source': 'match', 'scenario': 5},
    ):
        with pytest.raises(cuju.errors.ArgumentError):
            _ratings.record(wrong)
    # A line that a stop cut short is skipped.
    with path.open('a', encoding='utf-8') as stream:
        stream.write('{"time": 3.0, "scen')
    assert len(_ratings.results()) == 3
    # A reset deletes every row of a team, at any checkpoint, and what the store knew of it.
    assert _ratings.reset('rex') and not _ratings.reset('rex')
    assert _ratings.results() == [] and set(_ratings.models()) == {'builtin:0.60'}


def test_tournaments_hand_their_matches_to_the_store(arena_home):
    def player(match, teams, provenance, progress):
        return {'score': [match['seed'] % 3, match['seed'] // 3 % 3]}

    cups = runner.Tournaments(player=player, start=False, interval=0.01)
    config = {
        'name': 'Spring cup',
        'seed': 9,
        'entrants': [{'kind': 'builtin', 'ability': value} for value in (0.2, 0.5, 0.8)],
        'stages': [{'format': 'league', 'scenario': 'academy_empty_goal_close', 'cycles': 1}],
    }
    entry = cups.create(config)
    assert cups.run(entry['id'], report=lambda line: None) == 'done'
    done = cups.get(entry['id'])
    cups.close()
    # Each match of the draw is one row of its scenario, with the tournament and the match.
    rows = _ratings.results('academy_empty_goal_close')
    matches = {f'{entry["id"]}/{m["key"]}': m for m in done['matches'] if m['result']}
    assert {row['ref'] for row in rows} == set(matches) and len(rows) == 3
    entrants = done['config']['entrants']
    for row in rows:
        match = matches[row['ref']]
        assert row['source'] == 'tournament' and row['phase'] == 'main'
        assert row['home']['key'] == entrants[match['home']]['key']
        assert row['away']['key'] == entrants[match['away']]['key']
        assert row['score'] == match['result']['score']
    # The ratings of the tournaments are the rows of the table of that scenario.
    table = runner.ratings()
    held = {row['id']: row for row in _evidence.table('academy_empty_goal_close')['models']}
    assert set(table) == {'builtin:0.20', 'builtin:0.50', 'builtin:0.80'}
    for key, value in table.items():
        assert value['rating'] == held[key]['rating'] and value['matches'] == 2
        assert (
            value['sources']['tournament'] == 2 and value['scenario'] == 'academy_empty_goal_close'
        )
        assert value['low'] <= value['rating'] <= value['high'] and value['method'] == 'points'
    assert runner.ratings('academy_empty_goal_close') == table and runner.ratings('5_vs_5') == {}
    # No file of the earlier ratings is written any more.
    assert not (runner.folder() / 'ratings.json').exists()
    assert not (arena_home / 'ratings' / 'ratings.json').exists()


def test_a_queue_survives_a_restart_in_the_middle_of_a_phase(arena_home):
    state = {
        'mode': 'on_demand',
        'ratings': {'a': 1510.0, 'b': 1490.0},
        'played': [{'home': 'a', 'away': 'b', 'seed': 1, 'score': [1, 0]}],
        'playing': [{'home': 'b', 'away': 'c', 'seed': 2}],
        'next': [{'home': 'a', 'away': 'c', 'seed': 3}],
        'count': 1,
        'most': 12,
        'settled': False,
        'round': 2,
    }
    # A run keeps its queue in its manifest.
    owner = f'run:{_runs.create("evaluation")["id"]}'
    assert _queue.queue_state(owner) is None
    saved = _queue.save_queue(owner, state)
    assert saved['saved'] and {key: saved[key] for key in state} == state
    # A server that starts again reads the queue, and the match that was playing plays again.
    resumed = _queue.queue_state(owner, resume=True)
    assert resumed['playing'] == []
    assert resumed['next'] == [state['playing'][0], state['next'][0]]
    assert resumed['played'] == state['played'] and resumed['count'] == 1
    assert _queue.queue_state(owner)['playing'] == state['playing']
    # A field that does not fit its type is left out.
    _queue.save_queue(owner, {**state, 'count': 'many', 'most': None, 'next': [3]})
    kept = _queue.queue_state(owner, resume=True)
    assert 'count' not in kept and kept['most'] is None
    assert kept['next'] == state['playing'] and _runs.get(owner[4:])['queue']['round'] == 2
    assert _queue.clear_queue(owner) and not _queue.clear_queue(owner)
    assert _queue.queue_state(owner) is None
    # A rating run, the seeding phase of a tournament among them, keeps its queue in its own
    # document, so a tournament owns no queue here.
    for wrong in ('c-1', 'league:c-1', 'tournament:c-1', 'run:../x', None):
        with pytest.raises(cuju.errors.ArgumentError):
            _queue.queue_state(wrong)
    with pytest.raises(cuju.errors.ArgumentError):
        _queue.save_queue(owner, [1])
    with pytest.raises(cuju.errors.ArgumentError):
        _queue.save_queue('run:r-none', state)


def test_a_rating_run_keeps_its_document_in_the_store(arena_home):
    assert _ratings.runs() == [] and _ratings.run('rate-1') is None
    first = {'id': 'rate-1', 'created': 10.0, 'state': 'queued'}
    second = {'id': 'rate-2', 'created': 20.0, 'state': 'done'}
    _ratings.save_run(first)
    _ratings.save_run(second)
    assert _ratings.run('rate-1') == first
    assert [document['id'] for document in _ratings.runs()] == ['rate-2', 'rate-1']
    assert not list((arena_home / 'ratings' / 'runs').glob('*.part'))
    # A file that does not read, or holds another identifier, lists as no run.
    (arena_home / 'ratings' / 'runs' / 'rate-3.json').write_text('{', encoding='utf-8')
    (arena_home / 'ratings' / 'runs' / 'rate-4.json').write_text('{"id": "x"}', encoding='utf-8')
    assert [document['id'] for document in _ratings.runs()] == ['rate-2', 'rate-1']
    assert _ratings.remove_run('rate-1') and not _ratings.remove_run('rate-1')
    assert _ratings.run('../rate-2') is None and not _ratings.remove_run('../rate-2')
    for wrong in ({}, {'id': '../x'}, None):
        with pytest.raises(cuju.errors.ArgumentError):
            _ratings.save_run(wrong)


def test_the_tournament_file_keeps_the_seeding_and_final_ratings(arena_home):
    elo = _ratings.tournament_elo(None)
    assert elo == {
        'version': _ratings.TOURNAMENT_VERSION,
        'scale': 'table',
        'method': 'points',
        'scenario': None,
        'anchor': None,
        'entrants': {},
    }
    _ratings.keep_seeding(elo, 'rex', 1023.5, [890.0, 1157.0], 8, 'measured', 2, '3fa9')
    _ratings.keep_seeding(elo, 'ana', 1100.0, origin='given')
    _ratings.keep_seeding(elo, 'old', 1004.0, [700.0, 1308.0], 1, 'lacking', 0)
    _ratings.keep_seeding(elo, 'kept', 990.0, [900.0, 1080.0], 12, 'known', 4)
    assert elo['entrants']['rex']['seeding'] == {
        'rating': 1023.5,
        'interval': [890.0, 1157.0],
        'matches': 8,
        'origin': 'measured',
        'measured': True,
        'anchor_matches': 2,
        'checkpoint': '3fa9',
    }
    assert elo['entrants']['ana']['seeding'] == {
        'rating': 1100.0,
        'interval': None,
        'matches': 0,
        'origin': 'given',
        'measured': False,
        'anchor_matches': 0,
        'checkpoint': None,
    }
    assert [elo['entrants'][key]['seeding']['origin'] for key in ('old', 'kept')] == [
        'lacking',
        'known',
    ]
    # The rating after the tournament stands with its interval and its matches.
    _ratings.keep_final(
        elo,
        {'rex': {'rating': 1040.2, 'low': 930.0, 'high': 1150.4, 'matches': 11}, 'ana': None},
    )
    assert elo['entrants']['rex']['final'] == {
        'rating': 1040.2,
        'interval': [930.0, 1150.4],
        'matches': 11,
    }
    assert elo['entrants']['ana']['final'] is None
    # The field reads back as it was written, through JSON.
    assert _ratings.tournament_elo(json.loads(json.dumps(elo))) == elo
    for wrong in (
        {'rating': float('nan')},
        {'rating': 1000.0, 'origin': 'guessed'},
        {'rating': 1000.0, 'matches': -1},
        {'rating': 1000.0, 'anchor_matches': 1.5},
    ):
        with pytest.raises(cuju.errors.ArgumentError):
            _ratings.keep_seeding(elo, 'rex', **wrong)


def test_a_tournament_file_of_before_reads_with_its_numbers():
    # A file of the first version tells whether its phase measured a rating, by the Elo rule
    # from 1500, and keeps the rating after the tournament as a number.
    first = {
        'version': 1,
        'entrants': {
            'a': {'seeding': {'rating': 1510, 'matches': 3, 'measured': True, 'spread': 4}},
            'b': {'seeding': {'rating': 'high'}, 'final': 1490},
            'c': {
                'seeding': {
                    'rating': 1600,
                    'interval': [1500, 1700],
                    'matches': 0,
                    'measured': False,
                },
                'final': None,
            },
            'd': 'odd',
        },
    }
    found = _ratings.tournament_elo(first)
    assert found['scale'] == 'earlier' and found['method'] == 'points'
    assert found['version'] == _ratings.TOURNAMENT_VERSION
    assert found['entrants'] == {
        'a': {
            'seeding': {
                'rating': 1510.0,
                'interval': None,
                'matches': 3,
                'origin': 'measured',
                'measured': True,
                'anchor_matches': 0,
                'checkpoint': None,
            },
            'final': None,
        },
        'b': {'seeding': None, 'final': {'rating': 1490.0, 'interval': None, 'matches': None}},
        'c': {
            'seeding': {
                'rating': 1600.0,
                'interval': [1500.0, 1700.0],
                'matches': 0,
                'origin': 'given',
                'measured': False,
                'anchor_matches': 0,
                'checkpoint': None,
            },
            'final': None,
        },
    }
    # A field that does not fit reads as a field without entrants.
    assert _ratings.tournament_elo('odd')['entrants'] == {}
    assert _ratings.tournament_elo({'entrants': 4})['entrants'] == {}
