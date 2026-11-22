# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks the renderer's tournaments, their formats, draws, tiebreaks, runner, server and CLI."""

import itertools
import random

import pytest

from cuju.arena._tournaments import tournament as rules
from cuju.arena._tournaments import tournament_runner as runner

_BUILTIN = {
    'id': 'builtin',
    'kind': 'builtin',
    'name': 'Built-in AI',
    'style': {'kit': {'primary': [255, 111, 0], 'secondary': [255, 255, 255]}},
}


def _team(identifier, format='11v11'):
    return {
        'id': identifier,
        'kind': 'submission',
        'name': identifier.title(),
        'format': format,
        'style': {'kit': {'primary': [10, 20, 30], 'secondary': [40, 50, 60]}},
    }


_TEAMS = [_BUILTIN, _team('reds'), _team('blues'), _team('minis', '5v5')]


def _fits(entrant, scenario):
    """Rejects a team of one format in a full match of the other, as the registry check does."""
    size = '5v5' if scenario.startswith('5_vs_5') else '11v11'
    if entrant['format'] and scenario[0].isdigit() and entrant['format'] != size:
        return f'expected a scenario of {entrant["format"]}'
    return None


def _builtins(count):
    return [
        {'kind': 'builtin', 'ability': round(index / max(1, count), 4)} for index in range(count)
    ]


def _config(stages, count=8, seed=11, **options):
    return rules.check(
        {'name': 'Cup', 'seed': seed, 'entrants': _builtins(count), 'stages': stages, **options},
        _TEAMS,
        _fits,
    )


def _play_out(config, score=None, seed=0, limit=100000):
    """Plays a tournament to its end with made up results, and returns its last evaluation."""
    draw = random.Random(seed)
    results = {}
    for _ in range(limit):
        view = rules.evaluate(config, results)
        if view['complete']:
            return view, results
        ready = rules.ready(view)
        assert ready, 'the tournament stopped with nothing to play'
        for match in ready:
            goals = score(match) if score else [draw.randrange(4), draw.randrange(4)]
            results[match['key']] = {'score': goals, 'cards': [[0, 0], [0, 0]]}
    raise AssertionError('the tournament never ended')


_FORMATS = [
    {'format': 'knockout'},
    {'format': 'knockout', 'third_place': True, 'legs': 2, 'settle': ['away_goals', 'lots']},
    {'format': 'knockout', 'bracket': 'play_in'},
    {'format': 'double_elimination'},
    {'format': 'double_elimination', 'grand_final_reset': False},
    {'format': 'league'},
    {'format': 'league', 'cycles': 2, 'legs': 2},
    {'format': 'swiss'},
    {'format': 'ladder'},
    {'format': 'king_of_the_hill', 'laps': 2},
    {'format': 'gauntlet'},
    {'format': 'gauntlet', 'draws': 'holder'},
]


@pytest.mark.parametrize('stage', _FORMATS, ids=lambda stage: '-'.join(map(str, stage.values())))
@pytest.mark.parametrize('count', [2, 3, 5, 7, 8, 13, 16, 17, 31])
def test_every_format_ends_with_a_full_ranking(stage, count):
    config = _config([stage], count)
    view, _ = _play_out(config, seed=count)
    assert sorted(view['ranking']) == list(range(count))
    assert view['champion'] == view['ranking'][0]
    keys = [match['key'] for match in view['matches']]
    assert len(keys) == len(set(keys))
    assert all(match['home'] != match['away'] for match in view['matches'])


@pytest.mark.parametrize('stage', _FORMATS, ids=lambda stage: '-'.join(map(str, stage.values())))
@pytest.mark.parametrize('count', [2, 3, 5, 8, 13])
def test_the_expected_matches_count_the_plan_and_never_fall(stage, count):
    """A tournament of decisive matches plays what it expected, and the count never goes down."""
    stop = {'until': 2} if stage['format'] == 'knockout' else {}
    chain = [{**stage, 'advance': 2, **stop}, {'format': 'knockout'}]
    config = _config(chain if count > 4 else [stage], count)
    draw = random.Random(count)
    results, counts = {}, []
    view = rules.evaluate(config, results)
    while not view['complete']:
        counts.append(view['expected'])
        assert view['expected'] >= len(view['matches'])
        for match in rules.ready(view):
            goals = draw.randrange(3)
            score = [goals + 1, goals] if draw.random() < 0.5 else [goals, goals + 1]
            results[match['key']] = {'score': score, 'cards': [[0, 0], [0, 0]]}
        view = rules.evaluate(config, results)
    assert counts == sorted(counts) and view['expected'] == view['played']
    # Only the reset of a grand final and the later laps of a king, whose challengers depend on
    # who is king at their turn, play other than the first count.
    if stage.get('laps', 1) == 1:
        resets = [m for m in view['matches'] if m['label_id'].endswith('grand_final_reset')]
        assert counts[0] + len(resets) == view['played'], (counts, view['played'])


@pytest.mark.parametrize(
    'stage',
    [
        {'format': 'knockout'},
        {'format': 'knockout', 'bracket': 'play_in'},
        {'format': 'double_elimination'},
        {'format': 'league'},
        {'format': 'groups', 'group_size': 8, 'qualifiers': 1},
        {'format': 'swiss'},
        {'format': 'ladder', 'rounds': 3},
        {'format': 'king_of_the_hill'},
        {'format': 'gauntlet'},
    ],
    ids=lambda stage: stage['format'],
)
def test_formats_take_256_entrants(stage):
    config = _config([stage], 256)
    view, _ = _play_out(config, seed=3)
    assert sorted(view['ranking']) == list(range(256))


@pytest.mark.parametrize('count', [4, 5, 6, 7, 11, 13, 23, 24, 37])
def test_groups_then_knockout_chain(count):
    config = _config(
        [
            {
                'format': 'groups',
                'group_size': 4,
                'qualifiers': 2,
                'best_position': 3,
                'best_count': 'fill',
            },
            {'format': 'knockout', 'third_place': True},
        ],
        count,
    )
    first = config['stages'][0]
    assert sum(first['group_sizes']) == count
    assert max(first['group_sizes']) - min(first['group_sizes']) <= 1
    view, _ = _play_out(config, seed=count)
    groups, knockout = view['stages']
    assert knockout['entrants'] == groups['ranking'][: first['advance']]
    assert sorted(view['ranking']) == list(range(count))
    # The final ranking keeps the order of the knockout first, then of the groups.
    assert view['ranking'][: len(knockout['ranking'])] == knockout['ranking']


def test_group_stage_with_uneven_sizes_pots_and_best_places():
    config = _config(
        [
            {
                'format': 'groups',
                'group_sizes': [5, 4, 3],
                'pots': 3,
                'qualifiers': [2, 2, 1],
                'best_position': 3,
                'best_count': 1,
            },
            {'format': 'league'},
        ],
        12,
    )
    stage = config['stages'][0]
    assert stage['advance'] == 6
    groups = rules.draw_groups(rules.seeding(config), stage, config['seed'], 0)
    assert [len(group) for group in groups] == [5, 4, 3]
    assert sorted(itertools.chain(*groups)) == list(range(12))
    # The first pot holds the four best seeds, dealt one to each group before any repeats.
    seeds = rules.seeding(config)
    top = set(seeds[:4])
    assert all(top & set(group) for group in groups)
    view, _ = _play_out(config, seed=1)
    tables = view['stages'][0]['tables']
    assert [table['qualify'] for table in tables] == [2, 2, 1]
    best = view['stages'][0]['best']
    assert len(best) == 1
    third = [table['rows'][2]['entrant'] for table in tables if table['qualify'] < 3]
    assert best[0] in third
    assert len(view['stages'][1]['entrants']) == 6


