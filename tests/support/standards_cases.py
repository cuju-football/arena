# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Builds the results of the standards of the plugin protocols on fixed inputs.

`cases()` returns a mapping that holds the ratings tables of both methods, the seedings of a
tournament and the pairings of a schedule, all from fixed seeds. `tests/support/plugins/
standards.json` holds the SHA-256 of the JSON of each case as it was before the standards were
plugins. `tests/arena/test_plugin_standards.py` keeps the cases equal, byte for byte. This
module uses only names that exist before and after, so the file can be made again from any tree.
"""

import hashlib
import json
import random

from cuju.arena._evaluate import ratings as _ratings
from cuju.arena._evaluate import schedule as _schedule
from cuju.arena._store import ratings as _store
from cuju.arena._tournaments import tournament as rules

SCENARIO = 'academy_run_to_score_with_keeper'
_SOURCES = ('tournament', 'rating', 'evaluation', 'match')


def _side(key, checkpoint=None):
    return {'key': key, 'checkpoint': checkpoint, 'name': key.title()}


def _keep_results():
    """Keeps 60 results among the built-in AI and four teams, the stronger winning more often."""
    strength = {
        'builtin:0.20': -300,
        'builtin:0.60': 0,
        'builtin:0.90': 250,
        'rex': 120,
        'ada': -80,
    }
    names = list(strength)
    generator = random.Random(11)
    for number in range(60):
        home, away = generator.sample(names, 2)
        gap = strength[home] - strength[away]
        share = 1 / (1 + 10 ** (-gap / 400))
        draw = generator.random()
        goals = [generator.randint(0, 3), generator.randint(0, 3)]
        if draw < share and goals[0] <= goals[1]:
            goals = [goals[1] + 1, goals[0]]
        elif draw >= share and goals[0] >= goals[1] and generator.random() < 0.7:
            goals = [goals[1], goals[0] + 1]
        checkpoint = {'rex': 'c1', 'ada': 'c2'}.get(home)
        other = {'rex': 'c1', 'ada': 'c2'}.get(away)
        _store.record(
            {
                'scenario': SCENARIO,
                'home': _side(home, checkpoint),
                'away': _side(away, other),
                'score': goals,
                'source': _SOURCES[number % 4],
                'ref': f'r{number}',
                'time': 1000.0 + number,
            }
        )


def _ratings_cases():
    _keep_results()
    found = {}
    for method in ('points', 'bradley_terry'):
        found[f'table {method}'] = _ratings.table(SCENARIO, method=method)
    found['table sources'] = _ratings.table(
        SCENARIO, overrides=['ratings.sources=["match", "rating"]']
    )
    found['table wide'] = _ratings.table(
        SCENARIO, overrides=['ratings.evidence.width=100', 'ratings.confidence=0.8']
    )
    found['table anchor'] = _ratings.table(
        SCENARIO, overrides=['ratings.anchor=0.9', 'ratings.method=bradley_terry']
    )
    found['overview'] = _ratings.overview()
    found['team'] = _ratings.team('rex', SCENARIO)
    sides = [_ratings.builtin_side(0.2), _ratings.builtin_side(0.4)]
    found['evidence'] = _ratings.evidence(sides, SCENARIO)
    return found


def _entrants(count):
    return [
        {'kind': 'builtin', 'difficulty': round(0.1 + 0.07 * n, 2), 'rating': 1100 - 37 * (n % 5)}
        for n in range(count)
    ]


def _seeding_cases():
    teams = [{'id': 'builtin', 'kind': 'builtin', 'name': 'Built-in AI', 'style': {}}]
    found = {}
    for seed in (3, 21, 99):
        for first in ('random', 'manual', 'rating'):
            for later in ('standing', 'random', 'rating'):
                config = rules.check(
                    {
                        'name': 'Seeded',
                        'seed': seed,
                        'seeding': first,
                        'entrants': _entrants(10),
                        'rating_scale': 'table',
                        'stages': [
                            {'format': 'league', 'scenario': SCENARIO, 'advance': 6},
                            {
                                'format': 'knockout',
                                'scenario': SCENARIO,
                                'seeding': later,
                                'settle': ['lots'],
                            },
                        ],
                    },
                    teams,
                )
                key = f'{seed} {first} {later}'
                found[key] = {
                    'first': rules.seeding(config),
                    'later': rules._reseed(config, config['stages'][1], 1, [7, 2, 9, 0, 5, 3]),
                }
    return found


def _pairing_cases():
    found = {}
    strength = {'p': 2, 'weak': 0, 'level': 2, 'strong': 4, 'x': 1, 'y': 3, 'z': 2}
    for name, options in (
        (
            'explicit',
            {'pairs': [('p', other) for other in ('weak', 'level', 'strong')], 'limit': 30},
        ),
        (
            'explicit once',
            {'pairs': [('p', 'x'), ('p', 'y'), ('x', 'y')], 'limit': 6, 'settle': None},
        ),
        ('each', {'each': 5, 'settle': 0.8}),
        ('anchor', {'each': 4, 'fixed': ['z'], 'anchor': 'z', 'anchor_matches': 2}),
        ('batched', {'mode': 'batched', 'each': 4}),
    ):
        sides = (
            ['p', 'weak', 'level', 'strong', 'x', 'y', 'z']
            if 'pairs' not in options
            else (['p', 'weak', 'level', 'strong'] if name == 'explicit' else ['p', 'x', 'y'])
        )
        for seed in (1, 5):
            schedule = _schedule.Schedule(
                sides,
                **{'mode': 'on_demand', 'seed': seed, 'at_once': 2, **options},
            )
            generator = random.Random(seed)
            rounds = 0
            while not schedule.done:
                for pairing in schedule.due():
                    gap = strength[pairing['home']] - strength[pairing['away']]
                    goals = [
                        max(0, int(generator.gauss(1 + gap / 4, 1))),
                        max(0, int(generator.gauss(1, 1))),
                    ]
                    schedule.finish(pairing['number'], goals)
                rounds += 1
                assert rounds < 100000
            found[f'{name} {seed}'] = {'state': schedule.state(), 'progress': schedule.progress()}
    return found


def cases():
    """Returns the three groups of results, ready for `json.dumps`."""
    return {
        'ratings': _ratings_cases(),
        'seeding': _seeding_cases(),
        'pairing': _pairing_cases(),
    }


def text():
    """Returns the golden text, the SHA-256 and the size of the JSON of each case."""
    found = {}
    for group, members in cases().items():
        found[group] = {}
        for name, value in members.items():
            data = json.dumps(value, sort_keys=True, default=str).encode()
            found[group][name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    return json.dumps(found, indent=1, sort_keys=True) + '\n'
