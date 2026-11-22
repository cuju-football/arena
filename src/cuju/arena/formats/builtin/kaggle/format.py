# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The Kaggle format, built in, the reference format of contract 1.

The method is Kaggle's agent, called once for each step whose answer is used. The bookkeeping is
the renderer's own code from before formats, `kaggle_agent` (the body of loading a team, the last
of all callables, every call from the folder) and `kaggle_observation`, so Kaggle open plays
exactly as cuju-render played teams. With timing on, the same code runs in a child process, and
the time of each step and the bank of overage are measured from outside it.
"""

from cuju.arena.formats import v1 as formats
from cuju.arena.formats.builtin.kaggle import answers
from cuju.kaggle import AgentRunner


def accept(folder, team):
    """The pre-check, a main.py with an agent in it, and one player under Kaggle's rule."""
    if not (folder / 'main.py').is_file():
        return formats.refuse('load', 'main.py is missing at the top of the folder', detail='entry')
    if team.options['players'] == 'one' and team.control.players != 1:
        return formats.refuse(
            'rule', f'{team.name} controls {team.control.players} players, Kaggle plays 1'
        )
    formats.kaggle_agent(folder)
    return formats.accept()


def start(folder, match):
    """Returns the side's player, Kaggle open in the match process or timed Kaggle in a child."""
    if match.options['timing']:
        return Timed(folder, match)
    return Open(formats.kaggle_agent(folder), match.team_action_set)


class Open:
    """Kaggle open, the play of every team before formats.

    No call is made on a step whose answer is not used, and Kaggle's `step` counts the calls.
    """

    def __init__(self, agent, team_set):
        self.runner = AgentRunner(agent)
        self.team_set = team_set
        self.calls = 0

    def act(self, step):
        if not step.used:
            return None
        obs = formats.kaggle_observation(step.raw, step=self.calls)
        self.calls += 1
        return answers.actions(self.runner, obs, self.team_set, step.action_set)


class Timed:
    """Kaggle's own timing, from its football environment, measured around the child from outside.

    Each step has `step_seconds`, and a step that takes longer draws the excess from a bank of
    `overage_seconds`, which the agent sees as `remainingOverageTime` as it stands. A step that
    outlasts its time and the bank forfeits the side as a timeout of the overage.
    """

    def __init__(self, folder, match):
        self.step_seconds = match.options['step_seconds']
        self.bank = match.options['overage_seconds']
        self.calls = 0
        try:
            self.child = formats.Child(
                folder,
                'inner.py',
                'Agent',
                match.team_action_set,
                load_timeout=match.options['load_seconds'],
                forfeits=True,
            )
        except formats.Forfeit as found:
            if found.kind == 'timeout':
                raise formats.Forfeit('load', found.message, detail='timeout') from None
            if found.kind == 'crash':
                raise formats.Forfeit('load', found.message) from None
            raise

    def act(self, step):
        if not step.used:
            return None
        bank = max(self.bank, 0.0)
        try:
            actions, seconds = self.child.call(
                'act',
                step.raw,
                self.calls,
                bank,
                step.action_set,
                timeout=self.step_seconds + bank,
                forfeits=True,
            )
        except formats.Forfeit as found:
            if found.kind == 'timeout':
                raise formats.Forfeit(
                    'timeout',
                    f'step {step.number} used the last of the overage bank of {bank:g} s',
                    detail='overage',
                ) from None
            raise
        self.calls += 1
        self.bank -= max(0.0, seconds - self.step_seconds)
        if self.bank < 0:
            raise formats.Forfeit(
                'timeout', f'step {step.number} used the last of the overage bank', detail='overage'
            )
        return actions

    def close(self):
        self.child.close()
