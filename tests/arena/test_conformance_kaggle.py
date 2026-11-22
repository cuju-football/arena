# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Holds the format `kaggle/1` under Kaggle open to the play of every team before formats.

Each case plays twice, through `play.Policies` with the loading of a team that cuju-render always
used, and through the contract's match host with `kaggle/1` and its preset `open`. The two must
agree on the actions of every step, the calls each agent got, the state of the engine after every
step, the score and the calls and actions that the statistics count. Timed Kaggle, which is new,
has its own cases at the end.
"""

import hashlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import cuju.arena
from cuju import BatchEnvironment
from cuju.arena._format import recording as _recording
from cuju.arena._playing import play as _play
from cuju.arena._registry import teams as _teams
from support.common import _control, _toy_team

_SUPPORT = Path(__file__).parents[1] / 'support' / 'strict'
_SAMPLE = Path(cuju.arena.__file__).parent / 'samples' / 'teams' / 'starter'
_OPEN = {'id': 'kaggle/1', 'preset': 'open'}
# The steps of a cut match, and the seeds of every case.
_STEPS = 400
_SEEDS = (0, 1, 2)

# An agent that logs every call it gets and answers from what it saw, so a different observation
# gives a different action. `ACTIONS` is the size of the set it answers in.
_RECORDER = """
import hashlib, json, os

ACTIONS = {actions}


def agent(obs):
    seen = json.dumps(obs['players_raw'], sort_keys=True)
    digest = hashlib.md5(seen.encode()).hexdigest()
    players = len(obs['players_raw'])
    with open(os.environ['CUJU_ARENA_TEST_CALLS'], 'a', encoding='utf-8') as stream:
        line = [obs['step'], obs['remainingOverageTime'], players, digest]
        stream.write(json.dumps(line) + '\\n')
    answer = [int(hashlib.md5((digest + str(i)).encode()).hexdigest(), 16) % ACTIONS
              for i in range(players)]
    return answer
