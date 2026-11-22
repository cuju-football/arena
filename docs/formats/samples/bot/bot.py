"""A Bot submission that chooses one gfootball action name for the observed player."""

import random


def decide(observation):
    me = observation['active']
    x, y = observation['left_team'][me]
    owner = observation['ball_owned_team'] == 0 and observation['ball_owned_player'] == me
    if not owner:
        bx, by = observation['ball'][:2]
        if abs(bx - x) > abs(by - y):
            return 'right' if bx > x else 'left'
        return 'bottom' if by > y else 'top'
    if x > 0.7 and abs(y) < 0.2:
        return 'shot'
    return random.choice(['right', 'top_right', 'bottom_right'])
