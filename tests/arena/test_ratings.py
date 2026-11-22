# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the ratings of Evaluate, their tables, their two methods and the rating runs."""

import itertools
import json
import math
import re
import threading
from importlib import metadata
from pathlib import Path

import numpy as np
import pytest

import cuju.arena
import cuju.errors
from cuju.arena._evaluate import evaluate as _evaluate
from cuju.arena._evaluate import ratings as _ratings
from cuju.arena._evaluate import runner as _runner
from cuju.arena._evaluate import schedule as _schedule
from cuju.arena._store import library as _library
from cuju.arena._store import ratings as _store
from cuju.arena._tournaments import results as _results
from cuju.arena._tournaments import tournament as rules
from support.common import _SINGLE, _short_matches, _toy_team

ROOT = Path(__file__).resolve().parents[2]
_SCENARIO = 'academy_run_to_score_with_keeper'
_ANCHOR = 'builtin:0.60'


def _side(key, checkpoint=None, **fields):
    return {'key': key, 'checkpoint': checkpoint, 'name': key.title(), **fields}


def _keep(scenario, home, away, score, **fields):
    home = _side(home) if isinstance(home, str) else home
    away = _side(away) if isinstance(away, str) else away
    return _store.record(
        {'scenario': scenario, 'home': home, 'away': away, 'score': score, 'source': 'match'}
        | fields
    )


def _rows(table):
    return {row['id']: row for row in table['models']}


def _fake(strength, calls=None):
    """A player of rating runs in whose matches the stronger side wins by one goal."""

    def player(spec):
        if calls is not None:
            calls.append((spec['number'], spec['home'], spec['away'], spec['index']))
        home, away = strength[spec['home']], strength[spec['away']]
        goals = [1, 0] if home > away else [0, 1] if home < away else [1, 1]
        return _runner.Played([goals], [3], np.zeros((3, 1, 0), dtype=np.uint8))

    return player


def _builtins(*levels):
    return [_ratings.builtin_side(level) for level in levels]


def _settings(**run):
    return _ratings.settings({'ratings': {'run': {'seed': 5, **run}}})


@pytest.mark.parametrize('renderer', [None, '0.2.0'])
def test_a_new_rating_row_keeps_the_renderer_version_if_installed(
    arena_home, monkeypatch, renderer
):
    def version(package):
        assert package == 'cuju-render'
        if renderer is None:
            raise metadata.PackageNotFoundError(package)
        return renderer

    monkeypatch.setattr(metadata, 'version', version)
    result = _ratings.requested_match(
        '5_vs_5', ['builtin', 'builtin'], (0.2, 0.9), [0, 1], 'm-1', 3
    )
    versions = result['versions']
    assert versions['cuju']
    assert versions['cuju-arena'] == cuju.arena.__version__
    assert versions['numpy'] == np.__version__
    if renderer is None:
        assert 'cuju-render' not in versions
    else:
        assert versions['cuju-render'] == renderer


def test_a_table_holds_the_results_of_its_scenario_alone(arena_home):
    assert _ratings.overview()['scenarios'] == []
    _keep('5_vs_5', 'reds', 'blues', [2, 0])
    _keep('5_vs_5', 'blues', 'greens', [1, 1])
    _keep('11_vs_11_kaggle', 'reds', 'blues', [0, 3])
    # A result without a known scenario stays readable in a table of its own.
    _keep(None, 'reds', 'greys', [1, 0])
    # A match of a set length is another game, so it joins a table named by its length.
    _ratings.requested_match('5_vs_5', ['builtin', 'builtin'], (0.2, 0.9), [0, 1], 'm-1', 3, 400)
    listed = _ratings.scenarios()
    assert [entry['scenario'] for entry in listed] == [
        '11_vs_11_kaggle',
        '5_vs_5',
        '5_vs_5@400',
        None,
    ]
    assert [entry['results'] for entry in listed] == [1, 2, 1, 1]
    assert [entry['models'] for entry in listed] == [2, 3, 2, 2]
    small = _rows(_ratings.table('5_vs_5'))
    big = _rows(_ratings.table('11_vs_11_kaggle'))
    # Each table holds its own models beside the anchor, and the same teams rate apart.
    assert set(small) == {'reds', 'blues', 'greens', _ANCHOR}
    assert set(big) == {'reds', 'blues', _ANCHOR}
    assert small['reds']['rating'] > small['blues']['rating']
    assert big['reds']['rating'] < big['blues']['rating']
    assert small['reds']['matches'] == 1 and small['blues']['matches'] == 2
    unknown = _ratings.table(None)
    assert set(_rows(unknown)) == {'reds', 'greys', _ANCHOR} and unknown['scenario'] is None
    assert set(_rows(_ratings.table('5_vs_5@400'))) == {'builtin:0.20', 'builtin:0.90', _ANCHOR}
    # A scenario without a result has a table of the anchor alone.
    empty = _ratings.table('academy_corner')
    assert [row['id'] for row in empty['models']] == [_ANCHOR] and empty['results'] == 0
    overview = _ratings.overview()
    assert overview['anchor']['key'] == _ANCHOR and overview['anchor']['rating'] == 1000.0
    assert overview['method'] == 'points' and overview['earlier'] == 0
    assert overview['evidence'] == {'matches': 4, 'anchor_matches': 2, 'width': None}
    assert overview['sources'] == ['tournament', 'rating', 'evaluation', 'match']


