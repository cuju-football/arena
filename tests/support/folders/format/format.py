"""The Namer format of the tests, a Kaggle main.py that a manifest names.

`accept` reads `manifest.json`, where there is one, for the name and the players of a team and
refuses a folder whose manifest does not read. Its option `crash` makes the format fail on
purpose, as a format with a bug does, and `matches` plays a check match of that many steps.
"""

from cuju.arena.formats import v1 as formats


def accept(folder, team):
    """Refuses a folder without a main.py or a readable manifest, else names the team."""
    if team.options['crash']:
        raise RuntimeError('the namer broke on purpose')
    if not (folder / 'main.py').is_file():
        return formats.refuse('load', 'main.py is missing', detail='entry')
    manifest = {}
    if (folder / 'manifest.json').is_file():
        try:
            manifest = formats.read_manifest(folder, 'manifest.json')
        except formats.ManifestError as error:
            return formats.refuse(
                'load', f'manifest.json does not read, {error}', detail='manifest'
            )
    if team.options['matches']:
        match = formats.test_match('builtin', steps=team.options['matches'])
        if match.forfeit:
            return formats.refuse(match.forfeit.kind, match.forfeit.message, detail='check')
    return formats.accept(players=manifest.get('players'), name=manifest.get('name'))


def start(folder, match):
    """Returns the side's player, the agent of the main.py."""
    return Player(folder, match)


class Player:
    def __init__(self, folder, match):
        self.agent = formats.kaggle_agent(folder)
        self.calls = 0

    def act(self, step):
        if not step.used:
            return None
        obs = formats.kaggle_observation(step.raw, step=self.calls)
        self.calls += 1
        return formats.as_actions(self.agent(obs), len(step.raw), step.action_set)
