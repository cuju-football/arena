# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of `annotate`, the batched annotations by agent, their overlays and their settings."""

import math
import re
import struct
import time
from pathlib import Path

import numpy as np
import pytest
import torch

import cuju
import cuju.errors
import cuju.raw
from conftest import actions_at, tensor_bytes
from cuju.arena._format import attachments as _attachments
from cuju.arena._format import overlays as _overlays
from cuju.arena._format import recording as _recording
from cuju.arena._watch import annotate as _annotate


def _places(lineup):
    return {(entry['team'], entry['index']): at for at, entry in enumerate(lineup)}


def _expected(batch, environment, lineup):
    """Returns the header index of the player of each agent, from GRF's raw observations."""
    left = int(batch.left_agents[environment])
    place = _places(lineup)
    found = []
    for agent, raw in enumerate(cuju.raw.grf_observations(batch, environment)):
        team = int(agent >= left)
        found.append(place[(team, int(raw['active']))] if raw['active'] >= 0 else -1)
    return found


def _notes(path):
    return {(record['f'], record.get('p', -1)): record for record in _attachments.annotations(path)}


def _signal(batch):
    """Returns values that name each agent and its environment, `[E, A]`."""
    environments = torch.arange(batch.environment_count, dtype=torch.float32)[:, None]
    return environments * 100 + torch.arange(batch.agents, dtype=torch.float32)[None]


@pytest.mark.parametrize('scenario', ['5_vs_5_l2_r1', '11_vs_11_easy_stochastic_l3_r2'])
def test_agents_map_to_the_players_of_the_raw_view(device, arena_home, scenario):
    """Every agent lands on its active player, on both teams and under both reverse flags."""
    batch = cuju.BatchEnvironment(
        scenario, environment_count=2, device=device, with_raw=True, seed=5
    )
    recorders = [cuju.arena.Recorder(batch, environment) for environment in range(2)]
    batch.reset(reverse=[0, 1])
    signal = _signal(batch).to(batch.torch_device)
    expected = {0: [], 1: []}
    for step in range(40):
        batch.step(actions_at(step, 2, batch.agents, batch.action_count).to(batch.torch_device))
        for recorder in recorders:
            lineup = recorder._lineup
            players = _expected(batch, recorder.environment, lineup)
            assert (
                _annotate.agent_players(recorder._lanes[-1], lineup, recorder._reader.agents)
                == players
            )
            expected[recorder.environment].append(players)
        assert _annotate.annotate(batch, values={'agent': signal}) == 2
    moved = set()
    for recorder in recorders:
        assert recorder._reverse == bool(recorder.environment)
        notes = _notes(recorder.save())
        for frame, players in enumerate(expected[recorder.environment], start=1):
            for agent, player in enumerate(players):
                if player >= 0:
                    record = notes[(frame, player)]
                    assert record['v'] == {'agent': recorder.environment * 100 + agent}
                    moved.add((recorder.environment, agent, player))
            # A player that no agent controls takes nothing.
            held = {player for player in players if player >= 0}
            assert {key[1] for key in notes if key[0] == frame} == held
    # The agents moved between players.
    assert len(moved) > 2 * batch.agents
    batch.close()


