# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the schedule of a phase of matches, batched and on demand."""

import itertools
import json
import random

import pytest

import cuju.errors
from cuju.arena._evaluate import evaluate as _evaluate
from cuju.arena._evaluate import replay as _replay
from cuju.arena._evaluate import schedule as _schedule
from cuju.arena._store import queue as _queue
from cuju.arena._store import runs as _runs
from cuju.arena._tournaments import tournament as rules
from support.common import _SINGLE, _toy_team

_SCENARIO = 'academy_run_to_score_with_keeper'


def _settings(**evaluate):
    return {
        'evaluate': {
            'scenario': _SCENARIO,
            'episodes': 3,
            'opponents': [
                {'kind': 'builtin', 'difficulty': 0.2},
                {'kind': 'builtin', 'difficulty': 0.9},
            ],
            'record': {'keep': 'nothing', 'background': False},
            **evaluate,
        }
    }


def _winner(strength):
    """A result in which the stronger side wins by one goal, and level sides draw."""

    def result(pairing):
        home, away = strength[pairing['home']], strength[pairing['away']]
        return [1, 0] if home > away else [0, 1] if home < away else [1, 1]

    return result


def _play(schedule, result, order=None):
    """Plays a schedule to its end, the matches of each round ending in the given order."""
    rounds = 0
    while not schedule.done:
        due = schedule.due()
        assert due or schedule.playing
        for pairing in order(list(schedule.playing)) if order else list(schedule.playing):
            schedule.finish(pairing['number'], result(pairing))
        rounds += 1
        assert rounds < 100000
    return schedule


def test_the_ratings_move_by_the_elo_rule_of_the_tournaments():
    schedule = _schedule.Schedule(
        ['a', 'b'], mode='on_demand', pairs=[('a', 'b')], limit=3, settle=None
    )
    table = {}
    sides = ({'key': 'a', 'name': 'a'}, {'key': 'b', 'name': 'b'})
    for score in ([2, 0], [1, 1], [0, 3]):
        (pairing,) = schedule.due()
        (counted,) = schedule.finish(pairing['number'], score)
        rules.update_ratings(table, *sides, score)
        assert counted['ratings']['a']['rating'] == table['a']['rating']
        assert counted['ratings']['b']['rating'] == table['b']['rating']
        assert counted['score'] == score and counted['meeting'] == pairing['number']
    assert schedule.done and schedule.due() == []
    ratings = schedule.ratings()
    assert ratings['a']['matches'] == ratings['b']['matches'] == 3
    # The interval narrows with every match, from 350 points of deviation before the first.
    fresh = _schedule.Schedule(['a', 'b'], mode='on_demand', pairs=[('a', 'b')], limit=1)
    wide = fresh.ratings()['a']
    assert wide['rating'] == rules.BASE_RATING and wide['matches'] == 0
    assert wide['high'] - wide['rating'] == pytest.approx(350 * _schedule.quantile(0.95), abs=0.01)
    assert ratings['a']['high'] - ratings['a']['low'] < wide['high'] - wide['low']
    assert _schedule.quantile(0.95) == pytest.approx(1.95996, abs=1e-4)
    assert _schedule.expected(1500, 1500) == 0.5


def test_on_demand_picks_the_pair_that_is_least_settled():
    sides = ['p', 'weak', 'level', 'strong']
    pairs = [('p', other) for other in sides[1:]]
    schedule = _schedule.Schedule(sides, mode='on_demand', seed=5, pairs=pairs, limit=40)
    strength = {'p': 2, 'weak': 0, 'level': 2, 'strong': 4}
    _play(schedule, _winner(strength))
    met = {other: 0 for other in sides[1:]}
    for entry in schedule.played:
        assert entry['home'] == 'p'
        met[entry['away']] += 1
    # The two sides that the policy always beats or always loses to settle soon, and the level
    # side takes the matches that are left, up to its limit.
    assert met['level'] == 40 and met['weak'] < 25 and met['strong'] < 25
    assert not schedule.settled
    # Every pick is the pair with the least ratio of gap to deviation, with a quarter for each
    # earlier meeting, among the pairs that are not settled yet.
    again = _schedule.Schedule(sides, mode='on_demand', seed=5, pairs=pairs, limit=40)
    threshold = _schedule.quantile(0.9)
    for number in range(len(schedule.played)):
        table = again._tables[-1]
        met_so_far = {other: 0 for other in sides[1:]}
        for entry in again.played:
            met_so_far[entry['away']] += 1
        open_pairs = [
            other
            for other in sides[1:]
            if met_so_far[other] < 40
            and (table.matches[other] == 0 or table.ratio('p', other, frozenset()) < threshold)
        ]
        least = min(
            table.ratio('p', other, frozenset()) + 0.25 * met_so_far[other] for other in open_pairs
        )
        (pairing,) = again.due()
        picked = table.ratio('p', pairing['away'], frozenset()) + 0.25 * met_so_far[pairing['away']]
        assert pairing['away'] in open_pairs and picked == pytest.approx(least)
        again.finish(number, schedule.played[number]['score'])
    assert again.done