def test_a_new_checkpoint_starts_without_evidence(arena_home):
    old, new = _side('rex', 'aaaa1111', kind='team'), _side('rex', 'bbbb2222', kind='team')
    anchor = _ratings.builtin_side(0.6)
    for number, score in enumerate(([2, 0], [1, 0], [3, 1], [1, 1])):
        _keep('5_vs_5', old, anchor, score, time=100.0 + number, ref=f'old-{number}')
    before = _rows(_ratings.table('5_vs_5'))
    assert before['rex@aaaa1111']['latest'] and before['rex@aaaa1111']['matches'] == 4
    assert before['rex@aaaa1111']['anchor_matches'] == 4 and before['rex@aaaa1111']['rated']
    # The files of the team change, so the new checkpoint is a model of its own.
    report = _ratings.evidence([new], '5_vs_5')
    assert not report['complete']
    (fresh,) = report['models']
    assert fresh['matches'] == 0 and fresh['rating'] == 1000.0
    assert fresh['lacking'] == {'matches': 4, 'anchor_matches': 2}
    assert fresh['high'] - fresh['low'] > 1300
    _keep('5_vs_5', anchor, new, [2, 0], time=200.0, ref='new-0')
    rows = _rows(_ratings.table('5_vs_5'))
    # The earlier checkpoint keeps its rating and its matches under the same team.
    for name in ('rating', 'low', 'high', 'matches', 'points', 'bradley_terry'):
        assert rows['rex@aaaa1111'][name] == pytest.approx(before['rex@aaaa1111'][name])
    assert rows['rex@bbbb2222']['matches'] == 1 and rows['rex@bbbb2222']['rating'] < 1000
    assert rows['rex@bbbb2222']['latest'] and not rows['rex@aaaa1111']['latest']
    assert rows['rex@bbbb2222']['lacking'] == {'matches': 3, 'anchor_matches': 1}
    team = _ratings.team('rex', '5_vs_5')
    assert team['latest']['id'] == 'rex@bbbb2222' and team['name'] == 'Rex'
    assert [row['id'] for row in team['checkpoints']] == ['rex@bbbb2222', 'rex@aaaa1111']
    # The history runs across the checkpoints, each result with the rating by points around it.
    history = team['history']
    assert [entry['checkpoint'] for entry in history] == ['aaaa1111'] * 4 + ['bbbb2222']
    assert [entry['score'] for entry in history] == [[2, 0], [1, 0], [3, 1], [1, 1], [0, 2]]
    assert history[0]['points']['before'] == 1000.0
    for entry, after in itertools.pairwise(history[:4]):
        assert after['points']['before'] == entry['points']['after']
    assert history[3]['points']['after'] == rows['rex@aaaa1111']['points']['rating']
    assert history[4]['points'] == {
        'before': 1000.0,
        'after': rows['rex@bbbb2222']['points']['rating'],
    }
    assert history[4]['opponent']['key'] == _ANCHOR and history[4]['ref'] == 'new-0'
    assert team['opponents'] == [
        {
            'id': _ANCHOR,
            'key': _ANCHOR,
            'checkpoint': None,
            'name': 'Built-in AI 0.60',
            'name_id': 'server.tournament.label.builtin_ability',
            'name_values': {'ability': '0.60'},
            'matches': 5,
            'wins': 3,
            'draws': 1,
            'losses': 1,
        }
    ]
    assert _ratings.team('nobody', '5_vs_5') is None
    # The row that stands for the team is that of its newest checkpoint.
    assert _ratings.by_key('5_vs_5')['rex']['id'] == 'rex@bbbb2222'


def test_the_built_in_ai_is_a_model_and_the_anchor_stands_at_1000(arena_home):
    weak, strong, anchor = _builtins(0.2, 0.9, 0.6)
    for score in ([0, 2], [1, 2], [0, 1]):
        _keep(_SCENARIO, weak, anchor, score)
    for score in ([2, 0], [1, 0], [1, 1]):
        _keep(_SCENARIO, strong, anchor, score)
    _keep(_SCENARIO, strong, weak, [3, 0])
    for method in ('points', 'bradley_terry'):
        table = _ratings.table(_SCENARIO, method=method)
        rows = _rows(table)
        assert table['method'] == method and table['anchor']['matches'] == 6
        assert [row['id'] for row in table['models']] == ['builtin:0.90', _ANCHOR, 'builtin:0.20']
        assert [row['rank'] for row in table['models']] == [1, 2, 3]
        # The anchor is the fixed point of the scale by either method, without an interval.
        for name in ('points', 'bradley_terry'):
            assert rows[_ANCHOR][name] == {
                'rating': 1000.0,
                'low': 1000.0,
                'high': 1000.0,
                'deviation': 0.0,
            }
        assert rows[_ANCHOR]['anchor'] and rows[_ANCHOR]['rated'] and not rows[_ANCHOR]['lacking']
        assert rows['builtin:0.90']['kind'] == 'builtin'
        assert rows['builtin:0.90']['name'] == 'Built-in AI 0.90'
        assert rows['builtin:0.90']['anchor_matches'] == 3 and rows['builtin:0.90']['matches'] == 4
        assert rows['builtin:0.90'][method]['rating'] == rows['builtin:0.90']['rating']
        for row in table['models']:
            assert row['low'] <= row['rating'] <= row['high']
            assert row['wins'] + row['draws'] + row['losses'] == row['matches']
    # Another anchor moves the fixed point, and every rating reads against the new one.
    moved = _ratings.table(
        _SCENARIO, overrides=['ratings.anchor=0.9', 'ratings.method=bradley_terry']
    )
    assert moved['anchor']['key'] == 'builtin:0.90'
    assert _rows(moved)['builtin:0.90']['rating'] == 1000.0
    assert _rows(moved)['builtin:0.60']['rating'] < 1000.0
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _ratings.table(_SCENARIO, method='glicko')
    assert caught.value.message_id == 'server.ratings.method'


def _games(count=40, seed=3):
    """Results among four models and the anchor, the stronger winning more often."""
    import random

    generator = random.Random(seed)
    strength = {'a': 300.0, 'b': 100.0, 'c': -150.0, 'd': -400.0, _ANCHOR: 0.0}
    names = list(strength)
    games = []
    for _ in range(count):
        home, away = generator.sample(names, 2)
        share = 1 / (1 + 10 ** ((strength[away] - strength[home]) / 400))
        draw = generator.random()
        score = [1, 1] if draw < 0.2 else [1, 0] if generator.random() < share else [0, 1]
        games.append((home, away, score))
    return games


def test_points_follow_the_stored_results_in_their_order(arena_home):
    games = _games()
    for number, (home, away, score) in enumerate(games):
        _keep('5_vs_5', home, away, score, time=10.0 + number)
    # The Elo rule of the tournaments, played by hand over the rows in the order of the file.
    expected = dict.fromkeys(('a', 'b', 'c', 'd', _ANCHOR), 1000.0)
    information = dict.fromkeys(expected, 0.0)
    for row in _store.results('5_vs_5'):
        home, away, score = row['home']['key'], row['away']['key'], row['score']
        share = 1 / (1 + 10 ** ((expected[away] - expected[home]) / 400))
        actual = 1.0 if score[0] > score[1] else 0.5 if score[0] == score[1] else 0.0
        change = rules.RATING_STEP * (actual - share)
        if home != _ANCHOR:
            expected[home] = round(expected[home] + change, 2)
        if away != _ANCHOR:
            expected[away] = round(expected[away] - change, 2)
        information[home] += share * (1 - share)
        information[away] += share * (1 - share)
    table = _ratings.table('5_vs_5')
    rows = _rows(table)
    assert {key: rows[key]['points']['rating'] for key in expected} == expected
    quantile = _schedule.quantile(0.95)
    for key in ('a', 'b', 'c', 'd'):
        deviation = 1 / math.sqrt(1 / 350**2 + (math.log(10) / 400) ** 2 * information[key])
        assert rows[key]['points']['deviation'] == round(deviation, 2)
        assert rows[key]['points']['low'] == round(expected[key] - quantile * deviation, 2)
        assert rows[key]['points']['high'] == round(expected[key] + quantile * deviation, 2)
    # The same rows read again give the same table, and the table names its methods.
    assert _ratings.table('5_vs_5') == table
    assert (
        table['methods']['points']['step'] == 20.0 and table['methods']['points']['start'] == 1000
    )
    assert table['methods']['bradley_terry']['prior_draws'] == _results.PRIOR
    # The value by points depends on the order of the matches, and the fit does not.
    forward, _ = _ratings.points(games, _ANCHOR)
    backward, _ = _ratings.points(games[::-1], _ANCHOR)
    assert forward['a']['rating'] != backward['a']['rating']
    assert forward[_ANCHOR]['rating'] == backward[_ANCHOR]['rating'] == 1000.0
    fitted = _ratings.bradley_terry(games, _ANCHOR)
    again = _ratings.bradley_terry(games[::-1], _ANCHOR)
    for key in fitted:
        assert again[key]['rating'] == pytest.approx(fitted[key]['rating'], abs=1e-6)
        assert again[key]['deviation'] == pytest.approx(fitted[key]['deviation'], abs=1e-6)