"""

# The cases, each a team and the scenario it plays. `academy` plays a drill of the benchmark, where
# the team's extra players are out.
_CASES = {
    'one_5v5': ('5v5', _control(1), '5_vs_5', 19),
    'three_5v5': ('5v5', _control(3), '5_vs_5', 19),
    'v2_5v5': ('5v5', _control(1, action_set='v2'), '5_vs_5', 20),
    'one_11v11': ('11v11', _control(1), '11_vs_11_kaggle', 19),
    'fixed_11v11': (
        '11v11',
        _control(2, 'fixed', [1, 2]),
        'benchmark_full_game_11_vs_11_easy',
        19,
    ),
    'academy': ('5v5', _control(4), 'academy_empty_goal_close', 19),
}


def _team(tmp_path, name):
    size, control, _, actions = _CASES[name]
    return _toy_team(tmp_path, name, _RECORDER.format(actions=actions), size, control)


def _setup(team, scenario, seed, formats):
    """Returns the batch and the policies of a match of the team against the built-in AI."""
    sides = [_teams.side_entry(team), _teams.side_entry('builtin')]
    fit = None
    if scenario.startswith('academy_'):
        fit = _teams.task_fit(sides[0], scenario)
        agents = [fit['agents'], 0]
    else:
        _teams.check_scenario(sides, scenario)
        agents = [_teams.agent_count(found, scenario, side) for side, found in enumerate(sides)]
    control = _teams.check_control(sides[0].get('control'), sides[0]['format'])
    extended = control['assignment'] == 'fixed' or control['action_set'] == 'v2'
    asset = f'{scenario}_l{agents[0]}_r{agents[1]}'
    if scenario in _teams.renderer_scenarios():
        asset = str(_teams.scenario_path(scenario))
    batch = BatchEnvironment(
        asset,
        agent_counts=[tuple(agents)],
        environment_count=1,
        representation='raw',
        seed=seed,
        auto_reset=False,
        device='cpu',
        action_set='v2' if extended else 'default',
    )
    batch.reset()
    policies = _play.Policies(
        batch,
        sides,
        scenario,
        agents,
        extended,
        [0, agents[0]],
        seed=seed,
        formats=None if formats is None else _play.chosen_formats(formats),
        kind='match',
        length=3000,
        fits=[fit, None],
    )
    return batch, policies


def _through(tmp_path, monkeypatch, team, scenario, seed, steps, formats, tag):
    """Plays the match and returns what the two paths must share."""
    log = tmp_path / f'calls-{tag}.jsonl'
    monkeypatch.setenv('CUJU_ARENA_TEST_CALLS', str(log))
    batch, policies = _setup(team, scenario, seed, formats)
    rows, state = [], hashlib.sha256()
    try:
        for _ in range(steps):
            parts = policies.observe()
            answers = policies.decide(parts)
            actions = policies.actions(parts, answers)
            rows.append(actions[0].tolist())
            _, _, done, _ = batch.step(actions)
            state.update(batch.get_state(0))
            if bool(done[0]):
                break
        policies.finish()
        score = batch.raw()['score'][0, 0].tolist()
        stats = [
            None if side is None else {'calls': side['calls'], 'actions': side['actions']}
            for side in policies.stats()
        ]
    finally:
        policies.close()
        batch.close()
    calls = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
    return {
        'actions': rows,
        'calls': calls,
        'state': state.hexdigest(),
        'score': score,
        'stats': stats,
    }


def _same(tmp_path, monkeypatch, team, scenario, seed, steps=_STEPS):
    before = _through(tmp_path, monkeypatch, team, scenario, seed, steps, None, 'before')
    under = _through(tmp_path, monkeypatch, team, scenario, seed, steps, [_OPEN, None], 'kaggle')
    assert before['calls'], 'the agent was never called'
    assert under['actions'] == before['actions']
    assert under['calls'] == before['calls']
    assert under['state'] == before['state']
    assert under['score'] == before['score']
    assert under['stats'] == before['stats']
    return before


@pytest.mark.parametrize('seed', _SEEDS)
@pytest.mark.parametrize('name', sorted(_CASES))
def test_kaggle_open_plays_each_case_as_the_renderer_always_did(
    arena_home, tmp_path, monkeypatch, name, seed
):
    team = _team(tmp_path, name)['id']
    found = _same(tmp_path, monkeypatch, team, _CASES[name][2], seed)
    if name == 'v2_5v5':
        # The v2 team answers 20 actions, one of which hands the player to the built-in AI.
        assert any(19 in row for row in found['actions'])
    if name == 'fixed_11v11':
        assert [call[0] for call in found['calls']] == list(range(len(found['calls'])))
    if name == 'academy':
        # The team controls 4 players and sees one observation for each, those out included.
        assert {call[2] for call in found['calls']} == {4}


def test_kaggle_open_plays_a_whole_match_of_a_fixed_assignment_as_the_renderer_always_did(
    arena_home, tmp_path, monkeypatch
):
    team = _team(tmp_path, 'fixed_11v11')['id']
    found = _same(tmp_path, monkeypatch, team, _CASES['fixed_11v11'][2], 0, steps=3000)
    # GRF hands the players over at a set piece, where the agent is called for fewer steps than
    # played, and Kaggle's step counts the calls alone either way.
    assert len(found['calls']) <= len(found['actions'])
    assert [call[0] for call in found['calls']] == list(range(len(found['calls'])))


@pytest.mark.parametrize('seed', _SEEDS)
@pytest.mark.parametrize('size', ['5v5', '11v11'])
def test_kaggle_open_plays_the_sample_team_as_the_renderer_always_did(
    arena_home, tmp_path, monkeypatch, size, seed
):
    team = _teams.add_folder(_SAMPLE, 'starter', size)['id']
    scenario = '5_vs_5' if size == '5v5' else '11_vs_11_kaggle'
    monkeypatch.setenv('CUJU_ARENA_TEST_CALLS', str(tmp_path / 'unused'))
    before = _through_plain(tmp_path, monkeypatch, team, scenario, seed, None)
    under = _through_plain(tmp_path, monkeypatch, team, scenario, seed, [_OPEN, None])
    assert under == before


def _through_plain(tmp_path, monkeypatch, team, scenario, seed, formats):
    """Like `_through` for a team that logs nothing, the calls counted by the statistics."""
    batch, policies = _setup(team, scenario, seed, formats)
    rows, state = [], hashlib.sha256()
    try:
        for _ in range(_STEPS):
            parts = policies.observe()
            actions = policies.actions(parts, policies.decide(parts))
            rows.append(actions[0].tolist())
            batch.step(actions)
            state.update(batch.get_state(0))
        stats = [None if s is None else (s['calls'], s['actions']) for s in policies.stats()]
        score = batch.raw()['score'][0, 0].tolist()
    finally:
        policies.close()
        batch.close()
    return rows, state.hexdigest(), score, stats


@pytest.mark.parametrize('submission', ['plays'])
def test_kaggle_open_plays_the_test_submissions_as_the_renderer_always_did(
    arena_home, tmp_path, monkeypatch, submission
):
    monkeypatch.setenv('CUJU_TEST_SEEN', str(tmp_path))
    team = _teams.add_folder(_SUPPORT / submission, submission, '5v5')['id']
    for seed in _SEEDS:
        before = _through_plain(tmp_path, monkeypatch, team, '5_vs_5', seed, None)
        under = _through_plain(tmp_path, monkeypatch, team, '5_vs_5', seed, [_OPEN, None])
        assert under == before


@pytest.mark.parametrize('name', ['one_5v5', 'three_5v5', 'v2_5v5'])
def test_kaggle_open_plays_a_whole_match_and_saves_the_same_recording(
    arena_home, tmp_path, monkeypatch, name
):
    team = _team(tmp_path, name)['id']
    found = {}
    for tag, formats in (('before', None), ('kaggle', [_OPEN, None])):
        monkeypatch.setenv('CUJU_ARENA_TEST_CALLS', str(tmp_path / f'full-{tag}.jsonl'))
        replay, stats = {}, {}
        path = _play.play(
            team,
            'builtin',
            '5_vs_5',
            seed=5,
            save=True,
            replay=replay,
            stats=stats,
            submission_format=formats,
        )
        frames = hashlib.sha256(b''.join(_recording.frames(path))).hexdigest()
        calls = (tmp_path / f'full-{tag}.jsonl').read_text(encoding='utf-8')
        side = stats['sides'][0]
        found[tag] = (
            replay['actions'],
            frames,
            path.summary['score'],
            calls,
            (side['calls'], side['actions']),
        )
    assert found['kaggle'] == found['before']
    assert len(found['before'][0]) >= 3000


def test_the_open_preset_and_its_identity_are_the_ones_of_a_side_that_chose_nothing():
    from cuju.arena._formats import identity, registry, side

    choice = side.choose(_OPEN)
    assert choice.format.label == 'kaggle/1' and choice.preset == 'open'
    assert choice.playing['timing'] is False and choice.playing['players'] == 'control'
    assert identity.is_default(choice.identity())
    # A choice of Kaggle itself turns its timing on, with Kaggle's own 0.5 s and 60 s.
    chosen = side.choose('kaggle')
    assert chosen.playing['timing'] is True
    assert (chosen.playing['step_seconds'], chosen.playing['overage_seconds']) == (0.5, 60.0)
    assert not identity.is_default(chosen.identity())
    assert registry.get('kaggle').builtin


# Timed Kaggle, which runs the agent in a child process and measures it from outside.

_SLOW = """
import json, os, time