def test_on_demand_stops_once_the_order_is_settled():
    sides = ['p', 'weak', 'strong']
    pairs = [('p', 'weak'), ('p', 'strong')]
    schedule = _schedule.Schedule(sides, mode='on_demand', pairs=pairs, limit=100)
    _play(schedule, _winner({'p': 1, 'weak': 0, 'strong': 2}))
    assert schedule.settled and schedule.done
    progress = schedule.progress()
    # The phase ended well before its most matches, and its total is what it played.
    assert progress['played'] == progress['total'] < 60 and progress['most'] == 200
    assert progress['settled'] and progress['done'] and progress['next'] == []
    ratings = progress['ratings']
    assert ratings['weak']['rating'] < ratings['p']['rating'] < ratings['strong']['rating']
    # Without a settling rule the phase plays its most matches.
    whole = _schedule.Schedule(sides, mode='on_demand', pairs=pairs, limit=30, settle=None)
    _play(whole, _winner({'p': 1, 'weak': 0, 'strong': 2}))
    assert len(whole.played) == 60 and not whole.settled


def test_on_demand_stops_at_the_most_matches():
    sides = ['a', 'b', 'c', 'd']
    schedule = _schedule.Schedule(sides, mode='on_demand', seed=2, each=3, most=7, at_once=2)
    _play(schedule, lambda pairing: [1, 1])
    progress = schedule.progress()
    assert progress['played'] == progress['total'] == progress['most'] == 7
    assert not progress['settled'] and progress['done']
    # By default a phase without pairs plays the matches of each measured side times the sides.
    assert _schedule.Schedule(sides, mode='on_demand', each=3).most == 12
    assert _schedule.Schedule(sides, mode='on_demand', each=3, fixed=['d']).most == 9


def test_a_given_rating_stays_and_anchors_the_others():
    schedule = _schedule.Schedule(
        ['new', 'other', 'anchor'],
        mode='on_demand',
        seed=1,
        each=4,
        fixed=['anchor'],
        ratings={'anchor': 1650.0},
        settle=None,
    )
    _play(schedule, _winner({'new': 3, 'other': 1, 'anchor': 2}))
    ratings = schedule.ratings()
    assert ratings['anchor'] == {
        'rating': 1650.0,
        'low': 1650.0,
        'high': 1650.0,
        'matches': ratings['anchor']['matches'],
        'measured': False,
    }
    assert ratings['anchor']['matches'] >= 2
    assert ratings['new']['rating'] > ratings['other']['rating']
    # Each measured side meets the anchor first, and the home side alternates.
    first = schedule.played[:2]
    assert all('anchor' in (entry['home'], entry['away']) for entry in first)
    homes = [entry['home'] for entry in schedule.played]
    assert max(homes.count(side) for side in ('new', 'other', 'anchor')) <= 5


