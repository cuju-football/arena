# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Calls a Kaggle agent and checks its answer against the action set, as Kaggle's runner does.

The call is the team's, so an error in it blames the team, and the check is the format's, so an
answer that is no action is an invalid answer of the team and never a failure of the format.
"""

from cuju.arena.formats import v1 as formats
from cuju.kaggle import action_index, action_indices

# GRF: the actions of the default set, which a team of that set answers in any batch.
DEFAULT_ACTIONS = formats.ACTION_COUNTS['default']


def actions(runner, obs, team_set, step_set):
    """Returns the actions of a Kaggle agent's answer, one for each controlled player.

    A team of the default set in a batch of the v2 set answers 19 actions, and its answers
    translate into the batch's set, as the renderer always played such a team.

    Args:
        runner: The `cuju.kaggle.AgentRunner` of the agent.
        obs: The Kaggle observation.
        team_set: The action set the team's control chose.
        step_set: The action set of the batch, `Step.action_set`.

    Raises:
        Invalid: If the answer holds the wrong number of actions, an action of another type or
            one outside the set.
    """
    answer = runner(obs)
    translate = step_set != team_set
    count = DEFAULT_ACTIONS if translate else formats.ACTION_COUNTS[step_set]
    players = len(obs['players_raw'])
    try:
        values = (
            [action_index(answer, count)]
            if players == 1
            else action_indices(answer, count, players)
        )
    except (TypeError, ValueError, IndexError) as error:
        raise formats.Invalid(' '.join(str(error).split())) from None
    return [value % DEFAULT_ACTIONS for value in values] if translate else values
