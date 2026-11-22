# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Kaggle episodes of the GRF competition, read into the steps of their match.

Kaggle keeps an episode as JSON. Its `steps` hold one entry for each step of the match and, in
each, one entry for each agent, the left agent first, with the agent's `action`, `status` and
`observation`. The observation holds `players_raw`, GRF's raw observation of each player that the
agent controls, as the agent saw it: the left agent sees the match in the frame of the pitch, and
the right agent sees it turned by half a turn with its own team as `left_team`. `configuration`
names the scenario as `scenario_name`, and `info` may name the teams as `TeamNames`.

The first entry of `steps` is the observation after the reset, and each later one holds the
actions of the step that led to it. Every step becomes a step in the frame of the pitch, from the
left agent's view, or from the right agent's turned back where the left one is missing.

An episode holds no engine seed that cuju can confirm, so it imports as positions.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from . import grf as _grf


class EpisodeError(ValueError):
    """Raised when a file holds no Kaggle episode that the import reads, with the reason."""


class Episode:
    """The steps of a Kaggle episode in the frame of the pitch.

    Attributes:
        steps: The steps, `grf.Step` each.
        scenario: The scenario, as `configuration.scenario_name` names it.
        teams: The name of the left and the right team, or `None` where the file names none.
        agents: The controlled players of the left and the right agent.
        action_set: The action set that numbers the actions, or `None` when an action lies
            outside the default set.
        ended: Whether every agent's status at the last step is no longer active.
        turned_back: The steps whose frame came from the right agent's view, turned back.
        disagreeing: The steps where the two agents' views turned to one frame differ.
    """

    def __init__(self) -> None:
        self.steps: list[_grf.Step] = []
        self.scenario = '11_vs_11_kaggle'
        self.teams: list[str] | None = None
        self.agents = (0, 0)
        self.action_set: str | None = 'default'
        self.ended = False
        self.turned_back = 0
        self.disagreeing = 0


def looks_like(data: bytes) -> bool:
    """Returns whether bytes start as a JSON object, which a Kaggle episode is."""
    return data.lstrip()[:1] == b'{'


def _views(entry: Any, right: bool) -> list[_grf.Step]:
    """Returns the views of one agent at one step, each turned to the frame of the pitch."""
    if not isinstance(entry, dict):
        return []
    observation = entry.get('observation')
    if not isinstance(observation, dict):
        return []
    raw = observation.get('players_raw')
    if not isinstance(raw, list):
        return []
    return [_grf.agent_view(view, right) for view in raw if isinstance(view, dict)]


def _actions(entry: Any, count: int, names: tuple[str, ...]) -> tuple[list[str | None], bool]:
    """Returns the names of an agent's actions at a step, and whether each lay in the set."""
    value = entry.get('action') if isinstance(entry, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = [value]
    if not isinstance(value, list):
        return [None] * count, True
    found: list[str | None] = []
    fits = True
    for item in value[:count]:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or int(item) != item:
            found.append(None)
            continue
        index = int(item)
        # GRF: an action indexes a Python list, so it may count from the end.
        if -len(names) <= index < len(names):
            found.append(names[index])
        else:
            found.append(None)
            fits = False
    return found + [None] * (count - len(found)), fits


def read(data: bytes) -> Episode:
    """Returns the episode of a Kaggle JSON file.

    Raises:
        EpisodeError: If the bytes hold no episode with steps and raw observations.
    """
    try:
        document = json.loads(data)
    except (ValueError, RecursionError) as error:
        raise EpisodeError(f'no JSON, {error}'[:200]) from None
    if not isinstance(document, dict) or not isinstance(document.get('steps'), list):
        raise EpisodeError('no list of steps')
    episode = Episode()
    configuration = document.get('configuration')
    if isinstance(configuration, dict) and isinstance(configuration.get('scenario_name'), str):
        episode.scenario = configuration['scenario_name'][:100]
    info = document.get('info')
    names = info.get('TeamNames') if isinstance(info, dict) else None
    if isinstance(names, list) and len(names) == 2 and all(isinstance(n, str) for n in names):
        episode.teams = [name[:100] for name in names]
    steps = document['steps']
    if not steps or not all(isinstance(step, list) and step for step in steps):
        raise EpisodeError('steps without the entries of their agents')
    action_names = _grf.ACTION_SETS['default']
    agents: tuple[int, int] | None = None
    for index, entries in enumerate(steps):
        try:
            left = _views(entries[0], False)
            right = _views(entries[1], True) if len(entries) > 1 else []
        except (KeyError, TypeError, ValueError, IndexError) as error:
            raise EpisodeError(
                f'step {index} holds a raw observation that does not read, '
                f'{type(error).__name__}: {error}'[:200]
            ) from None
        if not left and not right:
            raise EpisodeError(f'step {index} holds no raw observation of either agent')
        if agents is None:
            agents = (len(left), len(right))
        base = left[0] if left else right[0]
        if not left:
            episode.turned_back += 1
        elif right and not np.array_equal(
            np.concatenate([team.positions for team in left[0].teams]),
            np.concatenate([team.positions for team in right[0].teams]),
        ):
            episode.disagreeing += 1
        for side, views in ((0, left), (1, right)):
            team = base.teams[side]
            team.controlled = [
                view.teams[side].controlled[0] for view in views if view.teams[side].controlled
            ]
            team.sticky = [view.teams[side].sticky[0] for view in views if view.teams[side].sticky]
            designated = [
                view.teams[side].designated
                for view in views
                if view.teams[side].designated is not None
            ]
            team.designated = designated[0] if designated else None
        base.step = index
        if index:
            left_actions, left_fits = _actions(entries[0], agents[0], action_names)
            right_actions, right_fits = (
                _actions(entries[1], agents[1], action_names)
                if len(entries) > 1
                else ([None] * agents[1], True)
            )
            if not (left_fits and right_fits):
                episode.action_set = None
            base.actions = [name or '' for name in (*left_actions, *right_actions)]
        episode.steps.append(base)
    episode.agents = agents or (0, 0)
    last = steps[-1]
    statuses = [entry.get('status') for entry in last if isinstance(entry, dict)]
    episode.ended = bool(statuses) and all(
        isinstance(status, str) and status != 'ACTIVE' for status in statuses
    )
    sizes = {tuple(len(team.positions) for team in step.teams) for step in episode.steps}
    if len(sizes) != 1:
        raise EpisodeError('teams whose sizes change from step to step')
    return episode