def test_a_batched_phase_plays_its_whole_plan_at_once():
    sides = ['a', 'b', 'c', 'd', 'e', 'x', 'y']
    schedule = _schedule.Schedule(
        sides, mode='batched', seed=3, each=4, fixed=['x', 'y'], ratings={'x': 1600, 'y': 1400}
    )
    due = schedule.due()
    assert [pairing['number'] for pairing in due] == list(range(len(due)))
    assert schedule.due() == [] and len(schedule.playing) == len(due)
    assert schedule.total() == len(due) == schedule.most
    measured = sides[:5]
    met = {side: [] for side in measured}
    for pairing in due:
        for side, other in (
            (pairing['home'], pairing['away']),
            (pairing['away'], pairing['home']),
        ):
            if side in met:
                met[side].append(other)
    for side in measured:
        # The first two rounds meet the two anchors, and the others meet other measured sides.
        assert len(met[side]) >= 4 and set(met[side][:2]) == {'x', 'y'}
        assert len(set(met[side])) >= 4
    assert all('x' not in (p['home'], p['away']) or 'y' not in (p['home'], p['away']) for p in due)
    # Results count in the order of the match numbers, whatever the order they come in.
    for pairing in reversed(due):
        counted = schedule.finish(pairing['number'], [1, 0])
        assert bool(counted) == (pairing['number'] == 0)
    assert [entry['number'] for entry in schedule.played] == list(range(len(due)))
    assert schedule.done and schedule.progress()['playing'] == []
    # With pairs given, each pair meets its limit of times, pair after pair.
    pairs = [('p', 'o1'), ('p', 'o2')]
    plan = _schedule.Schedule(['p', 'o1', 'o2'], mode='batched', pairs=pairs, limit=3).due()
    assert [(p['away'], p['meeting']) for p in plan] == [
        ('o1', 0),
        ('o1', 1),
        ('o1', 2),
        ('o2', 0),
        ('o2', 1),
        ('o2', 2),
    ]


def test_the_pairings_do_not_depend_on_the_order_of_the_results():
    strength = {'a': 5, 'b': 4, 'c': 3, 'd': 2, 'e': 1}

    def history(order):
        schedule = _schedule.Schedule(
            list(strength), mode='on_demand', seed=9, each=6, at_once=3, settle=None
        )
        most = [0]

        def ordered(playing):
            most[0] = max(most[0], len(playing))
            return order(playing)

        _play(schedule, _winner(strength), ordered)
        assert most[0] == 3
        return [
            (e['number'], e['home'], e['away'], e['meeting'], e['score']) for e in schedule.played
        ]

    draw = random.Random(7)

    def shuffled(playing):
        draw.shuffle(playing)
        return playing

    first = history(lambda playing: playing)
    assert history(lambda playing: playing[::-1]) == first
    assert history(shuffled) == first
    assert len(first) == 30


def test_a_match_without_a_result_counts_and_moves_no_rating():
    schedule = _schedule.Schedule(['a', 'b', 'c'], mode='on_demand', each=1, settle=None)
    (first,) = schedule.due()
    (counted,) = schedule.finish(first['number'], None, error='the policy failed')
    assert counted['score'] is None and counted['error'] == 'the policy failed'
    assert all(rating['rating'] == rules.BASE_RATING for rating in counted['ratings'].values())
    assert all(rating['matches'] == 0 for rating in schedule.ratings().values())
    with pytest.raises(ValueError, match='expected the number of a match under way'):
        schedule.finish(first['number'], [1, 0])
    _play(schedule, lambda pairing: [1, 0])
    assert len(schedule.played) == 3


