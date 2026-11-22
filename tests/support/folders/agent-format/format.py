"""The Agent file format of the tests, whose submission is an agent.py and nothing else.

`accept` refuses a folder without `agent.py`, so a team registered under it needs no `main.py`,
and its player calls the function `agent` of the file with Kaggle's observation.
"""

from cuju.arena.formats import v1 as formats


def accept(folder, team):
    """Refuses a folder without an agent.py."""
    if not (folder / 'agent.py').is_file():
        return formats.refuse('load', 'agent.py is missing', detail='entry')
    return formats.accept()


def start(folder, match):
    """Returns the side's player, the agent of the agent.py."""
    return Player(folder)


class Player:
    def __init__(self, folder):
        self.agent = formats.load_module(folder, 'agent.py').agent
        self.calls = 0

    def act(self, step):
        if not step.used:
            return None
        obs = formats.kaggle_observation(step.raw, step=self.calls)
        self.calls += 1
        return formats.as_actions(self.agent(obs), len(step.raw), step.action_set)
