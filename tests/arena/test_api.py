# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the public names of `cuju.arena`."""

import gzip
import json
import os
import re
import struct
from pathlib import Path

import numpy as np
import pytest
import torch

import cuju
import cuju.errors
import cuju.state
from conftest import actions_at
from support.common import (
    _SEVERAL,
    _control,
    _filtered_episodes,
    _goals,
    _short_matches,
    _short_tasks,
    _toy_team,
)


def test_layout_offsets():
    layout = cuju.state.layout()
    position = layout['match.players[0].base.humanoid.nodes[0].position']
    assert (position.size, position.type) == (12, 'N7blunted7Vector3E')
    # GRF: a node holds 44 bytes, so the fourteen nodes of a player lie one after another.
    last = layout['match.players[0].base.humanoid.nodes[13].position']
    assert last.offset - position.offset == 13 * 44
    assert layout['match.goalCount[1]'].offset == layout['match.goalCount[0]'].offset + 4
    assert all(field.offset >= 0 and field.size > 0 for field in layout.values())


def test_gather_refuses_bad_input():
    with pytest.raises(cuju.StateError):
        cuju.state.gather(b'abc', ['environment.step'])
    batch = cuju.BatchEnvironment('5_vs_5_l1_r0', environment_count=1, device='cpu')
    batch.reset()
    state = batch.get_state(0)
    with pytest.raises(cuju.ArgumentError):
        cuju.state.gather(state, ['no.such.field'])
    with pytest.raises(cuju.ChoiceError):
        cuju.state.gather(state, ['environment.step'], frame='mirror')


def test_every_field_reads_and_mirrored_arrays_agree():
    layout = cuju.state.layout()
    batch = cuju.BatchEnvironment('5_vs_5_l4_r0', environment_count=2, device='cpu', seed=3)
    batch.reset(reverse=[0, 1])
    for step in range(30):
        batch.step(actions_at(step, 2, 4, 19))
    for environment in range(2):
        state = batch.get_state(environment)
        # Every field of the layout lies inside the match, so all of them read at once.
        assert len(cuju.state.gather(state, list(layout.values()))) == sum(
            field.size for field in layout.values()
        )
        position, prediction = cuju.state.read(
            state, ['match.ball.positionBuffer', 'match.ball.predictions[0]'], frame='pitch'
        )
        assert np.sign(position[:2]).tolist() == np.sign(prediction[:2]).tolist()


def _header(path):
    raw = gzip.decompress(path.read_bytes())
    (length,) = struct.unpack_from('<I', raw, 8)
    return json.loads(raw[12 : 12 + length])


def test_difficulty_and_its_earlier_name_ability_set_what_plays(arena_home):
    path = cuju.arena.play(
        'builtin', 'builtin', 'academy_empty_goal_close', seed=3, difficulty=(0.2, None)
    )
    header = _header(path)
    # The left plays the difficulty given, the right the scenario's own.
    assert header['teams'][0]['ability'] == 0.2
    assert 0 <= header['teams'][1]['ability'] <= 1
    earlier = cuju.arena.play(
        'builtin', 'builtin', 'academy_empty_goal_close', seed=3, ability=(0.2, None)
    )
    assert _header(earlier)['teams'] == header['teams']
    assert earlier.summary['score'] == path.summary['score']
    with pytest.raises(cuju.errors.ArgumentError):
        cuju.arena.play('builtin', 'builtin', 'academy_empty_goal_close', difficulty=(-0.1, None))
    with pytest.raises(cuju.errors.ArgumentError):
        cuju.arena.play(
            'builtin',
            'builtin',
            'academy_empty_goal_close',
            difficulty=(0.2, None),
            ability=(0.5, None),
        )


@pytest.mark.parametrize(
    ('scenario', 'control', 'message'),
    [
        ('5_vs_5', _control(5), 'only 4 controllable players'),
        ('academy_3_vs_1_with_keeper', _control(5), 'only 4 controllable players'),
        ('11_vs_11_kaggle', _control(1, 'fixed', ['AM'], 'builtin'), 'cannot select player AM'),
        (
            'benchmark_full_game_11_vs_11_easy',
            _control(1, 'fixed', ['GK'], 'agent'),
            'cannot select player GK',
        ),
        ('11_vs_11_kaggle', _control(1, 'fixed', ['CF']), 'controllable goalkeeper'),
    ],
)
def test_team_scenario_refusal(arena_home, tmp_path, scenario, control, message):
    """A refused match identifies the scenario and the conflicting setting."""
    format = '5v5' if scenario == '5_vs_5' else '11v11'
    team = _toy_team(tmp_path, 'refused', _SEVERAL, format, control)
    with pytest.raises(cuju.ArgumentError, match=message) as caught:
        cuju.arena.play(team['id'], 'builtin', scenario, seed=1)
    assert scenario in str(caught.value)


