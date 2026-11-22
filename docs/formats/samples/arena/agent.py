"""An Arena submission that reads GRF observations through a hand-written agent."""

from pathlib import Path

import numpy as np

RIGHT, TOP_RIGHT, TOP, TOP_LEFT, LEFT, BOTTOM_LEFT, BOTTOM, BOTTOM_RIGHT = 5, 4, 3, 2, 1, 8, 7, 6
SHOT = 12
DIRECTIONS = [RIGHT, TOP_RIGHT, TOP, TOP_LEFT, LEFT, BOTTOM_LEFT, BOTTOM, BOTTOM_RIGHT]
GOAL = np.array([1.0, 0.0])


def towards(here, there):
    dx, dy = there - here
    return DIRECTIONS[int(np.round(np.arctan2(-dy, dx) / (np.pi / 4))) % 8]


class Agent:
    def __init__(self, path):
        self.path = Path(path)

    def reset(self):
        pass

    def act(self, observations):
        return [self.one(obs) for obs in observations]

    def one(self, obs):
        me = obs['active']
        here = obs['left_team'][me]
        if obs['ball_owned_team'] == 0 and obs['ball_owned_player'] == me:
            return SHOT if np.linalg.norm(GOAL - here) < 0.3 else towards(here, GOAL)
        return towards(here, obs['ball'][:2])