def test_grids_targets_and_arrows_lie_in_their_declared_view(arena_home):
    """An agent's grid and targets name its side, arrows always do, and pitch names none."""
    batch = cuju.BatchEnvironment('5_vs_5_l2_r1', environment_count=1, device='cpu', seed=3)
    agent_view = cuju.arena.Recorder(batch)
    pitch_view = cuju.arena.Recorder(batch, overrides=['annotations.perspective=pitch'])
    batch.reset(reverse=[1])
    grid = torch.arange(3 * 2 * 3, dtype=torch.float32).reshape(1, 3, 2, 3)
    match = torch.linspace(0, 1, 12).reshape(1, 3, 4)
    targets = torch.rand(1, 3, 10)
    arrows = torch.rand(1, 3, 19)
    for step in range(3):
        batch.step(actions_at(step, 1, 3, 19))
    columns = _annotate.agent_players(
        agent_view._lanes[-1], agent_view._lineup, agent_view._reader.agents
    )
    assert (
        _annotate.annotate(
            batch,
            grids={'value': grid, 'pressure': match},
            targets={'pass': targets},
            arrows={'policy': arrows},
            extent=[-1, -0.42, 1, 0.42],
        )
        == 2
    )
    lineup = agent_view._lineup
    for recorder, perspective in ((agent_view, 'agent'), (pitch_view, 'pitch')):
        found = {
            (record['p'] if 'p' in record else -1, record['name']): record
            for record in _attachments.overlays(recorder.save())
        }
        assert found[(-1, 'pressure')].get('side') is None
        assert found[(-1, 'pressure')]['values'].shape == (3, 4)
        for agent, player in enumerate(columns):
            team = lineup[player]['team']
            side = team if perspective == 'agent' else None
            assert found[(player, 'value')].get('side') == side
            assert found[(player, 'value')]['extent'] == [-1.0, -0.42, 1.0, 0.42]
            assert np.allclose(
                found[(player, 'value')]['values'], grid[0, agent].numpy(), atol=0.02
            )
            assert found[(player, 'pass')].get('side') == side
            assert np.array_equal(
                found[(player, 'pass')]['values'],
                targets[0, agent].numpy().astype(np.float16).astype(np.float32),
            )
            assert found[(player, 'policy')]['side'] == team
            assert found[(player, 'policy')]['kind'] == 'actions'
    batch.close()


def test_overlays_round_trip_and_limits_refuse_at_the_call(arena_home):
    batch = cuju.BatchEnvironment('5_vs_5_l1_r0', environment_count=2, device='cpu')
    recorder = cuju.arena.Recorder(batch, environment=1)
    batch.reset()
    batch.step(torch.zeros((2, 1), dtype=torch.int32))
    small = torch.zeros(2, 1, 4, 6)
    grids = {f'grid{k}': small + k for k in range(4)}
    assert _annotate.annotate(batch, grids=grids) == 1
    # A fifth grid of the step and player passes the limit, at the call and before any change.
    with pytest.raises(cuju.errors.ArgumentError, match='at most 4 grids'):
        _annotate.annotate(batch, grids={'one more': small}, values={'v': torch.zeros(2, 1)})
    assert not recorder._notes
    # The same name replaces its grid.
    assert _annotate.annotate(batch, grids={'grid0': small + 9}) == 1
    vectors = {f'vector{k}': torch.zeros(2, 1, 5) for k in range(4)}
    assert _annotate.annotate(batch, targets=vectors) == 1
    with pytest.raises(cuju.errors.ArgumentError, match='4 vectors of a step and player'):
        _annotate.annotate(batch, arrows={'arrows': torch.zeros(2, 1, 19)})
    refused = [
        ({'grids': {'tall': torch.zeros(2, 1, 73, 2)}}, 'cells'),
        ({'grids': {'big': torch.zeros(2, 1, 72, 97)}}, 'cells'),
        ({'grids': {'hole': torch.full((2, 1, 2, 2), math.nan)}}, 'finite'),
        ({'targets': {'long': torch.zeros(2, 1, 65)}}, 'at most 64 values'),
        ({'targets': {'huge': torch.full((2, 1, 3), 1e6)}}, '16-bit'),
        ({'grids': {'': small}}, 'names of 1 to 40'),
        ({'values': {'v': torch.zeros(3, 1)}}, 'one row for each of the 2 environments'),
        ({'values': {'v': torch.zeros(2, 2)}}, r'shape \[E, 1\]'),
        ({'probabilities': torch.zeros(2, 1, 3), 'logits': torch.zeros(2, 1, 3)}, 'both'),
        ({'actions': torch.full((2, 1), -1)}, 'whole number'),
        ({}, 'expected values'),
    ]
    for options, message in refused:
        with pytest.raises(cuju.errors.ArgumentError, match=message):
            _annotate.annotate(batch, **options)
    # The settings keep grids smaller, and only every few steps.
    few = cuju.arena.Recorder(
        batch, environment=0, overrides=['annotations.grid_cells=12', 'annotations.grid_every=2']
    )
    batch.step(torch.zeros((2, 1), dtype=torch.int32))
    with pytest.raises(cuju.errors.ArgumentError, match='at most 12 cells'):
        _annotate.annotate(batch, grids={'g': torch.zeros(2, 1, 4, 4)})
    for _ in range(3):
        _annotate.annotate(batch, grids={'g': torch.zeros(2, 1, 3, 4)})
        batch.step(torch.zeros((2, 1), dtype=torch.int32))
    path = recorder.save()
    kept = {record['name']: record for record in _attachments.overlays(path) if record['f'] == 1}
    assert sorted(kept) == [*sorted(grids), *sorted(vectors)]
    assert np.array_equal(kept['grid0']['values'], np.full((4, 6), 9, np.float32))
    assert np.array_equal(kept['grid3']['values'], np.full((4, 6), 3, np.float32))
    few.save()
    frames = list(_recording.frames(few.saved[-1]))
    kept_steps = [
        struct.unpack_from('<i', frames[record['f']])[0]
        for record in _attachments.overlays(few.saved[-1])
    ]
    assert kept_steps and all(step % 2 == 0 for step in kept_steps)
    batch.close()