def test_the_fit_holds_the_anchor_and_widens_with_little_evidence():
    games = _games(60, seed=9)
    fitted = _ratings.bradley_terry(games, _ANCHOR)
    assert fitted[_ANCHOR] == {'rating': 1000.0, 'deviation': 0.0}
    # At the fit, what each model won equals what the ratings and the prior expect of it, one
    # draw of the model against a model of the strength of the anchor.
    scale = math.log(10) / 400
    for name in ('a', 'b', 'c', 'd'):
        won = expected = 0.0
        for home, away, score in games:
            if name not in (home, away):
                continue
            other = away if name == home else home
            mine, theirs = score if name == home else score[::-1]
            won += 1.0 if mine > theirs else 0.5 if mine == theirs else 0.0
            gap = fitted[other]['rating'] - fitted[name]['rating']
            expected += 1 / (1 + math.exp(scale * gap))
        virtual = 1 / (1 + math.exp(scale * (1000.0 - fitted[name]['rating'])))
        assert won + _results.PRIOR / 2 == pytest.approx(
            expected + _results.PRIOR * virtual, abs=1e-8
        )
    order = sorted(fitted, key=lambda name: -fitted[name]['rating'])
    assert order.index('a') < order.index('d')
    # A model that has only met the anchor keeps its rating whatever the others play.
    alone = _ratings.bradley_terry([('x', _ANCHOR, [1, 0])] * 3, _ANCHOR)
    among = _ratings.bradley_terry(
        [('x', _ANCHOR, [1, 0])] * 3 + [('y', _ANCHOR, [0, 2])] * 5, _ANCHOR
    )
    assert among['x'] == pytest.approx(alone['x'])
    # A model with one match shows a wide interval, and one with many a narrow one.
    one = _ratings.bradley_terry([('new', _ANCHOR, [1, 0])], _ANCHOR)
    many = _ratings.bradley_terry(
        [('old', _ANCHOR, [1, 0] if number % 2 else [0, 1]) for number in range(60)], _ANCHOR
    )
    assert one['new']['rating'] > 1000 and one['new']['deviation'] > 250
    assert many['old']['rating'] == pytest.approx(1000.0, abs=1e-6)
    assert many['old']['deviation'] < 100
    by_points, _ = _ratings.points([('new', _ANCHOR, [1, 0])], _ANCHOR)
    assert by_points['new']['rating'] == 1010.0 and by_points['new']['deviation'] > 240
    # A model that won every match keeps a finite rating and a wide interval.
    perfect = _ratings.bradley_terry([('top', _ANCHOR, [2, 0])] * 10, _ANCHOR)
    assert 1200 < perfect['top']['rating'] < 2000 and perfect['top']['deviation'] > 150
    # Matches among models that never meet the anchor leave them loosely tied to it.
    apart = _ratings.bradley_terry(
        [('x', 'y', [1, 0] if number % 3 else [0, 1]) for number in range(30)], _ANCHOR
    )
    tied = _ratings.bradley_terry(
        [('x', 'y', [1, 0] if number % 3 else [0, 1]) for number in range(30)]
        + [('x', _ANCHOR, [1, 1])] * 6,
        _ANCHOR,
    )
    assert apart['x']['deviation'] > 2 * tied['x']['deviation']


def test_the_methods_agree_on_a_clear_order_and_the_setting_ranks_the_table(arena_home):
    order = ['a', 'b', _ANCHOR, 'c', 'd']
    for _ in range(3):
        for first, second in itertools.combinations(order, 2):
            _keep('5_vs_5', first, second, [2, 0])
    by_points = _ratings.table('5_vs_5')
    fitted = _ratings.table('5_vs_5', overrides=['ratings.method=bradley_terry'])
    assert by_points['method'] == 'points' and fitted['method'] == 'bradley_terry'
    assert [row['id'] for row in by_points['models']] == order
    assert [row['id'] for row in fitted['models']] == order
    for table in (by_points, fitted):
        rows = _rows(table)
        assert rows[_ANCHOR]['rating'] == 1000.0
        assert rows['a']['rating'] > rows['b']['rating'] > 1000 > rows['c']['rating']
        # Every row holds both methods, whichever ranks the table.
        assert rows['a']['points'] == _rows(by_points)['a']['points']
        assert rows['a']['bradley_terry'] == _rows(fitted)['a']['bradley_terry']
    assert _rows(by_points)['a']['rating'] == _rows(by_points)['a']['points']['rating']
    assert _rows(fitted)['a']['rating'] == _rows(fitted)['a']['bradley_terry']['rating']
    # The methods part on results whose order matters, a late run of wins of a weak start.
    _keep('11_vs_11_kaggle', 'late', _ANCHOR, [0, 1])
    for _ in range(6):
        _keep('11_vs_11_kaggle', 'early', _ANCHOR, [1, 0])
    for _ in range(6):
        _keep('11_vs_11_kaggle', 'early', 'late', [0, 1])
    rows = _rows(_ratings.table('11_vs_11_kaggle'))
    assert rows['late']['points']['rating'] != rows['late']['bradley_terry']['rating']
    # An interval that the evidence asks for counts by the method of the table.
    wide = _ratings.table('5_vs_5', overrides=['ratings.evidence.width=100'])
    assert _rows(wide)['a']['lacking'] == {'width': _rows(wide)['a']['width']}
    assert _rows(wide)['a']['width'] > 100 and not _rows(wide)['a']['rated']