def test_default_submission_rejects_builtin_action(arena_home, tmp_path, monkeypatch):
    """Each submission uses its own action range in a mixed action set match."""
    _short_matches(monkeypatch, 1)
    default = _toy_team(tmp_path, 'default', 'def agent(obs):\n    return [19]\n', '5v5')
    v2 = _toy_team(
        tmp_path, 'v2', 'def agent(obs):\n    return [19]\n', '5v5', _control(1, action_set='v2')
    )
    # Kaggle open forfeits the side of the default set, and the v2 side's 19 stands.
    with pytest.raises(cuju.arena.PolicyError, match='to 18') as caught:
        cuju.arena.play(default['id'], v2['id'], '5_vs_5', seed=1)
    assert (caught.value.rule['side'], caught.value.rule['kind']) == ('home', 'invalid')


@pytest.mark.skipif(os.name != 'posix', reason='sends SIGINT as Ctrl+C does')
def test_recorder_keeps_every_nth_episode_and_a_reward_threshold(arena_home):
    episodes, kept, _, _ = _filtered_episodes(every=3)
    assert kept == episodes[::3]
    episodes, kept, _, _ = _filtered_episodes(min_reward=0.5)
    # GRF: the left team's agent receives 1 for its goal, the scenario's only reward.
    assert kept == [frames for frames in episodes if struct.unpack_from('<i', frames[-1], 8)[0]]


def test_recorder_keeps_what_a_function_chooses_from_the_summary(arena_home):
    episodes, kept, seen, _ = _filtered_episodes(keep='record')
    assert [summary['episode'] for summary in seen] == list(range(len(episodes)))
    for summary, frames in zip(seen, episodes, strict=True):
        assert summary['length'] == len(frames)
        assert summary['score'] == list(struct.unpack_from('<2i', frames[-1], 8))
        assert summary['goals'] == _goals(frames)
        assert summary['steps'] == struct.unpack_from('<i', frames[-1], 0)[0]
        assert summary['environment'] == 0
        assert summary['reward'] == sum(summary['rewards']) == summary['score'][0]
    assert kept == [frames for frames in episodes if len(frames) % 2 == 0]


def test_recorder_filters_refuse_invalid_values(arena_home):
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu'
    )
    for options in (
        {'keep': 'foo:1'},
        {'every': 0},
        {'every': 1.5},
        {'min_reward': float('nan')},
        {'min_reward': 'high'},
    ):
        with pytest.raises(cuju.errors.ArgumentError):
            cuju.arena.Recorder(batch, **options)
    batch.close()


def test_search_passes_its_settings_to_the_thresholds_of_the_signals(arena_home):
    """The public search reads the `[search]` table of the settings that it is given."""
    from cuju.arena._moments import search as _search

    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu', seed=11
    )
    try:
        recorder = cuju.arena.Recorder(batch)
        batch.reset()
        for step in range(12):
            batch.step(torch.tensor([[0]], dtype=torch.int32))
            # The value falls by 0.9 at the seventh step.
            recorder.annotate({'value': -0.4 if step == 6 else 0.5}, player=0)
        recorder.save()
    finally:
        batch.close()
    _search.wait()
    assert cuju.arena.search('value-drop')['total'] == 1
    harder = {'search': {'value_drop': 1.0}}
    assert cuju.arena.search('value-drop', config=harder)['total'] == 0
    assert cuju.arena.search('value-drop', overrides=['search.value_drop=1.0'])['total'] == 0
    assert (
        cuju.arena.search('value-drop', 5, config=harder, overrides=['search.value_drop=0.5'])[
            'total'
        ]
        == 1
    )
    with pytest.raises(cuju.errors.ConfigurationError):
        cuju.arena.search('value-drop', overrides=['search.value_drop=-1'])


