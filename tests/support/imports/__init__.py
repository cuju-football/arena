# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Files to import that cuju writes for the tests, GRF dumps, Kaggle episodes and action logs.

Each helper plays a short match in a batch on the CPU, with a recorder of the renderer attached
where a test compares the import with a native recording of the same match. Nothing is downloaded.
"""

import io
import json
import pickle
from pathlib import Path

import torch

import cuju
from cuju import dumps
from cuju import kaggle as _kaggle
from cuju.arena._watch.recorder import Recorder

__all__ = ['action_log', 'full_dump', 'goal_dump', 'kaggle_episode', 'write_dump']

SCENARIO = '5_vs_5_l2_r1'


def _actions(batch, step, generator):
    return torch.randint(
        0,
        batch.action_count,
        (batch.environment_count, batch.agents),
        generator=generator,
        dtype=torch.int32,
    )


def full_dump(folder, steps=40, reverse=0, seed=11):
    """Plays a match with a `DumpWriter` and a recorder attached, and returns both files.

    Returns:
        The dump, which starts at the kick off, and the native recording of the same match.
    """
    batch = cuju.BatchEnvironment(
        SCENARIO, environment_count=1, device='cpu', seed=seed, with_raw=True, auto_reset=False
    )
    try:
        writer = dumps.DumpWriter(batch, str(folder))
        recorder = Recorder(batch, 0, title='native')
        batch.reset(reverse=[reverse])
        generator = torch.Generator().manual_seed(seed)
        for step in range(steps):
            actions = _actions(batch, step, generator)
            batch.step(actions)
            writer.record(actions)
        (path,) = writer.close()
        native = recorder.close()
    finally:
        batch.close()
    return Path(path), native


def goal_dump(folder):
    """Plays an academy drill that scores after 110 idle steps, and returns its goal dump.

    GRF's goal dump holds the 100 steps before the goal and the step that scored, so this one
    starts in the middle of its episode.
    """
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close',
        environment_count=1,
        device='cpu',
        seed=5,
        with_raw=True,
        auto_reset=False,
    )
    written = []
    try:
        writer = dumps.DumpWriter(
            batch, str(folder), write_full_episode_dumps=False, write_goal_dumps=True
        )
        batch.reset()
        for step in range(200):
            value = 0 if step < 110 else (12 if step % 2 else 5)
            actions = torch.full((1, batch.agents), value, dtype=torch.int32)
            _, _, done, _ = batch.step(actions)
            written += writer.record(actions)
            if bool(done[0]):
                break
        writer.close()
    finally:
        batch.close()
    (path,) = written
    return Path(path)


class _Action:
    """GRF's `CoreAction` in a pickle, by its attributes."""

    def __init__(self, name):
        self._backend_action = 0
        self._name = name
        self._sticky = False
        self._directional = False


class _Pickler(pickle._Pickler):
    """Pickles `_Action` under GRF's name of `CoreAction`, and numpy's arrays as numpy does."""

    def save_global(self, obj, name=None):
        if obj is _Action:
            self.save('gfootball.env.football_action_set')
            self.save('CoreAction')
            self.write(pickle.STACK_GLOBAL)
            self.memoize(obj)
            return None
        return super().save_global(obj, name)


def _with_actions(value):
    if isinstance(value, dict):
        return {
            key: [_Action(name) for name in item] if key == 'action' else _with_actions(item)
            for key, item in value.items()
        }
    return value


def write_dump(path, entries):
    """Writes steps as the reader of the import reads them back into a dump, as GRF writes one."""
    with open(path, 'wb') as stream:
        for entry in entries:
            buffer = io.BytesIO()
            _Pickler(buffer, protocol=4).dump(_with_actions(entry))
            stream.write(buffer.getvalue())
    return Path(path)


def kaggle_episode(path, steps=30, seed=9):
    """Plays a 5v5 match of one agent a side and writes it as a Kaggle episode, beside a native
    recording of the same match.

    Each step holds both agents' entries, the left agent's view in the frame of the pitch and the
    right agent's turned, as Kaggle writes them, with the actions of the step before.

    Returns:
        The episode and the native recording.
    """
    batch = cuju.BatchEnvironment(
        '5_vs_5',
        environment_count=1,
        device='cpu',
        seed=seed,
        representation='raw',
        auto_reset=False,
        agent_counts=[(1, 1)],
    )
    try:
        recorder = Recorder(batch, 0, title='native')
        batch.reset()
        generator = torch.Generator().manual_seed(seed)
        rows = []

        def entries(actions, status):
            return [
                {
                    'action': [] if actions is None else [int(actions[0, agent])],
                    'reward': 0,
                    'info': {},
                    'observation': dict(_kaggle.observation(batch, 0, agent, step=len(rows))),
                    'status': status,
                }
                for agent in range(2)
            ]

        rows.append(entries(None, 'ACTIVE'))
        for step in range(steps):
            actions = _actions(batch, step, generator)
            batch.step(actions)
            rows.append(entries(actions, 'DONE' if step == steps - 1 else 'ACTIVE'))
        native = recorder.close()
    finally:
        batch.close()
    episode = {
        'id': 'test-episode',
        'name': 'football',
        'configuration': {'scenario_name': '5_vs_5', 'episodeSteps': steps + 1},
        'info': {'TeamNames': ['Greens', 'Blues']},
        'steps': rows,
        'statuses': ['DONE', 'DONE'],
    }
    Path(path).write_text(json.dumps(episode), encoding='utf-8')
    return Path(path), native


def action_log(path, steps=40, seed=3):
    """Plays a match through `cuju.ActionLog` with a recorder attached, and saves the log.

    Returns:
        The log and the native recording.
    """
    log = cuju.ActionLog(SCENARIO, environment_count=1, device='cpu', seed=seed, auto_reset=False)
    try:
        recorder = Recorder(log.batch, 0, title='native')
        log.reset()
        generator = torch.Generator().manual_seed(seed)
        for step in range(steps):
            log.step(_actions(log.batch, step, generator))
        native = recorder.close()
        log.save(str(path))
    finally:
        log.close()
    return Path(path), native