def test_a_result_counts_once_and_the_sources_are_a_setting(arena_home):
    first = _keep(
        '5_vs_5', 'reds', 'blues', [1, 0], source='tournament', ref='cup/0/1', seed='7/0/left'
    )
    assert first['source'] == 'tournament' and first['scenario'] == '5_vs_5'
    # The same source and reference, the same recording and the same play are the same match.
    assert _keep('5_vs_5', 'reds', 'blues', [1, 0], source='tournament', ref='cup/0/1') is None
    assert _keep('5_vs_5', 'reds', 'blues', [1, 0], source='match', seed='7/0/left') is None
    kept = _keep('5_vs_5', 'reds', 'blues', [0, 0], source='match', ref='m-9', recording='m-9')
    assert kept['recording'] == 'm-9'
    assert (
        _keep('5_vs_5', 'reds', 'blues', [0, 0], source='rating', ref='r/1', recording='m-9')
        is None
    )
    # Another seed, another scenario and another checkpoint are other matches.
    assert _keep('5_vs_5', 'reds', 'blues', [1, 0], seed='8/0/left') is not None
    assert _keep('5_vs_5@400', 'reds', 'blues', [1, 0], seed='7/0/left') is not None
    assert _keep('5_vs_5', _side('reds', 'c1'), 'blues', [1, 0], seed='7/0/left') is not None
    # Two sides of one model tell nothing.
    assert _keep('5_vs_5', 'reds', 'reds', [1, 0]) is None
    assert len(_store.results('5_vs_5')) == 4
    table = _ratings.table('5_vs_5')
    assert table['results'] == 4 and _rows(table)['blues']['matches'] == 4
    assert _rows(table)['blues']['sources'] == {
        'tournament': 1,
        'rating': 0,
        'evaluation': 0,
        'match': 3,
    }
    # The setting names the sources that count, and a table of the others leaves the rest out.
    only = _ratings.table('5_vs_5', overrides=['ratings.sources=["tournament"]'])
    assert only['results'] == 1 and only['sources'] == ['tournament']
    assert _rows(only)['blues']['matches'] == 1 and 'reds@c1' not in _rows(only)
    assert [
        entry['results'] for entry in _ratings.scenarios(overrides=['ratings.sources=["rating"]'])
    ] == []


def test_a_rating_run_meets_the_anchor_first_and_settles(arena_home):
    strength = {'builtin:0.20': 1, 'builtin:0.90': 3, _ANCHOR: 2}
    calls = []
    seen = []
    document = _ratings.create(
        _builtins(0.2, 0.9), '5_vs_5', _settings(mode='on_demand', at_once=1, matches=40)
    )
    assert document['state'] == 'queued' and document['models'] == ['builtin:0.20', 'builtin:0.90']
    assert document['plan']['needs'] == {'builtin:0.20': 4, 'builtin:0.90': 4}
    done = _ratings.execute(document['id'], player=_fake(strength, calls), progress=seen.append)
    assert done['state'] == 'done' and done['record']['state'] == 'none'
    progress = _ratings.progress_of(done)
    # Each model first meets the anchor twice, from either side, before any other match.
    assert all(_ANCHOR in (home, away) for _, home, away, _ in calls[:4])
    assert sorted(home if away == _ANCHOR else away for _, home, away, _ in calls[:4]) == [
        'builtin:0.20',
        'builtin:0.20',
        'builtin:0.90',
        'builtin:0.90',
    ]
    assert {home for _, home, _, _ in calls[:4]} == {'builtin:0.20', 'builtin:0.90', _ANCHOR}
    assert progress['phase'] == 'rating' and progress['method'] == 'points'
    assert progress['anchor'] == _ANCHOR and progress['scenario'] == '5_vs_5'
    assert progress['done'] and progress['settled'] and progress['played'] == progress['total']
    assert progress['played'] < progress['most'] == 80
    assert len(progress['history']) == progress['played'] == len(calls)
    assert [entry['number'] for entry in progress['history']] == list(range(len(calls)))
    assert seen[-1]['played'] == progress['played'] and seen[0]['played'] == 0
    ratings = progress['ratings']
    assert ratings[_ANCHOR] == {
        'rating': 1000.0,
        'low': 1000.0,
        'high': 1000.0,
        'matches': ratings[_ANCHOR]['matches'],
        'measured': False,
    }
    assert ratings['builtin:0.90']['rating'] > 1000 > ratings['builtin:0.20']['rating']
    assert all(ratings[key]['matches'] >= 4 for key in ('builtin:0.20', 'builtin:0.90'))
    assert progress['entrants']['builtin:0.90'] == {
        'name': 'Built-in AI 0.90',
        'name_id': 'server.tournament.label.builtin_ability',
        'name_values': {'ability': '0.90'},
        'kind': 'builtin',
        'index': 1,
        'checkpoint': None,
        'model': 'builtin:0.90',
        'label': None,
        'step': None,
        'measured': True,
    }
    # Every counted match is a row of the table, with where its actions lie.
    rows = _store.results('5_vs_5')
    assert len(rows) == progress['played']
    assert all(row['source'] == 'rating' and row['phase'] == 'rating' for row in rows)
    assert all(row['versions']['cuju-arena'] == cuju.arena.__version__ for row in rows)
    assert [row['ref'] for row in rows] == [f'{done["id"]}/{n}' for n in range(len(rows))]
    assert rows[0]['actions'] == {'run': done['id'], 'number': 0}
    assert all('recording' not in row for row in rows)
    # The rating by points of the table is the one that the run showed after its last match.
    table = _rows(_ratings.table('5_vs_5'))
    for key in ('builtin:0.20', 'builtin:0.90'):
        assert table[key]['points']['rating'] == ratings[key]['rating']
        assert table[key]['matches'] == ratings[key]['matches']
        assert done['after'][key]['points'] == table[key]['points']
        assert done['after'][key]['lacking'] is None
        assert done['before'][key] is None
    view = _ratings.get(done['id'])
    assert view['progress'] == progress and len(view['matches']) == progress['played']
    assert view['matches'][0]['actions'] == 'm0.npz' and view['matches'][0]['digest']
    assert [entry['id'] for entry in _ratings.runs()] == [done['id']]
    assert _ratings.runs()[0]['played'] == progress['played'] and _ratings.runs()[0]['settled']
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _ratings.execute(done['id'], player=_fake(strength))
    assert caught.value.message_id == 'server.ratings.ended'
    # A second run starts from the table, so the models that hold evidence need less.
    again = _ratings.create(_builtins(0.2, 0.9), '5_vs_5', _settings(mode='on_demand'))
    assert again['plan']['needs'] == {'builtin:0.20': 1, 'builtin:0.90': 1}
    assert again['start']['builtin:0.90']['rating'] == table['builtin:0.90']['points']['rating']
    assert again['before']['builtin:0.90']['matches'] == table['builtin:0.90']['matches']
    assert _ratings.remove(done['id']) and not _ratings.remove(done['id'])
    assert len(_store.results('5_vs_5')) == len(rows)