def test_overlays_of_an_episode_stay_within_the_room_of_a_section(arena_home, monkeypatch):
    monkeypatch.setattr(_overlays, 'MAX_BYTES', 4096)
    batch = cuju.BatchEnvironment('5_vs_5_l1_r0', environment_count=1, device='cpu')
    recorder = cuju.arena.Recorder(batch)
    batch.reset()
    with pytest.raises(cuju.errors.ArgumentError, match='MiB in a recording'):
        for _ in range(10):
            batch.step(torch.zeros((1, 1), dtype=torch.int32))
            _annotate.annotate(batch, grids={'g': torch.rand(1, 1, 24, 36)})
    # What the recorder took before the refusal writes as it is.
    assert len(_attachments.overlays(recorder.save())) >= 1
    batch.close()


class _Untouchable:
    """A stand-in for a tensor that fails on any use."""

    def __getattr__(self, name):
        raise AssertionError(f'read {name} of a tensor')


def test_a_call_with_nothing_attached_returns_at_once(arena_home):
    batch = cuju.BatchEnvironment('5_vs_5_l4_r0', environment_count=4, device='cpu')
    batch.reset()
    untouchable = _Untouchable()
    calls = 20_000
    started = time.perf_counter()
    for _ in range(calls):
        _annotate.annotate(
            batch,
            values={'value': untouchable},
            probabilities=untouchable,
            grids={'map': untouchable},
            played=True,
        )
    each = (time.perf_counter() - started) / calls
    assert _annotate.annotate(batch, values={'value': untouchable}) == 0
    # The bound is generous for a busy machine, where a call takes about ten microseconds.
    assert each < 50e-6, f'{each * 1e6:.1f} microseconds for a call with nothing attached'
    # An environment that holds the batch reaches it the same way.
    holder = type('Holder', (), {'batch': batch})()
    assert _annotate.annotate(holder, values={'value': untouchable}) == 0
    batch.close()


def test_a_batched_call_equals_the_calls_of_each_player(arena_home):
    """A file of batched annotations equals one of the old calls by player, byte for byte."""

    def play(batched):
        batch = cuju.BatchEnvironment('5_vs_5_l2_r1', environment_count=1, device='cpu', seed=9)
        recorder = cuju.arena.Recorder(batch, title='same', teams=[{'name': 'a'}, {'name': 'b'}])
        batch.reset()
        generator = torch.Generator().manual_seed(4)
        for step in range(30):
            batch.step(actions_at(step, 1, 3, 19))
            values = torch.rand((1, 3), generator=generator)
            probabilities = torch.rand((1, 3, 19), generator=generator).softmax(-1)
            chosen = probabilities.argmax(-1)
            if batched:
                _annotate.annotate(
                    batch,
                    values={'value': values},
                    probabilities=probabilities,
                    actions=chosen,
                    text='a step',
                )
                continue
            columns = _annotate.agent_players(
                recorder._lanes[-1], recorder._lineup, recorder._reader.agents
            )
            for agent, player in enumerate(columns):
                if player < 0:
                    continue
                entry = recorder._lineup[player]
                recorder.annotate(
                    values={'value': values[0, agent]},
                    player=(entry['team'], entry['index']),
                    probabilities=probabilities[0, agent],
                    action=chosen[0, agent],
                )
            recorder.annotate(text='a step')
        path = recorder.save()
        batch.close()
        return _attachments.read(_attachments.path_of(path, 'annotations')).body

    assert play(True) == play(False)


