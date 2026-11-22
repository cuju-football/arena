"""The Arena 5 vs 5 format, an arena's rules written under contract 1, an example in the guide.

The method is the author's Agent.act, run in a child process so that every limit is measured and
enforced from outside it. The bookkeeping is here, the manifest, the folder's size, the five time
limits, and the full self-check with matches against the random side and the built-in AI.
"""

from collections import Counter

from cuju.arena.formats import v1 as formats

MANIFEST_KEYS = {'team', 'members', 'controlled_players', 'deterministic', 'description'}


def read_manifest(folder):
    """The manifest's values, or a problem that refuses the team."""
    try:
        data = formats.read_manifest(folder, 'manifest.yml')
    except formats.ManifestError as error:
        return None, formats.problem('load', str(error), detail='manifest')
    name = str(data.get('team') or '').strip()
    players = data.get('controlled_players')
    deterministic = data.get('deterministic', True)
    if not name:
        return None, formats.problem('load', "the manifest needs 'team'", detail='manifest.team')
    if type(players) is not int or not 1 <= players <= 4:
        return None, formats.problem(
            'load',
            f"'controlled_players' must be a whole number from 1 to 4, found {players!r}",
            detail='manifest.players',
        )
    if type(deterministic) is not bool:
        return None, formats.problem(
            'load', "'deterministic' must be true or false", detail='manifest.deterministic'
        )
    return {
        'name': name,
        'players': players,
        'deterministic': deterministic,
        'unknown': sorted(set(data) - MANIFEST_KEYS),
    }, None


def accept(folder, team):
    """Checks the layout, manifest, size and load, and plays matches on a full check."""
    options = team.options
    formats.require('numpy', hint='install numpy')
    if 'gfootball' in formats.imports_of(folder):
        formats.require(
            'gfootball', hint='this submission imports gfootball, install cuju[gfootball]'
        )
    if not (folder / 'agent.py').is_file():
        return formats.refuse('load', 'agent.py is missing', detail='layout')
    manifest, problem = read_manifest(folder)
    if problem:
        return formats.accept(problems=[problem])
    found = []
    if manifest['unknown']:
        keys = ', '.join(manifest['unknown'])
        found.append(
            formats.problem(
                'load', f'unknown keys ignored, {keys}', 'warning', detail='manifest.keys'
            )
        )
    if team.size != '5v5' or team.scenario not in (None, '5_vs_5'):
        return formats.refuse('rule', 'this format plays 5_vs_5 only', detail='scenario')
    size = formats.folder_size(folder) / 2**20
    if size > options['folder_mib']:
        return formats.refuse(
            'rule', f'{size:.1f} MiB, the limit is {options["folder_mib"]:g} MiB', detail='size'
        )
    child = formats.Child(
        folder, 'inner.py', 'Remote', load_timeout=options['load_seconds'], forfeits=True
    )
    child.close()
    if team.full:
        found += full_check(options, manifest)
    return formats.accept(
        players=manifest['players'],
        name=manifest['name'],
        deterministic=manifest['deterministic'],
        problems=found,
    )


def full_check(options, manifest):
    """Checks speed and behavior in full matches against random actions and the built-in AI."""
    found, seconds, counts = [], [], Counter()
    for opponent in ('random', 'builtin'):
        for index in range(options['check_matches']):
            side = 'left' if index % 2 == 0 else 'right'
            match = formats.test_match(opponent, side=side, seed=index)
            if match.forfeit:
                found.append(
                    formats.problem(
                        match.forfeit.kind, match.forfeit.message, detail=f'match.{opponent}'
                    )
                )
                return found
            score = f'{match.goals[0]} to {match.goals[1]} in {match.steps} steps'
            found.append(formats.problem('note', score, 'note', detail=f'match.{opponent}'))
            seconds += match.seconds[1:]
            counts.update(match.actions)
    mean = 1000 * sum(seconds) / max(len(seconds), 1)
    found.append(
        formats.problem(
            'rule',
            f'{mean:.2f} ms after the first call, the limit is {options["mean_ms"]:g} ms',
            'error' if mean > options['mean_ms'] else 'note',
            detail='mean',
        )
    )
    twice = formats.test_match('builtin', steps=10, repeat=True)
    if not twice.same_actions:
        found.append(
            formats.problem(
                'note',
                'the same 10 steps played twice gave different actions',
                'warning' if manifest['deterministic'] else 'note',
                detail='deterministic',
            )
        )
    total = sum(counts.values())
    if total:
        action, count = counts.most_common(1)[0]
        if count > 0.9 * total:
            found.append(
                formats.problem(
                    'note',
                    f'action {action} is {count / total:.0%} of all actions',
                    'warning',
                    detail='behavior',
                )
            )
    return found


def start(folder, match):
    return Player(folder, match)


class Player:
    """One side of one match, the author's Agent behind a child process with the arena's limits."""

    def __init__(self, folder, match):
        self.options = match.options
        self.action_set = match.team_action_set
        self.seed = match.policy_seed
        self.first = True
        self.seconds = []
        self.child = formats.Child(
            folder,
            'inner.py',
            'Remote',
            load_timeout=self.options['load_seconds'],
            forfeits=True,
        )

    def reset(self):
        seed = self.seed if self.options['seed_globals'] else None
        self.child.call('reset', seed, timeout=self.options['reset_seconds'], forfeits=True)

    def act(self, step):
        if not step.used:
            return None
        limit = self.options['first_call_seconds'] if self.first else self.options['call_seconds']
        actions, seconds = self.child.call(
            'act', step.raw, self.action_set, timeout=limit, forfeits=True
        )
        if not self.first:
            self.seconds.append(seconds)
        self.first = False
        return actions

    def end(self, result):
        """The mean call time over the match, after the first call, is the arena's last rule."""
        if self.seconds:
            mean = 1000 * sum(self.seconds) / len(self.seconds)
            limit = self.options['mean_ms']
            if mean > limit:
                message = f'{mean:.2f} ms a call after the first, the limit is {limit:g} ms'
                return formats.Forfeit('rule', message, detail='mean')
        return None

    def close(self):
        self.child.close()