def test_a_rating_run_stops_at_its_most_matches_and_refuses_what_does_not_fit(arena_home):
    level = {'builtin:0.40': 1, 'builtin:0.50': 1, _ANCHOR: 1}
    document = _ratings.create(
        _builtins(0.4, 0.5), '5_vs_5', _settings(mode='on_demand', matches=3)
    )
    done = _ratings.execute(document['id'], player=_fake(level))
    progress = _ratings.progress_of(done)
    # Level models never settle, so the run plays its most matches, two models times three.
    assert progress['played'] == progress['total'] == progress['most'] == 6
    assert not progress['settled'] and progress['done']
    batched = _ratings.create(_builtins(0.4, 0.5), '5_vs_5', _settings(matches=3))
    assert batched['plan']['mode'] == 'batched'
    played = _ratings.execute(batched['id'], player=_fake(level))
    # A batched run plays its whole plan, the anchor first, each model three matches.
    history = _ratings.progress_of(played)['history']
    assert len(history) == 5 and all(_ANCHOR in (e['home'], e['away']) for e in history[:4])
    assert {history[4]['home'], history[4]['away']} == {'builtin:0.40', 'builtin:0.50'}
    for models, message in (
        ([], 'server.ratings.nobody'),
        ([_ratings.builtin_side(0.6)], 'server.ratings.nobody'),
        (_builtins(0.3, 0.3), 'server.ratings.twice'),
    ):
        with pytest.raises(cuju.errors.ArgumentError) as caught:
            _ratings.create(models, '5_vs_5', _settings())
        assert caught.value.message_id == message
    for text in ('builtin:2', 'builtin:low', '', None, 'no-such-team'):
        with pytest.raises(cuju.errors.ArgumentError):
            _ratings.side_of(text)
    assert _ratings.side_of('builtin')['key'] == 'builtin:scenario'
    assert _ratings.side_of('builtin:0.3')['key'] == 'builtin:0.30'
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _ratings.get('rate-none')
    assert caught.value.message_id == 'server.ratings.no_run'


def test_a_rating_run_resumes_after_a_stop_with_the_same_pairings(arena_home):
    strength = {'builtin:0.10': 1, 'builtin:0.30': 2, 'builtin:0.80': 4, _ANCHOR: 3}
    models = _builtins(0.1, 0.3, 0.8)
    options = {'mode': 'on_demand', 'at_once': 2, 'settle': None, 'matches': 4}

    def played(document):
        return [
            (e['number'], e['home'], e['away'], e['index'], e['score'])
            for e in _ratings.progress_of(document)['history']
        ]

    whole = _ratings.execute(
        _ratings.create(models, '5_vs_5', _settings(**options))['id'], player=_fake(strength)
    )
    assert len(played(whole)) == 12
    _store.reset('builtin:0.10'), _store.reset('builtin:0.30'), _store.reset('builtin:0.80')
    assert _store.results('5_vs_5') == []
    stopped = _ratings.create(models, '5_vs_5', _settings(**options))
    count = [0]
    calls = []

    def stop():
        return count[0] >= 5

    def player(spec):
        count[0] += 1
        return _fake(strength, calls)(spec)

    left = _ratings.execute(stopped['id'], player=player, stopped=stop)
    # The run left off with what it had played, and waits to go on.
    assert left['state'] == 'queued' and 4 <= len(played(left)) < 12
    assert len(_store.results('5_vs_5')) == len(played(left))
    assert _ratings.get(stopped['id'])['progress']['done'] is False
    again = _ratings.execute(stopped['id'], player=_fake(strength, calls))
    assert again['state'] == 'done'
    # The same pairings in the same order, on the same indexes, as the run that nothing cut.
    assert played(again) == played(whole)
    assert len(_store.results('5_vs_5')) == 12
    assert [row['ref'].split('/')[1] for row in _store.results('5_vs_5')] == [
        str(number) for number in range(12)
    ]
    # No match whose result was kept played again.
    assert len(calls) <= 12 + 2 and {call[0] for call in calls} == set(range(12))
    # A cancel ends a run for good.
    cancelled = _ratings.create(models, '5_vs_5', _settings(**options))
    ended = _ratings.execute(cancelled['id'], player=_fake(strength), cancelled=lambda: True)
    assert ended['state'] == 'cancelled' and ended['ended']


def test_a_run_of_the_built_in_ai_keeps_its_actions_and_draws_what_is_asked(arena_home):
    document = cuju.arena._evaluate.ratings.rate(
        ['builtin:0.1', 'builtin:1'],
        _SCENARIO,
        overrides=['ratings.run.matches=2', 'ratings.run.seed=11', 'ratings.run.keep="first:1"'],
    )
    assert document['state'] == 'done' and document['progress']['mode'] == 'batched'
    matches = document['matches']
    # Two models, two matches each against the anchor, all in batches, none as a recording.
    assert len(matches) == 4 and all(match['length'] > 0 for match in matches)
    assert [match['keep'] for match in matches] == [True, False, False, False]
    assert matches[0]['recording'] and not any(match['recording'] for match in matches[1:])
    assert document['record'] == {'state': 'done', 'drawn': 1, 'failed': 0}
    rows = _store.results(_SCENARIO)
    assert rows[0]['recording'] == matches[0]['recording'] and 'recording' not in rows[1]
    assert all(row['actions']['run'] == document['id'] for row in rows)
    # The kept recording is the match, with its score, its sides and its run.
    summary = next(m for m in _library.matches() if m['id'] == matches[0]['recording'])
    assert summary['score'] == matches[0]['score']
    assert summary['provenance']['run']['id'] == document['id']
    # Every match plays again from its seed and its actions to the same result.
    assert _ratings.check(document['id']) == {
        'id': document['id'],
        'checked': 4,
        'same': True,
        'problems': [],
    }
    # Any other match is drawn on request, and its row then names the recording.
    drawn = _ratings.draw(document['id'], [2])
    assert set(drawn['drawn']) == {'2'} and not drawn['failed']
    assert _store.results(_SCENARIO)[2]['recording'] == drawn['drawn']['2']
    assert _ratings.get(document['id'])['matches'][2]['recording'] == drawn['drawn']['2']
    assert _ratings.draw(document['id'], [2]) == drawn
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _ratings.draw(document['id'], [9])
    assert caught.value.message_id == 'server.ratings.no_match'
    # A changed file of actions is found, by the check and by a draw.
    folder = _runner.actions_folder(document['id'])
    with np.load(folder / 'm3.npz') as data:
        arrays = {name: data[name] for name in data.files}
    arrays['lengths'] = arrays['lengths'] - 1
    np.savez_compressed(folder / 'm3.npz', **arrays)
    found = _ratings.check(document['id'])
    assert not found['same'] and [p['problem'] for p in found['problems']] == ['changed']
    assert 'changed' in _ratings.draw(document['id'], [3])['failed']['3']
    # The same run again, on the same seed, plays the same matches, which count once.
    before = len(_store.results(_SCENARIO))
    again = _ratings.rate(
        ['builtin:0.1', 'builtin:1'],
        _SCENARIO,
        overrides=['ratings.run.matches=2', 'ratings.run.seed=11'],
    )
    assert [m['score'] for m in again['matches']] == [m['score'] for m in matches]
    assert len(_store.results(_SCENARIO)) == before


