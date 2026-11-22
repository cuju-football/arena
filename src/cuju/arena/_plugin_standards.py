# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Names of the implementations that ship with the arena."""

PROVIDES = {
    'rating_method/1': ['points', 'bradley_terry'],
    'evidence/1': ['default'],
    'seeding/1': ['random', 'manual', 'rating', 'standing'],
    'pairing/1': ['least_settled'],
    'stage_type/1': [
        'knockout',
        'double_elimination',
        'league',
        'groups',
        'swiss',
        'ladder',
        'king_of_the_hill',
        'gauntlet',
    ],
    'tiebreaker/1': [
        'goal_difference',
        'goals_for',
        'goals_against',
        'head_to_head',
        'wins',
        'away_goals',
        'fair_play',
        'buchholz',
        'lots',
    ],
    'moments/1': ['cuju', 'value_drop', 'surprise', 'entropy'],
    'statistics/1': ['match', 'heat', 'signals'],
    'analytics/1': ['tracking', 'events', 'annotations', 'metrica', 'secondspectrum'],
    'attachment/1': ['annotations', 'overlays'],
}
