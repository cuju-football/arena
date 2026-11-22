# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the filter `keep` of a recorder, which takes the query language of the search."""

import struct

import pytest
import torch

import cuju
import cuju.errors
from cuju.arena._format import attachments as _attachments
from cuju.arena._format import keep as _keep
from cuju.arena._format import recording as _recording
from support.common import _filtered_episodes, _goals


def _frames(players, count, score=(0, 0)):
    """Returns blank frames of version 1 for `players` players, the last with `score`."""
    size = _recording.frame_bytes(players)
    frames = []
    for step in range(count):
        frame = bytearray(size)
        struct.pack_into('<i', frame, 0, step)
        frames.append(bytes(frame))
    last = bytearray(frames[-1])
    struct.pack_into('<2i', last, 8, *score)
    frames[-1] = bytes(last)
    return frames


def _note(frame, player, value, probabilities=None, action=None):
    return {
        'frame': frame,
        'step': frame,
        'player': player,
        'values': {'value': value},
        'text': None,
        'action': action,
        'probabilities': probabilities or [],
    }


def test_keep_takes_a_query_of_the_moments_language():
    for query in ('goal team:left', 'value-drop surprise:0.05', 'ann:value<-0.5', 'shots'):
        assert _keep.Filter(query).query is not None
    assert _keep.Filter('all').query is None and _keep.Filter('goals').query is None
    for bad in ('foo:1', '-surprise', 'surprise:2', 'entropy:1', 3):
        with pytest.raises(cuju.errors.ArgumentError):
            _keep.Filter(bad)


def test_a_query_keeps_the_episodes_whose_signals_it_finds():
    lineup = [{'team': side, 'index': k, 'goalkeeper': k == 0} for side in (0, 1) for k in (0, 1)]
    frames = _frames(4, 60)
    falling = [_note(f, 1, 1.0 if f < 30 else 0.2) for f in range(60)]
    steady = [_note(f, 1, 1.0) for f in range(60)]
    unlikely = [*steady[:40], _note(40, 3, 1.0, [[0, 0.9], [1, 0.05], [2, 0.04]], 9)]

    def summary(notes, header=None):
        return _keep.episode_summary(0, frames, [0.0], 0, notes, header=header)

    drop = _keep.Filter('value-drop')
    assert drop(summary(falling)) and not drop(summary(steady))
    assert drop(summary(falling, {'players': lineup}))
    # The thresholds of the filter apply at the end of each episode, as those of a search.
    harder = {'value_drop': 0.9, 'window': 2.0, 'surprise': 0.1}
    assert not _keep.Filter('value-drop', thresholds=harder)(summary(falling))
    assert _keep.Filter('value-drop team:left')(summary(falling, {'players': lineup}))
    assert not _keep.Filter('value-drop team:right')(summary(falling, {'players': lineup}))
    # Player 3 is the second player of the right team, and its chosen action lies outside the
    # three kept probabilities, so it had at most 0.01.
    assert _keep.Filter('surprise:0.02 team:right')(summary(unlikely, {'players': lineup}))
    assert not _keep.Filter('surprise:0.005')(summary(unlikely, {'players': lineup}))
    assert _keep.Filter('ann:value<0.5')(summary(falling))
    assert not _keep.Filter('ann:value<0.5')(summary(steady))
    # The summary stays the mapping that a function of keep reads.
    made = summary(steady)
    assert isinstance(made, dict) and made['length'] == 60 and made.frames is frames


def test_a_query_keeps_the_episodes_whose_moments_it_finds(arena_home):
    episodes, kept, _, _ = _filtered_episodes(keep='goal')
    scored = [frames for frames in episodes if _goals(frames)]
    assert scored and len(scored) < len(episodes)
    assert kept == scored
    episodes, kept, _, _ = _filtered_episodes(keep='goal team:right')
    assert kept == []
    episodes, kept, _, _ = _filtered_episodes(keep='-goal')
    assert len(kept) == len(episodes)


def test_a_recorder_keeps_the_episodes_that_a_query_of_signals_finds(arena_home):
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu', seed=11
    )
    try:
        everything = cuju.arena.Recorder(batch)
        filtered = cuju.arena.Recorder(batch, keep='value-drop')
        # The fall of 2 stays under a threshold of 3 read from the settings, so nothing is kept.
        strict = cuju.arena.Recorder(batch, keep='value-drop', overrides=['search.value_drop=3'])
        recorders = (everything, filtered, strict)
        batch.reset()
        episode = step = 0
        while everything.episodes < 4:
            action = 12 if step % 3 else 5
            _, _, done, _ = batch.step(torch.tensor([[action]], dtype=torch.int32))
            # Even episodes see their value fall at their third step.
            value = -1.0 if episode % 2 == 0 and step == 2 else 1.0
            for recorder in recorders:
                recorder.annotate({'value': value}, player=0)
            step += 1
            if bool(done[0]):
                episode, step = episode + 1, 0
        batch.step(torch.tensor([[0]], dtype=torch.int32))
        for recorder in recorders:
            recorder.flush()
    finally:
        batch.close()
    assert len(everything.saved) == 4
    assert len(filtered.saved) == 2 and filtered.dropped == 2
    assert strict._filter.thresholds['value_drop'] == 3
    assert strict.saved == [] and strict.dropped == 4
    for path in filtered.saved:
        notes = _attachments.annotations(path)
        assert min(note['v']['value'] for note in notes if 'v' in note) == -1.0
