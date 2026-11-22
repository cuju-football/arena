"""The Probe format of the tests, a Kaggle main.py played as Kaggle open plays it.

Its option `mode` makes it fail on purpose once `after` steps have passed, so the tests of the
hosts reach every forfeit, ceiling and failure.
"""

import os
import time

from cuju.arena.formats import v1 as formats

from . import helper

# The matches this process has started, which a format of scope run keeps across a run.
STARTED = []


def accept(folder, team):
    """The pre-check, a main.py with an agent, and a check match of `check_steps` steps."""
    if not (folder / 'main.py').is_file():
        return formats.refuse('load', 'main.py is missing', detail='entry')
    formats.kaggle_agent(folder)
    found = [formats.problem('note', f'{helper.NAME} read main.py', 'note', detail='load')]
    if team.options['check_steps']:
        match = formats.test_match('builtin', steps=team.options['check_steps'], repeat=True)
        if match.forfeit:
            return formats.refuse(match.forfeit.kind, match.forfeit.message, detail='check')
        found.append(
            formats.problem('note', f'{match.steps} steps, same {match.same_actions}', 'note')
        )
    return formats.accept(deterministic=True, problems=found)


def start(folder, match):
    STARTED.append(match.seed)
    if match.options['mode'] == 'load_stall':
        time.sleep(3600)
    return Player(folder, match)


class Player:
    def __init__(self, folder, match):
        self.agent = formats.kaggle_agent(folder)
        self.mode = match.options['mode']
        self.after = match.options['after']
        self.calls = 0
        self.child = None
        if self.mode == 'child':
            self.child = formats.Child(folder, 'inner.py', 'Echo', forfeits=True)

    def reset(self):
        self.calls = 0

    def act(self, step):
        if step.number >= self.after:
            self.fail(step)
        if not step.used:
            return None
        obs = formats.kaggle_observation(step.raw, step=self.calls)
        self.calls += 1
        answer = self.agent(obs)
        if self.child is not None:
            answer, _ = self.child.call('echo', answer, timeout=60)
        return formats.as_actions(answer, len(step.raw), step.action_set, negative=True)

    def fail(self, step):
        if self.mode == 'stall':
            time.sleep(3600)
        if self.mode == 'spin':
            while True:
                pass
        if self.mode == 'second' and len(STARTED) == 2:
            os._exit(3)
        if self.mode == 'exit':
            os._exit(3)
        if self.mode == 'format_error':
            raise RuntimeError('the probe broke on purpose')
        if self.mode == 'forfeit':
            raise formats.Forfeit(
                'rule', f'the probe forfeits at step {step.number}', detail='probe'
            )
        if self.mode == 'invalid':
            formats.as_actions(99, len(step.raw), step.action_set)

    def end(self, result):
        if self.mode == 'end_forfeit':
            return formats.Forfeit('rule', f'{result.steps} steps were too many', detail='mean')
        return None

    def close(self):
        if self.child is not None:
            self.child.close()
