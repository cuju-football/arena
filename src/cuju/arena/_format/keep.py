# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The filters of a recorder, which decide at the end of each episode whether to save it.

A filter reads the summary of the episode, its score, outcome, goals, length, rewards and
annotations, so it never touches the batch, and the frames of an episode that it drops are
discarded unwritten.

`keep` takes `all`, `goals`, a function of the summary, or a query of the moments language that
the search of the library reads, such as `goal team:left` or `value-drop surprise:0.05`. A query
keeps an episode when it finds at least one moment in it, read by the same rules and with the
same thresholds, the `[search]` table of the settings, as a search of the saved recording would.
"""

from __future__ import annotations

import datetime
import math
import struct
from collections.abc import Callable
from typing import Any

from cuju.errors import ArgumentError

# The names that `keep` takes besides a function and a query.
KEEP = ('all', 'goals')
# How an episode ended for the left team, by the score of its last frame.
OUTCOMES = ('win', 'draw', 'loss')
# The bytes of a frame before its players and of each player, as version 1 stores them, which
# tell the number of players of a frame when the summary carries no header.
_FRAME_HEAD = 76
_NODE_BYTES = 196


def outcome(score: list[int] | tuple[int, ...]) -> str:
    """Returns how the left team stands at a score of the left and the right team."""
    left, right = score
    return 'win' if left > right else 'loss' if left < right else 'draw'


class Summary(dict):
    """The summary of an ended episode, a mapping of the fields that `episode_summary` lists.

    Beside the fields it holds the `frames` of the episode and the `header` of its recording, as
    attributes, which a query reads for the moments of the rules of play.
    """

    frames: list[bytes]
    header: dict[str, Any] | None


class Filter:
    """Decides from the summary of an ended episode whether a recorder saves it.

    Every condition must hold, the choice, function or query of `keep`, every `every`-th episode
    from the first, and a return of at least `min_reward`.
    """

    def __init__(
        self,
        keep: str | Callable[[dict[str, Any]], Any] | None = None,
        every: int = 1,
        min_reward: float | None = None,
        *,
        thresholds: dict[str, Any] | None = None,
    ) -> None:
        """Checks and keeps the conditions.

        Args:
            keep: `all` or `None` for every episode, `goals` for those with a goal, a function
                that takes the summary and returns whether to save it, or a query of the moments
                language.
            every: Saves the first episode and every `every`-th after it.
            min_reward: Saves only episodes whose `reward` is at least this.
            thresholds: The thresholds of the policy signals that a query reads, by default the
                `[search]` table of the settings.

        Raises:
            ArgumentError: If `keep` is neither a function, one of `KEEP` nor a query that reads,
                `every` is not a whole number of at least 1, or `min_reward` is not a finite
                number.
        """
        if keep is None:
            keep = 'all'
        self.query = None
        if not callable(keep) and keep not in KEEP:
            if not isinstance(keep, str):
                raise ArgumentError(
                    f'expected keep as a function, a query or one of {KEEP}, found {keep!r}'
                )
            from .._moments import search as _search

            self.query = _search.Query(keep)
            self.thresholds = dict(thresholds) if thresholds is not None else _search.thresholds()
        if isinstance(every, bool) or not isinstance(every, int) or every < 1:
            raise ArgumentError(f'expected every as a whole number of at least 1, found {every!r}')
        if min_reward is not None and (
            isinstance(min_reward, bool)
            or not isinstance(min_reward, int | float)
            or not math.isfinite(min_reward)
        ):
            raise ArgumentError(f'expected min_reward as a finite number, found {min_reward!r}')
        self.keep = keep
        self.every = every
        self.min_reward = min_reward

    def __call__(self, summary: dict[str, Any]) -> bool:
        """Returns whether to save the episode of `summary`, as `episode_summary` returns it."""
        if summary['episode'] % self.every:
            return False
        if self.min_reward is not None and summary['reward'] < self.min_reward:
            return False
        if self.query is not None:
            return bool(found(self.query, summary, self.thresholds))
        if self.keep == 'goals':
            return summary['goals'] > 0
        if callable(self.keep):
            return bool(self.keep(summary))
        return True


def _lineup(frames: list[bytes]) -> list[dict[str, Any]]:
    """Returns a lineup for frames without a header, the players split evenly between the teams,
    the left team's first."""
    size = len(frames[0]) if frames else 0
    for players in range(1, 64):
        padded = -(-players // 4) * 4
        if _FRAME_HEAD + players * _NODE_BYTES + 3 * padded == size:
            left = (players + 1) // 2
            return [
                {'team': 0 if k < left else 1, 'index': k if k < left else k - left}
                for k in range(players)
            ]
    return []


def found(query: Any, summary: dict[str, Any], thresholds: dict[str, Any]) -> list[Any]:
    """Returns the moments that a query finds in an ended episode.

    Args:
        query: The query, as `_moments.search.Query` reads it.
        summary: The summary of the episode, as `episode_summary` returns it.
        thresholds: The thresholds of the policy signals, the `[search]` table.
    """
    from .._moments import events as _events
    from .._moments import search as _search
    from .._store import stats as _stats
    from . import annotations as _annotations

    frames = getattr(summary, 'frames', None) or []
    header = dict(getattr(summary, 'header', None) or {})
    if not isinstance(header.get('players'), list):
        header['players'] = _lineup(frames)
    header.setdefault('steps_per_second', 10)
    header.setdefault('engine_scorer', True)
    header.setdefault('offside_called', True)
    lineup = header['players']
    records = []
    for note in summary.get('annotations') or []:
        player = note.get('player')
        if isinstance(player, int) and not isinstance(player, bool):
            try:
                player = _annotations.player_index(lineup, player)
            except ArgumentError:
                player = None
        record: dict[str, Any] = {'f': note.get('frame'), 'v': note.get('values') or {}}
        if isinstance(player, int):
            record['p'] = player
        if note.get('probabilities'):
            record['k'] = note['probabilities']
        if note.get('action') is not None:
            record['a'] = note['action']
        records.append(record)
    signals = _stats.signals(records) if query.signals else None
    moments = []
    if frames and query.needs_events() and lineup:
        moments = _events.moments(header, frames, timing=True)
    kept = _search._header(header)
    entry = {'id': '', 'header': kept}
    created = datetime.datetime.now().isoformat(timespec='seconds')
    record_ = _search._record(
        entry,
        {
            'id': '',
            'created': created,
            'score': summary.get('score'),
            'provenance': {'source': 'recorder'},
        },
        [],
    )
    return query.find(record_, moments, signals, kept, thresholds)


def step_rewards(batch: Any, environment: int, information: dict[str, Any]) -> list[float]:
    """Returns the reward of each agent of an environment on a step, none when it is not known.

    The rewards are those that the batch hands its step observers, read without a change, and the
    agents count in the batch's order, those of the left team first.

    Args:
        batch: The batch.
        environment: The environment.
        information: What the batch handed its observers for the step.
    """
    rewards = information.get('reward')
    if rewards is None:
        return []
    reward = rewards[environment].reshape(-1)
    mask = batch.agent_mask[environment].reshape(-1)
    return [
        float(value) for value, kept in zip(reward.tolist(), mask.tolist(), strict=True) if kept
    ]


def episode_summary(
    episode: int,
    frames: list[bytes],
    rewards: list[float],
    environment: int,
    annotations: list[dict[str, Any]] | None = None,
    *,
    header: dict[str, Any] | None = None,
) -> Summary:
    """Returns the summary of an episode that a filter reads.

    Args:
        episode: The number of episodes that the recorder ended before this one.
        frames: The frames of the episode.
        rewards: The return of each agent over the captured steps.
        environment: The environment of the recorder.
        annotations: The annotations of the episode, with long keys.
        header: What the header of the recording will hold, at least its `players`, which a
            query reads for the moments of the rules of play.
    """
    last = frames[-1]
    score = list(struct.unpack_from('<2i', last, 8))
    (step,) = struct.unpack_from('<i', last, 0)
    summary = Summary(
        episode=episode,
        environment=environment,
        score=score,
        outcome=outcome(score),
        goals=sum(score),
        length=len(frames),
        steps=step,
        rewards=list(rewards),
        reward=sum(rewards),
        annotations=list(annotations or ()),
    )
    summary.frames = frames
    summary.header = header
    return summary