def test_recorder_filters_leave_the_outputs_as_they_were(arena_home):
    """A batch steps the same bits with a filtering recorder as with none."""
    outputs = []
    for watched in (False, True):
        batch = cuju.BatchEnvironment(
            'academy_empty_goal_close_l1_r0', environment_count=2, device='cpu', seed=4
        )
        if watched:
            cuju.arena.Recorder(batch, keep=lambda summary: summary['goals'] > 0, every=2)
        steps = [batch.reset().clone()]
        for step in range(120):
            observation, reward, done, _ = batch.step(actions_at(step, 2, 1, 19))
            steps.append((observation.clone(), reward.clone(), done.clone()))
        steps.append(batch.get_state(0))
        outputs.append(steps)
        batch.close()
    assert torch.equal(outputs[0][0], outputs[1][0])
    for first, second in zip(outputs[0][1:-1], outputs[1][1:-1], strict=True):
        assert all(torch.equal(a, b) for a, b in zip(first, second, strict=True))
    assert outputs[0][-1] == outputs[1][-1]


def test_recorder_annotate_refuses_invalid_input(arena_home):
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu'
    )
    recorder = cuju.arena.Recorder(batch)
    with pytest.raises(cuju.errors.ArgumentError):
        recorder.annotate(text='before the first step')
    batch.reset()
    for options in (
        {},
        {'player': 1},
        {'text': 'x', 'player': 99},
        {'text': 'x', 'player': (1, 5)},
        {'values': {'value': float('nan')}},
        {'values': {'': 1.0}},
        {'values': {f'v{k}': k for k in range(17)}},
        {'text': 3},
        {'action': -1},
        {'probabilities': 'likely'},
        {'probabilities': [0.5, float('inf')]},
        {'text': 'x', 'step': 7},
    ):
        with pytest.raises(cuju.errors.ArgumentError):
            recorder.annotate(**options)
    recorder.annotate(text='a' * 300)
    assert recorder._notes[(0, -1)]['t'] == 'a' * 200
    batch.close()


def test_config_is_public_and_resolves_the_layers(arena_home, monkeypatch, tmp_path):
    """`cuju.arena.config` resolves the layered settings into the public `Config`."""
    from cuju.arena._config import layers as _layers

    monkeypatch.delenv(_layers.CONFIG_VARIABLE, raising=False)
    assert {'Config', 'config'} <= set(cuju.arena.__all__)
    assert cuju.arena.config is _layers.config and cuju.arena.Config is _layers.Config
    settings = cuju.arena.config()
    assert isinstance(settings, cuju.arena.Config)
    assert settings['recorder.every'] == 1 and settings.source('recorder.every') == 'defaults'
    path = tmp_path / 'settings.toml'
    path.write_text('[recorder]\nevery = 4\n', encoding='utf-8')
    settings = cuju.arena.config(path, overrides='recorder.keep=goals')
    assert settings['recorder.every'] == 4 and settings['recorder.keep'] == 'goals'
    assert settings.source('recorder.keep') == 'overrides'
    with pytest.raises(cuju.errors.ChoiceError):
        cuju.arena.config(overrides='recorder.kep=goals')


def test_statistics_and_runs_are_public(arena_home):
    """`statistics` and `runs` come from the store and read as names of `cuju.arena`."""
    from cuju.arena._store import runs as _runs
    from cuju.arena._store import stats as _stats

    assert {'runs', 'statistics'} <= set(cuju.arena.__all__)
    assert cuju.arena.statistics is _stats.of and cuju.arena.runs is _runs.list
    assert cuju.arena.statistics.__module__ == cuju.arena.runs.__module__ == 'cuju.arena'
    assert cuju.arena.runs() == []
    made = _runs.create('benchmark', name='a benchmark')
    assert [run['id'] for run in cuju.arena.runs(kind='benchmark')] == [made['id']]


def test_evaluate_benchmark_and_evaluation_are_public():
    """`evaluate`, `benchmark` and `Evaluation` read as names of `cuju.arena`."""
    from cuju.arena._evaluate import benchmark as _benchmark
    from cuju.arena._evaluate import evaluate as _evaluate

    names = ('Evaluation', 'benchmark', 'evaluate')
    assert set(names) <= set(cuju.arena.__all__)
    assert 'evaluations' not in cuju.arena.__all__
    assert cuju.arena.evaluate is _evaluate.evaluate
    assert cuju.arena.Evaluation is _evaluate.Evaluation
    assert cuju.arena.benchmark is _benchmark.benchmark
    for name in names:
        assert getattr(cuju.arena, name).__module__ == 'cuju.arena', name


