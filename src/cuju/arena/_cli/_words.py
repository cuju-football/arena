# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The fixed words that arena completes."""

from functools import lru_cache
from pathlib import Path

FORMATS = ('kaggle', 'kaggle/1', 'strict', 'strict/1')
TEAMS = (
    'builtin',
    'builtin:0',
    'builtin:0.05',
    'builtin:0.1',
    'builtin:0.3',
    'builtin:0.5',
    'builtin:0.6',
    'builtin:0.60',
    'builtin:0.7',
    'builtin:0.8',
    'builtin:1',
    'random',
)
DAYS = ('today', 'yesterday')
SEARCH = {
    'kind': 'goal',
    'team': 'Harbour',
    'player': '9',
    'scenario': '5_vs_5',
    'source': 'import',
    'tournament': 'cup-1',
    'match': 'sample-5v5',
    'date': 'today',
    'since': '2026-11-01',
    'until': '2026-11-20',
    'after': '60',
    'before': '75',
    'score': '0-0',
    'final': '2-1',
    'outcome': 'saved',
    'limit': '10',
    'value-drop': 'value',
    'surprise': '0.05',
    'entropy': '>1.5',
    'reward': '>0',
    'ann': 'value<0.5',
}


@lru_cache(maxsize=1)
def scenarios() -> tuple[str, ...]:
    """Return the shipped scenarios from the engine and the arena."""
    from cuju import bundled_scenarios

    folder = Path(__file__).parents[1] / 'scenarios'
    return tuple(sorted(set(bundled_scenarios()) | {p.stem for p in folder.glob('*.py')}))


def offer(parser, *arguments, completion, **keywords):
    """Add an argument with static suggestions for its open vocabulary."""
    action = parser.add_argument(*arguments, **keywords)
    action.completion = completion
    return action