def test_a_team_plays_its_rating_run_in_a_process_of_its_own(arena_home, tmp_path):
    team = _toy_team(tmp_path, 'toy', _SINGLE, '11v11')
    checkpoint = _ratings.checkpoint_of(team['id'])
    assert checkpoint and _ratings.side_of(f'team:{team["id"]}')['checkpoint'] == checkpoint
    options = ['ratings.run.matches=1', 'ratings.run.anchor_matches=1', 'ratings.run.seed=4']
    document = _ratings.rate([team['id']], _SCENARIO, overrides=options)
    assert document['state'] == 'done' and document['progress']['mode'] == 'on_demand'
    (match,) = document['matches']
    assert match['score'] is not None and match['length'] > 0 and not match['recording']
    assert {match['home'], match['away']} == {team['id'], _ANCHOR}
    (row,) = _store.results(_SCENARIO)
    mine = row['home'] if row['home']['key'] == team['id'] else row['away']
    assert mine == {'key': team['id'], 'checkpoint': checkpoint}
    table = _rows(_ratings.table(_SCENARIO))
    assert table[f'{team["id"]}@{checkpoint}']['current'] is True
    assert table[f'{team["id"]}@{checkpoint}']['kind'] == 'team'
    assert _ratings.check(document['id'])['same']
    # The files of the team change, so its next result belongs to another model.
    waiting = _ratings.create(
        [_ratings.side_of(team['id'])], _SCENARIO, _ratings.settings(overrides=options)
    )
    folder = arena_home / 'teams' / team['id']
    (folder / 'main.py').write_text(_SINGLE + '\n# trained further\n', encoding='utf-8')
    changed = _ratings.checkpoint_of(team['id'])
    assert changed and changed != checkpoint
    assert _rows(_ratings.table(_SCENARIO))[f'{team["id"]}@{checkpoint}']['current'] is False
    # A run that began with the earlier files refuses to rate the new ones under their name.
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _ratings.execute(waiting['id'])
    assert caught.value.message_id == 'server.ratings.changed'
    assert _ratings.get(waiting['id'])['state'] == 'failed'
    report = _ratings.evidence([_ratings.side_of(team['id'])], _SCENARIO)
    assert report['models'][0]['matches'] == 0 and report['models'][0]['checkpoint'] == changed


def test_evaluations_and_requested_matches_are_evidence(arena_home, monkeypatch):
    from cuju.arena._playing import play as _play

    settings = {
        'evaluate': {
            'scenario': _SCENARIO,
            'episodes': 2,
            'checkpoint': {'id': 'step-2000', 'label': 'PPO at 2000', 'step': 2000},
            'name': 'PPO',
            'agents': 0,
            'opponents': [{'kind': 'builtin', 'difficulty': 0.6}],
            'record': {'keep': 'nothing', 'background': False},
        }
    }
    found = _evaluate.evaluate(lambda observation, batch: None, settings)
    rows = _store.results(_SCENARIO)
    # A named policy is rated by its name at the checkpoint of its evaluation.
    assert len(rows) == 2 and all(row['source'] == 'evaluation' for row in rows)
    assert all(row['versions']['cuju-arena'] == cuju.arena.__version__ for row in rows)
    assert rows[0]['home'] == {'key': 'policy:ppo', 'checkpoint': 'step-2000'}
    assert rows[0]['away'] == {'key': _ANCHOR, 'checkpoint': None}
    assert [row['ref'] for row in rows] == [f'{found.id}/0', f'{found.id}/1']
    assert rows[1]['actions'] == {'run': found.id, 'number': 1}
    assert [row['score'] for row in rows] == [
        [e['goals_for'], e['goals_against']] for e in found.episodes
    ]
    table = _rows(_ratings.table(_SCENARIO))
    row = table['policy:ppo@step-2000']
    assert row['matches'] == row['anchor_matches'] == 2 and row['sources']['evaluation'] == 2
    assert row['label'] == 'PPO at 2000' and row['step'] == 2000 and row['name'] == 'PPO'
    # The same evaluation again plays the same matches, which count once.
    _evaluate.evaluate(lambda observation, batch: None, settings)
    assert len(_store.results(_SCENARIO)) == 2
    # With rate false an evaluation hands nothing over.
    off = {'evaluate': {**settings['evaluate'], 'rate': False, 'seed': 77}}
    _evaluate.evaluate(lambda observation, batch: None, off)
    assert len(_store.results(_SCENARIO)) == 2
    # A requested match is evidence too, each side the built-in AI at the difficulty it was given.
    _short_matches(monkeypatch, 30)
    path = _play.play('builtin', 'builtin', _SCENARIO, seed=5, difficulty=(0.2, None))
    rows = _store.results(_SCENARIO)
    assert len(rows) == 3 and rows[2]['source'] == 'match'
    assert rows[2]['home']['key'] == 'builtin:0.20' and rows[2]['away']['key'] == 'builtin:scenario'
    assert rows[2]['recording'] == rows[2]['ref'] == path.stem
    assert rows[2]['score'] == path.summary['score']
    # The same seed again is the same match, and a match of a set length joins another table.
    _play.play('builtin', 'builtin', _SCENARIO, seed=5, difficulty=(0.2, None))
    assert len(_store.results(_SCENARIO)) == 3
    _play.play('builtin', 'builtin', _SCENARIO, seed=5, difficulty=(0.2, None), length=60)
    assert len(_store.results(_SCENARIO)) == 3
    assert len(_store.results(f'{_SCENARIO}@60')) == 1


