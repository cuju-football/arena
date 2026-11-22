# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Parsers for the arena commands of the combined entry point."""

from __future__ import annotations

import argparse
from typing import Any

from . import _references as references
from . import _words as words
from . import check, contract
from .contract import details, device, group, leaf, output_path, team_name


def _subject(parser: contract.Parser, metavar: str, help_text: str) -> None:
    parser.add_argument('target', metavar=metavar, help=help_text)


def _size(parser: contract.Parser, help_text: str) -> None:
    parser.add_argument('--size', choices=('5v5', '11v11'), metavar='NAME', help=help_text)


def _format(parser: contract.Parser, default: str = 'Kaggle open') -> None:
    words.offer(
        parser,
        '--format',
        completion=words.FORMATS,
        metavar='NAME',
        help=f'the submission format, {default} by default, see cuju arena format list',
    )
    check.format_options(parser)


def register(commands: Any) -> None:
    """Adds the arena commands that share the combined entry point."""
    actions = group(commands, 'team', 'manage teams in the registry')
    add = leaf(
        actions,
        'add',
        'register a submission',
        './submission --size 5v5 --selection CB,CM',
        './submissions --each --size 5v5 --format kaggle',
    )
    _subject(add, 'PATH', 'a submission folder, Python file or archive')
    _size(add, 'the team size, the imported size or 11v11 by default')
    _format(add)
    check.verdict_options(add)
    details(
        add,
        'Run format list to find submission formats and format show for options. '
        'Kaggle checks load the agent. Play a match to exercise its actions. '
        'The --full option requires --each. Use a team name without @.',
    )
    add.add_argument('--each', action='store_true', help='register each submission in a directory')
    add.add_argument(
        '--name', metavar='NAME', help='the name of the team, the folder name by default'
    )
    _control(add, adding=True)
    leaf(actions, 'list', 'list the teams in the registry', '--json')
    team_id = references.TEAM
    show = leaf(actions, 'show', 'show a registered team', 'greens')
    details(
        show,
        'The text output includes the folder of a registered submission. ' + references.CHECKPOINT,
    )
    _subject(show, 'TEAM', team_id)
    change = leaf(actions, 'set', 'change a registered team', 'greens --size 11v11')
    _subject(change, 'TEAM', team_id)
    _size(change, 'the team size, the current size by default')
    change.add_argument('--name', metavar='NAME', help='the new name of the team, without @')
    _control(change)
    export = leaf(actions, 'export', 'export a team as a zip archive', 'greens -o greens.zip')
    _subject(export, 'TEAM', team_id)
    output_path(export, help_text='the zip file, the team name with .zip by default')
    export.add_argument(
        '--all-checkpoints',
        action='store_true',
        help='include every checkpoint, the current one by default',
    )
    _subject(leaf(actions, 'remove', 'remove a team from the registry', 'greens'), 'TEAM', team_id)

    actions = group(commands, 'checkpoint', 'manage checkpoints of registered teams')
    add = leaf(
        actions,
        'add',
        'add a checkpoint to a team',
        'greens ./team --step 3000 --label baseline',
    )
    details(
        add,
        'The new files become current and the earlier files go to history. '
        'Label, step and note are optional. The step is supplied by the user. '
        'Use TEAM@ID if a label and latest or step:N match different checkpoints.',
    )
    _subject(add, 'TEAM', team_id)
    add.add_argument(
        'path', metavar='PATH', help='the folder, archive or entry file of the checkpoint'
    )
    add.add_argument(
        '--label', metavar='NAME', help='a label of up to 80 characters, usable in TEAM@LABEL'
    )
    add.add_argument(
        '--step',
        type=int,
        metavar='N',
        help='a training step from zero, usable in TEAM@step:N',
    )
    add.add_argument(
        '--note', metavar='NAME', help='a note of up to 500 characters, displayed in JSON'
    )
    checkpoint_list = leaf(actions, 'list', 'list the checkpoints of a team', 'greens')
    details(
        checkpoint_list,
        'Use TEAM by itself to list all its checkpoints. '
        'The table shows each checkpoint identifier, label and training step.',
    )
    _subject(checkpoint_list, 'TEAM', team_id)

    play = leaf(
        commands,
        'play',
        'play and record a match',
        '--scenario academy_empty_goal_close --right builtin:0.3',
        '--left greens@step:3000 --left-difficulty 0.3',
    )
    details(
        play,
        'Use TEAM, TEAM@CHECKPOINT, builtin, builtin:D or random for either side. '
        'TEAM can be an identifier, unique prefix or exact name ignoring case. '
        + references.CHECKPOINT
        + ' Academy scenarios accept either team size when the requested players exist.',
    )
    for side in ('left', 'right'):
        words.offer(
            play,
            f'--{side}',
            type=team_name,
            completion=words.TEAMS,
            metavar='TEAM',
            help=(
                'the right team, play.right by default, shipped as builtin'
                if side == 'right'
                else 'the left team, builtin by default'
            ),
        )
        play.add_argument(
            f'--{side}-difficulty',
            type=float,
            metavar='DIFFICULTY',
            help=f'the {side} built-in players, from 0 to 1, the scenario value by default',
        )
    words.offer(
        play,
        '--scenario',
        completion=(*words.scenarios(), 'random'),
        metavar='SCENARIO',
        help='the scenario or random, play.scenario by default, shipped as 11_vs_11_kaggle',
    )
    words.offer(
        play,
        '--pool',
        completion=words.TEAMS,
        metavar='TEAM',
        help='comma-separated teams for a draw, all registered teams by default',
    )
    play.add_argument(
        '--same-team',
        action='store_true',
        help='allow a random draw to pick the same team for both sides',
    )
    play.add_argument(
        '--seed', type=int, metavar='N', help='the seed, play.seed by default, drawn when unset'
    )
    device(play)
    details(
        play,
        'Use random for a side to draw a team, or for the scenario to draw a '
        'scenario that fits. Shell completion lists shipped scenario names. '
        'In JSON, left_checkpoint and right_checkpoint give the identifier, label and step '
        'of each registered side. A label names a version. A step records its training progress.',
    )
    _format(play)

    actions = group(commands, 'recording', 'manage recordings in the library')
    listing = leaf(actions, 'list', 'list the recordings in the library', '--json')
    details(
        listing,
        'Use --json to find a group identifier under group.id. '
        'A group contains recordings kept by the same run.',
    )
    listing.add_argument('--group', metavar='ID', help='list the recordings of this group')
    words.offer(
        listing,
        '--date',
        completion=words.DAYS,
        metavar='NAME',
        help='list recordings from today, yesterday or a day as YYYY-MM-DD',
    )
    listing.add_argument('--trash', action='store_true', help='list the recordings in the trash')
    show = leaf(actions, 'show', 'show a recording in the library', 'sample-5v5')
    references.kept_argument(show, 'REC')
    details(
        show, 'Provenance gives the checkpoint digest in text and JSON when the recording kept one.'
    )
    show.description += '\n\nRun cuju arena sample add sample-5v5 to try the example.'
    add = leaf(actions, 'add', 'import recordings and bundles', './match.cjz')
    details(
        add,
        'Imports also accept GRF dumps, Kaggle episodes and action logs. '
        'Use --set import.fidelity=exact to require exact replay.',
    )
    add.add_argument('paths', nargs='+', metavar='PATH', help='the files to import')
    export = leaf(
        actions, 'export', 'export a recording and its attachments', 'sample-5v5 -o match.cjz'
    )
    references.kept_argument(export, 'REC')
    export.description += '\n\nRun cuju arena sample add sample-5v5 to try the example.'
    output_path(export, help_text='the .cjz bundle, the recording identifier plus .cjz by default')
    for verb, summary in (
        ('remove', 'move a recording group to the trash'),
        ('restore', 'restore a recording group from the trash'),
    ):
        action = leaf(actions, verb, summary, '--group regular-season')
        lookup = 'recording list --trash --json' if verb == 'restore' else 'recording list --json'
        details(action, f'Find a group identifier under group.id in cuju arena {lookup}.')
        action.add_argument('--group', metavar='ID', required=True, help='the recording group')

    actions = group(commands, 'sample', 'manage the shipped samples')
    listing = leaf(actions, 'list', 'list the samples and their states', '', '--json')
    details(listing, 'Samples ready to add show as not added in text and missing in JSON.')
    add = leaf(actions, 'add', 'add or restore shipped samples', 'sample-5v5')
    details(add, 'Choose sample-5v5, sample-11v11 or starter-team. See cuju arena sample list.')
    add.add_argument(
        'name',
        nargs='?',
        choices=('sample-11v11', 'sample-5v5', 'starter-team'),
        metavar='NAME',
        help='the sample to add, every sample by default',
    )

    search = leaf(
        commands,
        'search',
        'find moments in the recording library',
        'goal --limit 10',
        "'card -yellow'",
        '--json -- pass -team:left',
        '\'team:"Harbour Rovers"\'',
    )
    details(
        search,
        'The score filter uses the score at the moment. The final filter uses the final score. '
        'The after and before filters require finite match clock times. '
        'The team, match and tournament filters accept exact identifiers and unique prefixes of '
        'at least four characters after the type prefix. Team also accepts an exact registered '
        'name ignoring case. Match and tournament accept last for the newest. '
        'Quote comparison filters in the shell. For a negated term, quote the whole query or '
        'put -- after the options and before the query. For a filter value with spaces, put '
        'double quotes around the value and single quotes around the whole filter.',
    )
    search.epilog = '\n'.join(
        (
            search.epilog,
            '',
            'Filters',
            *(
                f"  '{key}:{example}'" if '<' in example or '>' in example else f'  {key}:{example}'
                for key, example in words.SEARCH.items()
            ),
        )
    )
    search.add_argument(
        'query', nargs='+', metavar='QUERY', help='the terms and filters of the search'
    )
    search.add_argument(
        '--limit', type=int, metavar='N', help='the most results, from 1 to 1000, 50 by default'
    )
    captions = leaf(
        commands,
        'captions',
        'write captions of a recording',
        'sample-5v5',
        'sample-5v5 -o match.vtt',
    )
    references.kept_argument(captions, 'REC', 'recording', allow_file=True)
    captions.description += '\n\nRun cuju arena sample add sample-5v5 to try the example.'
    output_path(captions, help_text='the WebVTT file, standard output by default')

    _tournament(commands)


