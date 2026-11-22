# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The submission's side of the Kaggle format, run in a child process when timing is on.

It uses the same helpers as the untimed path, so the agent is found, called and its answer checked
by the same code in both, and only where it runs differs.
"""

from cuju.arena.formats import v1 as formats
from cuju.arena.formats.builtin.kaggle import answers
from cuju.kaggle import AgentRunner


class Agent:
    """Kaggle's agent in the child, built from the submission's folder."""

    def __init__(self, folder, team_set):
        self.runner = AgentRunner(formats.kaggle_agent(folder))
        self.team_set = team_set

    def act(self, raw, calls, overage, step_set):
        """Returns the actions of the agent for the observation of a step."""
        obs = formats.kaggle_observation(raw, step=calls, overage=overage)
        return answers.actions(self.runner, obs, self.team_set, step_set)