def test_every_measured_side_meets_the_anchor_before_any_other_match():
    strength = {'a': 5, 'b': 4, 'c': 1, 'known': 3, 'anchor': 2}
    options = dict(
        seed=6,
        each=5,
        fixed=['known', 'anchor'],
        ratings={'a': 1000.0, 'b': 1000.0, 'c': 1000.0, 'known': 1120.0, 'anchor': 1000.0},
        anchor='anchor',
        anchor_matches=2,
        settle=None,
    )
    schedule = _play(
        _schedule.Schedule(list(strength), mode='on_demand', at_once=2, **options),
        _winner(strength),
    )
    played = schedule.played
    # The first six matches are the two of each measured side against the anchor.
    assert all('anchor' in (entry['home'], entry['away']) for entry in played[:6])
    met = [entry['home'] if entry['away'] == 'anchor' else entry['away'] for entry in played[:6]]
    assert sorted(met) == ['a', 'a', 'b', 'b', 'c', 'c']
    assert not any({'known', 'anchor'} == {entry['home'], entry['away']} for entry in played)
    assert len(played) == schedule.most == 15
    # The rest goes where the order is least settled, which is not the anchor alone.
    assert any('anchor' not in (entry['home'], entry['away']) for entry in played[6:])
    assert schedule.ratings()['anchor']['rating'] == 1000.0
    # A batched phase opens with the same matches, the home side in turn, then its rounds.
    batched = _schedule.Schedule(list(strength), mode='batched', **options)
    plan = batched.due()
    assert [(p['home'], p['away']) for p in plan[:6]] == [
        *[(side, 'anchor') for side in batched._rank and sorted('abc', key=batched._rank.get)],
        *[('anchor', side) for side in sorted('abc', key=batched._rank.get)],
    ]
    games = {side: 0 for side in 'abc'}
    for pairing in plan:
        for side in (pairing['home'], pairing['away']):
            if side in games:
                games[side] += 1
    assert all(count >= 5 for count in games.values())
    # The anchor of a phase is a side whose rating is given.
    with pytest.raises(ValueError, match='expected an anchor whose rating is given'):
        _schedule.Schedule(['a', 'b'], mode='on_demand', each=2, anchor='b', anchor_matches=1)
    # The matches against the anchor are owed even where the ratings count the pair settled.
    far = _schedule.Schedule(
        ['a', 'anchor'],
        mode='on_demand',
        each=6,
        fixed=['anchor'],
        ratings={'a': 2400.0, 'anchor': 1000.0},
        deviations={'a': 40.0},
        anchor='anchor',
        anchor_matches=3,
    )
    _play(far, _winner({'a': 2, 'anchor': 1}))
    assert len(far.played) == 3 and far.settled


def test_a_side_starts_with_the_deviation_that_its_earlier_matches_left():
    options = dict(mode='on_demand', seed=2, each=40, fixed=['anchor'], anchor='anchor')
    fresh = _schedule.Schedule(['new', 'anchor'], **options)
    known = _schedule.Schedule(
        ['new', 'anchor'],
        ratings={'new': 1300.0, 'anchor': 1000.0},
        deviations={'new': 60.0, 'anchor': 25.0},
        **options,
    )
    quantile = _schedule.quantile(0.95)
    assert fresh.ratings()['new'] == {
        'rating': rules.BASE_RATING,
        'low': round(rules.BASE_RATING - quantile * 350, 2),
        'high': round(rules.BASE_RATING + quantile * 350, 2),
        'matches': 0,
        'measured': True,
    }
    start = known.ratings()
    width = start['new']['high'] - start['new']['low']
    assert width == pytest.approx(2 * quantile * 60, abs=0.02)
    # A given side keeps its deviation as it is, whatever it plays.
    width = start['anchor']['high'] - start['anchor']['low']
    assert width == pytest.approx(2 * quantile * 25, abs=0.02)
    _play(known, _winner({'new': 2, 'anchor': 1}))
    after = known.ratings()
    assert after['anchor'] == {**start['anchor'], 'matches': after['anchor']['matches']}
    assert after['new']['high'] - after['new']['low'] < start['new']['high'] - start['new']['low']
    # The earlier evidence settles the pair after one match, and a fresh side plays on.
    assert len(known.played) == 1 and known.settled
    _play(fresh, _winner({'new': 2, 'anchor': 1}))
    assert len(fresh.played) > 5


def test_a_phase_asks_matches_and_a_narrow_interval_of_its_measured_sides():
    # The two sides draw every match, so the rating of the measured side moves little.
    strength = {'a': 1, 'anchor': 1}
    options = dict(
        mode='on_demand',
        seed=2,
        each=60,
        fixed=['anchor'],
        ratings={'a': 1100.0, 'anchor': 1000.0},
        deviations={'a': 50.0},
        settle=0.5,
    )
    # The ratings alone settle the pair once the side has played.
    assert len(_play(_schedule.Schedule(list(strength), **options), _winner(strength)).played) == 1
    # A side that must play four matches keeps its pairs open until it has.
    needs = _play(_schedule.Schedule(list(strength), needs={'a': 4}, **options), _winner(strength))
    assert len(needs.played) == 4 and needs.settled
    # A side whose interval is wider than the phase allows plays on until it is narrow enough.
    narrow = _play(_schedule.Schedule(list(strength), width=150.0, **options), _winner(strength))
    rating = narrow.ratings()['a']
    assert rating['high'] - rating['low'] <= 150 and len(narrow.played) > 4
    before = narrow.played[-2]['ratings']['a']
    assert before['high'] - before['low'] > 150


