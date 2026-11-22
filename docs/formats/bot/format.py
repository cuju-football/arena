"""The Bot format, rules in bot.py, one named action for each player each step.

The method is bot.decide, called with one player's GRF observation. The bookkeeping is here,
loading bot.py, seeding the usual random generators from the match so that a bot that rolls dice
replays, calling it from its folder, and turning gfootball's action names into numbers.
"""

from cuju.arena.formats import v1 as formats
from cuju.gfootball.env import football_action_set


def names(action_set):
    """The names of the actions of a set, in the order of their numbers."""
    found = football_action_set.get_action_set({'action_set': action_set})
    return [str(action) for action in found]


def accept(folder, team):
    """The pre-check, bot.py with decide, then 20 steps against the built-in AI."""
    if not (folder / 'bot.py').is_file():
        return formats.refuse('load', 'bot.py is missing', detail='entry')
    bot = formats.load_module(folder, 'bot.py')
    if not callable(getattr(bot, 'decide', None)):
        return formats.refuse('load', 'bot.py defines no decide(observation)', detail='entry')
    smoke = formats.test_match('builtin', steps=20)
    if smoke.forfeit:
        return formats.refuse(
            smoke.forfeit.kind, smoke.forfeit.message, detail=smoke.forfeit.detail
        )
    return formats.accept(deterministic=True)


def start(folder, match):
    return Player(folder, formats.load_module(folder, 'bot.py'), match)


class Player:
    """One side of one match, bot.decide for each controlled player."""

    def __init__(self, folder, bot, match):
        self.folder = folder
        self.bot = bot
        self.seed = match.policy_seed
        self.index = {name: number for number, name in enumerate(names(match.team_action_set))}

    def reset(self):
        formats.seed_globals(self.seed)

    def act(self, step):
        if not step.used:
            return None
        with formats.from_folder(self.folder):
            chosen = [self.bot.decide(observation) for observation in step.raw]
        unknown = [name for name in chosen if name not in self.index]
        if unknown:
            raise formats.Forfeit(
                'invalid', f'decide returned {unknown[0]!r}, which is no action of this match'
            )
        return [self.index[name] for name in chosen]
