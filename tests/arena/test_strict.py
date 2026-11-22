# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks strict/1 rules, the random entrant, the pre-flight, more legs and the ratings of results.

The small submissions of `support/strict` each break one rule, or play, or note what their
process sees, so the forfeits, the isolation of each team and the limits read from real matches.
"""

import csv
import io
import json
import math
import threading
import time
from pathlib import Path

import pytest

import cuju.errors
from cuju.arena._playing import play as _play
from cuju.arena._registry import teams as _teams
from cuju.arena._tournaments import results as _results
from cuju.arena._tournaments import tournament as rules
from cuju.arena._tournaments import tournament_runner as runner
from cuju.arena.formats.builtin import strict as _strict

_SUPPORT = Path(__file__).parents[1] / 'support' / 'strict'
# Limits of a call short enough for a quick test, the rest the defaults.
_QUICK = {**rules.STRICT_DEFAULTS, 'call_seconds': 0.5, 'first_call_seconds': 5.0}
# The limits and switches of strict/1 rules as a tournament resolves them by default.
_STRICT = {**rules.STRICT_DEFAULTS, **rules.STRICT_FLAGS}
_BUILTIN = {'id': 'builtin', 'kind': 'builtin', 'name': 'Built-in AI'}
_STRICT_FORMAT = Path(_strict.__file__).parent


def _team(name):
    return _teams.add_folder(_SUPPORT / name, name, '5v5')['id']


def _forfeit(left, right='builtin', limits=None, steps=20):
    with pytest.raises(_play.ForfeitError) as found:
        _play.play(left, right, '5_vs_5', seed=3, strict=limits or _QUICK, steps=steps, save=False)
    return found.value


# The rules that a side breaks, each with its reason and the score at that moment.


@pytest.mark.parametrize(
    ('name', 'kind', 'message', 'detail'),
    [
        ('crashes', 'crash', 'server.strict.crash', None),
        ('sleeps', 'timeout', 'server.strict.call_time', None),
        ('invalid', 'invalid', 'server.strict.invalid_range', 'range'),
        ('unloadable', 'load', 'server.strict.load', None),
        ('hungry', 'memory', 'server.strict.memory_out', None),
        ('spawner', 'process', 'server.strict.process', None),
        ('threaded', 'threads', 'server.strict.threads', None),
        ('slow', 'total_time', 'server.strict.total_time', None),
    ],
)
def test_each_broken_rule_ends_the_match_with_its_reason(arena_home, name, kind, message, detail):
    team = _team(name)
    # A test's own limits of memory and of the time of a match, low enough to break quickly.
    limits = dict(_QUICK)
    # A low memory limit breaks quickly for the hungry team, and stays away from the others, since
    # on Linux each new thread reserves stack memory and would fail to start under it.
    if kind == 'memory':
        limits['memory_mb'] = 300
        # Where the system does not stop the allocation, the watch ends the team within a second.
        limits['call_seconds'] = 5.0
    if kind == 'total_time':
        limits['match_seconds'] = 0.02
    forfeit = _forfeit(team, limits=limits)
    rule = forfeit.rule
    # Where the system stops the allocation, the team runs out of memory, and where only the watch
    # sees it, as on macOS, the watch names the memory it found.
    if kind == 'memory' and rule['reason_id'] == 'server.strict.memory':
        message = 'server.strict.memory'
    assert (rule['side'], rule['kind'], rule['reason_id'], rule['detail']) == (
        'home',
        kind,
        message,
        detail,
    )
    assert rule['reason_values']['player'] == name
    # The load fails before the first step, and each of the others in the third call, or for a
    # limit that the guard watches, soon after it.
    if kind == 'load':
        assert rule['step'] == 0
    elif kind in ('threads', 'total_time'):
        assert 0 <= rule['step'] <= 8
    else:
        assert rule['step'] == 2
    assert rule['at'] == [0, 0]
    assert forfeit.stats['cut_short'] is True


def test_an_answer_of_another_type_or_count_is_invalid(arena_home, tmp_path):
    for source, detail in (('return [5.0]', 'type'), ('return [5, 6]', 'count')):
        folder = tmp_path / detail
        folder.mkdir()
        (folder / 'main.py').write_text(f'def agent(obs):\n    {source}\n', encoding='utf-8')
        team = _teams.add_folder(folder, detail, '5v5')['id']
        rule = _forfeit(team).rule
        assert (rule['kind'], rule['detail']) == ('invalid', detail)
        assert rule['reason_id'] == f'server.strict.invalid_{detail}'


def test_a_side_on_the_right_breaks_its_rule_as_the_away_side(arena_home):
    rule = _forfeit('builtin', _team('crashes')).rule
    assert rule['side'] == 'away' and rule['kind'] == 'crash'


def test_the_mean_of_a_call_after_the_first_is_limited_in_a_match(arena_home):
    team = _team('slow')
    forfeit = _forfeit(team, limits={**_QUICK, 'mean_call_ms': 1.0, 'match_seconds': 60}, steps=8)
    assert forfeit.rule['kind'] == 'mean' and forfeit.rule['reason_id'] == 'server.strict.mean'
    side = forfeit.stats['sides'][0]
    assert side['calls'] == 8 and side['mean_after_first_ms'] >= 4
    # A mean below the limit plays on, and the stats say how the match ended.
    stats = {}
    _play.play(
        team,
        'builtin',
        '5_vs_5',
        seed=3,
        # A mean far above the sleep of the team, so a slow runner does not break it.
        strict={**_QUICK, 'mean_call_ms': 400, 'match_seconds': 60},
        stats=stats,
        steps=8,
        save=False,
    )
    assert stats['cut_short'] is True and stats['steps'] == 8
    assert stats['sides'][0]['actions'] == {'5': 8}


def test_two_teams_keep_their_modules_threads_folders_and_inputs_apart(
    arena_home, tmp_path, monkeypatch
):
    monkeypatch.setenv('CUJU_TEST_SEEN', str(tmp_path))
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '0')
    left, right = _team('twin_left'), _team('twin_right')
    stats = {}
    path = _play.play(left, right, '5_vs_5', seed=3, strict=_QUICK, stats=stats, length=50)
    assert path is not None and stats['cut_short'] is False
    # Each helper module of the same name answered its own team's action.
    assert stats['sides'][0]['actions'] == {'7': 51} and stats['sides'][1]['actions'] == {'3': 51}
    assert stats['sides'][0]['memory_mb'] > 0
    seen = [
        json.loads((tmp_path / f'{action}.json').read_text(encoding='utf-8')) for action in (7, 3)
    ]
    for found in seen:
        # Each team played from a copy of its folder inside its own private working folder,
        # both removed once the match ended.
        # The paths are resolved, since macOS spells a temporary folder as /var and /private/var.
        copy = Path(found['helper']).parent.resolve()
        assert Path(found['path']).resolve() == copy and copy.name == 'team'
        assert copy.parent == Path(found['folder']).resolve() and copy.parent.name.startswith(
            'cuju-policy-'
        )
        assert not copy.parent.exists()
        assert found['threads'] == {'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
        assert not found['renderer'] and found['stdin'] == '' and found['pythonpath'] == []
        # No GPU shows unless the rules allow one.
        assert found['gpu'] == ''
    assert seen[0]['folder'] != seen[1]['folder']
    assert seen[0]['torch'] == 1
    _play.play(
        left, 'builtin', '5_vs_5', seed=3, strict={**_QUICK, 'gpu': True}, steps=2, save=False
    )
    assert json.loads((tmp_path / '7.json').read_text(encoding='utf-8'))['gpu'] == '0'
    # What the team printed went to its log.
    assert 'a print of the team' in _strict.log_path(left).read_text(encoding='utf-8')


def test_a_team_imports_no_module_of_the_renderer_by_a_bare_name(arena_home, tmp_path):
    from cuju.arena.formats.builtin.strict import policy_worker

    # Modules beside the script of a team's process, which a bare name would find were the folder
    # of that script on the import path.
    for name in ('policy_worker', 'strict'):
        folder = tmp_path / name
        folder.mkdir()
        source = f'import {name}\n\n\ndef agent(obs):\n    return [5]\n'
        (folder / 'main.py').write_text(source, encoding='utf-8')
        rule = _forfeit(_teams.add_folder(folder, name, '5v5')['id']).rule
        assert (rule['kind'], rule['reason_id']) == ('load', 'server.strict.load')
        assert f"No module named '{name}'" in rule['reason_values']['reason']
    # The process takes the folder off the path itself, whatever the version of Python leaves.
    own = str(Path(policy_worker.__file__).parent)
    other = str(tmp_path)
    assert policy_worker._without_own_folder([own, other, own + '/', other]) == [other, other]


def test_the_clock_of_loading_starts_once_the_copies_of_both_folders_are_made(
    arena_home, monkeypatch
):
    left, right = _team('plays'), _team('plays')
    copy = _strict.frozen_copy

    def slow_copy(team, target):
        # A large folder, which takes longer to copy than the team has to load.
        time.sleep(5.0)
        return copy(team, target)

    monkeypatch.setattr(_strict, 'frozen_copy', slow_copy)
    stats = {}
    limits = {**_QUICK, 'load_seconds': 4.0}
    _play.play(left, right, '5_vs_5', seed=3, strict=limits, stats=stats, steps=2, save=False)
    # Each side's load counts from its own process's start to its answer, and neither its own copy
    # nor the copy of the other side's folder, made after its process started, counts in it.
    loads = [side['load_seconds'] for side in stats['sides']]
    assert all(0 < load < 4.0 for load in loads), loads


class _Answers(_strict.Worker):
    """A team's process that answers [5], read after a pause, stamped as arriving some time after
    its request, with the compute time it reports."""

    def __init__(self, arrives, pause=0.0, compute=None):
        self.calls, self.total, self.translate = 0, 0.0, False
        self.arrives, self.pause, self.compute = arrives, pause, compute

    def send(self, request):
        self.sent = time.perf_counter()

    def receive(self, timeout):
        time.sleep(self.pause)
        self.arrived = self.sent + self.arrives
        return {'answer': [5], 'compute': self.compute}


def _decide(left, right):
    policies = object.__new__(_strict.StrictPolicies)
    policies.limits = {**_QUICK, 'first_call_seconds': 1.0, 'call_seconds': 1.0}
    policies.batch = type('Batch', (), {'action_count': 19})()
    policies.players = [
        {'side': side, 'name': name, 'runner': runner, 'stats': _play.CallStats()}
        for side, (name, runner) in enumerate((('left', left), ('right', right)))
    ]
    part = (None, None, {'players_raw': [{}]})
    return policies.decide([part, part])


def test_each_answer_counts_to_its_own_arrival_whatever_side_is_read_first():
    # The left side takes most of its second, and the right side answered at once without a
    # compute time of its own, so its call counts from its sending to its arrival alone.
    left, right = _Answers(0.6, pause=0.6, compute=0.6), _Answers(0.05)
    assert _decide(left, right) == [[5], [5]]
    assert right.total == pytest.approx(0.05, abs=0.03)
    assert left.total == pytest.approx(0.6, abs=0.01)
    # An answer that arrived after its limit is late, though the match read it at once.
    with pytest.raises(_play.RuleError) as found:
        _decide(_Answers(0.01), _Answers(1.2))
    assert [(side, kind) for side, kind, *_ in found.value.breaks] == [(1, 'timeout')]


def _link(path, target, folder=False):
    try:
        path.symlink_to(target, target_is_directory=folder)
    except OSError as error:
        pytest.skip(f'this system makes no links here, {error}')


def test_a_submission_that_holds_a_link_is_refused_in_a_match_and_in_its_check(
    arena_home, tmp_path
):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'weights.bin').write_bytes(bytes(1000))
    team = _team('plays')
    folder = _teams._home.folder('teams') / team
    # A link in the renderer's own folder of the team stays out of the copy, so it counts for
    # nothing.
    _link(folder / _teams.TEAM_FOLDER / 'kit', outside, folder=True)
    assert _strict.links(team) == []
    assert _strict.preflight(team, '5_vs_5', _STRICT)['ok']
    # A link to a folder and a link to a file inside the submission are both found.
    _link(folder / 'models', outside, folder=True)
    _link(folder / 'weights.bin', outside / 'weights.bin')
    assert _strict.links(team) == ['models', 'weights.bin']
    with pytest.raises(_strict.LinkError):
        _strict.frozen_copy(team, tmp_path / 'copy')
    # A match refuses the side before its process starts, as a load it broke, whichever side it
    # plays, and both sides together lose when both hold a link.
    for sides, side in (((team, 'builtin'), 'home'), (('builtin', team), 'away')):
        rule = _forfeit(*sides).rule
        assert (rule['side'], rule['kind'], rule['detail']) == (side, 'load', 'link')
        assert rule['reason_id'] == 'server.strict.link'
        assert rule['reason_values']['path'] == 'models'
    assert _forfeit(team, team).rule['side'] == 'both'
    # The check of a team, which the card, Review and the command line run, refuses it alike, and
    # so does a match with folders that are not frozen.
    report = _strict.preflight(team, '5_vs_5', _STRICT)
    assert not report['ok'] and report['rule']['detail'] == 'link'
    assert report['rule']['reason_id'] == 'server.strict.link'
    rule = _forfeit(team, limits={**_QUICK, 'frozen': False}).rule
    assert rule['detail'] == 'link'
    # The tournament's file keeps the detail of the refusal.
    kept = rules.check_result({'score': [0, 3], 'forfeit': True, 'rule': rule})
    assert kept['rule']['detail'] == 'link'


# The random entrant.


def test_the_random_entrant_plays_the_same_actions_from_the_same_seed(arena_home):
    def actions(seed):
        stats, replay = {}, {}
        _play.play(
            'random-actions',
            'builtin',
            '5_vs_5',
            seed=seed,
            stats=stats,
            steps=30,
            save=False,
            replay=replay,
        )
        return stats['sides'][0]['actions'], replay['actions']

    first, again, other = actions(4), actions(4), actions(5)
    assert first == again and first != other
    assert sum(first[0].values()) == 30 and all(0 <= int(a) < 19 for a in first[0])


def test_the_random_entrant_enters_with_a_key_that_no_team_holds():
    config = rules.check(
        {
            'name': 'R',
            'seed': 1,
            'entrants': [{'kind': 'random'}, {'kind': 'builtin'}],
            'stages': [{'format': 'league'}],
        },
        [_BUILTIN],
    )
    random_entrant = config['entrants'][0]
    assert random_entrant['key'] == 'random:uniform' and random_entrant['team'] == 'random-actions'
    assert random_entrant['name'] == 'Random' and random_entrant['name_id']
    assert not _teams._home.valid_identifier(random_entrant['key'])


# Matches that replay from their seed and actions.


def test_a_strict_match_replays_from_its_seed_and_actions(arena_home):
    team = _team('plays')
    replay, stats = {}, {}
    _play.play(
        team,
        'random-actions',
        '5_vs_5',
        seed=9,
        strict=_QUICK,
        stats=stats,
        length=60,
        replay=replay,
    )
    again = _strict.replay(replay)
    assert again['steps'] == stats['steps'] == len(replay['actions'])
    assert again == _strict.replay(json.loads(json.dumps(replay)))


# The check of a team before a tournament.


def test_the_check_of_a_team_plays_both_sides_and_warns(arena_home):
    team = _team('plays')
    report = _strict.preflight(team, '5_vs_5', _STRICT)
    assert report['ok'] and set(report['sides']) == {'left', 'right'}
    for side in report['sides'].values():
        assert side['ok'] and side['timings']['calls'] == _strict.PREFLIGHT_STEPS
        assert side['timings']['load_seconds'] is not None
    # One action makes up every action, which a warning names, and so does a setting of the
    # team's file that the renderer does not know.
    assert [w['id'] for w in report['warnings']] == ['server.strict.warning_action']
    path = _teams._home.folder('teams') / team / _teams.TEAM_FOLDER / 'team.json'
    saved = json.loads(path.read_text(encoding='utf-8'))
    path.write_text(json.dumps({**saved, 'colour': 'red'}), encoding='utf-8')
    report = _strict.preflight(team, '5_vs_5', _STRICT)
    assert report['warnings'][0]['values'] == {'setting': 'colour', 'file': 'team.json'}


def test_the_check_of_a_team_fails_on_load_and_on_the_size_of_its_folder(arena_home):
    report = _strict.preflight(_team('unloadable'), '5_vs_5', _STRICT)
    assert not report['ok'] and report['rule']['kind'] == 'load'
    assert list(report['sides']) == ['left']
    team = _team('plays')
    (_teams._home.folder('teams') / team / 'weights.bin').write_bytes(bytes(1_200_000))
    report = _strict.preflight(team, '5_vs_5', {**_STRICT, 'folder_mb': 1.0})
    assert not report['ok'] and report['rule']['detail'] == 'size'
    assert report['rule']['reason_id'] == 'server.strict.folder_size'


def test_the_size_of_a_submission_counts_in_mebibytes_without_the_renderers_folder(arena_home):
    team = _team('plays')
    folder = _teams._home.folder('teams') / team
    alone = _strict.folder_bytes(folder)
    # The renderer's folder of the team with its kept checkpoints, and the caches of Python, stay
    # out of the size, as they stay out of the digest.
    kept = folder / _teams.TEAM_FOLDER / 'checkpoints'
    kept.mkdir(parents=True, exist_ok=True)
    (kept / 'step-1000.bin').write_bytes(bytes(3 * _strict.MEBIBYTE))
    (folder / '__pycache__').mkdir(exist_ok=True)
    (folder / '__pycache__' / 'main.cpython-312.pyc').write_bytes(bytes(_strict.MEBIBYTE))
    assert _strict.folder_bytes(folder) == alone
    limits = {**_STRICT, 'folder_mb': 1.0}
    # More than a million bytes and less than a mebibyte keeps to a limit of 1.
    (folder / 'weights.bin').write_bytes(bytes(_strict.MEBIBYTE - alone - 1))
    report = _strict.preflight(team, '5_vs_5', limits)
    assert report['ok'] and _strict.folder_bytes(folder) > 1_000_000
    assert report['folder_mb'] == pytest.approx(1.0, abs=0.001)
    (folder / 'weights.bin').write_bytes(bytes(_strict.MEBIBYTE - alone + 1))
    report = _strict.preflight(team, '5_vs_5', limits)
    assert not report['ok'] and report['rule']['detail'] == 'size'


def test_the_command_line_checks_a_team_with_an_exit_code_and_json(arena_home, capsys):
    from cuju.arena.__main__ import main

    good, bad = _team('plays'), _team('unloadable')
    assert main(['tournament', 'preflight', good, '--format', 'strict/1']) == 0
    out = capsys.readouterr().out
    assert out.startswith('pass  plays') and 'format strict/1' in out
    assert main(['tournament', 'preflight', bad, '--format', 'strict/1', '--json']) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['ok'] is False and report['rule']['kind'] == 'load'
    # A tournament that names no format checks a team as Kaggle open does, that it loads.
    assert main(['tournament', 'preflight', good]) == 0
    assert 'format kaggle/1' in capsys.readouterr().out


# Strict tournaments in the runner, with made up matches and checks.


def _checker(failed=()):
    calls = []

    def check(team, scenario, limits):
        calls.append((team, scenario, limits))
        if team in failed:
            rule = {
                'side': 'home',
                'kind': 'load',
                'reason': f'{team} failed to load, on purpose',
                'reason_id': None,
                'reason_values': None,
                'step': 0,
                'at': [0, 0],
            }
            return {'team': team, 'name': team, 'ok': False, 'rule': rule, 'timings': {}}
        return {'team': team, 'name': team, 'ok': True, 'rule': None, 'timings': {'calls': 11}}

    check.calls = calls
    return check


def _strict_player(broken=None):
    """Plays made up matches, the entrants of `broken` breaking the rule of a timeout at 1 to 0."""

    def player(match, teams, provenance, progress):
        for side, team in zip(('home', 'away'), teams, strict=True):
            if team in (broken or {}):
                rule = {
                    'side': side,
                    'kind': 'timeout',
                    'reason': 'late on purpose',
                    'reason_id': None,
                    'reason_values': None,
                    'step': 40,
                    'at': [1, 0],
                }
                return {
                    'score': [0, 0],
                    'rule': rule,
                    'forfeit': True,
                    'match': None,
                    'stats': {'steps': 40, 'cut_short': True, 'sides': [None, None]},
                }
        seed = match['seed']
        stats = {
            'steps': 50,
            'cut_short': False,
            'sides': [
                {'calls': 51, 'mean_ms': 1.0, 'first_ms': 3.0, 'max_ms': 3.0, 'actions': {'5': 51}},
                None,
            ],
        }
        return {
            'score': [seed % 3, seed // 3 % 3],
            'cards': [[0, 0], [0, 0]],
            'match': None,
            'stats': stats,
        }

    return player


def _fake_teams(monkeypatch, names):
    """Makes registry teams of the given names that a configuration may enter."""
    found = [_BUILTIN] + [
        {'id': name, 'kind': 'kaggle', 'name': name.title(), 'format': '5v5', 'style': {}}
        for name in names
    ]
    monkeypatch.setattr(runner._teams, 'teams', lambda: found)
    monkeypatch.setattr(runner, 'fits', lambda entrant, scenario, entries=None: None)


def _strict_config(teams, **options):
    return {
        'name': 'Strict cup',
        'seed': 5,
        'entrants': [{'kind': 'team', 'team': team} for team in teams],
        'strict': True,
        'preflight': True,
        'include': [{'kind': 'random'}, {'kind': 'builtin'}],
        'defaults': {'scenario': '5_vs_5', 'length': 50},
        'stages': [{'format': 'league', 'name': 'Round robin', 'legs': 2}],
        **options,
    }


def test_a_strict_tournament_scores_a_broken_rule_as_a_forfeit_and_goes_on(arena_home, monkeypatch):
    _fake_teams(monkeypatch, ['reds', 'slows'])
    cups = runner.Tournaments(
        player=_strict_player({'slows'}), start=False, interval=0.01, checker=_checker()
    )
    entry = cups.create(_strict_config(['reds', 'slows']))
    assert cups.run(entry['id'], report=lambda line: None) == 'done'
    done = cups.get(entry['id'])
    cups.close()
    names = [e['name'] for e in done['config']['entrants']]
    assert names == ['Reds', 'Slows', 'Random', 'Built-in AI']
    slow = names.index('Slows')
    broken = [m for m in done['matches'] if slow in (m['home'], m['away'])]
    assert len(broken) == 6
    for match in broken:
        result = match['result']
        assert result['forfeit'] and result['rule']['at'] == [1, 0]
        # The side that broke the rule loses by the tournament's forfeit score, 0 to 3.
        expected = [0, 3] if match['home'] == slow else [3, 0]
        assert result['score'] == expected and result['match'] is None
    table = done['stages_view'][0]['tables'][0]['rows']
    assert next(r for r in table if r['entrant'] == slow)['lost'] == 6


def test_the_preflight_leaves_out_teams_that_fail_and_draws_again(arena_home, monkeypatch):
    _fake_teams(monkeypatch, ['reds', 'blues', 'broken'])
    checker = _checker({'broken'})
    cups = runner.Tournaments(player=_strict_player(), start=False, interval=0.01, checker=checker)
    entry = cups.create(_strict_config(['reds', 'broken', 'blues']))
    assert entry['preflight'] == 'waiting'
    assert cups.run(entry['id'], report=lambda line: None) == 'done'
    done = cups.get(entry['id'])
    cups.close()
    # Each team is checked once, under the tournament's limits, on the first stage's scenario.
    assert sorted(team for team, _, _ in checker.calls) == ['blues', 'broken', 'reds']
    assert all(s == '5_vs_5' and lim['id'] == 'strict/1' for _, s, lim in checker.calls)
    assert all(lim['options'] == _STRICT for _, _, lim in checker.calls)
    assert [e['name'] for e in done['config']['entrants']] == [
        'Reds',
        'Blues',
        'Random',
        'Built-in AI',
    ]
    assert done['preflight'] == 'done' and done['excluded_count'] == 1
    assert done['excluded'][0]['name'] == 'Broken' and 'on purpose' in done['excluded'][0]['reason']
    assert [c['ok'] for c in done['checks']] == [True, False, True]
    assert done['played'] == 4 * 3 // 2 * 2
    files = _results.files(done)
    assert json.loads(files['excluded.json'])[0]['team'] == 'broken'


def test_a_preflight_that_leaves_too_few_entrants_stops_with_the_reason(arena_home, monkeypatch):
    _fake_teams(monkeypatch, ['reds', 'broken'])
    cups = runner.Tournaments(
        player=_strict_player(), start=False, interval=0.01, checker=_checker({'broken'})
    )
    config = _strict_config(['reds', 'broken'], include=[])
    entry = cups.create(config)
    assert cups.run(entry['id'], report=lambda line: None) == 'stopped'
    stopped = cups.get(entry['id'])
    cups.close()
    assert stopped['error_id'] == 'server.preflight.left_out'


def test_matches_at_once_count_the_threads_of_strict_processes(arena_home, monkeypatch):
    _fake_teams(monkeypatch, ['reds', 'blues', 'greens', 'golds'])
    gate = threading.Event()
    active, peak = [0], [0]
    lock = threading.Lock()

    def player(match, teams, provenance, progress):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        gate.wait(5)
        with lock:
            active[0] -= 1
        return {'score': [1, 0], 'cards': [[0, 0], [0, 0]], 'match': None}

    # A match of two teams takes its own thread and one for each team's process, 3 of the 6.
    monkeypatch.setattr(runner, '_threads', lambda: 6)
    cups = runner.Tournaments(player=player, start=False, interval=0.01, checker=_checker())
    config = _strict_config(
        ['reds', 'blues', 'greens', 'golds'], include=[], concurrency=8, preflight=False
    )
    entry = cups.create(config)
    cups.tick()
    time.sleep(0.3)
    assert peak[0] == 2
    gate.set()
    assert cups.run(entry['id'], report=lambda line: None) == 'done'
    cups.close()
    record = {
        'config': rules.check(
            config,
            [_BUILTIN]
            + [
                {'id': t, 'kind': 'kaggle', 'name': t, 'format': '5v5'}
                for t in ('reds', 'blues', 'greens', 'golds')
            ],
        )
    }
    match = {'home': 0, 'away': 1}
    assert runner.Tournaments._cost(record, match) == 3
    record['config']['submission_format'] = None
    assert runner.Tournaments._cost(record, match) == 1


def test_a_real_strict_tournament_plays_forfeits_and_replays(arena_home):
    good, crash = _team('plays'), _team('crashes')
    cups = runner.Tournaments(start=False, interval=0.05)
    config = {
        'name': 'Real strict',
        'seed': 2,
        'strict': _QUICK,
        'entrants': [
            {'kind': 'team', 'team': good},
            {'kind': 'team', 'team': crash},
            {'kind': 'random'},
        ],
        'stages': [{'format': 'league', 'scenario': '5_vs_5', 'length': 50}],
    }
    entry = cups.create(config)
    assert cups.run(entry['id'], report=lambda line: None) == 'done'
    done = cups.get(entry['id'])
    names = [e['name'] for e in done['config']['entrants']]
    for match in done['matches']:
        result = match['result']
        sides = [names[match['home']], names[match['away']]]
        if 'crashes' in sides:
            assert result['forfeit'] and result['rule']['kind'] == 'crash'
            assert result['rule']['submission_format'] == 'strict/1'
        else:
            assert not result['forfeit'] and result['match']
            assert result['stats']['steps'] >= 50 and result['stats']['cut_short'] is False
        # Every strict match keeps its seed and actions, which replay it.
        found = runner.replay_match(entry['id'], match['key'])
        assert found['same'], found
    found = cups.analysis(entry['id'])
    cups.close()
    assert found['cut_short'] == 2 and found['played'] == 3


# The ratings, the matrix and the files.


def test_ratings_follow_a_case_worked_by_hand():
    # Without the prior, one entrant winning three of four is three times as strong, 400 times
    # the logarithm of 3 apart, about 190.85 points, either side of 1000.
    apart = 400 * math.log10(3)
    found = _results.bradley_terry(2, [(0, 1, 1.0)] * 3 + [(0, 1, 0.0)], prior=0)
    assert found[0] == pytest.approx(1000 + apart / 2) and found[1] == pytest.approx(
        1000 - apart / 2
    )
    # With the prior of one virtual draw, a single win gives a strength x of the winner and 1 / x
    # of the loser, where x squared over x squared plus 1, plus x over x plus 1, is 1.5.
    winner, loser = _results.bradley_terry(2, [(0, 1, 1.0)])
    x = 10 ** ((winner - 1000) / 400)
    assert x * x / (x * x + 1) + x / (x + 1) == pytest.approx(1.5)
    assert winner + loser == pytest.approx(2000) and winner == pytest.approx(1131.38, abs=0.01)
    # Three entrants in a cycle, and draws alone, stand level, and so does no match at all.
    assert _results.bradley_terry(3, [(0, 1, 1.0), (1, 2, 1.0), (2, 0, 1.0)]) == pytest.approx(
        [1000] * 3
    )
    assert _results.bradley_terry(2, [(0, 1, 0.5)] * 4) == pytest.approx([1000, 1000])
    assert _results.bradley_terry(3, []) == pytest.approx([1000] * 3)


def _match(home, away, score, **result):
    return {
        'key': f'{home}-{away}-{score}',
        'stage': 0,
        'home': home,
        'away': away,
        'seed': 1,
        'decider': False,
        'result': {'score': score, **result},
    }


def test_intervals_come_from_a_bootstrap_seeded_from_the_tournament():
    matches = [_match(0, 1, [2, 0])] * 3 + [_match(1, 2, [1, 1]), _match(2, 0, [0, 1])]
    first = _results.ratings(3, matches, seed=7)
    assert first == _results.ratings(3, matches, seed=7)
    assert first != _results.ratings(3, matches, seed=8)
    assert [row['entrant'] for row in first] == [0, 2, 1]
    for row in first:
        assert row['low'] <= row['rating'] <= row['high']
    assert {row['entrant']: row['matches'] for row in first} == {0: 4, 1: 4, 2: 2}
    method = _results.method(7)
    assert method['prior']['virtual_draws'] == 1.0 and method['bootstrap']['resamples'] == 200


def test_the_matrix_holds_the_mean_goal_difference_of_every_pair():
    matches = [_match(0, 1, [2, 0]), _match(1, 0, [1, 2]), _match(1, 2, [3, 3])]
    matrix = _results.goal_matrix(3, matches)
    assert matrix['mean'][0][1] == 1.5 and matrix['mean'][1][0] == -1.5
    assert matrix['mean'][1][2] == 0 and matrix['mean'][0][2] is None
    assert matrix['matches'] == [[0, 2, 0], [2, 0, 1], [0, 1, 0]]


def _rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def test_the_files_of_results_have_their_columns(arena_home):
    config = rules.check(
        {
            'name': 'F',
            'seed': 3,
            'entrants': [
                {'kind': 'builtin', 'ability': 0.5},
                {'kind': 'random'},
                {'kind': 'builtin', 'ability': 0.5, 'name': 'Twin'},
            ],
            'stages': [{'format': 'league'}],
        },
        [_BUILTIN],
    )
    view = rules.evaluate(config, {})
    rule = {'side': 'away', 'kind': 'crash', 'reason': 'broke', 'step': 7, 'at': [1, 0]}
    results = {
        view['matches'][0]['key']: {'score': [3, 0], 'forfeit': True, 'rule': rule},
        view['matches'][1]['key']: {'score': [1, 1]},
        view['matches'][2]['key']: {'score': [0, 2]},
    }
    view = rules.evaluate(config, {k: rules.check_result(v) for k, v in results.items()})
    entry = {
        'config': config,
        'matches': view['matches'],
        'ranking': view['ranking'],
        'excluded': [{'name': 'Gone', 'team': 't-x', 'reason': 'no load'}],
    }
    files = _results.files(entry)
    assert list(files) == list(_results.FILES)
    standings = _rows(files['standings.csv'])
    assert list(standings[0]) == list(_results.STANDINGS_COLUMNS)
    assert sum(int(row['forfeits']) for row in standings) == 1
    assert sum(int(row['cut_short']) for row in standings) == 2
    assert list(_rows(files['ratings.csv'])[0]) == list(_results.RATINGS_COLUMNS)
    pairs = _rows(files['pairwise_gd.csv'])
    assert list(pairs[0]) == list(_results.PAIRWISE_COLUMNS) and len(pairs) == 6
    # Two entrants of the built-in AI at one ability share a key, and their ids differ.
    ids = [row['id'] for row in json.loads(files['entrants.json'])]
    assert ids == ['builtin:0.50', 'random:uniform', 'builtin:0.50#2']
    counts = list(csv.reader(io.StringIO(files['pairwise_counts.csv'])))
    assert counts[0] == ['id', *ids] and [row[0] for row in counts[1:]] == ids
    assert all(sum(map(int, row[1:])) == 2 for row in counts[1:])
    lines = [json.loads(line) for line in files['matches.jsonl'].splitlines()]
    assert len(lines) == 3 and lines[0]['rule']['score'] == [1, 0] and lines[0]['cut_short']
    assert set(lines[0]) >= {'key', 'seed', 'score', 'steps', 'timings', 'actions', 'forfeit'}
    method = json.loads(files['ratings_method.json'])
    assert method['bootstrap']['tournament_seed'] == 3 and method['cut_short'] == 1
    assert json.loads(files['excluded.json'])[0]['name'] == 'Gone'


# More legs later.


def test_sides_alternate_leg_by_leg_and_more_legs_play_only_the_missing_matches(arena_home):
    cups = runner.Tournaments(player=_strict_player(), start=False, interval=0.01)
    config = {
        'name': 'Legs',
        'seed': 4,
        'entrants': [{'kind': 'builtin', 'ability': a} for a in (0.1, 0.5, 0.9)],
        'stages': [{'format': 'league', 'legs': 3}],
    }
    entry = cups.create(config)
    assert cups.run(entry['id'], report=lambda line: None) == 'done'
    before = {
        m['key']: (m['seed'], m['home'], m['away'], m['result']['score'])
        for m in cups.get(entry['id'])['matches']
    }
    pairs = {}
    for _, home, away, _ in before.values():
        pairs.setdefault(frozenset((home, away)), []).append(home)
    for homes in pairs.values():
        # Three legs of a pair, the venue in turn, so one side is at home twice.
        assert len(homes) == 3 and homes[0] != homes[1] and homes[0] == homes[2]
    after = cups.add_legs(entry['id'], 2)
    assert after['state'] == 'running' and after['expected'] == 15
    assert cups.run(entry['id'], report=lambda line: None) == 'done'
    done = cups.get(entry['id'])
    for match in done['matches']:
        if match['key'] in before:
            kept = before[match['key']]
            assert (match['seed'], match['home'], match['away']) == kept[:3]
            assert match['result']['score'] == kept[3]
    for pair, homes in pairs.items():
        added = [
            m['home']
            for m in done['matches']
            if m['key'] not in before and frozenset((m['home'], m['away'])) == pair
        ]
        # The venue goes on in turn from the last leg.
        assert len(added) == 2 and added[0] != homes[-1] and added[0] != added[1]
    with pytest.raises(cuju.errors.ArgumentError):
        cups.add_legs(entry['id'], 100)
    cups.close()


def test_a_knockout_takes_no_more_legs():
    config = rules.check(
        {
            'name': 'K',
            'seed': 1,
            'entrants': [{'kind': 'builtin', 'ability': a} for a in (0.1, 0.2)],
            'stages': [{'format': 'knockout'}],
        },
        [_BUILTIN],
    )
    with pytest.raises(rules.TournamentError, match='league or groups'):
        rules.add_legs(config, 1)


# Old files, the preset and the settings.


def test_old_files_without_the_new_fields_load_and_resume(arena_home):
    cups = runner.Tournaments(player=_strict_player(), start=False, interval=0.01)
    entry = cups.create(
        {
            'name': 'Old',
            'seed': 2,
            'entrants': [{'kind': 'builtin', 'ability': a} for a in (0.1, 0.2, 0.3)],
            'stages': [{'format': 'league'}],
        }
    )
    cups.close()
    path = runner.folder() / f'{entry["id"]}.json'
    stored = json.loads(path.read_text(encoding='utf-8'))
    stored.pop('seal')
    for key in ('preflight', 'excluded'):
        stored.pop(key)
    for key in ('submission_format', 'strict', 'preflight', 'include', 'given_entrants'):
        stored['config'].pop(key, None)
    runner._write_record(path, stored)
    again = runner.Tournaments(player=_strict_player(), start=False, interval=0.01)
    assert again.get(entry['id'])['state'] == 'running'
    assert again.run(entry['id'], report=lambda line: None) == 'done'
    done = again.get(entry['id'])
    assert done['submission_format'] is None and done['preflight'] is None
    assert again.analysis(entry['id'])['played'] == 3
    again.close()


def test_strict_settings_combine_in_any_tournament():
    # A league of ten legs with strict/1 rules, a pre-flight and the floor of the random entrant
    # and the built-in AI, as any tournament may set them.
    config = rules.check(
        {
            'name': 'S',
            'seed': 1,
            'strict': True,
            'preflight': True,
            'include': [{'kind': 'random'}, {'kind': 'builtin'}],
            'entrants': [{'kind': 'builtin', 'ability': 0.5}],
            'defaults': {'scenario': '5_vs_5', 'length': 3000},
            'stages': [{'format': 'league', 'legs': 10}],
        },
        [_BUILTIN],
    )
    # The rules resolve the shape alone, and the registry resolves every option of the format.
    assert config['submission_format'] == {'id': 'strict/1', 'options': {}}
    assert config['preflight'] is True
    assert [e['kind'] for e in config['entrants']] == ['builtin', 'random', 'builtin']
    assert len(rules.evaluate(config, {})['matches']) == 3 * 10
    assert 'strict_round_robin' not in rules.PRESETS
    assert rules.check_format(None) is None and rules.check_format(None, False) is None
    assert rules.check_format(None, True) == {'id': 'strict/1', 'options': {}}
    assert rules.check_format(None, {'call_seconds': 2}) == {
        'id': 'strict/1',
        'options': {'call_seconds': 2},
    }
    assert rules.check_format('kaggle') == {'id': 'kaggle'}
    assert rules.check_format({'id': 'strict/1', 'options': {'gpu': True}, 'preset': None}) == {
        'id': 'strict/1',
        'options': {'gpu': True},
    }
    for wrong in ('fast', {'speed': 1}, {'id': 3}, {'id': 'kaggle', 'options': 3}):
        with pytest.raises(rules.TournamentError):
            rules.check_format(wrong if isinstance(wrong, dict) else None, wrong)
    # A configuration names a format or the limits of earlier versions, never both.
    with pytest.raises(rules.TournamentError):
        rules.check_format('strict/1', True)
    # The registry resolves the options of the format, so each limit keeps its range.
    for wrong in (
        {'call_seconds': 0},
        {'threads': 0},
        {'speed': 1},
        {'memory_mb': 10},
        {'match_seconds': 0},
        {'frozen': 'yes'},
    ):
        with pytest.raises(cuju.errors.ArgumentError):
            runner.check_config(
                {
                    'seed': 1,
                    'strict': wrong,
                    'entrants': [{'kind': 'builtin', 'ability': 0.1}, {'kind': 'random'}],
                    'stages': [{'format': 'league', 'scenario': '5_vs_5'}],
                }
            )
    saved = rules.check_preset(
        {'title': 'Mine', 'submission_format': 'strict/1', 'stages': [{'format': 'league'}]}
    )
    assert saved['submission_format'] == 'strict/1'


def test_the_limits_of_the_tournament_tables_are_the_options_of_strict_1():
    from cuju.arena._formats import contract

    spec = contract.read(_STRICT_FORMAT, True)
    assert set(spec.options) == set(rules.STRICT_DEFAULTS) | set(rules.STRICT_FLAGS)
    for key, default in {**rules.STRICT_DEFAULTS, **rules.STRICT_FLAGS}.items():
        option = spec.options[key]
        assert option.default == default, key
        if key in rules.STRICT_RANGES:
            assert option.range == rules.STRICT_RANGES[key], key


def test_a_file_of_the_removed_strict_round_robin_preset_loads_and_resumes(arena_home):
    cups = runner.Tournaments(player=_strict_player(), start=False, interval=0.01)
    entry = cups.create(
        {
            'name': 'Old strict',
            'seed': 2,
            'strict': True,
            'entrants': [{'kind': 'builtin', 'ability': a} for a in (0.1, 0.2)],
            'include': [{'kind': 'random'}],
            'stages': [{'format': 'league', 'name': 'Round robin', 'legs': 2}],
        }
    )
    cups.close()
    path = runner.folder() / f'{entry["id"]}.json'
    stored = json.loads(path.read_text(encoding='utf-8'))
    stored.pop('seal')
    # A file of before names the preset that made it, and its settings stand in the file.
    stored['config']['preset'] = 'strict_round_robin'
    runner._write_record(path, stored)
    again = runner.Tournaments(player=_strict_player(), start=False, interval=0.01)
    assert again.get(entry['id'])['state'] == 'running'
    assert again.run(entry['id'], report=lambda line: None) == 'done'
    done = again.get(entry['id'])
    again.close()
    assert done['config']['preset'] == 'strict_round_robin' and done['played'] == 6


# Frozen folders, the determinism of a team and its changed files.


def test_a_team_plays_from_a_read_only_copy_and_learns_nothing_between_matches(
    arena_home, tmp_path, monkeypatch
):
    monkeypatch.setenv('CUJU_TEST_SEEN', str(tmp_path))
    team = _team('writer')
    for _ in range(2):
        stats = {}
        _play.play(
            team, 'builtin', '5_vs_5', seed=3, strict=_QUICK, stats=stats, steps=3, save=False
        )
        # The count of loads that it wrote stayed in its copy, so each match finds none.
        assert stats['sides'][0]['actions'] == {'5': 3}
        seen = json.loads((tmp_path / 'writer.json').read_text(encoding='utf-8'))
        assert seen == {'changed': False, 'loads': 0}
    folder = _teams._home.folder('teams') / team
    assert not (folder / 'count.txt').exists()
    # Without a frozen folder the team writes in its own folder and learns.
    _play.play(
        team, 'builtin', '5_vs_5', seed=3, strict={**_QUICK, 'frozen': False}, steps=1, save=False
    )
    assert (folder / 'count.txt').exists()


def test_the_digest_of_a_folder_follows_its_files(arena_home):
    team = _team('plays')
    folder = _teams._home.folder('teams') / team
    first = _strict.folder_digest(team)
    assert len(first) == 64 and _strict.folder_digest(team) == first
    # The renderer's own folder of the team and the caches of Python leave it as it is.
    (folder / '__pycache__').mkdir()
    (folder / '__pycache__' / 'main.cpython-312.pyc').write_bytes(b'x')
    (folder / _teams.TEAM_FOLDER / 'note.txt').write_text('kit', encoding='utf-8')
    assert _strict.folder_digest(team) == first
    (folder / 'weights.txt').write_text('1', encoding='utf-8')
    second = _strict.folder_digest(team)
    assert second != first
    (folder / 'weights.txt').write_text('2', encoding='utf-8')
    assert _strict.folder_digest(team) not in (first, second)
    assert _strict.folder_digest('t-none') is None


def test_strict_rules_read_an_earlier_checkpoint_from_its_own_folder(arena_home, tmp_path):
    from cuju.arena._registry import checkpoints as _checkpoints

    team = _team('plays')
    first = _checkpoints.current_id(team)
    before = _strict.folder_digest(team)
    _checkpoints.add(team, 'main.py', b'def agent(obs):\n    return [4]\n')
    entrant = f'{team}@{first}'
    folder = _checkpoints.resolve(entrant)['folder']
    assert _strict.submission_folder(entrant) == folder
    assert _strict.submission_folder(team) == _teams._home.folder('teams') / team
    # The size, the digest, the links and the copy of the entrant are those of its own files.
    assert _strict.folder_digest(entrant) == before != _strict.folder_digest(team)
    assert _strict.links(entrant) == []
    copy = _strict.frozen_copy(entrant, tmp_path / 'copy')
    assert (copy / 'main.py').read_bytes() == (folder / 'main.py').read_bytes()
    assert b'[4]' not in (copy / 'main.py').read_bytes()
    found = _strict.preflight(entrant, '5_vs_5')
    assert found['ok'], found['rule']
    assert found['digest'] == before


def test_a_team_whose_files_change_is_refused_at_its_next_match(arena_home):
    team = _team('plays')
    folder = _teams._home.folder('teams') / team
    played, changed = [], []

    def player(match, teams, provenance, progress):
        played.append(match['key'])
        if team in teams and not changed:
            changed.append(match['key'])
            # The team's author changes its policy while the tournament plays.
            with (folder / 'main.py').open('a', encoding='utf-8') as stream:
                stream.write('# a change\n')
        return {'score': [1, 0], 'cards': [[0, 0], [0, 0]], 'match': None}

    cups = runner.Tournaments(player=player, start=False, interval=0.01)
    entry = cups.create(
        {
            'name': 'Frozen',
            'seed': 3,
            'strict': True,
            'entrants': [
                {'kind': 'team', 'team': team},
                {'kind': 'builtin', 'ability': 0.2},
                {'kind': 'builtin', 'ability': 0.6},
            ],
            'stages': [{'format': 'league', 'scenario': '5_vs_5'}],
        }
    )
    assert cups.run(entry['id'], report=lambda line: None) == 'done'
    done = cups.get(entry['id'])
    cups.close()
    ours = [
        m
        for m in done['matches']
        if done['config']['entrants'][m['home']]['team'] == team
        or done['config']['entrants'][m['away']]['team'] == team
    ]
    rules_broken = [m['result'].get('rule') for m in ours]
    kinds = [rule and (rule['kind'], rule['detail']) for rule in rules_broken]
    # The first match read the digest, and the change refused the team at the next one.
    assert sorted(kinds, key=str) == sorted([None, ('rule', 'changed')], key=str)
    changed = next(rule for rule in rules_broken if rule)
    assert changed['reason_id'] == 'server.strict.changed' and changed['step'] == 0
    stored = json.loads((runner.folder() / f'{entry["id"]}.json').read_text(encoding='utf-8'))
    assert stored['digest_scheme'] == 2
    # The other two matches played, and the refused one never reached the player.
    assert len(played) == 2


def test_the_preflight_checks_determinism(arena_home):
    team = _team('wanders')
    report = _strict.preflight(team, '5_vs_5', _STRICT)
    assert report['ok']
    assert 'server.strict.warning_deterministic' in [w['id'] for w in report['warnings']]
    refused = _strict.preflight(team, '5_vs_5', {**_STRICT, 'deterministic': True})
    assert not refused['ok'] and refused['rule']['kind'] == 'deterministic'
    steady = _strict.preflight(_team('plays'), '5_vs_5', {**_STRICT, 'deterministic': True})
    assert steady['ok'] and len(steady['digest']) == 64


def test_the_support_submissions_stay_where_the_tests_find_them():
    names = sorted(path.name for path in _SUPPORT.iterdir() if path.is_dir())
    assert names == sorted(
        [
            'crashes',
            'hungry',
            'invalid',
            'plays',
            'sleeps',
            'slow',
            'spawner',
            'threaded',
            'twin_left',
            'twin_right',
            'unloadable',
            'wanders',
            'writer',
        ]
    )
