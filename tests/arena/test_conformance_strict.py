# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Holds the format `strict/1` to the strict/1 rules of earlier tournaments.

Each case plays twice, through `play.Policies` of the strict/1 rules, `StrictPolicies`, and through
the contract's match host with `strict/1`. The forfeit must agree in its kind, its step and the
score at that moment, the kind being one of the contract's five with the rule of earlier
tournaments as its detail, and the pre-check must agree with the pre-flight of earlier tournaments
on whether a team passes and the rule it breaks. A match that plays must give the same actions.
"""

from pathlib import Path

import pytest

from cuju.arena._formats import contract
from cuju.arena._formats import host as _host
from cuju.arena._playing import play as _play
from cuju.arena._registry import checkpoints as _checkpoints
from cuju.arena._registry import teams as _teams
from cuju.arena._tournaments import format_check
from cuju.arena._tournaments import tournament as rules
from cuju.arena.formats.builtin import strict as _strict

_SUPPORT = Path(__file__).parents[1] / 'support' / 'strict'
_QUICK = {
    **rules.STRICT_DEFAULTS,
    **rules.STRICT_FLAGS,
    'call_seconds': 0.5,
    'first_call_seconds': 5.0,
}


def _team(name):
    return _teams.add_folder(_SUPPORT / name, name, '5v5')['id']


def _choice(limits):
    return {'id': 'strict/1', 'options': limits}


def _forfeit(left, right, limits, formats, steps=20):
    kept = {'strict': limits} if not formats else {'submission_format': _choice(limits)}
    with pytest.raises(_play.ForfeitError) as found:
        _play.play(left, right, '5_vs_5', seed=3, steps=steps, save=False, **kept)
    return found.value


def _detail(rule):
    """Returns the kind and detail that the contract records for a rule of earlier tournaments."""
    kind = _strict.KINDS[rule['kind']]
    return kind, rule['detail'] if rule['detail'] is not None else (
        rule['kind'] if rule['kind'] != kind else None
    )


# The rules that a side breaks, each with the limits that make it quick to break.
_BROKEN = [
    ('crashes', {}),
    ('sleeps', {}),
    ('invalid', {}),
    ('unloadable', {}),
    ('hungry', {'memory_mb': 300, 'call_seconds': 5.0}),
    ('spawner', {}),
    ('threaded', {}),
    ('slow', {'match_seconds': 1.0}),
    ('slow', {'mean_call_ms': 1.0, 'match_seconds': 60}),
]


@pytest.mark.parametrize(('name', 'tweak'), _BROKEN, ids=lambda value: str(value)[:24])
def test_each_rule_is_broken_alike_in_play(arena_home, name, tweak):
    team = _team(name)
    limits = {**_QUICK, **tweak}
    steps = 8 if 'mean_call_ms' in tweak else 20
    if tweak.get('match_seconds') == 1.0:
        steps = 600
    old = _forfeit(team, 'builtin', limits, False, steps).rule
    new = _forfeit(team, 'builtin', limits, True, steps).rule
    assert new['submission_format'] == 'strict/1'
    assert (new['kind'], new['detail']) == _detail(old)
    assert new['side'] == old['side'] == 'home'
    assert new['at'] == old['at']
    # A rule of the guard or a clock may break a step or two apart, and the others on the step.
    if old['kind'] in ('threads', 'total_time', 'memory', 'process'):
        assert abs(new['step'] - old['step']) <= 8
    else:
        assert new['step'] == old['step']
    # The count of calls that a side answered before it broke the rule.
    assert new['kind'] in rules.FORFEIT_KINDS


def test_a_side_on_the_right_and_two_sides_together_forfeit_alike(arena_home):
    team = _team('crashes')
    for left, right in (('builtin', team), (team, team)):
        old = _forfeit(left, right, _QUICK, False).rule
        new = _forfeit(left, right, _QUICK, True).rule
        assert (new['side'], new['step'], new['at']) == (old['side'], old['step'], old['at'])
        assert (new['kind'], new['detail']) == _detail(old)


def test_a_team_that_plays_gives_the_same_actions_and_counts(arena_home):
    team = _team('plays')
    found = []
    for kept in ({'strict': _QUICK}, {'submission_format': _choice(_QUICK)}):
        replay, stats = {}, {}
        _play.play(
            team,
            'builtin',
            '5_vs_5',
            seed=3,
            steps=30,
            save=False,
            replay=replay,
            stats=stats,
            **kept,
        )
        side = stats['sides'][0]
        found.append((replay['actions'], stats['steps'], side['calls'], side['actions']))
    assert found[0] == found[1]
    assert found[0][2] == 30 and found[0][3] == {'5': 30}


def test_two_sides_under_strict_1_think_at_once_and_answer_as_in_turn(arena_home, monkeypatch):
    left = _team('plays')
    right = _teams.add_folder(_SUPPORT / 'plays', 'plays-again', '5v5')['id']
    events = []
    send, receive = _strict.Player.send, _strict.Player.receive

    def logged_send(player, step):
        events.append(('send', player._team, step.used))
        return send(player, step)

    def logged_receive(player):
        events.append(('receive', player._team, player._pending is not None))
        return receive(player)

    monkeypatch.setattr(_strict.Player, 'send', logged_send)
    monkeypatch.setattr(_strict.Player, 'receive', logged_receive)
    found = []
    for overlap in (True, False):
        if not overlap:
            # In turn, the host calls `act`, which sends and receives for one side at a time.
            monkeypatch.setattr(_host.MatchHost, 'concurrent', property(lambda host: False))
        events.clear()
        replay = {}
        _play.play(
            left,
            right,
            '5_vs_5',
            seed=3,
            steps=12,
            save=False,
            replay=replay,
            submission_format=_choice(_QUICK),
        )
        thinking = [event for event in events if event[2]]
        found.append((replay['actions'], thinking))
    (together, overlapped), (in_turn, alone) = found
    assert together == in_turn
    # Together, both sides receive their step before either answer is read, so they overlap.
    pairs = [overlapped[at : at + 4] for at in range(0, len(overlapped), 4)]
    assert all(
        [kind for kind, _, _ in pair] == ['send', 'send', 'receive', 'receive'] for pair in pairs
    )
    assert len(overlapped) == 4 * 12
    # In turn, each side's answer is read before the other side is sent its step.
    assert [kind for kind, _, _ in alone[:4]] == ['send', 'receive', 'send', 'receive']


def test_a_submission_with_a_link_is_refused_at_load_under_both(arena_home, tmp_path):
    import os

    team = _team('plays')
    folder = _teams._home.folder('teams') / team
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'weights.bin').write_bytes(bytes(10))
    try:
        os.symlink(outside / 'weights.bin', folder / 'weights.bin')
    except OSError:
        pytest.skip('this system makes no links')
    old = _forfeit(team, 'builtin', _QUICK, False).rule
    new = _forfeit(team, 'builtin', _QUICK, True).rule
    assert (old['kind'], old['detail']) == ('load', 'link')
    assert (new['kind'], new['detail']) == ('load', 'link')


# The pre-check against the pre-flight of earlier tournaments.


@pytest.mark.parametrize(
    ('name', 'tweak'),
    [
        ('plays', {}),
        ('unloadable', {}),
        ('crashes', {}),
        ('invalid', {}),
        ('plays', {'folder_mb': 1.0}),
        ('wanders', {}),
        ('wanders', {'deterministic': True}),
    ],
    ids=lambda value: str(value)[:24],
)
def test_the_pre_check_agrees_with_the_pre_flight(arena_home, name, tweak):
    team = _team(name)
    limits = {**_QUICK, **tweak}
    if tweak.get('folder_mb'):
        (_teams._home.folder('teams') / team / 'weights.bin').write_bytes(bytes(1_200_000))
    old = _strict.preflight(team, '5_vs_5', limits)
    new = format_check.check(team, '5_vs_5', _choice(limits))
    assert new['ok'] == old['ok']
    old_ids = sorted(w['id'] for w in old['warnings'])
    if old['rule'] is None:
        assert new['rule'] is None
        # The same steps from the same seed gave the same answers, or a warning said they did not.
        assert new['deterministic'] == ('server.strict.warning_deterministic' not in old_ids)
    else:
        assert (new['rule']['kind'], new['rule']['detail']) == _detail(old['rule'])
    # The warnings that the pre-flight gave, a note that answers differ and one action that makes
    # up nearly all of them, are the warnings of the verdict.
    kinds = {
        'server.strict.warning_action': 'action',
        'server.strict.warning_deterministic': 'rule',
    }
    new_kinds = sorted(item['kind'] for item in new['problems'] if item['level'] == 'warning')
    if old['ok']:
        assert new_kinds == sorted(kinds[found] for found in old_ids if found in kinds)
    assert new['digest'] == _checkpoints.resolve(team)['checkpoint']


def test_the_options_of_strict_1_are_the_limits_and_switches_of_earlier_tournaments():
    spec = contract.read(Path(_strict.__file__).parent, True)
    assert spec.version == '1' and spec.process.host == 'match'
    assert spec.forfeit_score is None and spec.evidence
    resolved = contract.options(spec)
    assert resolved == {**rules.STRICT_DEFAULTS, **rules.STRICT_FLAGS}
    # The identity of the format under limits is the one that the migration of old rows gives.
    from cuju.arena._formats import identity, migrate, registry

    found = registry.get('strict/1')
    limits = {**rules.STRICT_DEFAULTS, **rules.STRICT_FLAGS, 'call_seconds': 2.0, 'threads': 2}
    made = identity.of(found, contract.options(spec, limits))
    assert identity.model_suffix(made, None) == identity.model_suffix(
        migrate.strict_identity(limits), None
    )
