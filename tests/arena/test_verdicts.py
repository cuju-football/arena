# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The verdicts a team keeps, their key, what is never kept, the cut check, and the routes."""

import dataclasses
import shutil
from pathlib import Path

import pytest

from cuju.arena._formats import checks as _checks
from cuju.arena._formats import gpu as _gpu
from cuju.arena._formats import registry as _registry
from cuju.arena._formats import side as _side
from cuju.arena._formats import verdicts as _verdicts
from cuju.arena._registry import teams as _teams
from cuju.arena.formats import v1 as formats
from support.common import _toy_team

NAMER = Path(__file__).parents[1] / 'support' / 'folders' / 'format'
AGENT = 'def agent(obs):\n    return [5 if obs["step"] % 3 else 13]\n'


@pytest.fixture
def namer(arena_home):
    """The Namer format, added to the home as a person's format, which checks in a process."""
    return _registry.add(NAMER, 'namer')


@pytest.fixture
def innamer(tmp_path, monkeypatch):
    """The Namer format as a built-in one, which checks in the process that asks."""
    root = tmp_path / 'builtin'
    shutil.copytree(NAMER, root / 'innamer')
    toml = root / 'innamer' / 'format.toml'
    text = toml.read_text(encoding='utf-8').replace('[process]', '[process]\nhost = "match"')
    toml.write_text(text, encoding='utf-8')
    monkeypatch.setattr(_registry, 'BUILTIN', root)
    return _registry.get('innamer')


@pytest.fixture
def team(arena_home, tmp_path):
    return _toy_team(tmp_path, 'kept', AGENT, '5v5')['id']


def _choice(identifier='kaggle/1', preset=None, **options):
    return _side.choose({'id': identifier, 'preset': preset, 'options': options})


def _counting(monkeypatch, verdicts):
    """Makes the pre-check answer each of `verdicts` in turn, and returns the calls it counts."""
    calls = []
    answers = iter(verdicts)

    def check(format, values, folder, found):
        calls.append(found)
        return next(answers)

    monkeypatch.setattr(_checks, 'check', check)
    return calls


def test_the_key_holds_every_part_of_the_check(team, namer, monkeypatch):
    monkeypatch.setattr(_gpu, 'devices', lambda *arguments, **options: [])
    subject = _verdicts.subject_of_team(team)
    base = _verdicts.key_of(subject, _choice('namer'), None, False)
    assert base == _verdicts.key_of(subject, _choice('namer'), None, False)
    # The scenario of the size is the one that no scenario names.
    assert base == _verdicts.key_of(subject, _choice('namer'), '5_vs_5', False)
    other = dataclasses.replace
    changed = {
        'an option that only the check reads': _verdicts.key_of(
            subject, _choice('namer', matches=9), None, False
        ),
        'an option that plays': _verdicts.key_of(subject, _choice('namer', tag='x'), None, False),
        'the scenario': _verdicts.key_of(subject, _choice('namer'), '5_vs_5_stochastic', False),
        'full': _verdicts.key_of(subject, _choice('namer'), None, True),
        'the control': _verdicts.key_of(
            other(subject, control={**subject.control, 'players': 2}), _choice('namer'), None, False
        ),
        'the checkpoint': _verdicts.key_of(
            other(subject, checkpoint='f' * 16), _choice('namer'), None, False
        ),
        'the format': _verdicts.key_of(subject, _choice('kaggle/1'), None, False),
    }
    monkeypatch.setattr(_verdicts, '__version__', '9.9.9')
    changed['the renderer'] = _verdicts.key_of(subject, _choice('namer'), None, False)
    monkeypatch.setattr(_gpu, 'devices', lambda *arguments, **options: ['0'])
    changed['the GPU'] = _verdicts.key_of(subject, _choice('namer'), None, False)
    assert len({base, *changed.values()}) == len(changed) + 1, changed


@pytest.mark.parametrize(
    ('verdict', 'cut', 'why'),
    [
        (formats.accept(), False, None),
        (formats.refuse('load', 'main.py is missing', detail='entry'), False, None),
        (formats.refuse('rule', 'two players, one allowed'), False, None),
        (
            formats.refuse('load', 'no torch', detail='environment', environment=True),
            False,
            'environment',
        ),
        (formats.refuse('crash', 'too much memory', detail='ceiling_memory'), False, 'ceiling'),
        (formats.refuse('timeout', 'the check took too long'), False, 'timing'),
        (formats.accept(), True, 'cut'),
        (None, False, 'environment'),
    ],
)
def test_what_is_never_kept_and_why(verdict, cut, why):
    assert _verdicts.unkept(verdict, cut) == why