def test_knockout_byes_go_to_the_top_seeds():
    config = _config([{'format': 'knockout'}], 5, seeding='manual')
    view = rules.evaluate(config, {})
    first = view['stages'][0]['rounds'][0]['fixtures']
    byes = [fixture for fixture in first if fixture['decided_by'] == 'bye']
    assert sorted(fixture['winner'] for fixture in byes) == [0, 1, 2]
    played = [fixture for fixture in first if fixture['decided_by'] != 'bye']
    assert [(fixture['home'], fixture['away']) for fixture in played] == [(3, 4)]


def test_play_in_round_brings_a_bracket_to_a_power_of_two():
    config = _config([{'format': 'knockout', 'bracket': 'play_in'}], 6, seeding='manual')
    assert config['stages'][0]['bracket_size'] == 4
    view = rules.evaluate(config, {})
    play_in = view['stages'][0]['rounds'][0]
    assert play_in['name'] == 'Play-in round'
    assert [(f['home'], f['away']) for f in play_in['fixtures']] == [(2, 5), (3, 4)]
    semi = view['stages'][0]['rounds'][1]['fixtures']
    assert semi[0]['home'] == 0 and semi[0]['away']['from'] == 'Winner of Play-in 2'
    assert semi[0]['away']['from_id'] == 'server.tournament.label.winner_of'


def test_two_legged_ties_add_up_and_settle_by_away_goals():
    config = _config(
        [{'format': 'knockout', 'legs': 2, 'settle': ['away_goals', 'lots']}], 2, seeding='manual'
    )
    view = rules.evaluate(config, {})
    first, second = view['matches']
    # The higher seed plays the second leg at home.
    assert (first['home'], first['away'], second['home'], second['away']) == (1, 0, 0, 1)
    results = {first['key']: {'score': [1, 2]}, second['key']: {'score': [0, 1]}}
    tie = rules.evaluate(config, results)['stages'][0]['rounds'][0]['fixtures'][0]
    assert tie['score'] == [2, 2]
    # Seed 0 scored two away goals in the first leg, seed 1 one in the second.
    assert (tie['winner'], tie['decided_by']) == (0, 'away goals')
    results = {first['key']: {'score': [1, 0]}, second['key']: {'score': [3, 0]}}
    tie = rules.evaluate(config, results)['stages'][0]['rounds'][0]['fixtures'][0]
    assert (tie['score'], tie['winner'], tie['decided_by']) == ([3, 1], 0, 'aggregate')


def test_drawn_ties_settle_by_replay_then_decider_then_lots():
    stage = {
        'format': 'knockout',
        'settle': ['replay', 'decider', 'lots'],
        'replays': 2,
        'decider_scenario': 'academy_empty_goal_close',
    }
    config = _config([stage], 2, seeding='manual')
    results = {}
    view = rules.evaluate(config, results)
    results[view['matches'][0]['key']] = {'score': [1, 1]}
    view = rules.evaluate(config, results)
    replays = [m for m in view['matches'] if m['result'] is None]
    assert [m['key'] for m in replays] == ['0/w2-1/r1']
    results['0/w2-1/r1'] = {'score': [0, 0]}
    results['0/w2-1/r2'] = {'score': [2, 2]}
    view = rules.evaluate(config, results)
    deciders = [m for m in view['matches'] if m['result'] is None]
    assert [m['key'] for m in deciders] == ['0/w2-1/d1', '0/w2-1/d2']
    assert all(m['scenario'] == 'academy_empty_goal_close' and m['decider'] for m in deciders)
    # Each side attacks from the left once, and the goals of both add up.
    assert (deciders[0]['home'], deciders[1]['home']) == (0, 1)
    results['0/w2-1/d1'] = {'score': [1, 0]}
    results['0/w2-1/d2'] = {'score': [1, 0]}
    tie = rules.evaluate(config, results)['stages'][0]['rounds'][0]['fixtures'][0]
    assert tie['decider'] == [1, 1] and tie['decided_by'] == 'lots'
    lots = tie['winner']
    again = rules.evaluate(config, results)['stages'][0]['rounds'][0]['fixtures'][0]
    assert again['winner'] == lots
    results['0/w2-1/d2'] = {'score': [2, 0]}
    tie = rules.evaluate(config, results)['stages'][0]['rounds'][0]['fixtures'][0]
    assert (tie['winner'], tie['decided_by']) == (1, 'decider')


def _league_with(results_by_pair, tiebreakers, count=4, points=None):
    stage = {'format': 'league', 'tiebreakers': tiebreakers}
    if points:
        stage['points'] = points
    config = _config([stage], count, seeding='manual')
    view = rules.evaluate(config, {})
    results = {}
    for match in view['matches']:
        pair = (match['home'], match['away'])
        if pair in results_by_pair:
            results[match['key']] = {'score': results_by_pair[pair]}
        else:
            home, away = results_by_pair[pair[::-1]]
            results[match['key']] = {'score': [away, home]}
        cards = results_by_pair.get(('cards', pair))
        if cards is None and ('cards', pair[::-1]) in results_by_pair:
            cards = results_by_pair[('cards', pair[::-1])][::-1]
        if cards:
            results[match['key']]['cards'] = cards
    return rules.evaluate(config, results)['stages'][0]['tables'][0]['rows']


def test_tiebreakers_follow_the_order_of_the_stage_and_name_their_reason():
    # Entrants 0 and 1 both win twice and lose once. Entrant 0 has the better goal difference,
    # entrant 1 scored more goals, and entrant 1 won the match between them.
    games = {
        (0, 1): [0, 1],
        (0, 2): [3, 0],
        (0, 3): [3, 0],
        (1, 2): [5, 4],
        (1, 3): [2, 3],
        (2, 3): [0, 0],
    }
    rows = _league_with(games, ['goal_difference', 'goals_for'])
    assert [row['entrant'] for row in rows[:2]] == [0, 1]
    assert rows[0]['tiebreak'] == 'goal_difference'
    rows = _league_with(games, ['goals_for', 'goal_difference'])
    assert [row['entrant'] for row in rows[:2]] == [1, 0]
    assert rows[0]['tiebreak'] == 'goals_for'
    rows = _league_with(games, ['head_to_head', 'goal_difference'])
    assert [row['entrant'] for row in rows[:2]] == [1, 0]
    assert rows[0]['tiebreak'] == 'head_to_head'
    # Rows that differ in points carry no tiebreak.
    assert rows[1]['tiebreak'] is None


