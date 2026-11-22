# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the summary that each team carries in `GET /api/teams`, which the grid of teams shows.

The server reads a team's rating, its last five results and its kept verdict under Kaggle open
from the stores, and never runs a check. The mock server gives its teams the same summary from
its made up stores, through `ci/mocks/teams.py`, and the two answers hold the same shapes.
"""

from cuju.arena._formats import verdicts as _verdicts
from cuju.arena._registry import checkpoints as _checkpoints
from cuju.arena._registry import teams as _teams
from cuju.arena._store import ratings as _store_ratings
from support.common import _SINGLE, _toy_team

SCENARIO = '11_vs_11_kaggle'
TEAM_KEYS = {'scenario', 'rating', 'form', 'check'}
BUILTIN_KEYS = {'scenario', 'rating', 'form', 'levels'}
RATING_KEYS = {'rating', 'low', 'high', 'matches', 'checkpoint'}
LEVEL_KEYS = {'key', 'difficulty', 'rating', 'low', 'high', 'matches', 'anchor'}
REFUSAL = {
    'kind': 'rule',
    'message': 'this format plays one player',
    'level': 'error',
    'detail': None,
    'environment': False,
}


def _shape(summary, keys):
    assert set(summary) == keys
    assert isinstance(summary['scenario'], str)
    assert isinstance(summary['form'], list)
    assert all(result in ('win', 'draw', 'loss') for result in summary['form'])
    assert len(summary['form']) <= 5
    if summary['rating'] is not None:
        assert set(summary['rating']) == RATING_KEYS


def _played(team, point, own, other, moment):
    _store_ratings.record(
        {
            'home': {'key': team, 'checkpoint': point},
            'away': {'key': 'builtin:0.60'},
            'score': [own, other],
            'source': 'match',
            'scenario': SCENARIO,
            'ref': f'listing/{moment}',
            'time': moment,
        }
    )


def test_a_team_carries_its_rating_its_last_five_and_its_kept_check(arena_home, tmp_path):
    team = _toy_team(tmp_path, 'listed', _SINGLE, '11v11')
    point = _checkpoints.current_id(team['id'])
    for moment, score in enumerate([(1, 0), (0, 0), (0, 2), (3, 1), (1, 1), (2, 0)]):
        _played(team['id'], point, *score, 1_000 + moment)
    found = {entry['id']: entry for entry in _teams.listing()}
    summary = found[team['id']]['summary']
    _shape(summary, TEAM_KEYS)
    assert summary['scenario'] == SCENARIO
    assert summary['rating']['matches'] == 6 and summary['rating']['checkpoint'] == point
    assert summary['form'] == ['draw', 'loss', 'win', 'draw', 'win']
    assert summary['check'] == 'not_checked'
    builtin = found[_teams.BUILTIN]['summary']
    _shape(builtin, BUILTIN_KEYS)
    assert all(set(level) == LEVEL_KEYS for level in builtin['levels'])
    assert sum(level['anchor'] for level in builtin['levels']) == 1


def test_the_listing_reads_the_kept_verdict_and_never_runs_a_check(
    arena_home, tmp_path, monkeypatch
):
    team = _toy_team(tmp_path, 'judged', _SINGLE, '11v11')
    subject = _verdicts.subject_of_team(team['id'])
    choice = _verdicts.choice_of({})
    outcome = _verdicts.Outcome(
        format='kaggle/1',
        key=_verdicts.key_of(subject, choice, None, False),
        options=dict(choice.values),
        preset='open',
        scenario=None,
        full=False,
        max_steps=None,
        checked='2026-11-18T12:00:00',
        verdict=_verdicts._v1.Verdict.from_dict({'problems': [REFUSAL]}),
        failed=None,
        cut=False,
        terminal=None,
        subject=subject,
    )
    _verdicts.store(team['id'], outcome)

    def refuse(*args, **kwargs):
        raise AssertionError('the listing ran a check')

    monkeypatch.setattr(_verdicts, 'check', refuse)
    monkeypatch.setattr(_verdicts, 'run', refuse)
    found = {entry['id']: entry for entry in _teams.listing('5_vs_5')}
    assert found[team['id']]['summary']['check'] == 'refused'
    assert found[team['id']]['summary']['scenario'] == '5_vs_5'
    assert found[team['id']]['summary']['rating'] is None