def test_rate_is_public():
    """`rate` reads as a name of `cuju.arena`."""
    from cuju.arena._evaluate import ratings as _ratings

    assert 'rate' in cuju.arena.__all__ and cuju.arena.rate is _ratings.rate
    assert cuju.arena.rate.__module__ == 'cuju.arena'


def test_add_recordings_is_public():
    """`add_recordings` comes from the importer and reads as a name of `cuju.arena`."""
    from cuju.arena._import import core as _core

    assert 'add_recordings' in cuju.arena.__all__
    assert cuju.arena.add_recordings is _core.add_recordings
    assert cuju.arena.add_recordings.__module__ == 'cuju.arena'


def test_policy_errors_are_public_and_quiet(arena_home, tmp_path, monkeypatch, capsys):
    """A failing policy raises the public PolicyError in one line, with a traceback on request."""
    from cuju.arena._evaluate import academy as _academy
    from cuju.arena._playing import play as _play

    _short_matches(monkeypatch, 4)
    _short_tasks(monkeypatch, 4)
    assert issubclass(cuju.arena.PolicyError, cuju.errors.Error)
    assert issubclass(cuju.arena.PolicyError, RuntimeError)
    assert 'PolicyError' in cuju.arena.__all__
    wild = _toy_team(tmp_path, 'wild', 'def agent(obs):\n    return [99]\n', '5v5')
    # Kaggle open, the format of a side that chose none, forfeits an answer outside the set.
    with pytest.raises(cuju.arena.PolicyError, match=re.escape('wild forfeits, ')) as caught:
        _play.play(wild['id'], 'builtin', '5_vs_5', seed=1, policy_errors=True, save=False)
    assert isinstance(caught.value, _play.ForfeitError)
    assert caught.value.rule['kind'] == 'invalid' and '99' in str(caught.value)
    # A run of the benchmark keeps the error with its attempt, in the same line and as quiet.
    eleven = _toy_team(tmp_path, 'eleven', 'def agent(obs):\n    return [99]\n', '11v11')
    outcome = _academy.run('corner', eleven['id'], keep_recordings=False)
    assert outcome['failed_attempts'] == 5
    assert outcome['attempts'][0]['error'].startswith('the policy of eleven failed, it answered')
    assert 'Traceback' not in capsys.readouterr().err
    monkeypatch.setenv('CUJU_ARENA_DEBUG', 'on')
    _academy.run('corner', eleven['id'], keep_recordings=False)
    assert 'Traceback' in capsys.readouterr().err


def test_recorder_repr_names_its_state(arena_home):
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu'
    )
    recorder = cuju.arena.Recorder(batch)
    batch.reset()
    batch.step(torch.tensor([[5]], dtype=torch.int32))
    shown = repr(recorder)
    assert shown.startswith('Recorder(environment 0, ') and 'version 2, capturing, ' in shown
    assert '2 frames of the current episode, 0 episodes ended, 0 saved, 0 dropped)' in shown
    recorder.close()
    assert 'detached, 0 frames' in repr(recorder) and '1 saved' in repr(recorder)
    batch.close()


@pytest.mark.skipif(os.name != 'posix', reason='sends SIGINT to the group as Ctrl+C does')
def test_play_returns_a_path_with_the_summary_and_json_names_the_score(
    arena_home, monkeypatch, capsys
):
    """The recording of a requested match says how it ended, in Python and under --json."""
    from cuju.arena.__main__ import main
    from cuju.arena._format import recording as _recording

    _short_matches(monkeypatch, 60)
    path = cuju.arena.play('builtin', 'builtin', '5_vs_5', seed=1)
    assert isinstance(path, Path) and path.is_file() and path.suffix == '.cjr'
    assert path == arena_home / 'matches' / path.name
    last = list(_recording.frames(path))[-1]
    assert path.summary['score'] == list(struct.unpack_from('<2i', last, 8))
    assert path.summary['id'] == path.stem and path.summary['provenance']['seed'] == 1
    assert path.summary == cuju.arena.recordings()[0]
    # A path derived from it is a plain path of the same kind, with no summary.
    assert path.parent.summary is None and path.with_suffix('.json').is_file()
    capsys.readouterr()
    assert main(['play', '--scenario', '5_vs_5', '--seed', '1', '--json']) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed['score'] == printed['summary']['score'] == path.summary['score']
    assert printed['summary']['id'] == printed['match'] != path.stem
    assert printed['summary']['provenance']['seed'] == 1
