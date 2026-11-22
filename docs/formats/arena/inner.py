"""The submission's side of the Arena format, the author's Agent in a child process of its own.

A child runs every call from the submission's folder, first on its path, as Kaggle runs agents.
"""

from cuju.arena.formats import v1 as formats


class Remote:
    """Agent(path) of agent.py, with reset once a match and act over GRF's typed dicts."""

    def __init__(self, folder):
        module = formats.load_module(folder, 'agent.py')
        if not hasattr(module, 'Agent'):
            raise formats.Forfeit(
                'load', 'agent.py does not define a class named Agent', detail='agent'
            )
        self.agent = module.Agent(folder)

    def reset(self, seed=None):
        if seed is not None:
            formats.seed_globals(seed)
        if hasattr(self.agent, 'reset'):
            self.agent.reset()

    def act(self, observations, action_set):
        return formats.as_actions(self.agent.act(observations), len(observations), action_set)
