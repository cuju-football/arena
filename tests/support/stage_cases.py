# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Builds the results of the stage types and tiebreakers of a tournament on fixed inputs.

`cases()` returns three groups. `stages` plays every stage type to its end over seeds and numbers
of entrants, with the results of each round drawn from the key of the match, and keeps every view
that `evaluate` gave on the way. `tiebreakers` plays a league for each tiebreaker as the first
criterion, and `tournaments` plays whole tournaments of the presets and of chains of stages.
`tests/support/plugins/stages.json` holds the SHA-256 of the JSON of each case as it was before
the stage types and tiebreakers were plugins, and `tests/arena/test_plugin_stages.py` holds today's
cases equal to it, byte for byte. The module uses only names that exist before and after, so the
file can be made again from any tree.
"""

import hashlib
import json
import random

from cuju.arena._tournaments import tournament as rules

SCENARIO = 'academy_run_to_score_with_keeper'
TEAMS = [{'id': 'builtin', 'kind': 'builtin', 'name': 'Built-in AI', 'style': {}}]


def entrants(count):
    """Returns `count` built-in AI entrants of different difficulties."""
    return [
        {'kind': 'builtin', 'difficulty': round(0.05 + 0.9 * n / count, 3)} for n in range(count)
    ]


def _result(seed, match):
    """Draws a result from the tournament seed and the key of a match, with many level scores."""
    draw = random.Random(f'{seed}/{match["key"]}')
    score = [draw.choice((0, 0, 1, 1, 2, 3)), draw.choice((0, 0, 1, 1, 2, 3))]
    cards = [[draw.choice((0, 0, 1, 2)), draw.choice((0, 0, 0, 1))] for _ in range(2)]
    return {'score': score, 'cards': cards}


def play(config, one_at_a_time=False, limit=3000):
    """Plays a tournament to its end and returns every view that `evaluate` gave on the way."""
    results = {}
    views = []
    for _ in range(limit):
        view = rules.evaluate(config, results)
        views.append(view)
        ready = [match for match in view['matches'] if match['result'] is None]
        if not ready:
            return views
        for match in ready[:1] if one_at_a_time else ready:
            results[match['key']] = _result(config['seed'], match)
    raise AssertionError('the tournament did not end')


def _config(count, seed, stages, **options):
    return rules.check(
        {
            'name': 'Cases',
            'seed': seed,
            'seeding': options.pop('seeding', 'random'),
            'entrants': entrants(count),
            'rating_scale': 'table',
            'stages': [{'scenario': SCENARIO, **stage} for stage in stages],
            **options,
        },
        TEAMS,
    )


def _digest(views):
    data = json.dumps(views, sort_keys=True, default=str).encode()
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'views': len(views)}


# One list of stages for each stage type, the settings that change its draw and its schedule.
VARIANTS = {
    'knockout': [
        {},
        {'bracket': 'play_in'},
        {'third_place': True, 'legs': 2, 'final_legs': 1, 'settle': ['away_goals', 'replay']},
        {'until': 2, 'settle': ['decider', 'lots']},
        {'settle': ['replay', 'decider'], 'replays': 2},
    ],
    'double_elimination': [{}, {'grand_final_reset': False, 'legs': 2}],
    'league': [{}, {'cycles': 2, 'legs': 2}],
    'groups': [
        {'group_size': 4, 'qualifiers': 2},
        {'groups': 2, 'qualifiers': 1, 'best_position': 2, 'best_count': 1},
        {'group_size': 3, 'qualifiers': 1, 'pots': 2},
    ],
    'swiss': [{}, {'rounds': 3, 'legs': 2, 'bye_points': 'draw'}],
    'ladder': [{}, {'rounds': 4, 'draws': 'settle', 'settle': ['replay', 'lots']}],
    'king_of_the_hill': [{}, {'laps': 2, 'legs': 2, 'draws': 'settle'}],
    'gauntlet': [{}, {'legs': 2}],
}
COUNTS = (2, 3, 4, 5, 6, 8, 9, 13)
SEEDS = (3, 21, 99)


def _stage_cases():
    found = {}
    for name, variants in VARIANTS.items():
        for number, variant in enumerate(variants):
            for count in COUNTS:
                for seed in SEEDS:
                    try:
                        config = _config(count, seed, [{'format': name, **variant}])
                    except rules.TournamentError as error:
                        found[f'{name} {number} {count} {seed}'] = {'refused': str(error)}
                        continue
                    key = f'{name} {number} {count} {seed}'
                    views = play(config, one_at_a_time=count <= 4 and seed == 3)
                    found[key] = _digest(views)
    return found


TIEBREAKERS = (
    'goal_difference',
    'goals_for',
    'goals_against',
    'head_to_head',
    'wins',
    'away_goals',
    'fair_play',
    'buchholz',
    'lots',
)


def _tiebreaker_cases():
    found = {}
    for criterion in TIEBREAKERS:
        for count in (4, 7, 10):
            for seed in SEEDS:
                for form in ('league', 'swiss'):
                    stage = {'format': form, 'tiebreakers': [criterion]}
                    if form == 'swiss':
                        stage['rounds'] = 2
                    config = _config(count, seed, [stage])
                    found[f'{criterion} {form} {count} {seed}'] = _digest(play(config))
    # Chains of tiebreakers, including one that is a refusal.
    chains = (
        ['head_to_head', 'goal_difference', 'goals_for'],
        ['wins', 'buchholz', 'fair_play', 'away_goals', 'goals_against'],
        list(TIEBREAKERS),
    )
    for number, chain in enumerate(chains):
        for seed in SEEDS:
            config = _config(9, seed, [{'format': 'league', 'tiebreakers': chain}])
            found[f'chain {number} {seed}'] = _digest(play(config))
            grouped = _config(
                12, seed, [{'format': 'groups', 'group_size': 4, 'tiebreakers': chain}]
            )
            found[f'chain groups {number} {seed}'] = _digest(play(grouped))
    try:
        _config(4, 1, [{'format': 'league', 'tiebreakers': ['coin_toss']}])
    except rules.TournamentError as error:
        found['refused'] = {'text': str(error)}
    return found


CHAINS = {
    'groups then knockout': [
        {'format': 'groups', 'group_size': 4, 'qualifiers': 2},
        {'format': 'knockout', 'third_place': True},
    ],
    'league then swiss then gauntlet': [
        {'format': 'league', 'advance': 8},
        {'format': 'swiss', 'rounds': 2, 'advance': 4, 'seeding': 'rating'},
        {'format': 'gauntlet'},
    ],
    'swiss then double elimination': [
        {'format': 'swiss', 'rounds': 3, 'advance': 8},
        {'format': 'double_elimination', 'seeding': 'random'},
    ],
    'ladder then king then knockout': [
        {'format': 'ladder', 'rounds': 3, 'advance': 8},
        {'format': 'king_of_the_hill', 'advance': 4, 'seeding': 'random'},
        {'format': 'knockout', 'settle': ['lots']},
    ],
}


def _tournament_cases():
    found = {}
    for name in rules.PRESETS:
        for count in (6, 10, 16):
            for seed in (5, 77):
                base = rules.preset_config(name)
                config = rules.check(
                    {
                        **base,
                        'name': name,
                        'seed': seed,
                        'entrants': entrants(count),
                        'rating_scale': 'table',
                        'defaults': {**base.get('defaults', {}), 'scenario': SCENARIO},
                    },
                    TEAMS,
                )
                found[f'preset {name} {count} {seed}'] = _digest(play(config))
    for name, stages in CHAINS.items():
        for seed in (2, 8):
            config = _config(16, seed, stages, seeding='rating')
            found[f'chain {name} {seed}'] = _digest(play(config))
            found[f'chain one {name} {seed}'] = _digest(
                play(_config(10, seed, stages), one_at_a_time=True)
            )
    return found


def cases():
    """Returns the three groups of digests, ready for `json.dumps`."""
    return {
        'stages': _stage_cases(),
        'tiebreakers': _tiebreaker_cases(),
        'tournaments': _tournament_cases(),
    }


def text():
    """Returns the golden text, the SHA-256 and the size of the JSON of each case."""
    return json.dumps(cases(), indent=1, sort_keys=True) + '\n'