def agent(obs):
    with open(os.environ['CUJU_ARENA_TEST_CALLS'], 'a', encoding='utf-8') as stream:
        stream.write(json.dumps([obs['step'], obs['remainingOverageTime']]) + '\\n')
    time.sleep(float(os.environ.get('CUJU_ARENA_TEST_SLEEP', '0')))
    return [5]
"""


def _timed(tmp_path, monkeypatch, sleep, options, steps=6):
    monkeypatch.setenv('CUJU_ARENA_TEST_CALLS', str(tmp_path / 'timed.jsonl'))
    monkeypatch.setenv('CUJU_ARENA_TEST_SLEEP', str(sleep))
    team = _toy_team(tmp_path, 'slow', _SLOW, '5v5')['id']
    stats = {}
    formats = [{'id': 'kaggle/1', 'options': {'players': 'control', **options}}, None]
    _play.play(
        team,
        'builtin',
        '5_vs_5',
        seed=1,
        steps=steps,
        save=False,
        stats=stats,
        submission_format=formats,
    )
    calls = (tmp_path / 'timed.jsonl').read_text(encoding='utf-8').splitlines()
    return [json.loads(line) for line in calls], stats


def test_timed_kaggle_plays_in_a_child_and_gives_the_whole_bank_to_a_quick_agent(
    arena_home, tmp_path, monkeypatch
):
    calls, stats = _timed(tmp_path, monkeypatch, 0.0, {'timing': True})
    assert [call[0] for call in calls] == list(range(len(calls))) and len(calls) == 6
    assert {call[1] for call in calls} == {60}
    assert stats['sides'][0]['calls'] == 6


@pytest.mark.parametrize(
    'macos_sampler',
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(sys.platform != 'linux', reason='exercise macOS ps on Linux'),
        ),
    ],
    ids=['native', 'macos-ps'],
)
def test_timed_kaggle_draws_the_excess_of_each_step_from_the_bank(
    arena_home, tmp_path, monkeypatch, capfd, macos_sampler
):
    from cuju.arena._formats import launch, tree
    from cuju.arena.formats.v1 import Child

    sampled = set()
    if macos_sampler:
        monkeypatch.setattr(
            tree, 'sys', SimpleNamespace(platform='darwin', executable=sys.executable)
        )
        sample = tree._ps_sample

        def record_sample(pid, table):
            sampled.add(pid)
            return sample(pid, table)

        monkeypatch.setattr(tree, '_ps_sample', record_sample)

    agent_sleep, dispatch_delay = 0.15, 0.08
    send, call = launch.Process.send, Child.call
    measured = []
    children = set()

    def delayed_send(process, kind, *args, **kwargs):
        if kind == 'call':
            time.sleep(dispatch_delay)
        return send(process, kind, *args, **kwargs)

    def record_call(child, *args, **kwargs):
        answer, elapsed = call(child, *args, **kwargs)
        measured.append(elapsed)
        children.add(child.pid)
        return answer, elapsed

    monkeypatch.setattr(launch.Process, 'send', delayed_send)
    monkeypatch.setattr(Child, 'call', record_call)
    options = {'timing': True, 'step_seconds': 0.05, 'overage_seconds': 5.0}
    calls, _ = _timed(tmp_path, monkeypatch, agent_sleep, options, steps=4)

    assert [step for step, _ in calls] == list(range(4))
    assert len(measured) == 4
    expected = options['overage_seconds']
    for (_, bank), elapsed in zip(calls, measured, strict=True):
        assert bank == pytest.approx(expected)
        assert elapsed >= agent_sleep + dispatch_delay
        expected -= elapsed - options['step_seconds']

    if macos_sampler:
        assert children and children <= sampled
        assert 'the submission started a process' not in capfd.readouterr().err


def test_timed_kaggle_forfeits_a_side_whose_bank_runs_out(arena_home, tmp_path, monkeypatch):
    options = {'timing': True, 'step_seconds': 0.05, 'overage_seconds': 0.25}
    with pytest.raises(_play.ForfeitError) as found:
        _timed(tmp_path, monkeypatch, 0.2, options, steps=20)
    rule = found.value.rule
    assert (rule['side'], rule['kind'], rule['detail']) == ('home', 'timeout', 'overage')
    assert rule['submission_format'] == 'kaggle/1'


def test_timed_kaggle_forfeits_an_agent_that_fails_to_load_in_time(
    arena_home, tmp_path, monkeypatch
):
    source = 'import time\ntime.sleep(5)\n\ndef agent(obs):\n    return [5]\n'
    team = _toy_team(tmp_path, 'late', source, '5v5')['id']
    options = {'timing': True, 'load_seconds': 1.0, 'players': 'control'}
    started = time.perf_counter()
    with pytest.raises(_play.ForfeitError) as found:
        _play.play(
            team,
            'builtin',
            '5_vs_5',
            seed=1,
            steps=3,
            save=False,
            submission_format=[{'id': 'kaggle/1', 'options': options}, None],
        )
    assert time.perf_counter() - started < 5
    assert (found.value.rule['kind'], found.value.rule['detail']) == ('load', 'timeout')


def test_an_invalid_answer_forfeits_under_kaggle_as_invalid_and_blames_no_format(
    arena_home, tmp_path
):
    team = _toy_team(tmp_path, 'wrong', 'def agent(obs):\n    return [19, 3]\n', '5v5')['id']
    for options in ({'timing': False}, {'timing': True}):
        with pytest.raises(_play.ForfeitError) as found:
            _play.play(
                team,
                'builtin',
                '5_vs_5',
                seed=1,
                steps=3,
                save=False,
                submission_format=[
                    {'id': 'kaggle/1', 'options': {'players': 'control', **options}},
                    None,
                ],
            )
        assert found.value.rule['kind'] == 'invalid'


def test_the_pre_check_refuses_a_missing_main_and_too_many_players(arena_home, tmp_path):
    from cuju.arena._tournaments import format_check

    three = _toy_team(tmp_path, 'three', 'def agent(obs):\n    return [5]\n', '5v5', _control(3))
    assert format_check.check(three['id'], '5_vs_5', _OPEN)['ok']
    refused = format_check.check(three['id'], '5_vs_5', 'kaggle')
    assert not refused['ok'] and refused['rule']['kind'] == 'rule'
    empty = tmp_path / 'empty'
    empty.mkdir()
    (empty / 'main.py').write_text('x = 1\n', encoding='utf-8')
    plain = _teams.add_folder(empty, 'empty', '5v5')['id']
    found = format_check.check(plain, '5_vs_5', _OPEN)
    assert not found['ok'] and found['rule']['kind'] == 'load'
