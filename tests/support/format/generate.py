# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Writes the recordings of this folder and `expected.json`, from a batch on the CPU.

Run it by hand from the root of the repository, with cuju and the renderer installed, after a
change of the format that the fixtures should show.

    python tests/support/format/generate.py

Each file holds the start of one episode of five a side, past the keyframe at 100, with the
lanes of the players, the actions of the agents, the state of the kick off, a few annotations
and a few overlays. The matches play from fixed seeds with actions of integer arithmetic, so
the frames come out the same on every machine, and `expected.json` lists what went into each
file, taken before it was written, with the numbers of the overlays as their types store them.

The files of this folder come from the writer before attachments, which kept the annotations and
the overlays in sections of the file, and the readers take those as embedded attachments. A run
now writes each recording as the game alone with its attachments beside it.
"""

import json
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).parents[2]))

import cuju
from cuju.arena._format import actions as _actions
from cuju.arena._format import attachments as _attachments
from cuju.arena._format import overlays as _overlays
from cuju.arena._format import players as _players
from cuju.arena._format import recording as _recording
from support import format as _fixtures

_STEPS = 103
_SCENARIO = '5_vs_5_l2_r1'


def _actions_at(step, agents, count):
    """Returns the actions of a step, held for a few steps so that the players move."""
    return [(step // 4 * 7 + agent * 5 + 1) % count for agent in range(agents)]


def _overlays_of(players):
    """Returns a few overlays, a grid and a vector of each type."""
    grid = np.arange(24, dtype=np.float64).reshape(4, 6) / 2
    return [
        {'f': 0, 'name': 'pressure', 'kind': 'grid', 'values': grid},
        {
            'f': 2,
            'p': 1,
            'name': 'value',
            'kind': 'grid',
            'side': 1,
            'extent': [-1.0, -0.42, 1.0, 0.42],
            'type': 'float16',
            'values': grid.T / 4 - 1,
        },
        {'f': 2, 'p': 1, 'name': 'pass', 'kind': 'targets', 'values': np.arange(players) / 8},
        {
            'f': 101,
            'p': 0,
            'name': 'policy',
            'kind': 'actions',
            'type': 'uint8',
            'values': np.arange(19) / 2,
        },
    ]


def _episode(reverse):
    """Plays the start of an episode and returns its header, frames and what goes beside them."""
    batch = cuju.BatchEnvironment(
        _SCENARIO, environment_count=1, device='cpu', seed=7, auto_reset=False
    )
    batch.reset(reverse=[reverse])
    players = int(batch.left_players[0] + batch.right_players[0])
    reader = _recording.StateReader(players)
    state = batch.get_state(0)
    lineup = reader.lineup(state)
    frames = [reader.frame(state)]
    rows = [reader.lanes(batch.roles(0))]
    played = [[_actions.NONE] * batch.agents]
    for step in range(_STEPS):
        chosen = _actions_at(step, batch.agents, batch.action_count)
        batch.step(torch.tensor([chosen], dtype=torch.int32))
        frames.append(reader.frame(batch.get_state(0)))
        rows.append(reader.lanes(batch.roles(0)))
        played.append(chosen)
    played.append([_actions.NONE] * batch.agents)
    key = _recording.run_key(
        batch.scenario_name,
        reader.agents,
        'default',
        reader.engine_seed,
        reader.reverse,
        batch.difficulty[0].tolist(),
    )
    head = _recording.header(
        'A fixture of the format',
        _SCENARIO,
        lineup,
        None,
        len(frames),
        _recording.scenario_timing(state),
        {'source': 'recorder', 'environment': 0, 'engine_seed': reader.engine_seed},
        reader.agents,
        'default',
        key,
    )
    notes = [
        {'f': 1, 'p': 0, 'v': {'value': 0.25}, 'a': played[1][0], 'k': [[played[1][0], 0.5]]},
        {'f': 100, 't': 'the keyframe'},
    ]
    batch.close()
    return head, frames, rows, played, state, _overlays_of(players), notes


def main():
    expected = {}
    names = (*_fixtures.JOINT_FILES, _fixtures.TRACK_FILE)
    for name, reverse, version in zip(names, (0, 1, 0), (2, 2, 3), strict=True):
        head, frames, rows, played, state, overlays, notes = _episode(reverse)
        path = _fixtures.FOLDER / name
        carried = _recording.Frames(
            frames, players=rows, actions=played, state=state, overlays=overlays
        )
        _attachments.write_recording(path, head, carried, version, notes)
        players = len(_recording._document(head)['players'])
        expected[name] = _fixtures.described(
            # The header is the one part that the writer completes, so it comes from the file.
            _recording.read_header(path),
            _fixtures.track_frames(frames, players),
            _players.stack(rows, players),
            np.array(played, np.uint8),
            state,
            _overlays.decode(_overlays.encode(overlays)),
            notes,
            frames if version != 3 else None,
        )
        print(f'{name}: {path.stat().st_size} bytes, {len(frames)} frames')
    text = json.dumps(expected, indent=1) + '\n'
    (_fixtures.FOLDER / 'expected.json').write_text(text, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