def test_fair_play_and_lots_decide_level_rows():
    games = {
        (0, 1): [1, 1],
        (0, 2): [1, 0],
        (0, 3): [0, 1],
        (1, 2): [1, 0],
        (1, 3): [0, 1],
        (2, 3): [0, 0],
        ('cards', (0, 2)): [[2, 0], [0, 0]],
    }
    rows = _league_with(games, ['goal_difference', 'fair_play'])
    order = [row['entrant'] for row in rows]
    assert order.index(1) < order.index(0)
    assert rows[order.index(1)]['tiebreak'] == 'fair_play'
    first = _league_with(games, ['goal_difference'])
    again = _league_with(games, ['goal_difference'])
    assert [row['entrant'] for row in first] == [row['entrant'] for row in again]
    assert 'lots' in [row['tiebreak'] for row in first]


def test_points_for_a_win_draw_and_loss_are_configurable():
    games = {(0, 1): [1, 0], (0, 2): [0, 0], (1, 2): [0, 0]}
    rows = _league_with(games, ['goal_difference'], 3, {'win': 2, 'draw': 1, 'loss': -1})
    points = {row['entrant']: row['points'] for row in rows}
    assert points == {0: 3, 1: 0, 2: 2}


@pytest.mark.parametrize('count', range(2, 21))
def test_swiss_pairs_by_score_without_rematches_and_one_bye_each(count):
    rounds = max(1, -(-count // 2))
    config = _config([{'format': 'swiss', 'rounds': rounds}], count)
    view, _ = _play_out(config, seed=count)
    stage = view['stages'][0]
    pairs = [
        frozenset((fixture['home'], fixture['away']))
        for round_ in stage['rounds']
        for fixture in round_['fixtures']
    ]
    assert len(pairs) == len(set(pairs))
    byes = [round_['bye'] for round_ in stage['rounds'] if round_['bye'] is not None]
    assert len(byes) == len(set(byes)) == (rounds if count % 2 else 0)
    for round_ in stage['rounds']:
        seen = [e for f in round_['fixtures'] for e in (f['home'], f['away'])]
        assert len(seen) == len(set(seen)) == count - (count % 2)


def test_swiss_rounds_default_from_the_entrants_and_refuse_too_many():
    assert _config([{'format': 'swiss'}], 16)['stages'][0]['rounds'] == 4
    assert _config([{'format': 'swiss'}], 3)['stages'][0]['rounds'] == 2
    with pytest.raises(rules.TournamentError, match='without rematches'):
        _config([{'format': 'swiss', 'rounds': 9}], 16)


def test_swiss_pairing_falls_back_to_palmer_when_the_search_runs_out():
    order = list(range(12))
    met = {frozenset((a, b)) for a in range(12) for b in range(12) if a != b and abs(a - b) == 1}
    pairs, bye = rules.swiss_pairs(order, met, set(), budget=0)
    assert bye is None and len(pairs) == 6
    assert not {frozenset(pair) for pair in pairs} & met
    assert sorted(itertools.chain(*pairs)) == order


def test_swiss_second_round_pairs_winners_with_winners():
    config = _config([{'format': 'swiss', 'rounds': 2}], 8, seeding='manual')
    view = rules.evaluate(config, {})
    first = [m for m in view['matches']]
    results = {m['key']: {'score': [1, 0]} for m in first}
    view = rules.evaluate(config, results)
    winners = {m['home'] for m in first}
    second = [m for m in view['matches'] if m['result'] is None]
    assert len(second) == 4
    for match in second:
        assert (match['home'] in winners) == (match['away'] in winners)


def test_double_elimination_resets_the_grand_final():
    config = _config([{'format': 'double_elimination'}], 4, seeding='manual')

    def lower_seed_wins_grand_final(match):
        if match['label'].startswith('Grand final'):
            return [0, 1] if match['label'] == 'Grand final' else [2, 0]
        return [1, 0]

    view, _ = _play_out(config, lower_seed_wins_grand_final)
    rounds = view['stages'][0]['rounds']
    assert [r['name'] for r in rounds][-2:] == ['Grand final', 'Grand final, deciding match']
    assert view['champion'] == 0
    config = _config(
        [{'format': 'double_elimination', 'grand_final_reset': False}], 4, seeding='manual'
    )
    view, _ = _play_out(config, lower_seed_wins_grand_final)
    assert [r['name'] for r in view['stages'][0]['rounds']][-1] == 'Grand final'
    assert view['champion'] != 0


def test_knockout_ranks_by_round_and_third_place():
    config = _config([{'format': 'knockout', 'third_place': True}], 8, seeding='manual')
    view, _ = _play_out(config, lambda match: [1, 0])
    ranking = view['ranking']
    assert ranking[0] == 0
    final = view['stages'][0]['rounds'][2]['fixtures'][0]
    third = view['stages'][0]['rounds'][3]['fixtures'][0]
    assert ranking[1] == final['loser']
    assert ranking[2:4] == [third['winner'], third['loser']]


def test_ladder_king_and_gauntlet_follow_their_rules():
    higher_wins = lambda match: [1, 0]  # noqa: E731
    config = _config([{'format': 'ladder', 'rounds': 5}], 5, seeding='manual')
    view, _ = _play_out(config, higher_wins)
    assert view['ranking'] == [0, 1, 2, 3, 4]
    config = _config([{'format': 'ladder', 'rounds': 5}], 5, seeding='manual')
    view, _ = _play_out(config, lambda match: [0, 1])
    # Challengers always winning reverse a ladder of five in five rounds.
    assert view['ranking'] == [4, 3, 2, 1, 0]
    config = _config([{'format': 'king_of_the_hill'}], 4, seeding='manual')
    view, _ = _play_out(config, lambda match: [0, 0])
    assert view['champion'] == 0
    config = _config([{'format': 'gauntlet'}], 4, seeding='manual')
    view, _ = _play_out(config, lambda match: [0, 1])
    # The climber wins every step, so the lowest seed climbs to the top.
    assert view['champion'] == 3
    assert view['ranking'] == [3, 0, 1, 2]


def test_the_same_seed_gives_the_same_draw_and_match_seeds():
    stages = [{'format': 'groups', 'group_size': 4}, {'format': 'knockout'}]
    first = _config(stages, 16, seed=5)
    second = _config(stages, 16, seed=5)
    other = _config(stages, 16, seed=6)
    a, _ = _play_out(first, seed=1)
    b, _ = _play_out(second, seed=1)
    c, _ = _play_out(other, seed=1)
    seeds = lambda view: [(m['key'], m['seed'], m['home'], m['away']) for m in view['matches']]  # noqa: E731
    assert seeds(a) == seeds(b)
    assert seeds(a) != seeds(c)
    assert rules.match_seed(5, '0/A/1-1') == rules.match_seed(5, '0/A/1-1')
    assert 0 <= rules.match_seed(5, 'x') < 2**31


def test_seeding_by_rating_and_manual_order():
    entrants = [
        {'kind': 'builtin', 'ability': 0.1, 'rating': 1400},
        {'kind': 'builtin', 'ability': 0.2, 'rating': 1700},
        {'kind': 'builtin', 'ability': 0.3},
    ]
    config = rules.check(
        {
            'name': 'R',
            'seed': 1,
            'entrants': entrants,
            'seeding': 'rating',
            'rating_scale': 'table',
            'stages': [{'format': 'league'}],
        },
        _TEAMS,
        ratings={'builtin:0.30': 1600},
    )
    assert rules.seeding(config) == [1, 2, 0]
    config['seeding'] = 'manual'
    assert rules.seeding(config) == [0, 1, 2]


def test_a_configuration_without_evidence_reads_its_ratings_on_the_earlier_scale():
    """A configuration of 0.1.0 names no evidence, and its ratings from 1500 read 500 lower."""
    from cuju.arena._evaluate import ratings as _evidence

    assert rules.TABLE_START == _evidence.ANCHOR_RATING
    assert rules.EARLIER_SHIFT == rules.BASE_RATING - rules.TABLE_START == 500.0
    base = {
        'name': 'R',
        'seed': 1,
        'seeding': 'rating',
        'stages': [{'format': 'league'}],
        'entrants': [
            {'kind': 'builtin', 'ability': 0.1, 'rating': 1650},
            {'kind': 'builtin', 'ability': 0.2},
        ],
    }
    earlier = rules.check(base, _TEAMS)
    assert earlier['rating_scale'] == 'earlier' and earlier['entrants'][0]['rating'] == 1150.0
    table = rules.check({**base, 'evidence': 'proceed'}, _TEAMS)
    assert table['rating_scale'] == 'table' and table['entrants'][0]['rating'] == 1650.0
    stated = rules.check({**base, 'rating_scale': 'table'}, _TEAMS)
    assert stated['entrants'][0]['rating'] == 1650.0
    stated = rules.check({**base, 'evidence': 'proceed', 'rating_scale': 'earlier'}, _TEAMS)
    assert stated['entrants'][0]['rating'] == 1150.0
    # The entrants as given keep their numbers and the scale stays with the configuration, so a
    # configuration made again from them, as a pre-flight does, reads the same ratings.
    again = rules.check(
        {
            **{key: earlier[key] for key in ('seed', 'seeding', 'evidence', 'rating_scale')},
            'entrants': earlier['given_entrants'],
            'stages': earlier['given']['stages'],
        },
        _TEAMS,
    )
    assert earlier['given_entrants'][0]['rating'] == 1650
    assert again['entrants'][0]['rating'] == 1150.0
    with pytest.raises(rules.TournamentError):
        rules.check({**base, 'rating_scale': 'elo'}, _TEAMS)


def test_ratings_move_by_elo():
    table = {}
    home = {'key': 'a', 'name': 'A'}
    away = {'key': 'b', 'name': 'B'}
    rules.update_ratings(table, home, away, [2, 0])
    assert table['a']['rating'] == 1510 and table['b']['rating'] == 1490
    rules.update_ratings(table, home, away, [1, 1])
    assert table['a']['rating'] < 1510 and table['a']['matches'] == 2


def test_stages_take_their_own_scenarios_within_what_entrants_play():
    entrants = [
        {'kind': 'builtin', 'ability': 0.5},
        {'kind': 'person', 'name': 'Ana'},
        {'kind': 'builtin'},
        {'kind': 'builtin', 'ability': 1.0},
    ]
    stages = [
        {'format': 'league', 'scenario': '5_vs_5', 'advance': 2, 'length': 600},
        {'format': 'knockout', 'scenario': '11_vs_11_kaggle'},
    ]
    config = rules.check(
        {'name': 'Mixed', 'seed': 2, 'entrants': entrants, 'stages': stages}, _TEAMS, _fits
    )
    assert [stage['scenario'] for stage in config['stages']] == ['5_vs_5', '11_vs_11_kaggle']
    assert config['entrants'][1]['kind'] == 'person'
    view = rules.evaluate(config, {})
    assert all(match['length'] == 600 for match in view['matches'])
    assert any(match['person'] for match in view['matches'])
    with pytest.raises(rules.TournamentError, match='Minis cannot play 11_vs_11_kaggle'):
        rules.check(
            {
                'name': 'Mixed',
                'seed': 2,
                'entrants': [*entrants[:3], {'kind': 'team', 'team': 'minis'}],
                'stages': stages,
            },
            _TEAMS,
            _fits,
        )


@pytest.mark.parametrize(
    ('change', 'message'),
    [
        ({'entrants': _builtins(1)}, 'from 2 to 256 entrants'),
        ({'entrants': _builtins(257)}, 'from 2 to 256 entrants'),
        ({'stages': [{'format': 'cup'}]}, 'format of stage 1'),
        ({'stages': [{'format': 'knockout', 'bracket_size': 4}]}, 'needs from 3 to 4 entrants'),
        (
            {'stages': [{'format': 'knockout', 'bracket': 'play_in', 'bracket_size': 16}]},
            'needs from 16 to 31',
        ),
        ({'stages': [{'format': 'knockout', 'until': 3}]}, 'power of two below'),
        ({'stages': [{'format': 'groups', 'groups': 5}]}, 'at least 2 entrants'),
        ({'stages': [{'format': 'groups', 'group_sizes': [4, 3]}]}, 'hold 7 entrants'),
        ({'stages': [{'format': 'groups', 'qualifiers': 5}]}, 'qualifiers of group A of stage 1'),
        ({'stages': [{'format': 'swiss', 'rounds': 5}]}, 'without rematches'),
        (
            {
                'stages': [
                    {'format': 'league', 'advance': 2},
                    {'format': 'league', 'advance': 1},
                    {'format': 'league'},
                ]
            },
            'advance from stage 2',
        ),
        ({'stages': [{'format': 'knockout', 'legs': 11}]}, 'legs of stage 1'),
        (
            {
                'stages': [
                    {'format': 'league', 'tiebreakers': ['goal_difference', 'goal_difference']}
                ]
            },
            'without repeats',
        ),
        ({'stages': [{'format': 'league', 'points': {'win': 3}}]}, 'draw and loss in stage 1'),
        (
            {'stages': [{'format': 'league', 'points': {'win': 300, 'draw': 1, 'loss': 0}}]},
            'the points of a win in stage 1',
        ),
        (
            {'entrants': _builtins(3), 'stages': [{'format': 'knockout', 'bracket_size': 2}]},
            'a bracket of 2 needs exactly 2 entrants, and 3 enter stage 1',
        ),
        (
            {'entrants': _builtins(2), 'stages': [{'format': 'swiss', 'rounds': 2}]},
            'which pairs 2 entrants without rematches, to be 1, found 2',
        ),
        ({'stages': [{'format': 'league', 'cup': 1}]}, 'unknown settings'),
        ({'stages': [{'format': 'league', 'length': 10}]}, 'length of stage 1'),
        ({'concurrency': 9}, 'matches at once'),
        (
            {
                'entrants': [
                    {'kind': 'team', 'team': 'reds', 'name': 'Rex'},
                    {'kind': 'team', 'team': 'reds', 'name': ' rex '},
                ]
            },
            'a name of its own for entrant 2, found rex, the name of entrant 1',
        ),
        ({'entrants': [{'kind': 'person'}, {'kind': 'builtin'}]}, 'name for each person'),
        ({'stages': [{'format': 'double_elimination', 'bracket': 'play_in'}]}, 'fills with byes'),
    ],
)
def test_impossible_configurations_name_the_reason(change, message):
    config = {
        'name': 'Bad',
        'seed': 1,
        'entrants': _builtins(8),
        'stages': [{'format': 'knockout'}],
        **change,
    }
    with pytest.raises(rules.TournamentError, match=message):
        rules.check(config, _TEAMS, _fits)


@pytest.mark.parametrize('name', sorted(rules.PRESETS))
def test_shipped_presets_fit_every_number_of_entrants(name):
    for count in (2, 3, 4, 5, 6, 7, 8, 9, 12, 16, 17, 24, 33):
        preset = rules.preset_config(name)
        config = rules.check(
            {**preset, 'name': name, 'seed': 1, 'entrants': _builtins(count)}, _TEAMS
        )
        view, _ = _play_out(config, seed=count)
        # A preset may add entrants of its own, such as the random entrant.
        added = len(rules.with_included(_builtins(count), preset.get('include', []))) - count
        assert sorted(view['ranking']) == list(range(count + added))


def test_a_shipped_preset_fits_its_rounds_and_an_edited_one_refuses():
    preset = rules.preset_config('swiss_16')
    config = {**preset, 'name': 'Tiny', 'seed': 1, 'entrants': _builtins(3)}
    assert rules.check(config, _TEAMS)['stages'][0]['rounds'] == 2
    assert rules.check({**config, 'entrants': _builtins(16)}, _TEAMS)['stages'][0]['rounds'] == 5
    # Stages that differ from the preset are the user's own, and too many rounds name the reason.
    edited = {**config, 'stages': [{'format': 'swiss', 'name': 'Swiss', 'rounds': 4}]}
    with pytest.raises(rules.TournamentError, match='without rematches'):
        rules.check(edited, _TEAMS)
    phase = rules.check(
        {**rules.preset_config('league_phase'), 'name': 'x', 'seed': 1, 'entrants': _builtins(5)},
        _TEAMS,
    )
    assert phase['stages'][0]['advance'] == 4
    euro = rules.check(
        {**rules.preset_config('euro'), 'name': 'x', 'seed': 1, 'entrants': _builtins(2)}, _TEAMS
    )
    assert euro['stages'][0]['group_size'] == 2 and euro['stages'][0]['best_position'] is None


def test_a_tie_without_a_played_match_has_no_score():
    config = _config([{'format': 'knockout', 'legs': 2, 'settle': ['lots']}], 4)
    tie = rules.evaluate(config, {})['stages'][0]['rounds'][0]['fixtures'][0]
    assert tie['state'] == 'ready' and tie['score'] is None
    first = tie['matches'][0]
    results = {first: {'score': [2, 1]}}
    tie = rules.evaluate(config, results)['stages'][0]['rounds'][0]['fixtures'][0]
    assert tie['state'] == 'ready' and sorted(tie['score']) == [1, 2]


def test_entrant_names_repeat_with_a_number():
    config = _config([{'format': 'league'}], 2)
    assert [e['name'] for e in config['entrants']] == ['Built-in AI 0.00', 'Built-in AI 0.50']
    entrants = rules.check_entrants([{'kind': 'builtin'}, {'kind': 'builtin'}], _TEAMS)
    assert [e['name'] for e in entrants] == ['Built-in AI', 'Built-in AI 2']


def test_a_team_enters_more_than_once_under_names_of_its_own():
    entrants = rules.check_entrants(
        [
            {'kind': 'team', 'team': 'reds'},
            {'kind': 'team', 'team': 'reds'},
            {'kind': 'team', 'team': 'reds', 'name': 'Reds B'},
            {'kind': 'team', 'team': 'blues'},
        ],
        _TEAMS,
        {'reds': 1620.0},
    )
    assert [e['name'] for e in entrants] == ['Reds', 'Reds 2', 'Reds B', 'Blues']
    # Every entrant of the team plays the team and shares its rating.
    assert [e['team'] for e in entrants[:3]] == ['reds'] * 3
    assert [e['key'] for e in entrants[:3]] == ['reds'] * 3
    assert [e['rating'] for e in entrants] == [1620.0, 1620.0, 1620.0, rules.BASE_RATING]
    # A made name steps around a given one, wherever the given one stands.
    entrants = rules.check_entrants(
        [
            {'kind': 'team', 'team': 'reds'},
            {'kind': 'team', 'team': 'reds'},
            {'kind': 'person', 'name': 'REDS 2'},
        ],
        _TEAMS,
    )
    assert [e['name'] for e in entrants] == ['Reds', 'Reds 3', 'REDS 2']
    # The free name that the page proposes is the name that the rules would make.
    taken = {rules.name_key(name) for name in ('Reds', 'REDS 2')}
    assert rules.free_name('Reds', taken) == 'Reds 3'


def test_two_entrants_of_one_team_meet_and_leave_its_rating():
    config = rules.check(
        {
            'seed': 3,
            'seeding': 'manual',
            'entrants': [
                {'kind': 'team', 'team': 'reds'},
                {'kind': 'team', 'team': 'reds'},
                {'kind': 'team', 'team': 'blues'},
            ],
            'stages': [{'format': 'league', 'scenario': '11_vs_11_kaggle'}],
        },
        _TEAMS,
        _fits,
    )
    view, _ = _play_out(config, score=lambda match: [2, 1])
    derby = [m for m in view['matches'] if {m['home'], m['away']} == {0, 1}]
    assert len(derby) == 1
    rows = {row['entrant']: row for row in view['stages'][0]['tables'][0]['rows']}
    # The table keeps the two entrants of one team apart, each with its own record.
    assert len(rows) == 3 and sum(row['played'] for row in rows.values()) == 6
    assert sorted(view['ranking']) == [0, 1, 2]
    table = {}
    rules.update_ratings(table, config['entrants'][0], config['entrants'][1], [2, 1])
    assert table == {}
    rules.update_ratings(table, config['entrants'][0], config['entrants'][2], [2, 1])
    assert table['reds']['rating'] > rules.BASE_RATING > table['blues']['rating']


def test_bracket_order_and_round_robin():
    assert rules.bracket_order(8) == [0, 7, 3, 4, 1, 6, 2, 5]
    for count in (2, 3, 6, 7):
        rounds = rules.round_robin(list(range(count)))
        pairs = [frozenset(p) for r in rounds for p in r if None not in p]
        assert len(pairs) == len(set(pairs)) == count * (count - 1) // 2
    assert rules.group_name(0) == 'A' and rules.group_name(26) == 'AA'


def test_tables_name_no_tiebreak_before_any_match():
    config = _config([{'format': 'league'}], 4)
    table = rules.evaluate(config, {})['stages'][0]['tables'][0]['rows']
    assert all(row['tiebreak'] is None for row in table)
    view = rules.evaluate(config, {'0/1-1': {'score': [0, 0]}})
    rows = view['stages'][0]['tables'][0]['rows']
    assert any(row['tiebreak'] for row in rows)


def test_show_names_byes_replays_and_deciders():
    from cuju.arena._tournaments.tournament_cli import describe

    config = _config(
        [{'format': 'knockout', 'settle': ['replay', 'decider', 'lots']}], 2, seeding='manual'
    )
    results = {'0/w2-1/1': {'score': [1, 1]}, '0/w2-1/r1': {'score': [0, 0]}}
    results['0/w2-1/d1'] = {'score': [2, 0]}
    results['0/w2-1/d2'] = {'score': [1, 0]}
    view = rules.evaluate(config, results)
    entry = {
        'name': 'Cup',
        'id': 'cup-x',
        'state': 'done',
        'seed': 11,
        'played': 4,
        'scheduled': 4,
        'error': None,
        'config': config,
        'stages_view': view['stages'],
        'champion': 'x',
        'matches': [{**m, 'state': 'done'} for m in view['matches']],
        'ranking': view['ranking'],
    }
    text = '\n'.join(describe(entry))
    first = config['entrants'][0]['name']
    # The notes run in the order the tie went, and name who went on.
    assert f'1 to 1, replay 0 to 0, deciding matches 2 to 1, {first} wins' in text
    results['0/w2-1/d2'] = {'score': [2, 0]}
    view = rules.evaluate(config, results)
    entry.update(stages_view=view['stages'], ranking=view['ranking'])
    winner = config['entrants'][view['stages'][0]['rounds'][0]['fixtures'][0]['winner']]['name']
    text = '\n'.join(describe(entry))
    assert f'replay 0 to 0, deciding matches 2 to 2, then {winner} wins by lots' in text
    # A tie with a bye names the entrant alone, and a tie of two byes leaves no line.
    double = _config([{'format': 'double_elimination'}], 3, seeding='manual')
    view = rules.evaluate(double, {})
    entry.update(config=double, stages_view=view['stages'], matches=[], ranking=None)
    text = '\n'.join(describe(entry))
    assert f'    {double["entrants"][0]["name"]}, a bye' in text
    assert 'against a bye' not in text and 'a bye against' not in text
    assert '    Loser of Winners round 1, tie 2, a bye' in text
    entry.update(played=0, scheduled=2, expected=4)
    assert '0 of 4 matches played, 2 scheduled so far' in '\n'.join(describe(entry))
    # A cancelled tournament reads its unfinished stage and matches as cancelled.
    entry.update(state='cancelled', matches=[{**m, 'state': 'ready'} for m in view['matches']])
    text = '\n'.join(describe(entry))
    assert 'Double elimination, cancelled' in text and 'Matches not played' in text
    assert 'not played, the tournament was cancelled' in text and 'in the queue' not in text
    entry.update(state='done')
    swiss = _config([{'format': 'swiss', 'rounds': 1}], 3)
    view = rules.evaluate(swiss, {})
    entry.update(config=swiss, stages_view=view['stages'], matches=[], ranking=None)
    text = '\n'.join(describe(entry))
    bye = swiss['entrants'][view['stages'][0]['rounds'][0]['bye']]['name']
    assert f'Round 1, bye for {bye}' in text


# The names and labels that the rules make, which the page shows in its own language.


def _label_ids(value):
    """Returns the ids of a label and of the labels among its values, outermost first."""
    if not isinstance(value, dict) or 'id' not in value:
        return []
    found = [value['id']]
    for inner in value['values'].values():
        found += _label_ids(inner)
    return found


def test_generated_names_travel_as_labels_and_given_names_as_text():
    from cuju.arena._text import messages as _messages

    config = rules.check(
        {
            'seed': 4,
            'entrants': [
                {'kind': 'builtin', 'ability': 0.5},
                {'kind': 'builtin', 'ability': 0.5},
                {'kind': 'builtin'},
                {'kind': 'builtin', 'name': 'Rex'},
                {'kind': 'team', 'team': 'reds'},
            ],
            'stages': [
                {'format': 'groups', 'name': 'Group stage', 'group_size': 3, 'qualifiers': 1},
                {'format': 'knockout', 'name': 'Our final'},
            ],
        },
        _TEAMS,
    )
    # The English text stays as it was, for the command line and for older readers.
    assert config['name'] == 'Tournament'
    assert config['name_id'] == 'server.tournament.label.tournament'
    entrants = config['entrants']
    assert [e['name'] for e in entrants] == [
        'Built-in AI 0.50',
        'Built-in AI 0.50 2',
        'Built-in AI',
        'Rex',
        'Reds',
    ]
    assert entrants[0]['name_id'] == 'server.tournament.label.builtin_ability'
    assert entrants[0]['name_values'] == {'ability': '0.50'}
    assert _label_ids(rules.label_of(entrants[1])) == [
        'server.tournament.label.numbered',
        'server.tournament.label.builtin_ability',
    ]
    # A name that a person gave, and the name of a registered team, stay plain text.
    assert 'name_id' not in entrants[3] and 'name_id' not in entrants[4]
    stages = config['stages']
    assert stages[0]['name_id'] == 'server.tournament.label.stage.group_stage'
    assert stages[1]['name'] == 'Our final' and 'name_id' not in stages[1]
    view = rules.evaluate(config, {})
    groups, knockout = view['stages']
    assert groups['name_id'] == 'server.tournament.label.stage.group_stage'
    table = groups['tables'][0]
    assert (table['name'], table['name_id'], table['name_values']) == (
        'Group A',
        'server.tournament.label.group',
        {'group': 'A'},
    )
    first = groups['rounds'][0]
    assert (first['name'], first['name_id']) == ('Matchday 1', 'server.tournament.label.matchday')
    match = view['matches'][0]
    assert match['label'] == 'Group A, matchday 1'
    assert match['label_id'] == 'server.tournament.label.group_matchday'
    assert 'name_id' not in knockout
    # Every label is a known message whose text its values give.
    for holder, field in [(entrants[1], 'name'), (table, 'name'), (match, 'label')]:
        found = rules.label_of(holder, field)
        assert _messages.valid_label(found)
        assert _messages.text(found['id'], **found['values']) == holder[field]


def test_knockout_labels_name_rounds_ties_legs_and_sides_still_to_come():
    config = _config(
        [{'format': 'knockout', 'third_place': True, 'legs': 2, 'settle': ['lots']}], 8
    )
    view = rules.evaluate(config, {})
    rounds = view['stages'][0]['rounds']
    assert [r['name'] for r in rounds] == [
        'Quarter-finals',
        'Semi-finals',
        'Final',
        'Third place match',
    ]
    assert [r['name_id'].rsplit('.', 1)[1] for r in rounds] == [
        'quarter_finals',
        'semi_finals',
        'final',
        'third_place',
    ]
    semi = rounds[1]['fixtures'][0]
    assert semi['label'] == 'Semi-final 1' and semi['label_values'] == {'number': '1'}
    side = semi['home']
    assert side['from'] == 'Winner of Quarter-final 1'
    assert _label_ids(rules.label_of(side, 'from')) == [
        'server.tournament.label.winner_of',
        'server.tournament.label.quarter_final',
    ]
    third = rounds[3]['fixtures'][0]
    assert third['home']['from_id'] == 'server.tournament.label.loser_of'
    leg = view['matches'][0]
    assert leg['label'] == 'Quarter-final 1, leg 1'
    assert _label_ids(rules.label_of(leg, 'label')) == [
        'server.tournament.label.leg',
        'server.tournament.label.quarter_final',
    ]


@pytest.mark.parametrize('stage', _FORMATS, ids=lambda stage: '-'.join(map(str, stage.values())))
def test_every_name_of_every_format_is_a_label_of_its_text(stage):
    from cuju.arena._text import messages as _messages

    def same(holder, field):
        found = rules.label_of(holder, field)
        assert _messages.valid_label(found), holder[field]
        assert _messages.text(found['id'], **found['values']) == holder[field]

    config = _config([stage], 7)
    view, _ = _play_out(config)
    for part in view['stages']:
        same(part, 'name')
        for table in part['tables']:
            same(table, 'name')
        for round_ in part['rounds']:
            same(round_, 'name')
            for fixture in round_['fixtures']:
                if 'label' in fixture:
                    same(fixture, 'label')
    for match in view['matches']:
        same(match, 'label')


def test_a_refused_entrant_names_the_scenario_and_reason_as_values():
    def fits(entrant, scenario):
        if entrant['format'] == '5v5':
            return {
                'id': 'server.teams.scenario_format',
                'text': 'unused',
                'values': {'format': '5v5'},
            }
        return None

    with pytest.raises(rules.TournamentError) as caught:
        rules.check(
            {
                'name': 'Mixed',
                'seed': 1,
                'entrants': [{'kind': 'team', 'team': 'minis'}, {'kind': 'builtin'}],
                'stages': [{'format': 'knockout', 'scenario': '11_vs_11_kaggle'}],
            },
            _TEAMS,
            fits,
        )
    values = caught.value.values
    # The scenario travels as a scenario value, which the page names, and the text stays English.
    assert values['scenario'] == {
        'kind': 'scenario',
        'name': '11_vs_11_kaggle',
        'text': '11_vs_11_kaggle',
    }
    assert values['reason']['id'] == 'server.teams.scenario_format'
    assert str(caught.value).startswith('Minis cannot play 11_vs_11_kaggle in stage 1, unused')


def test_both_sizes_hear_plainly_when_no_scenario_fits_them():
    def fits(entrant, scenario):
        if scenario.startswith('11'):
            if entrant['format'] == '11v11':
                return None
            return {'id': 'server.tournament.team_format', 'text': 'x', 'values': {'format': '5v5'}}
        return 'too few players'

    with pytest.raises(rules.TournamentError) as caught:
        rules.check(
            {
                'seed': 1,
                'entrants': [{'kind': 'team', 'team': 'minis'}, {'kind': 'team', 'team': 'reds'}],
                'stages': [{'format': 'league', 'scenario': '11_vs_11_kaggle'}],
            },
            _TEAMS,
            fits,
            {},
            ['11_vs_11_kaggle', 'academy_empty_goal', 'academy_run_to_score_with_keeper'],
        )
    assert caught.value.message_id == 'server.tournament.mixed_sizes_none'
    assert str(caught.value).endswith('so enter teams of one size')


def test_a_saved_unchanged_copy_of_a_shipped_preset_fits_as_it_does():
    """A preset of the user that holds the stages of a shipped one fits the number of entrants
    as the shipped one does, and keeps the settings that the person changed."""
    shipped = rules.preset_config('swiss_16')
    saved = {**shipped, 'preset': 'my_swiss', 'seeding': 'manual', 'concurrency': 2}
    config = rules.check({**saved, 'name': 'Tiny', 'seed': 1, 'entrants': _builtins(3)}, _TEAMS)
    assert config['stages'][0]['rounds'] == 2
    assert (config['preset'], config['seeding'], config['concurrency']) == ('my_swiss', 'manual', 2)
    euro = {**rules.preset_config('euro'), 'preset': 'my_euro', 'defaults': {'length': 400}}
    small = rules.check({**euro, 'name': 'x', 'seed': 1, 'entrants': _builtins(2)}, _TEAMS)
    assert small['stages'][0]['group_size'] == 2 and small['stages'][0]['length'] == 400


def test_a_team_that_cannot_play_the_deciding_matches_is_refused_by_them():
    """The refusal names the deciding matches of the stage apart from the stage's scenario."""

    def fits(entrant, scenario):
        return 'too few players' if scenario.startswith('academy') else None

    config = {
        'name': 'Cup',
        'seed': 1,
        'entrants': _builtins(4),
        'stages': [{'format': 'knockout', 'settle': ['decider', 'lots']}],
    }
    with pytest.raises(rules.TournamentError) as caught:
        rules.check(config, _TEAMS, fits)
    assert caught.value.message_id == 'server.tournament.cannot_play_decider'
    assert str(caught.value.values['stage']) == '1'
    assert 'the scenario of the deciding matches of stage 1' in str(caught.value)
    config['stages'][0]['settle'] = ['replay', 'lots']
    assert rules.check(config, _TEAMS, fits)['stages'][0]['settle'] == ['replay', 'lots']


def test_a_failure_names_the_team_as_its_entrant(monkeypatch):
    monkeypatch.setattr(runner._teams, 'entry', lambda identifier: {'name': identifier.title()})
    record = {
        'config': {
            'entrants': [
                {'kind': 'team', 'team': 'crashers', 'name': 'Crash FC'},
                {'kind': 'team', 'team': 'reds', 'name': 'Reds'},
            ]
        }
    }
    match = {'home': 0, 'away': 1}
    ids = {
        'error_id': 'server.policy.failed',
        'error_values': {'player': 'Crashers', 'reason': 'TypeError: no'},
    }
    error, found = runner.Tournaments._entrant_failure(
        record, match, 'the policy of Crashers failed, TypeError: no', ids
    )
    assert error == 'the policy of Crash FC failed, TypeError: no'
    assert found['error_values']['player'] == 'Crash FC'
    # A team that plays under its own name, and an error without a player, stay as they are.
    ids['error_values']['player'] = 'Reds'
    assert runner.Tournaments._entrant_failure(record, match, 'x', ids) == ('x', ids)
    assert runner.Tournaments._entrant_failure(record, match, 'y', {}) == ('y', {})
    # Two entrants of one team leave a failure that names the team as it is, since either side
    # may have failed. A match of agents names its entrant itself, from the tournament's names.
    record['config']['entrants'][1] = {'kind': 'team', 'team': 'crashers', 'name': 'Crash B'}
    ids['error_values']['player'] = 'Crashers'
    assert runner.Tournaments._entrant_failure(record, match, 'z', ids) == ('z', ids)


# The abilities of the entrants, the stages and the tournament's standard.


def _abilities(config):
    """Returns the abilities of every first stage match by its sides' names, home first."""
    names = [e['name'] for e in config['entrants']]
    view = rules.evaluate(config, {})
    return {(names[m['home']], names[m['away']]): m['ability'] for m in view['matches']}


def test_an_entrant_then_the_stage_then_the_standard_then_the_scenario_set_the_ability():
    entrants = [
        {'kind': 'team', 'team': 'reds', 'ability': 0.2},
        {'kind': 'team', 'team': 'blues'},
        {'kind': 'builtin'},
        {'kind': 'person', 'name': 'Ana', 'ability': 0.0},
    ]
    stage = {'format': 'league', 'scenario': '11_vs_11_kaggle'}
    base = {'seed': 2, 'entrants': entrants, 'stages': [stage]}
    config = rules.check({**base, 'ability': 0.8}, _TEAMS, _fits)
    assert config['ability'] == 0.8
    assert [e['ability'] for e in config['entrants']] == [0.2, None, None, 0.0]
    # Every kind keeps its name and key, and a built-in AI without an ability stays as it was.
    assert [e['key'] for e in config['entrants']] == [
        'reds',
        'blues',
        'builtin:scenario',
        'person:Ana',
    ]
    own = {'Reds': 0.2, 'Blues': 0.8, 'Built-in AI': 0.8, 'Ana': 0.0}
    for (home, away), ability in _abilities(config).items():
        assert ability == [own[home], own[away]]
    # A stage's ability comes before the standard, and an entrant's own before both.
    config = rules.check(
        {**base, 'ability': 0.8, 'stages': [{**stage, 'ability': 0.5}]}, _TEAMS, _fits
    )
    own = {'Reds': 0.2, 'Blues': 0.5, 'Built-in AI': 0.5, 'Ana': 0.0}
    for (home, away), ability in _abilities(config).items():
        assert ability == [own[home], own[away]]
    # Without a standard or a stage's ability the scenario's own plays, as `None`.
    config = rules.check(base, _TEAMS, _fits)
    assert config['ability'] is None
    own = {'Reds': 0.2, 'Blues': None, 'Built-in AI': None, 'Ana': 0.0}
    for (home, away), ability in _abilities(config).items():
        assert ability == [own[home], own[away]]
    for wrong in (1.5, -0.1, 'high', True):
        with pytest.raises(rules.TournamentError, match='expected the standard ability from 0'):
            rules.check({**base, 'ability': wrong}, _TEAMS)
        with pytest.raises(rules.TournamentError, match='ability of entrant 2 from 0 to 1'):
            rules.check(
                {**base, 'entrants': [entrants[0], {**entrants[1], 'ability': wrong}]}, _TEAMS
            )


def test_two_entrants_of_one_team_play_at_their_own_abilities():
    config = rules.check(
        {
            'seed': 5,
            'ability': 0.4,
            'entrants': [
                {'kind': 'team', 'team': 'reds', 'ability': 0.9},
                {'kind': 'team', 'team': 'reds', 'ability': 0.1},
            ],
            'stages': [{'format': 'league', 'scenario': '11_vs_11_kaggle'}],
        },
        _TEAMS,
        _fits,
    )
    assert [e['name'] for e in config['entrants']] == ['Reds', 'Reds 2']
    assert [e['key'] for e in config['entrants']] == ['reds', 'reds']
    assert _abilities(config) in (
        {('Reds', 'Reds 2'): [0.9, 0.1]},
        {('Reds 2', 'Reds'): [0.1, 0.9]},
    )


def test_a_preset_keeps_its_standard_ability():
    preset = rules.check_preset(
        {'title': 'Strong cup', 'ability': 0.9, 'stages': [{'format': 'knockout'}]}
    )
    assert preset['ability'] == 0.9
    config = rules.preset_config('mine', {'mine': preset})
    assert config['ability'] == 0.9
    with pytest.raises(rules.TournamentError, match='standard ability'):
        rules.check_preset({'title': 'x', 'ability': 2, 'stages': [{'format': 'knockout'}]})


# The draw of a new tournament.


def _first_round(seed, preset='quick_cup', count=8, **options):
    config = rules.check(
        {**rules.preset_config(preset), 'seed': seed, 'entrants': _builtins(count), **options},
        _TEAMS,
    )
    stage = rules.evaluate(config, {})['stages'][0]
    if stage['tables'] and stage['format'] == 'groups':
        return [tuple(row['entrant'] for row in table['rows']) for table in stage['tables']]
    return [(fixture['home'], fixture['away']) for fixture in stage['rounds'][0]['fixtures']]


def test_a_new_tournament_draws_at_random_from_its_seed():
    assert {preset['seeding'] for preset in rules.PRESETS.values()} == {'random'}
    for preset in ('quick_cup', 'world_cup', 'double_elimination', 'swiss_16'):
        draws = {tuple(_first_round(seed, preset)) for seed in range(8)}
        assert len(draws) > 1, preset
        # The same seed draws the same again.
        assert _first_round(5, preset) == _first_round(5, preset)
    # By hand, the order of the entrants is the order of the seeds, seed 1 against the last.
    assert _first_round(5, seeding='manual')[0] == (0, 7)
    assert {tuple(_first_round(seed, seeding='manual')) for seed in range(4)} == {
        tuple(_first_round(0, seeding='manual'))
    }


def test_warming_keeps_quiet_about_a_failure(monkeypatch, caplog):
    from cuju.arena._registry import teams as _teams

    def broken(*arguments):
        raise RuntimeError('no builder')

    monkeypatch.setattr(_teams, 'scenario_players', broken)
    runner.warm()
    assert 'could not load the scenarios ahead of time' in caplog.text


def test_a_refused_entrant_carries_the_terminal_of_its_check(monkeypatch):
    config = {
        'stages': [{'scenario': '5_vs_5'}],
        'entrants': [
            {'kind': 'team', 'team': 'bad', 'name': 'Bad'},
            {'kind': 'team', 'team': 'quiet', 'name': 'Quiet'},
            {'kind': 'team', 'team': 'good', 'name': 'Good'},
        ],
    }
    rule = {'kind': 'load', 'reason': 'it did not load', 'reason_id': None, 'reason_values': None}
    reports = {
        'bad': {'ok': False, 'rule': rule, 'terminal': {'run': 'check-bad', 'side': 'left'}},
        'quiet': {'ok': False, 'rule': rule, 'terminal': None},
        'good': {'ok': True, 'rule': None},
    }
    monkeypatch.setattr(runner, 'check_config', lambda value: value)
    monkeypatch.setattr(runner, 'preflight', lambda team, scenario, choice: reports[team])
    excluded = {entry['team']: entry for entry in runner.preflight_config(config)['excluded']}
    assert excluded['bad']['terminal'] == {'run': 'check-bad', 'side': 'left'}
    assert excluded['quiet']['terminal'] is None
    assert 'good' not in excluded
    assert runner._terminal_of({'terminal': {'run': 7, 'side': 'right'}}) == {
        'run': '7',
        'side': 'right',
    }
    assert runner._terminal_of({'terminal': {'run': '', 'side': 'left'}}) is None


def test_tournament_can_create_and_preview_an_archived_checkpoint(arena_home, tmp_path, capsys):
    import json

    from cuju.arena.__main__ import main
    from cuju.arena._registry import checkpoints, teams

    folder = tmp_path / 'harbour'
    folder.mkdir()
    (folder / 'main.py').write_text('def agent(obs): return [0]')
    identifier = teams.add_folder(folder, 'Harbour', '5v5')['id']
    old = checkpoints.add(identifier, 'main.py', b'def agent(obs): return [1]', 'Early', 10)
    checkpoints.add(identifier, 'main.py', b'def agent(obs): return [2]', 'Late', 20)
    exact = f'{identifier}@{old["current"]}'
    for verb, reference in (('check', 'Harbour@Early'), ('new', exact)):
        assert (
            main(
                [
                    'tournament',
                    verb,
                    '--tournament-preset',
                    'round_robin',
                    '--team',
                    reference,
                    '--team',
                    'builtin:0.3',
                    '--scenario',
                    'academy_empty_goal_close',
                    '--length',
                    '50',
                    '--seed',
                    '7',
                    '--json',
                ]
            )
            == 0
        )
        answer = json.loads(capsys.readouterr().out)
        if verb == 'new':
            assert main(['tournament', 'show', answer['id'], '--json']) == 0
            answer = json.loads(capsys.readouterr().out)
        assert answer['config']['entrants'][0]['team'] == exact