def test_a_server_takes_up_the_rating_runs_that_a_stop_left(arena_home):
    document = _ratings.create(_builtins(0.3), '5_vs_5', _settings(matches=2), job='server')
    own = _ratings.create(_builtins(0.4), '5_vs_5', _settings(matches=2))
    started = []
    done = threading.Event()

    class Process:
        stderr = None

        def wait(self):
            done.set()
            return 3

        def kill(self):
            pass

    def starter(identifier):
        started.append(identifier)
        return Process()

    _ratings._Jobs(starter)
    assert done.wait(10)
    for _ in range(100):
        if _ratings.get(document['id'])['state'] == 'failed':
            break
        threading.Event().wait(0.05)
    # The run of the server was started again, and a process that ends without a result fails it.
    assert started == [document['id']]
    failed = _ratings.get(document['id'])
    assert failed['state'] == 'failed' and failed['error_id'] == 'server.ratings.exit'
    assert _ratings.get(own['id'])['state'] == 'queued'


def test_the_command_rates_models_and_prints_the_table(arena_home, capsys):
    from cuju.arena.__main__ import main

    arguments = ['rating', 'run', 'builtin:0.1', 'builtin:1', '--scenario', _SCENARIO]
    options = ['--set', 'ratings.run.matches=2', '--set', 'ratings.run.seed=3']
    assert main([*arguments, *options]) == 0
    printed = capsys.readouterr()
    lines = printed.out.splitlines()
    assert lines[0].endswith(f', {_SCENARIO}, done, 4 matches played')
    assert lines[1].startswith('    Built-in AI 0.10, points ') and 'Bradley and Terry' in lines[1]
    assert lines[1].endswith('2 matches, 2 against the anchor, lacks evidence')
    assert '4 of 4 matches played' in printed.err
    assert main(['rating', 'table']) == 0
    assert capsys.readouterr().out.strip() == f'{_SCENARIO}  3 models  4 results'
    assert main(['rating', 'table', '--scenario', _SCENARIO, '--method', 'bradley_terry']) == 0
    printed = capsys.readouterr()
    lines = printed.out.splitlines()
    assert printed.err.strip() == (
        f'{_SCENARIO}, 4 results, ranked by bradley_terry, anchor Built-in AI 0.60 at 1000'
    )
    assert len(lines) == 3 and any(line.endswith('the anchor') for line in lines)
    assert all(line.startswith('builtin:') for line in lines)
    assert main(['rating', 'table', '--scenario', _SCENARIO, '--json']) == 0
    assert json.loads(capsys.readouterr().out) == {
        'schema': 'arena.rating.table/1',
        'table': _ratings.table(_SCENARIO),
        'overview': None,
    }
    assert main(['rating', 'list', '--json']) == 0
    (listed,) = json.loads(capsys.readouterr().out)['runs']
    assert main(['rating', 'show', listed['id'], '--json']) == 0
    assert json.loads(capsys.readouterr().out)['run']['id'] == listed['id']
    assert main(['rating', 'check', listed['id']]) == 0
    assert capsys.readouterr().out.strip() == '4 matches played again, the same'
    assert main(['rating', 'draw', listed['id'], '1']) == 0
    path = Path(capsys.readouterr().out.strip())
    assert path.is_file() and path.suffix == '.cjr'
    assert main(['rating', 'draw', listed['id'], '1', '--json']) == 0
    assert json.loads(capsys.readouterr().out) == {
        'schema': 'arena.rating.draw/1',
        'files': [str(path)],
    }
    assert main(['rating', 'run', 'builtin:0.1']) != 0
    assert '--scenario' in capsys.readouterr().err


def _blocks(name):
    """Returns the fenced blocks of the guide of the ratings that follow a marker of a name."""
    text = (ROOT / 'docs' / 'evaluate-ratings.md').read_text(encoding='utf-8')
    pattern = rf'<!-- {name} -->\s*```[a-z]*\n(.*?)```'
    return re.findall(pattern, text, re.DOTALL)


def test_the_worked_example_of_the_guide_gives_its_numbers(arena_home, monkeypatch):
    (results,) = _blocks('example results')
    (expected,) = _blocks('example ratings')
    (program,) = _blocks('example program')
    results = json.loads(results)
    for number, result in enumerate(results):
        _keep(
            'example',
            result['home'],
            result['away'],
            result['score'],
            time=float(number),
            source='match',
        )
    table = _ratings.table('example')
    found = {
        row['id']: {
            'matches': row['matches'],
            'points': [row['points'][name] for name in ('rating', 'low', 'high')],
            'bradley_terry': [row['bradley_terry'][name] for name in ('rating', 'low', 'high')],
        }
        for row in table['models']
    }
    assert found == json.loads(expected)
    # The program of the guide runs as it is written, on a home of its own.
    monkeypatch.setattr(cuju.arena, 'rate', _ratings.rate, raising=False)
    printed = []

    def shown(*values):
        printed.append(' '.join(str(value) for value in values))

    exec(compile(program, 'evaluate-ratings.md', 'exec'), {'print': shown})
    assert printed[0] == 'done 4 matches' and len(printed) == 3
    assert printed[1].startswith('builtin:0.10 points ') and printed[1].endswith(' 2')
    assert printed[2].startswith('builtin:1.00 points ') and printed[2].endswith(' 2')


def test_rating_run_measures_two_checkpoints_of_one_team(arena_home, tmp_path, capsys):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import checkpoints, teams

    folder = tmp_path / 'harbour'
    folder.mkdir()
    (folder / 'main.py').write_text('def agent(obs): return [0]')
    identifier = teams.add_folder(folder, 'Harbour')['id']
    old = checkpoints.add(identifier, 'main.py', b'def agent(obs): return [1]', 'early', 10)
    new = checkpoints.add(identifier, 'main.py', b'def agent(obs): return [2]', 'aged', 20)
    references = [f'{identifier}@{held["current"]}' for held in (old, new)]
    args = [
        'rating',
        'run',
        'Harbour@early',
        'Harbour@step:20',
        '--scenario',
        'academy_empty_goal_close',
        '--set',
        'ratings.run.most=3',
        '--set',
        'ratings.run.anchor_matches=1',
        '--json',
    ]
    assert main(args) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer['models'] == references
    entrants = answer['progress']['entrants']
    assert [(entrants[key]['label'], entrants[key]['step']) for key in references] == [
        ('early', 10),
        ('aged', 20),
    ]
    meetings = _store.results()
    model_pairs = [{_store.model_of(row['home']), _store.model_of(row['away'])} for row in meetings]
    assert set(references) in model_pairs
    checkpoints.change(identifier, old['current'], label='edited early', step=90)
    checkpoints.change(identifier, new['current'], label='edited aged', step=99)
    assert main(['rating', 'show', answer['id'], '--json']) == 0
    kept = json.loads(capsys.readouterr().out)
    assert kept['schema'] == 'arena.rating.show/1'
    expected = {
        references[0]: {'checkpoint': {'id': old['current'], 'label': 'early', 'step': 10}},
        references[1]: {'checkpoint': {'id': new['current'], 'label': 'aged', 'step': 20}},
    }
    for match in kept['run']['matches']:
        assert match['sides'] == [
            expected.get(match[side], {'checkpoint': None}) for side in ('home', 'away')
        ]
    args[3] = 'Harbour@latest'
    args[2] = 'Harbour'
    assert main(args) == 2
    out, err = capsys.readouterr()
    error = json.loads(out)
    assert error['exit'] == 2 and error['error_id'] == 'server.ratings.twice'
    assert 'expected each model once' in err
    assert err.startswith('usage: cuju arena rating run')