def _control(parser: contract.Parser, *, adding: bool = False) -> None:
    default = 'one by default' if adding else 'the current count by default'
    parser.add_argument(
        '--players', type=int, metavar='N', help=f'the players the submission controls, {default}'
    )
    parser.add_argument(
        '--assignment',
        choices=('dynamic', 'fixed'),
        metavar='NAME',
        help='dynamic for players nearest the ball or fixed for a selection, dynamic clears it',
    )
    parser.add_argument(
        '--selection',
        metavar='NAME',
        help='zero-based indices or roles such as CB,CM, makes the assignment fixed',
    )
    parser.add_argument(
        '--goalkeeper',
        choices=('agent', 'builtin', 'scenario'),
        metavar='NAME',
        help='agent, builtin or scenario to use the scenario rule',
    )
    parser.add_argument(
        '--action-set',
        choices=('default', 'v2'),
        metavar='NAME',
        help='default for actions 0 to 18, v2 also offers action 19 to delegate',
    )


class _Entrant(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        kind = option_string[2:]
        entry = (kind, values)
        if kind == 'team':
            name, difficulty = contract.team_value(values)
            if name == 'builtin':
                entry = ('builtin', difficulty)
            elif name == 'random':
                entry = ('random', None)
        namespace.entrants = [*(namespace.entrants or []), entry]


def _tournament(commands: Any) -> None:
    from .._tournaments.tournament import PRESETS

    actions = group(commands, 'tournament', 'manage tournaments')
    for verb, summary in (
        ('new', 'create a tournament'),
        ('check', 'preview and check a tournament'),
    ):
        parser = leaf(
            actions,
            verb,
            summary,
            '--team builtin:0.3 --team builtin --length 50',
            '--person Ana --team builtin --scenario 5_vs_5',
        )
        next_step = (
            'The preview saves no tournament. Use tournament new to save it.'
            if verb == 'check'
            else 'Use tournament run with the returned identifier to play it.'
        )
        details(
            parser,
            'Use a TOML or JSON file or name two or more entrants with --team '
            'or --person. Use tournament presets to find the shipped draws. ' + next_step,
        )
        parser.add_argument('file', nargs='?', metavar='FILE', help='the tournament configuration')
        words.offer(
            parser,
            '--tournament-preset',
            completion=tuple(PRESETS),
            metavar='NAME',
            help='the tournament preset, quick_cup by default',
        )
        parser.add_argument('--name', metavar='NAME', help='the tournament name')
        parser.add_argument('--seed', type=int, metavar='N', help='the seed, drawn when absent')
        parser.set_defaults(entrants=None)
        for flag, metavar, help_text in (
            (
                '--team',
                'TEAM',
                references.ENTRANT + ', may repeat',
            ),
            ('--person', 'NAME', 'a person by name, may repeat'),
            ('--as', 'NAME', 'a display name for the immediately preceding entrant, may repeat'),
            (
                '--difficulty',
                'DIFFICULTY',
                'the built-in player difficulty from 0 to 1, or scenario before entrants '
                'and standard after an entrant, may repeat',
            ),
        ):
            action = parser.add_argument(
                flag,
                dest='entrants',
                action=_Entrant,
                metavar=metavar,
                type=team_name if flag == '--team' else str,
                help=help_text,
            )
            if flag == '--team':
                action.completion = words.TEAMS
            elif flag == '--difficulty':
                action.completion = ('scenario', 'standard')
        parser.add_argument(
            '--seeding',
            choices=('random', 'manual', 'rating'),
            metavar='NAME',
            help='the rule for the first draw, random by default',
        )
        parser.add_argument(
            '--evidence',
            choices=('refuse', 'collect', 'proceed'),
            metavar='NAME',
            help='refuse, collect or proceed when rating evidence is lacking, refuse by default',
        )
        words.offer(
            parser,
            '--scenario',
            completion=words.scenarios(),
            metavar='SCENARIO',
            help='the scenario of every stage, use academy_empty_goal_close for a short match',
        )
        for flag, help_text in (
            (
                '--length',
                'the GRF duration N, from 50 to 5000, plays N plus 1 steps at the time limit',
            ),
            ('--concurrency', 'the matches played at once, from 1 to 8'),
            ('--rounds', 'the rounds of each Swiss or ladder stage'),
        ):
            parser.add_argument(flag, type=int, metavar='N', help=help_text)
        _format(parser)
        parser.add_argument(
            '--preflight',
            action='store_true',
            help='load and check submissions, during a preview when requested',
        )

    for verb, summary in (
        ('list', 'list the tournaments'),
        ('presets', 'list the tournament presets'),
        ('ratings', 'list the tournament ratings'),
    ):
        listing = leaf(actions, verb, summary, '--json')
        if verb == 'ratings':
            details(
                listing,
                'Lists each model using its newest scenario result. Different checkpoints of '
                'a team have separate rows. In JSON, models holds those rows and ratings gives '
                'the newest result by team. Use rating table for a single scenario.',
            )
    state_help = {
        'show': 'Shows the draw, match keys and progress.',
        'run': 'Plays the saved draw in this process and retries stopped matches.',
        'resume': 'Queues a stopped draw or retries failed matches in a running draw.',
        'cancel': 'Stops a running or stopped draw. Finished draws can be removed.',
        'remove': 'Removes a finished, stopped or cancelled draw, keeping its recordings.',
    }
    for verb, summary in (
        ('show', 'show a tournament'),
        ('run', 'run a tournament'),
        ('resume', 'resume a tournament'),
        ('cancel', 'cancel a tournament'),
        ('remove', 'remove a tournament'),
    ):
        action = leaf(actions, verb, summary, 'last')
        details(action, state_help[verb])
        references.kept_argument(action, 'ID')
    for verb, summary, example in (
        ('forfeit', 'award a match by forfeit', 'cup-1 --match group/1 --winner home'),
        ('replay', 'replay a kept tournament match', 'cup-1 --match group/1'),
    ):
        parser = leaf(actions, verb, summary, example)
        references.kept_argument(parser, 'ID')
        parser.add_argument(
            '--match', required=True, metavar='ID', help='the key from tournament show'
        )
        if verb == 'forfeit':
            parser.add_argument(
                '--winner',
                required=True,
                choices=('home', 'away'),
                metavar='NAME',
                help='home or away, as shown in the fixture',
            )
        else:
            device(parser)
    preflight = leaf(
        actions, 'preflight', 'check a team before a tournament', 'greens --format strict'
    )
    details(
        preflight,
        'The format determines whether its check plays matches. Kaggle checks load the agent. '
        + references.CHECKPOINT,
    )
    _subject(preflight, 'TEAM', references.TEAM)
    _format(preflight, 'the configured tournament format')
    words.offer(
        preflight,
        '--scenario',
        completion=words.scenarios(),
        metavar='SCENARIO',
        help='the scenario of the check',
    )
    export = leaf(actions, 'export', 'export the tournament results', 'cup-1 -o ./results')
    references.kept_argument(export, 'ID')
    output_path(
        export,
        'DIR',
        help_text='the directory for CSV and JSON results, the current directory by default',
    )
    legs = leaf(actions, 'legs', 'add legs to the last stage', 'cup-1 --legs 2')
    details(
        legs,
        'The last stage must be a league or groups. A finished tournament '
        'runs again for the added matches.',
    )
    references.kept_argument(legs, 'ID')
    legs.add_argument('--legs', required=True, type=int, metavar='N', help='the legs to add')