def test_arguments_that_do_not_fit_are_refused():
    with pytest.raises(ValueError, match='expected a mode'):
        _schedule.Schedule(['a', 'b'], mode='fast', each=1)
    with pytest.raises(ValueError, match='expected each side once'):
        _schedule.Schedule(['a', 'a'], mode='batched', each=1)
    with pytest.raises(ValueError, match='expected a pair of two sides'):
        _schedule.Schedule(['a', 'b'], mode='batched', pairs=[('a', 'c')], limit=1)
    with pytest.raises(ValueError, match='expected pairs with limit, or each'):
        _schedule.Schedule(['a', 'b'], mode='batched')


def test_a_schedule_resumes_from_its_saved_queue(arena_home):
    strength = {'a': 4, 'b': 3, 'c': 2, 'd': 1}
    options = dict(mode='on_demand', seed=4, each=5, at_once=2, settle=None)
    whole = _play(_schedule.Schedule(list(strength), **options), _winner(strength))
    name = f'run:{_runs.create("evaluation")["id"]}'
    schedule = _schedule.Schedule(list(strength), **options)
    for _ in range(4):
        schedule.due()
        for pairing in list(schedule.playing)[:1]:
            schedule.finish(pairing['number'], _winner(strength)(pairing))
        _queue.save_queue(name, schedule.state())
    # The owner keeps fields of its own in a pairing, which the state holds.
    schedule.due()
    for pairing in schedule.playing:
        pairing['seed'] = 1000 + pairing['number']
    saved = _queue.save_queue(name, schedule.state())
    assert saved['mode'] == 'on_demand' and saved['count'] == len(schedule.played)
    assert len(saved['playing']) == 2 and saved['most'] == 20 and saved['settled'] is False
    assert set(saved['ratings']) == set(strength)
    assert all(entry.get('provisional') for entry in saved['next'])
    json.dumps(saved)
    # After a restart the matches that were under way wait at the front of the queue.
    state = _queue.queue_state(name, resume=True)
    assert [entry['number'] for entry in state['next'][:2]] == [
        entry['number'] for entry in saved['playing']
    ]
    again = _schedule.Schedule(list(strength), **options, state=state)
    assert again.played == schedule.played and again.ratings() == schedule.ratings()
    due = again.due()
    assert [(p['number'], p['home'], p['away'], p['seed']) for p in due] == [
        (p['number'], p['home'], p['away'], p['seed']) for p in schedule.playing
    ]
    _play(again, _winner(strength))
    assert [(e['number'], e['home'], e['away'], e['score']) for e in again.played] == [
        (e['number'], e['home'], e['away'], e['score']) for e in whole.played
    ]
    assert again.ratings() == whole.ratings()
    assert list(itertools.islice((e['number'] for e in again.played), 3)) == [0, 1, 2]


def test_an_evaluation_on_demand_plays_the_matches_of_the_batched_one(arena_home):
    """On demand, match k against an opponent is the match that the batch plays as index k."""
    batched = _evaluate.evaluate('builtin', _settings(mode='batched'))
    seen = []
    on_demand = _evaluate.evaluate(
        'builtin', _settings(mode='on_demand', settle=None), progress=seen.append
    )
    assert batched.config['mode'] == 'batched' and on_demand.config['mode'] == 'on_demand'
    assert on_demand.progress['played'] == on_demand.progress['total'] == 6

    def results(evaluation):
        return sorted(
            (e['opponent'], e['index'], e['goals_for'], e['goals_against'], e['length'])
            for e in evaluation.episodes
        )

    assert results(on_demand) == results(batched)
    assert _replay.check(on_demand.id)['same']
    # One match plays at a time, and the queue names it and the one that the results point to.
    under_way = [entry for entry in seen if entry['playing']]
    assert under_way and all(len(entry['playing']) == 1 for entry in under_way)
    assert under_way[0]['playing'][0]['home'] == 'builtin:scenario'
    assert under_way[0]['next'] and under_way[0]['next'][0]['provisional']
    assert [entry['played'] for entry in seen][-1] == 6
    # The opponents take turns while none is settled, the one that met the policy less first.
    order = [entry['away'] for entry in on_demand.progress['history']]
    assert sorted(order[:2]) == ['builtin:0.20', 'builtin:0.90']
    # The most matches end a run on demand before every opponent has met the policy in full.
    capped = _evaluate.evaluate('builtin', _settings(mode='on_demand', most=4, settle=None))
    assert len(capped.episodes) == 4 and capped.progress['total'] == 4