def test_a_check_keeps_its_verdict_and_a_start_does_not_check_again(team, namer, monkeypatch):
    calls = []
    real = _checks.check
    monkeypatch.setattr(_checks, 'check', lambda *a, **k: calls.append(1) or real(*a, **k))
    subject = _verdicts.subject_of_team(team)
    choice = _choice('namer')
    outcome = _verdicts.check(subject, choice)
    assert outcome.verdict.accepted and outcome.unkept is None
    assert outcome.terminal == {'run': _verdicts.terminal_name(subject, choice), 'side': 'check'}
    kept = _verdicts.lookup(team, outcome.key)
    assert kept.verdict.accepted and kept.checked == outcome.checked
    again = _verdicts.ensure(subject, choice, isolate=False)
    assert again.stored and again.key == outcome.key and len(calls) == 1
    rows = _verdicts.listing(team)
    by_format = {(row['format'], row['preset']): row for row in rows['formats']}
    found = by_format[('namer', None)]
    assert (found['state'], found['kept'], found['accepted']) == ('accepted', True, True)
    assert found['problems'] == [] and found['terminal']['side'] == 'check'
    assert by_format[('kaggle/1', None)]['state'] == 'not_checked'
    assert by_format[('kaggle/1', 'open')]['state'] == 'not_checked'
    assert (rows['accepting'], rows['checked'], rows['total']) == (1, 1, len(rows['formats']))
    # Any change of the key reads as a format that was never checked.
    monkeypatch.setattr(_verdicts, '__version__', '9.9.9')
    assert _verdicts.listing(team)['accepting'] == 0


def test_a_refusal_of_the_machine_or_of_time_is_never_kept(team, monkeypatch):
    subject = _verdicts.subject_of_team(team)
    choice = _choice()
    calls = _counting(
        monkeypatch,
        [
            formats.accept(),
            formats.refuse('load', 'needs torch', detail='environment', environment=True),
            formats.refuse('timeout', 'the check took too long'),
            formats.refuse('crash', 'memory ceiling', detail='ceiling_memory'),
            formats.refuse('load', 'main.py is missing', detail='entry'),
        ],
    )
    first = _verdicts.check(subject, choice)
    assert _verdicts.lookup(team, first.key) is not None
    # A fresh check that is not kept drops the verdict kept before, so it never reads as current.
    for answer in ('environment', 'timing', 'ceiling'):
        found = _verdicts.check(subject, choice)
        assert found.unkept == answer and found.key == first.key
        assert _verdicts.lookup(team, first.key) is None
    assert len(calls) == 4
    # A refusal of the team is kept, and a start then reads it without checking again.
    refused = _verdicts.check(subject, choice)
    assert refused.verdict.accepted is False and refused.unkept is None
    assert _verdicts.ensure(subject, choice, isolate=False).stored and len(calls) == 5


def test_a_start_checks_again_where_nothing_was_kept(team, monkeypatch):
    subject = _verdicts.subject_of_team(team)
    calls = _counting(
        monkeypatch,
        [formats.refuse('timeout', 'slow'), formats.refuse('timeout', 'slow'), formats.accept()],
    )
    for _ in range(3):
        _verdicts.ensure(subject, _choice(), isolate=False)
    assert len(calls) == 3
    assert _verdicts.ensure(subject, _choice(), isolate=False).stored


def test_the_row_of_a_refusal_names_its_problems(team, monkeypatch):
    _counting(monkeypatch, [formats.refuse('rule', 'two players, one allowed', detail='players')])
    subject = _verdicts.subject_of_team(team)
    _verdicts.check(subject, _choice())
    row = {(r['format'], r['preset']): r for r in _verdicts.listing(team)['formats']}[
        ('kaggle/1', None)
    ]
    assert row['state'] == 'refused' and row['accepted'] is False and row['kept'] is True
    assert row['problems'] == [
        {
            'kind': 'rule',
            'message': 'two players, one allowed',
            'level': 'error',
            'detail': 'players',
            'environment': False,
        }
    ]