@pytest.mark.parametrize('legacy', [False, True])
def test_tournament_ratings_keep_registered_checkpoint_metadata(
    arena_home, tmp_path, capsys, legacy
):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import checkpoints, teams

    folder = tmp_path / 'harbour'
    folder.mkdir()
    (folder / 'main.py').write_text('def agent(obs): return [0]', encoding='utf-8')
    identifier = teams.add_folder(folder, 'Harbour')['id']
    versions = [('early', 10), ('later', 20)]
    points = [
        checkpoints.add(
            identifier, 'main.py', f'def agent(obs): return [{step}]'.encode(), label, step
        )['current']
        for label, step in versions
    ]
    references = [f'{identifier}@{point}' for point in points]
    old, new = [_ratings.team_side(reference) for reference in references]
    assert _keep(_SCENARIO, old, new, [1, 0])
    if legacy:
        assert _keep(
            _SCENARIO,
            _side(identifier, points[0], kind='team', label=references[0]),
            _side(identifier, points[1], kind='team', label=identifier),
            [0, 1],
            source='tournament',
        )
        path = arena_home / 'ratings' / 'models.json'
        document = json.loads(path.read_text(encoding='utf-8'))
        for key in references:
            document['models'][key].pop('step', None)
        path.write_text(json.dumps(document), encoding='utf-8')
    else:
        entrants = [
            {'kind': 'team', 'key': identifier, 'team': reference, 'name': reference}
            for reference in references
        ]
        match = {'home': 0, 'away': 1, 'scenario': _SCENARIO, 'key': 'm1', 'seed': 42}
        assert _ratings.tournament_result('cup-1', entrants, match, [0, 1], None)
        stored = _store.models()
        assert [(stored[key]['label'], stored[key]['step']) for key in references] == versions
    assert main(['rating', 'table', '--scenario', _SCENARIO, '--json']) == 0
    rows = _rows(json.loads(capsys.readouterr().out)['table'])
    for key, (label, step) in zip(references, versions, strict=True):
        assert (rows[key]['label'], rows[key]['step']) == (label, step)
        assert rows[key]['sources']['tournament'] == 1

    assert main(['tournament', 'ratings']) == 0
    text_rows = [line.split('  ') for line in capsys.readouterr().out.splitlines()]
    assert {row[0] for row in text_rows} == set(references)
    assert [row[-1] for row in text_rows] == ['2 matches', '2 matches']

    assert main(['tournament', 'ratings', '--json']) == 0
    answer = json.loads(capsys.readouterr().out)
    models = _rows(answer)
    assert set(models) == set(references)
    assert [row[0] for row in text_rows] == list(models)
    assert answer['ratings'] == {identifier: {**rows[references[1]], 'scenario': _SCENARIO}}
    for key in references:
        assert models[key] == {**rows[key], 'scenario': _SCENARIO}

    second = 'academy_empty_goal_close'
    assert _keep(second, old, _ANCHOR, [1, 0], time=models[references[0]]['last'] + 1)
    assert main(['tournament', 'ratings', '--json']) == 0
    models = _rows(json.loads(capsys.readouterr().out))
    assert models[references[0]]['scenario'] == second
    assert models[references[1]]['scenario'] == _SCENARIO


def test_scenario_ratings_print_the_match_counts(arena_home, capsys):
    from cuju.arena.__main__ import main

    assert _keep(_SCENARIO, 'a', _ANCHOR, [2, 1])
    assert _keep(_SCENARIO, 'b', _ANCHOR, [1, 0])
    assert _keep(_SCENARIO, 'b', _ANCHOR, [0, 1])
    assert main(['rating', 'table', '--scenario', _SCENARIO]) == 0
    rows = {row.split('  ')[0]: row.split('  ')[-1] for row in capsys.readouterr().out.splitlines()}
    assert rows['a'].startswith('1 match, 1 against')
    assert rows['b'].startswith('2 matches, 2 against')


def test_tournament_ratings_prints_the_match_counts(arena_home, capsys):
    from cuju.arena.__main__ import main

    assert _keep(_SCENARIO, 'a', _ANCHOR, [2, 1])
    assert _keep(_SCENARIO, 'a', _ANCHOR, [1, 1])
    assert _keep(_SCENARIO, 'b', _ANCHOR, [0, 1])
    assert main(['tournament', 'ratings']) == 0
    rows = {row.split('  ')[0]: row.split('  ')[-1] for row in capsys.readouterr().out.splitlines()}
    assert rows == {'a': '2 matches', 'b': '1 match', _ANCHOR: '3 matches'}


def test_populated_rating_views_defer_numerical_imports(arena_home):
    import subprocess
    import sys

    assert _keep(_SCENARIO, 'a', _ANCHOR, [2, 1])
    assert _keep(_SCENARIO, 'a', _ANCHOR, [1, 1])
    assert _keep(_SCENARIO, 'b', _ANCHOR, [0, 1])
    document = _ratings.create([_ratings.builtin_side(0.1)], _SCENARIO, _ratings.settings())
    probe = """
import json
import sys
from contextlib import redirect_stdout
from io import StringIO
from cuju.arena.__main__ import main

scenario, identifier = sys.argv[1:]
for command, key in (
    (['rating', 'table', '--scenario', scenario], 'table'),
    (['rating', 'show', identifier], 'run'),
    (['tournament', 'ratings'], 'ratings'),
):
    output = StringIO()
    with redirect_stdout(output):
        assert main([*command, '--json', '--quiet']) == 0, command
    answer = json.loads(output.getvalue())[key]
    assert answer, command
    if key == 'table':
        rows = {row['id']: row['bradley_terry'] for row in answer['models']}
        assert rows['a']['rating'] == 1120.41 and rows['a']['deviation'] == 212.76
        assert rows['b']['rating'] == 809.15 and rows['b']['deviation'] == 283.68
    assert not {'numpy', 'torch', 'cuju._batch.environment'} & sys.modules.keys(), command
"""
    result = subprocess.run(
        [sys.executable, '-c', probe, _SCENARIO, document['id']],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