def test_an_evaluation_on_demand_plays_a_team_in_a_process_of_its_own(arena_home, tmp_path):
    team = _toy_team(tmp_path, 'toy', _SINGLE, '11v11')
    settings = _settings(episodes=2, at_once=2, settle=None)
    settings['evaluate']['opponents'] = [{'kind': 'builtin', 'difficulty': 0.3}]
    found = _evaluate.evaluate(f'team:{team["id"]}', settings)
    # A team cannot play in a batch, so its matches play on demand, each in a child process.
    assert found.config['mode'] == 'on_demand' and found.state == 'done'
    assert found.config['policy']['kind'] == 'team' and found.config['policy']['key'] == team['id']
    assert found.name == 'toy' and found.checkpoint['label'] == team['id']
    (setup,) = found.config['setups']
    assert setup['left'] == team['id'] and setup['right'] == 'builtin'
    assert setup['agents'] == [1, 0] and setup['difficulty'] == [None, 0.3]
    assert [(e['index'], e['side']) for e in found.episodes] == [(0, 'left'), (1, 'left')]
    assert all(e['outcome'] in ('win', 'draw', 'loss') and e['length'] > 0 for e in found.episodes)
    # The kept actions play the matches again without the team.
    assert _replay.check(found.id) == {'id': found.id, 'checked': 2, 'same': True, 'problems': []}
    # A batched run refuses a team, and a function meets the built-in AI alone.
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _evaluate.evaluate(f'team:{team["id"]}', _settings(mode='batched'))
    assert caught.value.message_id == 'server.evaluate.batched'
    against = _settings()
    against['evaluate']['opponents'] = [{'kind': 'team', 'team': team['id']}]
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _evaluate.evaluate(lambda observation, batch: None, against)
    assert caught.value.message_id == 'server.evaluate.function_team'
    # The built-in AI meets a team on demand as well, from the left.
    builtin = _evaluate.evaluate('builtin', {'evaluate': {**against['evaluate'], 'episodes': 1}})
    assert builtin.config['mode'] == 'on_demand' and len(builtin.episodes) == 1
    assert builtin.config['setups'][0]['agents'] == [0, 1]


def test_an_evaluation_on_demand_resumes_its_queue_after_a_restart(arena_home):
    """A run that a stop cut plays on from its manifest, and no kept match plays again."""
    whole = _evaluate.evaluate('builtin', _settings(mode='on_demand', settle=None))
    settings = _evaluate.request_settings(
        {**_settings(mode='on_demand', settle=None)['evaluate'], 'policy': 'builtin'}
    )
    made = _evaluate.plan(None, settings)
    identifier = _evaluate._create(made, settings, 'server', 'queued')

    class Stop(BaseException):
        pass

    def stop(progress):
        if progress['played'] >= 2:
            raise Stop

    play = _evaluate._Play(identifier, made, settings, stop)
    with pytest.raises(Stop):
        play.run()
    # The manifest holds the matches played and the queue as the stop left it.
    document = _runs.get(identifier)
    assert len(document['episodes']) == 2 and document['state'] == 'queued'
    saved = _queue.queue_state(f'run:{identifier}')
    assert saved['count'] == 2 and saved['mode'] == 'on_demand' and saved['most'] == 6
    assert set(saved['ratings']) == {'builtin:scenario', 'builtin:0.20', 'builtin:0.90'}
    assert set(saved['intervals']) == set(saved['ratings'])
    seen = []
    again = _evaluate.resume(identifier, progress=seen.append)
    assert again.state == 'done' and len(again.episodes) == 6
    assert seen[0]['played'] >= 2

    def results(evaluation):
        return [
            (e['number'], e['opponent'], e['index'], e['goals_for'], e['goals_against'])
            for e in evaluation.episodes
        ]

    # The run took the same pairings in the same order as one that nothing cut.
    assert results(again) == results(whole)
    assert [entry['number'] for entry in again.progress['history']] == list(range(6))
    assert _replay.check(identifier)['same']
