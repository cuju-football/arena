"""The Squad format, one policy per role, each controlled player with its own recurrent state.

The method is each role's network, called once a step with that player's observation and state.
The bookkeeping is here, squad.toml, the roles against the team's fixed control, loading every
network once for the run, and a state for each player kept by the player's index, cleared at every
match and updated on every step, so the state follows the match as training did.
"""

import numpy

from cuju.arena.formats import v1 as formats

REPRESENTATIONS = {'simple115v2', 'extracted'}


def read(folder):
    """The settings of squad.toml, or a ValueError that says what does not fit."""
    spec = formats.read_manifest(folder, 'squad.toml')
    roles = spec.get('roles') or {}
    if spec.get('representation') not in REPRESENTATIONS:
        raise ValueError(f'representation must be {" or ".join(sorted(REPRESENTATIONS))}')
    if type(spec.get('hidden')) is not int or spec['hidden'] < 1:
        raise ValueError("'hidden' must be the size of the recurrent state, a whole number")
    for role, file in roles.items():
        if not (folder / file).is_file():
            raise ValueError(f'the policy of {role}, {file}, is missing')
    return {'roles': roles, 'representation': spec['representation'], 'hidden': spec['hidden']}


def accept(folder, team):
    """Checks squad.toml and the policy for each fixed role, then plays 20 steps."""
    formats.require('torch', hint='install torch')
    try:
        spec = read(folder)
    except (ValueError, formats.ManifestError) as error:
        return formats.refuse('load', str(error), detail='squad.toml')
    selection = team.control.selection
    if team.control.assignment != 'fixed' or not all(isinstance(item, str) for item in selection):
        return formats.refuse(
            'rule',
            'set Control to fixed players by role, one policy plays each role',
            detail='control',
        )
    missing = sorted(set(selection) - set(spec['roles']))
    if missing:
        return formats.refuse('load', f'no policy for {", ".join(missing)}', detail='roles')
    smoke = formats.test_match('builtin', steps=20)
    if smoke.forfeit:
        return formats.refuse(
            smoke.forfeit.kind, smoke.forfeit.message, detail=smoke.forfeit.detail
        )
    note = formats.problem(
        'note', f'20 steps, {len(spec["roles"])} policies', 'note', detail='smoke'
    )
    return formats.accept(deterministic=True, problems=[note])


def start(folder, match):
    return Player(folder, read(folder), match)


class Player:
    """One side of one match, a network for each role and a recurrent state for each player."""

    def __init__(self, folder, spec, match):
        torch = formats.require('torch')
        self.torch = torch
        self.roles = match.roles
        self.networks = {
            role: torch.jit.load(str(folder / file)).eval() for role, file in spec['roles'].items()
        }
        self.observer = formats.Observer(spec['representation'])
        self.hidden = spec['hidden']
        self.states = {}
        print(f'{len(self.networks)} policies, roles {", ".join(sorted(self.networks))}')

    def reset(self):
        self.observer.reset()
        self.states = {}

    def act(self, step):
        torch, actions = self.torch, []
        with torch.no_grad():
            for player, observation in zip(
                step.indices, self.observer.per_player(step), strict=True
            ):
                network = self.networks[self.roles[player]]
                state = self.states.get(player, torch.zeros(1, self.hidden))
                tensor = torch.as_tensor(numpy.asarray(observation)[None], dtype=torch.float32)
                logits, self.states[player] = network(tensor, state)
                actions.append(int(logits.argmax()))
        # The states follow every step, whether or not the answer is used.
        if not step.used:
            return None
        return formats.as_actions(actions, len(actions), step.action_set)