def test_agent_and_played_in_the_calls_of_one_player(arena_home):
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu', seed=11
    )
    recorder = cuju.arena.Recorder(batch)
    batch.reset()
    batch.step(torch.tensor([[5]], dtype=torch.int32))
    (player,) = _annotate.agent_players(
        recorder._lanes[-1], recorder._lineup, recorder._reader.agents
    )
    recorder.annotate(values={'value': 1.5}, agent=0, logits=torch.tensor([0.0, 0.0, math.log(2)]))
    recorder.annotate(grids={'map': np.ones((2, 2))}, agent=0)
    with pytest.raises(cuju.errors.ArgumentError, match='a player or an agent, found both'):
        recorder.annotate(values={'value': 1.0}, agent=0, player=0)
    with pytest.raises(cuju.errors.ArgumentError, match='a player or an agent for targets'):
        recorder.annotate(targets={'pass': [1.0, 2.0]})
    while not recorder.episodes:
        _, reward, _, _ = batch.step(torch.tensor([[12]], dtype=torch.int32))
    _annotate.annotate(batch, values={'reward': reward}, played=True)
    recorder.flush()
    path = recorder.saved[0]
    notes = _notes(path)
    first = notes[(1, player)]
    assert first['v'] == {'value': 1.5}
    # The softmax of the logits, the most likely first, three kept.
    assert first['k'] == [[2, 0.5], [0, 0.25], [1, 0.25]]
    last = max(frame for frame, _ in notes)
    assert notes[(last, player)]['v'] == {'reward': 1}
    assert [record['name'] for record in _attachments.overlays(path)] == ['map']
    batch.close()


def test_the_guide_example_runs(arena_home):
    """The training loop of docs/annotations.md runs as written."""
    guide = Path(__file__).parents[2] / 'docs' / 'annotations.md'
    blocks = re.findall(r'```python\n(.*?)```', guide.read_text(encoding='utf-8'), re.S)
    (example,) = [block for block in blocks if 'cuju.arena.annotate(' in block][:1]
    assert cuju.arena.annotate is _annotate.annotate

    class Policy(torch.nn.Module):
        def forward(self, observation):
            count, agents = observation.shape[:2]
            logits = torch.zeros(count, agents, 19)
            logits[..., 12] = 2.0
            return logits, torch.zeros(count, agents)

    namespace = {'policy': Policy(), 'steps': 40}
    exec(compile(example, str(guide), 'exec'), namespace)
    recorder = namespace['recorder']
    notes = _attachments.annotations(recorder.saved[-1])
    assert notes and all(record.get('k', [[12]])[0][0] == 12 for record in notes)


def test_annotating_changes_no_output(device, arena_home):
    outputs = []
    for annotated in (False, True):
        batch = cuju.BatchEnvironment('5_vs_5_l2_r1', environment_count=2, device=device, seed=2)
        if annotated:
            cuju.arena.Recorder(batch, 1)
        seen = [tensor_bytes(batch.reset())]
        for step in range(30):
            actions = actions_at(step, 2, 3, 19).to(batch.torch_device)
            _annotate.annotate(
                batch,
                values={'value': torch.ones(2, 3, device=batch.torch_device)},
                grids={'map': torch.ones(2, 3, 2, 2, device=batch.torch_device)},
            )
            observation, reward, done, _ = batch.step(actions)
            seen += [tensor_bytes(observation), tensor_bytes(reward), tensor_bytes(done)]
        outputs.append([*seen, batch.get_state(0), batch.get_state(1)])
        batch.close()
    assert outputs[0] == outputs[1]