def test_a_check_that_the_steps_cut_refuses_and_is_never_kept(team, namer):
    subject = _verdicts.subject_of_team(team)
    choice = _choice('namer', matches=12)
    cut = _verdicts.check(subject, choice, max_steps=5)
    assert cut.cut and not cut.verdict.accepted and cut.unkept == 'cut'
    found = [problem for problem in cut.verdict.problems if problem.kind == 'cut']
    assert found and found[0].level == 'error' and '5 steps' in found[0].message
    assert _verdicts.lookup(team, cut.key) is None
    row = cut.row()
    assert (row['state'], row['kept'], row['unkept'], row['cut'], row['max_steps']) == (
        'refused',
        False,
        'cut',
        True,
        5,
    )
    # Steps that reach the end of every check match cut nothing.
    whole = _verdicts.check(subject, choice, max_steps=50)
    assert not whole.cut and whole.verdict.accepted and whole.unkept is None
    assert _verdicts.lookup(team, whole.key) is not None


def test_a_check_in_the_process_is_cut_too(team, innamer):
    subject = _verdicts.subject_of_team(team)
    choice = _choice('innamer', matches=12)
    cut = _verdicts.check(subject, choice, max_steps=3)
    assert cut.cut and not cut.verdict.accepted
    assert _verdicts.check(subject, choice, max_steps=12).verdict.accepted


def test_a_team_keeps_the_most_verdicts_that_the_setting_says(team, monkeypatch):
    monkeypatch.setattr(_verdicts, '_most', lambda: 2)
    subject = _verdicts.subject_of_team(team)
    for second, scenario in enumerate(('a', 'b', 'c')):
        outcome = _verdicts.Outcome(
            format='kaggle/1',
            key=f'key-{scenario}',
            options={},
            preset=None,
            scenario=scenario,
            full=False,
            max_steps=None,
            checked=f'2026-11-18T10:00:0{second}',
            verdict=formats.accept(),
            failed=None,
            cut=False,
            terminal=None,
            subject=subject,
        )
        _verdicts.store(team, outcome)
    assert [item['key'] for item in _verdicts._read(team)] == ['key-b', 'key-c']
    assert _verdicts.lookup(team, 'key-a') is None


def test_removing_a_team_drops_its_verdicts(team, monkeypatch, arena_home):
    _counting(monkeypatch, [formats.accept()])
    _verdicts.check(_verdicts.subject_of_team(team), _choice())
    path = arena_home / 'verdicts' / f'{team}.json'
    assert path.is_file()
    assert _teams.remove(team)
    assert not path.exists()


def test_the_listing_follows_the_checkpoint_that_plays(team, monkeypatch):
    subject = _verdicts.subject_of_team(team)
    _counting(monkeypatch, [formats.accept()])
    _verdicts.check(subject, _choice())
    first = _verdicts.listing(team)
    assert first['checkpoint'] == subject.checkpoint and first['accepting'] == 1
    from cuju.arena._registry import checkpoints as _checkpoints

    changed = _checkpoints.add(team, 'main.py', b'def agent(obs):\n    return [9]\n')
    assert _verdicts.listing(team)['checkpoint'] == changed['current']
    assert _verdicts.listing(team)['accepting'] == 0
    # The earlier checkpoint still shows what was kept for it.
    earlier = _verdicts.listing(team, checkpoint=subject.checkpoint)
    assert earlier['checkpoint'] == subject.checkpoint and earlier['accepting'] == 1


def test_a_new_team_is_checked_under_kaggle_open_after_its_upload(team, monkeypatch):
    seen = []

    def check(subject, choice, *arguments, **options):
        seen.append((subject.team, choice.format.label, choice.preset, options))
        raise RuntimeError('a failure of the check leaves the team as it is')

    monkeypatch.setattr(_verdicts, 'check', check)
    _verdicts.at_upload(team).join(30)
    assert seen == [(team, 'kaggle/1', 'open', {'isolate': True})]


def test_concurrent_isolated_checks_use_distinct_terminals(team, namer, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    subject = _verdicts.subject_of_team(team)
    choice = _choice('namer')
    ready = Barrier(2)
    real = _verdicts._isolated

    def isolated(*args):
        ready.wait(timeout=10)
        return real(*args)

    monkeypatch.setattr(_verdicts, '_isolated', isolated)
    with ThreadPoolExecutor(2) as pool:
        checks = [
            pool.submit(_verdicts.check, subject, choice, scenario, isolate=True, keep=False)
            for scenario in ('academy_corner', 'academy_empty_goal_close')
        ]
        first, second = [check.result(timeout=30) for check in checks]
    assert first.verdict.accepted and second.verdict.accepted
    assert first.terminal['run'] != second.terminal['run']
    assert first.terminal['side'] == second.terminal['side'] == 'check'
