# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The messages of the server that the page shows to people, each with an id and English text.

An answer or an entry that the page shows carries the English text in `error`, as it always has,
and beside it the id of its message in `error_id` and the values of its placeholders in
`error_values`, so the page shows the text in its own language. The command line and every
English answer read the same text as before. A template names its placeholders in braces, such
as `{scenario}`, as the language files of the page do, and Python fills it the way the page does,
with every value as text, so both read the same.

`TABLE` holds the ids of this package with their English templates, and a package that has
messages of its own, such as the one that shows the page, adds its table with `register`.
`MESSAGES` holds the ids of every table, and the part `server` of the language files holds the
same ids, which `compare` checks. A template whose words agree with a number is
an object of plural forms, keyed `one` and `other` and by exact counts such as `=0`, as in the
language files, and the value `count` picks the form. A template may mark words as the language
files do, **strong** and `code`, which the page shows as such, while `text` reads them without the
marks, so the command line and English answers read plain text.
"""

from __future__ import annotations

import importlib
import re
import sys
from typing import Any

from cuju.errors import ArgumentError

# A placeholder of a template, as the page's own runtime, web/js/core/i18n.js, reads it. Any other
# brace, such as that of `{"colors": [...]}`, is text.
_PLACEHOLDER = re.compile(r'\{([A-Za-z0-9_]+)\}')
# The marks of a template, strong emphasis in double asterisks and code in backticks, as the page
# reads them. A mark that does not close is text.
_MARK = re.compile(r'\*\*(.+?)\*\*|`([^`]+)`')

TABLE: dict[str, str | dict[str, str]] = {
    'server.benchmark.exit': 'the run ended with exit code {code}',
    'server.benchmark.exit_detail': 'the run ended with exit code {code}, {detail}',
    'server.benchmark.fields': (
        'expected teams, tasks, keep_recordings and workers, found {unknown}'
    ),
    'server.benchmark.keep_recordings': 'expected keep_recordings as true or false, found {value}',
    'server.benchmark.tasks': 'expected a list of distinct academy tasks, found {tasks}',
    'server.benchmark.team': 'expected a team of the registry, found {team}',
    'server.benchmark.teams': 'expected a list of distinct teams of the registry, found {teams}',
    'server.benchmark.unknown_task': 'expected a task, one of {tasks}, found {identifier}',
    'server.benchmark.workers': 'expected from 1 to {most} runs at once, found {workers}',
    'server.benchmark.refused': 'the format refused {team}, {reason}',
    'server.draw.left': (
        'expected a team of the pool that can play the left side of {scenario}, found none'
    ),
    'server.draw.pool': 'expected the pool as a list of teams of the registry, found {pool}',
    'server.draw.right': (
        'expected a team of the pool that can play the right side of {scenario}, found none'
    ),
    'server.draw.same_team': 'expected same_team as true or false, found {value}',
    'server.draw.scenario': (
        'expected a scenario that a team of the pool can play on each side, found none'
    ),
    'server.json_object': 'expected a JSON object',
    'server.library.favorite': 'expected favorite as true or false, found {favorite}',
    'server.library.folder_size': (
        'expected a folder of at most 8 names of 64 characters, found {folder}'
    ),
    'server.library.folder_text': 'expected a folder as text, found {folder}',
    'server.library.notes': 'expected notes as text of at most ten thousand characters',
    'server.library.samples': 'expected a sample from {shipped}, found {unknown}',
    'server.library.tags': 'expected up to twenty tags, each of one to forty characters',
    'server.library.title': 'expected a title with at least one character',
    'server.library.unknown': 'expected a recording of the library, found {identifier}',
    'server.library.statistics': (
        'expected the statistics of a recording that the index read, found none for {identifier}'
    ),
    'server.library.attachment_kind': (
        'expected an attachment kind of lowercase letters, digits and dashes, found {kind}'
    ),
    'server.library.attachment_file': 'expected an attachment of cuju in {path}, found other bytes',
    'server.library.attachment_key': (
        'expected an attachment of the recording {expected} in {path}, found one of {found}'
    ),
    'server.library.attachment_frames': (
        'expected an attachment that counts the frames of its recording in {path}'
    ),
    'server.library.attachment_missing': (
        'expected an attachment {kind} of the recording, found none'
    ),
    'server.library.bundle': (
        'expected a bundle of cuju with recording.cjr and bundle.json in {path}'
    ),
    # Names and titles that cuju makes, which travel as labels.
    # Names and titles that cuju makes, which travel as labels.
    'server.names.demo': 'Demo, the built-in AI against itself',
    'server.names.live': '{scenario}, environment {environment}, process {process}',
    'server.names.person_with': '{person} with {team}',
    'server.names.player': 'Player {number}',
    'server.names.you': 'You',
    'server.names.against': '{left} against {right}',
    'server.names.benchmark_attempt': '{task}, attempt {attempt}, {competitor}',
    'server.names.benchmark_run': '{task}, {competitor}',
    'server.names.builtin': "GRF's built-in AI",
    'server.names.left': 'Left',
    'server.names.random': 'Random',
    'server.names.recorder': '{scenario}, environment {environment}',
    'server.names.right': 'Right',
    'server.names.run_attempts': '{value} of {attempts}',
    'server.names.run_playing': 'under way, {played} of {attempts} played',
    'server.names.run_stopped': 'stopped after {played} of {attempts}',
    'server.names.sample_11v11': 'Sample match, 11 vs 11',
    'server.names.sample_5v5': 'Sample match, 5 vs 5',
    'server.names.benchmark_of': 'Benchmark of {competitor}',
    'server.names.benchmark_score': 'score {score}',
    'server.names.evaluation_match': '{policy} against {opponent}, match {number}',
    'server.names.evaluation_of': 'Evaluation of {policy}',
    'server.names.evaluation_result': '{wins} won, {draws} drawn, {losses} lost',
    'server.names.checkpoint': '{team} {checkpoint}',
    'server.names.checkpoint_step': '{team} at step {step}',
    'server.play.scenario': 'expected a shipped scenario, found {scenario}',
    'server.play.difficulty_twice': (
        'expected one of difficulty and ability, which read the same, found difficulty '
        '{difficulty} and ability {ability}'
    ),
    'server.policy.action_set': 'expected the v2 action set to hand players to the AI',
    'server.policy.error': 'the policy failed, {reason}',
    'server.policy.error_action': {
        'one': (
            'the policy failed, it answered the action {value}, outside its action set of {count} '
            'action, 0 to {last}'
        ),
        'other': (
            'the policy failed, it answered the action {value}, outside its action set of {count} '
            'actions, 0 to {last}'
        ),
    },
    'server.policy.failed': 'the policy of {player} failed, {reason}',
    'server.policy.failed_action': {
        'one': (
            'the policy of {player} failed, it answered the action {value}, outside its action set '
            'of {count} action, 0 to {last}'
        ),
        'other': (
            'the policy of {player} failed, it answered the action {value}, outside its action set '
            'of {count} actions, 0 to {last}'
        ),
    },
    'server.policy.load': {
        'one': 'the policies did not load within {count} second',
        'other': 'the policies did not load within {count} seconds',
    },
    'server.policy.silent': {
        'one': 'the policy has not answered for {count} second',
        'other': 'the policy has not answered for {count} seconds',
    },
    'server.preflight.failed': 'the check of the team failed, {reason}',
    'server.preflight.left_out': (
        'the pre-flight left out the teams that failed it, and the tournament cannot be made '
        'with the entrants that remain, {error}'
    ),
    'server.preflight.timeout': {
        'one': 'the check of the team took longer than {count} minute',
        'other': 'the check of the team took longer than {count} minutes',
    },
    'server.requests.cancelled': 'cancelled',
    'server.requests.exit': 'the match ended with exit code {code}',
    'server.samples.team': 'expected a sample team from {teams}, found {name}',
    'server.samples.unknown': 'expected samples from {every}, found {unknown}',
    'server.scenario': 'expected a shipped scenario, found {scenario}',
    'server.reference.ambiguous': 'expected one match for {reference}, found {count}',
    'server.search.date': 'expected {key}: as a date such as 2026-11-10, found {value}',
    'server.search.filter': 'expected a filter from {filters}, found {key}:',
    'server.search.kind': 'expected a kind of moment such as `goal` or `corner`, found {name}',
    'server.search.limit_range': 'expected a limit from 1 to {most}, found {limit}',
    'server.search.limit_text': 'expected `limit:` as a whole number, found {value}',
    'server.search.minutes': (
        'expected `{key}:` as a finite match clock time, such as 60, 60:30 or 90s, found {value}'
    ),
    'server.search.final_score': 'expected a final score such as 2-1, found {value}',
    'server.search.player': 'expected a shirt number or roster name, found {value}',
    'server.search.score': (
        'expected `score:` as leading, level, trailing or a score such as 2-1, found {value}'
    ),
    'server.search.source': (
        'expected `source:` as requested, game, benchmark, recorder, live, tournament or unknown, '
        'found {value}'
    ),
    'server.search.target': 'expected a recording of the library or a `.cjr` file, found {target}',
    'server.search.unknown_filter': 'Unknown filter `{key}:`.',
    'server.search.signal_negated': (
        'expected `{term}` without the minus sign, since a signal term finds moments rather than '
        'leaving them out'
    ),
    'server.search.value_name': (
        'expected `value-drop:` with the name of a value, such as `value-drop:value`, found {value}'
    ),
    'server.search.probability': (
        'expected `surprise:` with a probability from 0 to 1, such as `surprise:0.05`, found '
        '{value}'
    ),
    'server.search.bound': (
        'expected `{key}:` with a comparison, such as `{key}:>0.5`, found {value}'
    ),
    'server.search.annotation': (
        'expected `ann:` with a name and a comparison, such as `ann:value<-0.5`, found {value}'
    ),
    'server.strict.both': '{home}, and {away}',
    'server.strict.call_time': {
        'one': '{player} did not answer action call {call} within {count} second',
        'other': '{player} did not answer action call {call} within {count} seconds',
    },
    'server.strict.changed': 'the files of {player} changed since its pre-flight',
    'server.strict.crash': '{player} failed in an action call, {reason}',
    'server.strict.deterministic': (
        '{player} answered differently when the same steps played again from the same seed'
    ),
    'server.strict.first_time': {
        'one': '{player} did not answer its first action call within {count} second',
        'other': '{player} did not answer its first action call within {count} seconds',
    },
    'server.strict.folder_size': (
        '{player} holds {size} MiB in its submission, above the limit of {limit} MiB'
    ),
    'server.strict.invalid_count': '{player} answered a wrong count of actions, {reason}',
    'server.strict.invalid_range': '{player} answered an action outside its action set, {reason}',
    'server.strict.invalid_type': '{player} answered an action that is no whole number, {reason}',
    'server.strict.load': '{player} failed to load, {reason}',
    'server.strict.load_time': {
        'one': '{player} did not load within {count} second',
        'other': '{player} did not load within {count} seconds',
    },
    'server.strict.mean': (
        '{player} took {mean} ms for an action call on average, above the limit of {limit} ms'
    ),
    'server.strict.memory': '{player} used {used} MiB of memory, above the limit of {limit} MiB',
    'server.strict.memory_out': '{player} ran out of its memory of {limit} MiB',
    'server.strict.process': '{player} started another process, which strict/1 refuses',
    'server.strict.reset': '{player} failed in its reset, {reason}',
    'server.strict.reset_time': {
        'one': '{player} did not reset within {count} second',
        'other': '{player} did not reset within {count} seconds',
    },
    'server.strict.threads': (
        '{player} ran {found} threads, above the {limit} that its process may run'
    ),
    'server.strict.total_time': (
        '{player} computed for {total} seconds in all, above the limit of {limit} seconds for a '
        'match'
    ),
    'server.strict.warning_action': (
        '{player} answered the action {action} in {share}% of its actions.'
    ),
    'server.strict.warning_deterministic': (
        '{player} answered differently when the same steps played again from the same seed.'
    ),
    'server.strict.warning_setting': 'The setting {setting} of {file} is unknown to cuju render.',
    'server.strict.link': (
        '{player} holds a link in its submission, {path}, which strict/1 refuses, since the '
        'size and the digest of a team leave links out'
    ),
    'server.teams.ability': 'expected an ability from 0 to 1, found {value}',
    'server.teams.ability_pair': 'expected a pair of abilities, left and right, found {ability}',
    'server.teams.agent_function': 'expected an agent function in the submission {identifier}',
    'server.teams.archive.device': (
        'expected archive members whose names open no device on Windows and end in no dot or '
        'space, found {member}'
    ),
    'server.teams.archive.limits': (
        'expected an archive of at most 10,000 files and 500 MiB unpacked'
    ),
    'server.teams.archive.outside': 'expected archive members inside the archive, found {member}',
    'server.teams.archive.plain': 'expected plain files in the archive, found {member}',
    'server.teams.archive.tar': 'expected a readable .tar.gz archive, found {error}',
    'server.teams.archive.zip': 'expected a readable .zip archive, found {error}',
    'server.teams.beside_count': {
        '=0': (
            '{team} cannot play beside a person in {scenario}, which has no controllable player on '
            'that side'
        ),
        'one': (
            '{team} cannot play beside a person in {scenario}, which has {count} controllable '
            'player on that side'
        ),
        'other': (
            '{team} cannot play beside a person in {scenario}, which has {count} controllable '
            'players on that side'
        ),
    },
    'server.teams.beside_dynamic': 'expected dynamic assignment for {team} beside a person',
    'server.teams.cannot_play': '{team} cannot play {scenario}, {reason}',
    'server.teams.choice': 'expected {name} as one of {choices}, found {value}',
    'server.teams.control.fields': (
        'expected players, assignment, selection, goalkeeper and action_set'
    ),
    'server.teams.control.goalkeeper_builtin': (
        'scenario makes the goalkeeper uncontrollable, choose scenario'
    ),
    'server.teams.control.goalkeeper_fixed': (
        'expected fixed assignment for a goalkeeper handed to the AI'
    ),
    'server.teams.control.goalkeeper_outside': (
        'expected the goalkeeper outside the fixed selection'
    ),
    'server.teams.control.goalkeeper_scenario': (
        'scenario has a controllable goalkeeper, choose agent or builtin'
    ),
    'server.teams.control.goalkeeper_selected': 'expected the goalkeeper in the fixed selection',
    'server.teams.control.goalkeeper_uncontrollable': (
        'scenario makes the goalkeeper uncontrollable'
    ),
    'server.teams.control.legacy_goalkeeper': (
        'expected 10 players for the earlier goalkeeper setting'
    ),
    'server.teams.control.none_present': 'scenario holds none of the selected players',
    'server.teams.control.players': 'expected from 1 to {most} controlled players',
    'server.teams.control.select_player': 'scenario cannot select player {player}',
    'server.teams.control.selection_count': 'expected one selection for each controlled player',
    'server.teams.control.selection_distinct': 'expected distinct player indices or GRF role names',
    'server.teams.control.selection_empty': 'expected an empty selection for dynamic assignment',
    'server.teams.control.selection_list': 'expected a list of player indices or roles',
    'server.teams.control.too_few': {
        '=0': 'scenario has no controllable player',
        'one': 'scenario has only {count} controllable player',
        'other': 'scenario has only {count} controllable players',
    },
    'server.teams.duplicate_builtin': (
        'expected a registered submission to duplicate, found the built-in AI'
    ),
    'server.teams.field.action_set': 'action set',
    'server.teams.field.assignment': '`assignment`',
    'server.teams.field.format': 'the format',
    'server.teams.field.goalkeeper': '`goalkeeper`',
    'server.teams.folder.files': 'expected the files that the index of the folder lists',
    'server.teams.folder.index': 'expected the index of a submission folder, found {error}',
    'server.teams.folder.index_list': (
        'expected the index of a submission folder, a list of its files'
    ),
    'server.teams.folder.short': 'expected the files of a submission folder, found too few bytes',
    'server.teams.name': 'expected a team name with at least one character',
    'server.teams.name_separator': 'expected a team name without @, found {name}',
    'server.teams.name_reserved': 'expected a name outside the built-in identifiers, found {name}',
    'server.teams.player': 'expected player {index} as a dictionary, found {value}',
    'server.teams.player_entry': 'expected an entry of a player, found {key}',
    'server.teams.player_name': 'expected a name of at most 24 characters, found {name}',
    'server.teams.players': {
        'one': 'expected at most {count} player entry, found {value}',
        'other': 'expected at most {count} player entries, found {value}',
    },
    'server.teams.scenario_format': 'expected a scenario of {format} for {team}, found {scenario}',
    'server.teams.shirt_number': 'expected a shirt number from 0 to 99, found {number}',
    'server.teams.submission.kind': (
        'expected a submission folder, a .py file or a .tar.gz or .zip archive, found {filename}'
    ),
    'server.teams.submission.main_folder': (
        'expected {main} in the folder {folder}, as Kaggle requires'
    ),
    'server.teams.submission.main_top': (
        'expected {main} at the top of the submission, as Kaggle requires'
    ),
    'server.teams.submission.size': 'expected a submission of at most 100 MiB, found {size} bytes',
    'server.teams.submission.unpack': 'expected a submission whose files unpack, found {error}',
    'server.teams.unknown': 'expected a team of the registry, found {identifier}',
    'server.teams.unknown_kaggle': 'expected a Kaggle team of the registry, found {identifier}',
    'server.teams.trust': (
        'A submission is Python that runs with your permissions. Add only teams you trust.'
    ),
    'server.teams.scenario': 'expected a home scenario that the team can play, found {scenario}',
    'server.teams.number_taken': (
        'expected a shirt number that no other player wears, but player {player} wears {number}'
    ),
    'server.tournament.ability': (
        'expected the ability of entrant {entrant} from 0 to 1, found {ability}'
    ),
    'server.tournament.best_places': 'stage {stage} names best places without a position',
    'server.tournament.bracket_byes': {
        'one': (
            'a bracket of {size} with byes needs from {lowest} to {size} entrants, and {count} '
            'enters {where}'
        ),
        'other': (
            'a bracket of {size} with byes needs from {lowest} to {size} entrants, and {count} '
            'enter {where}'
        ),
    },
    'server.tournament.bracket_exact': {
        'one': 'a bracket of {size} needs exactly {size} entrants, and {count} enters {where}',
        'other': 'a bracket of {size} needs exactly {size} entrants, and {count} enter {where}',
    },
    'server.tournament.bracket_play_in': {
        'one': (
            'a bracket of {size} with a play-in round needs from {size} to {highest} entrants, and '
            '{count} enters {where}'
        ),
        'other': (
            'a bracket of {size} with a play-in round needs from {size} to {highest} entrants, and '
            '{count} enter {where}'
        ),
    },
    'server.tournament.bracket_power': 'expected a bracket size of {where} that is a power of two',
    'server.tournament.cancel_state': (
        'expected a tournament that runs or stopped, found it {state}'
    ),
    'server.tournament.cannot_play': '{entrant} cannot play {scenario} in stage {stage}, {reason}',
    'server.tournament.cannot_play_decider': (
        '{entrant} cannot play {scenario}, the scenario of the deciding matches of stage {stage}, '
        '{reason}, so choose another scenario for them or settle drawn ties without them'
    ),
    'server.tournament.cards': 'expected cards as two pairs of counts, found {cards}',
    'server.tournament.choice': 'expected {name} from {choices}, found {value}',
    'server.tournament.count': 'expected {name} from {lowest} to {highest}, found {value}',
    'server.tournament.count_exact': 'expected {name} to be {lowest}, found {value}',
    'server.tournament.defaults': 'expected the defaults as an object',
    'server.tournament.description': 'expected a description of at most 400 characters',
    'server.tournament.double_byes': 'a double elimination bracket fills with byes, in {where}',
    'server.tournament.ended': 'expected a tournament that has ended, cancel it first',
    'server.tournament.entrant_fields': 'entrant {entrant} has unknown fields {fields}',
    'server.tournament.entrant_object': 'expected entrant {entrant} as an object, found {value}',
    'server.tournament.entrant_team': (
        'expected a registered team for entrant {entrant}, found {team}, the built-in AI '
        'enters with the kind builtin'
    ),
    'server.tournament.entrants': 'expected from 2 to {most} entrants, found {count}',
    'server.tournament.field.added_legs': 'the legs to add to {where}',
    'server.tournament.field.advance': 'the entrants that advance from {where}',
    'server.tournament.field.best_count': 'the best places of position {position} of {where}',
    'server.tournament.field.best_position': 'the position of the best places of {where}',
    'server.tournament.field.bracket': 'the bracket of {where}',
    'server.tournament.field.bye_points': 'the bye points of {where}',
    'server.tournament.field.concurrency': 'the matches at once',
    'server.tournament.field.cycles': 'the cycles of {where}',
    'server.tournament.field.decider_length': 'the decider length of {where}',
    'server.tournament.field.draws': 'the draws of {where}',
    'server.tournament.field.each': 'each of {name}',
    'server.tournament.field.entrant_kind': 'the kind of entrant {number}',
    'server.tournament.field.entrant_name': 'an entrant name',
    'server.tournament.field.final_legs': 'the legs of the final of {where}',
    'server.tournament.field.forfeit_score': 'the forfeit score',
    'server.tournament.field.format': 'the format of {where}',
    'server.tournament.field.grand_final_reset': 'grand final reset',
    'server.tournament.field.group_size': 'the group size of {where}',
    'server.tournament.field.groups': 'the number of groups of {where}',
    'server.tournament.field.ladder_rounds': 'the rounds of the ladder of {where}',
    'server.tournament.field.laps': 'the laps of {where}',
    'server.tournament.field.legs': 'the legs of {where}',
    'server.tournament.field.length': 'the length of {where}',
    'server.tournament.field.name': 'a name',
    'server.tournament.field.points_draw': 'the points of a draw in {where}',
    'server.tournament.field.points_loss': 'the points of a loss in {where}',
    'server.tournament.field.points_win': 'the points of a win in {where}',
    'server.tournament.field.pots': 'the pots of {where}',
    'server.tournament.field.preset_title': 'a preset title',
    'server.tournament.field.qualifiers': 'the qualifiers of group {group} of {where}',
    'server.tournament.field.rating_scale': 'the scale of the given ratings',
    'server.tournament.field.replays': 'the replays of {where}',
    'server.tournament.field.seed': 'the seed',
    'server.tournament.field.seeding': 'the seeding of {where}',
    'server.tournament.field.settle': 'the settlements of {where}',
    'server.tournament.field.swiss_rounds': (
        'the rounds of the Swiss {where}, which pairs {entrants} entrants without rematches,'
    ),
    'server.tournament.field.third_place': 'third place',
    'server.tournament.field.tiebreakers': 'the tiebreakers of {where}',
    'server.tournament.field.tournament_seeding': 'the seeding',
    'server.tournament.flag': 'expected {setting} of {where} as true or false',
    'server.tournament.foreign': (
        'stopped, since this install did not write its file, resume it to play its matches'
    ),
    'server.tournament.group_minimum': {
        'one': (
            'every group of {where} needs at least 2 entrants, and {count} entrant makes groups of '
            '{sizes}'
        ),
        'other': (
            'every group of {where} needs at least 2 entrants, and {count} entrants make groups of '
            '{sizes}'
        ),
    },
    'server.tournament.group_sizes': 'expected group sizes as a list of counts in {where}',
    'server.tournament.group_total': {
        'one': 'the group sizes of {where} hold {total} entrants, and {count} enters it',
        'other': 'the group sizes of {where} hold {total} entrants, and {count} enter it',
    },
    'server.tournament.groups_advance': (
        'the groups of {where} send {advance} on, by qualifiers and best places'
    ),
    'server.tournament.groups_few': 'the groups of {where} send fewer than 2 entrants on',
    'server.tournament.include': (
        'expected the added entrants as a list of at most {most} entrants, none of them a person'
    ),
    'server.tournament.knockout_advance': {
        'one': 'the knockout of {where} stops at {count}, so {count} advances',
        'other': 'the knockout of {where} stops at {count}, so {count} advance',
    },
    'server.tournament.knockout_stop': (
        'expected the knockout of {where} to stop at a power of two below {size}'
    ),
    'server.tournament.label.builtin': 'Built-in AI',
    'server.tournament.label.builtin_ability': 'Built-in AI {ability}',
    'server.tournament.label.challenge': 'Challenge {number}',
    'server.tournament.label.challenges': 'Challenges',
    'server.tournament.label.decider': '{match}, decider',
    'server.tournament.label.final': 'Final',
    'server.tournament.label.grand_final': 'Grand final',
    'server.tournament.label.grand_final_reset': 'Grand final, deciding match',
    'server.tournament.label.group': 'Group {group}',
    'server.tournament.label.group_matchday': 'Group {group}, matchday {number}',
    'server.tournament.label.ladder_tie': 'Round {round}, places {upper} and {lower}',
    'server.tournament.label.lap': 'Lap {number}',
    'server.tournament.label.leg': '{match}, leg {number}',
    'server.tournament.label.loser_of': 'Loser of {match}',
    'server.tournament.label.losers_final': 'Losers final',
    'server.tournament.label.losers_round': 'Losers round {number}',
    'server.tournament.label.matchday': 'Matchday {number}',
    'server.tournament.label.numbered': '{name} {number}',
    'server.tournament.label.play_in': 'Play-in {number}',
    'server.tournament.label.play_in_round': 'Play-in round',
    'server.tournament.label.quarter_final': 'Quarter-final {number}',
    'server.tournament.label.quarter_finals': 'Quarter-finals',
    'server.tournament.label.random': 'Random',
    'server.tournament.label.replay': '{match}, replay',
    'server.tournament.label.round': 'Round {number}',
    'server.tournament.label.round_of': 'Round of {count}',
    'server.tournament.label.round_of_tie': 'Round of {count}, tie {number}',
    'server.tournament.label.semi_final': 'Semi-final {number}',
    'server.tournament.label.semi_finals': 'Semi-finals',
    'server.tournament.label.stage.cup': 'Cup',
    'server.tournament.label.stage.double_elimination': 'Double elimination',
    'server.tournament.label.stage.gauntlet': 'Gauntlet',
    'server.tournament.label.stage.group_stage': 'Group stage',
    'server.tournament.label.stage.groups': 'Groups',
    'server.tournament.label.stage.king_of_the_hill': 'King of the hill',
    'server.tournament.label.stage.knockout': 'Knockout',
    'server.tournament.label.stage.knockout_stage': 'Knockout stage',
    'server.tournament.label.stage.ladder': 'Ladder',
    'server.tournament.label.stage.league': 'League',
    'server.tournament.label.stage.league_phase': 'League phase',
    'server.tournament.label.stage.playoffs': 'Playoffs',
    'server.tournament.label.stage.regular_season': 'Regular season',
    'server.tournament.label.stage.round_robin': 'Round robin',
    'server.tournament.label.stage.swiss': 'Swiss',
    'server.tournament.label.step': 'Step {number}',
    'server.tournament.label.third_place': 'Third place match',
    'server.tournament.label.tie': '{round}, tie {number}',
    'server.tournament.label.title': '{tournament}, {match}, {home} against {away}',
    'server.tournament.label.tournament': 'Tournament',
    'server.tournament.label.winner_of': 'Winner of {match}',
    'server.tournament.label.winners_final': 'Winners final',
    'server.tournament.label.winners_round': 'Winners round {number}',
    'server.tournament.legs_cancelled': 'a cancelled tournament takes no more legs',
    'server.tournament.legs_format': (
        'expected the last stage, {where}, to be a league or groups to take more legs'
    ),
    'server.tournament.legs_most': 'the last stage, {where}, already plays the most legs, {most}',
    'server.tournament.length': (
        'expected a length from {lowest} to {highest} steps, found {length}'
    ),
    'server.tournament.list': 'expected {name} as a list without repeats, found {value}',
    'server.tournament.match_failed': '{label} failed, {error}',
    'server.tournament.match_failed_more': {
        'one': '{label} failed, {error}, and {count} more match failed',
        'other': '{label} failed, {error}, and {count} more matches failed',
    },
    'server.tournament.match_failed_one_more': '{label} failed, {error}, and 1 more match failed',
    'server.tournament.match_key': 'expected a match of the tournament still to play, found {key}',
    'server.tournament.mixed_sizes': (
        '{five} is a team of 5v5 and {eleven} a team of 11v11, and {scenario} of stage {stage} '
        'fits one size alone, so enter teams of one size or choose {fitting}, which fits both '
        'sizes and the players each team controls'
    ),
    'server.tournament.mixed_sizes_none': (
        '{five} is a team of 5v5 and {eleven} a team of 11v11, and {scenario} of stage {stage} '
        'fits one size alone, and no scenario fits both sizes and the players each team '
        'controls, so enter teams of one size'
    ),
    'server.tournament.name_taken': (
        'expected a name of its own for entrant {entrant}, '
        'found {name}, the name of entrant {other}'
    ),
    'server.tournament.no_frame': 'the recording {identifier} holds no frame',
    'server.tournament.no_replay': 'the match {key} kept no seed and actions to replay',
    'server.tournament.no_result': 'the match gave no result',
    'server.tournament.note.early': (
        'The game ended before full time, so the match waits to be played again.'
    ),
    'server.tournament.note.failed': (
        'The game stopped, {error}. Award the match to either side, or play it again.'
    ),
    'server.tournament.note.gone': 'The game of this match ended without a result.',
    'server.tournament.note.unreadable': 'The result could not be read, {error}',
    'server.tournament.object': 'expected the tournament as an object',
    'server.tournament.other_process': 'the tournament belongs to another process',
    'server.tournament.other_process_change': (
        'the tournament runs in another process, {owner}, which can change it'
    ),
    'server.tournament.other_process_runs': 'the tournament runs in another process, {owner}',
    'server.tournament.other_process_silent': (
        'the process {owner} that runs the tournament did not answer, send the change to its '
        'server as {route}'
    ),
    'server.tournament.people_page': 'the matches of people play on the page of the server',
    'server.tournament.person_match': 'expected a match of a person',
    'server.tournament.person_names': 'expected a name for each person',
    'server.tournament.playing': 'the match is playing now',
    'server.tournament.points': (
        'expected points for a win, draw and loss in {where}, found {points}'
    ),
    'server.tournament.preflight': 'expected the pre-flight as true or false, found {value}',
    'server.tournament.preset': 'expected a preset from {presets}, found {name}',
    'server.tournament.preset_name': (
        'expected a preset name of small letters, digits, dashes and underscores that names no '
        'shipped preset, found {name}'
    ),
    'server.tournament.preset_object': 'expected a preset as an object',
    'server.tournament.preset_settings': 'the preset has unknown settings {settings}',
    'server.tournament.qualifiers': (
        'expected the qualifiers of stage {stage} as a count or one count a group'
    ),
    'server.tournament.rating': 'expected a rating from 0 to 10000, found {rating}',
    'server.tournament.recording': 'expected the recording of a result as text, found {match}',
    'server.tournament.refused': 'the process that runs it refused the change',
    'server.tournament.result': 'expected a result with a score, found {result}',
    'server.tournament.running': 'expected a running tournament, found it {state}',
    'server.tournament.score': 'expected a score of two counts, found {score}',
    'server.tournament.settings': 'the tournament has unknown settings {settings}',
    'server.tournament.stage_ability': 'expected the ability of {where} from 0 to 1',
    'server.tournament.stage_entrants': {
        '=0': '{where} needs at least 2 entrants, and none reaches it',
        'one': '{where} needs at least 2 entrants, and {count} reaches it',
        'other': '{where} needs at least 2 entrants, and {count} reach it',
    },
    'server.tournament.stage_name': 'expected the name of {where} as text',
    'server.tournament.stage_object': 'expected stage {stage} as an object, found {value}',
    'server.tournament.stage_scenario': (
        'expected a shipped scenario for stage {stage}, found {scenario}'
    ),
    'server.tournament.stage_settings': 'stage {stage} has unknown settings {settings}',
    'server.tournament.stages': 'expected from 1 to {most} stages',
    'server.tournament.standard_ability': (
        'expected the standard ability from 0 to 1, found {ability}'
    ),
    'server.tournament.stopped': 'expected a stopped tournament, found it {state}',
    'server.tournament.swiss': 'no Swiss pairing without a rematch exists for this round',
    'server.tournament.team_format': 'it is a team of {format}',
    'server.tournament.teammates': 'expected registered teammates, found {mates}',
    'server.tournament.text': 'expected {name} as text, found {value}',
    'server.tournament.text_length': 'expected {name} of 1 to {most} characters, found {value}',
    'server.tournament.unknown_identifier': 'expected a tournament, found {identifier}',
    'server.tournament.waiting': 'waiting for cuju arena tournament run',
    'server.tournament.watched': 'expected the watched matches as a list of up to 10000 keys',
    'server.tournament.where': 'stage {number}',
    'server.tournament.winner': 'expected the winning side as home or away, found {side}',
    'server.tournament.difficulty_twice': (
        'expected one of difficulty and ability, which read the same, found difficulty '
        '{difficulty} and ability {ability}'
    ),
    'server.tournament.label.seeding': 'Seeding match {number}',
    'server.tournament.seeding_first': (
        'the seeding phase plays first, and the matches of the draw follow it'
    ),
    'server.tournament.evidence': (
        'expected every entrant to hold the evidence that a draw by rating asks on {scenario}, '
        'found that {lacking}. Set evidence to collect to gather it in a seeding phase first, or '
        'to proceed to seed the draw with the ratings as they stand'
    ),
    'server.tournament.field.evidence': 'what to do when an entrant lacks evidence',
    'server.tournament.format': (
        'expected a submission format as its identifier or as an object of id, preset, options and '
        'per_entrant, found {value}'
    ),
    'server.tournament.format_and_strict': (
        'expected a submission format or the strict limits of earlier versions, found both'
    ),
    'server.tournament.field.option': 'the option {option} of {where}',
    'server.tournament.option_number': (
        'expected {name} as a number from {lowest} to {highest}, found {value}'
    ),
    'server.tournament.plugins_gone': (
        'the tournament used the plugins {plugins}, which are not available now, so it opens read '
        'only and keeps what it showed'
    ),
    'server.evaluate.actions_changed': (
        'expected the kept actions of match {number} as the run wrote them, found them changed'
    ),
    'server.evaluate.actions_missing': 'expected the kept actions of match {number}, found none',
    'server.evaluate.actions_shape': (
        'expected the actions of a policy for every match and agent, of the shape {expected}, '
        'found the shape {shape}'
    ),
    'server.evaluate.batched': (
        'expected a policy and opponents that play in one batch for the mode batched, found '
        '{found}, which plays on demand'
    ),
    'server.evaluate.config': 'expected the settings of an evaluation, {reason}',
    'server.evaluate.ended': 'expected an evaluation that still plays, found it {state}',
    'server.evaluate.exit': 'the evaluation ended with exit code {code}',
    'server.evaluate.exit_detail': 'the evaluation ended with exit code {code}, {detail}',
    'server.evaluate.extras': (
        'expected the extras of a policy as a mapping of {names}, found {found}'
    ),
    'server.evaluate.function_team': (
        'expected opponents of the built-in AI for a policy given as a function, found the team '
        '{team}, and a policy registered as a team meets teams'
    ),
    'server.evaluate.keep': (
        'expected the matches to keep as all, nothing, losses, draws, wins, goals, first and a '
        'count or worst and a count, found {keep}'
    ),
    'server.evaluate.match_exit': 'the match ended with exit code {code}',
    'server.evaluate.match_exit_detail': 'the match ended with exit code {code}, {detail}',
    'server.evaluate.opponent_team': (
        'expected the team of an opponent as a team of the registry, found {team}'
    ),
    'server.evaluate.opponents': 'expected at least one opponent, each once, found {opponents}',
    'server.evaluate.page_policy': (
        'expected the policy of a request as builtin or team and the identifier of a team, found '
        '{policy}'
    ),
    'server.evaluate.policy': (
        'expected a policy as a function, builtin, a team of the registry or a module and a '
        'function, found {policy}'
    ),
    'server.evaluate.policy_load': (
        'expected {policy} to name a function that returns a policy, {reason}'
    ),
    'server.evaluate.policy_missing': (
        'expected a policy, as an argument or as the key policy of the settings, found none'
    ),
    'server.evaluate.replay_differs': (
        'expected the replay of match {number} to give {expected}, found {found}'
    ),
    'server.evaluate.replay_empty': (
        'expected the replay of match {number} to write a recording, found none'
    ),
    'server.evaluate.running': 'expected an evaluation that has ended, found it {state}',
    'server.evaluate.target': (
        'expected a TensorBoard writer with add_scalar or a W&B run with log, found {target}'
    ),
    'server.evaluate.unknown': (
        'expected an evaluation or a benchmark of the runs folder, found {identifier}'
    ),
    'server.evaluate.unknown_key': (
        'expected a key of the settings of an evaluation, one of {keys}, found {key}'
    ),
    'server.evaluate.refused': 'the format refused {team}, {reason}',
    'server.ratings.result': (
        'expected a result as a mapping of home, away, score and source, with the phase and the '
        'reference as text, found {result}'
    ),
    'server.ratings.score': (
        'expected the score of a result as the goals of the home side then the away side, found '
        '{score}'
    ),
    'server.ratings.seeding': (
        'expected the seeding rating of an entrant as a number with its interval, matches and '
        'whether it was measured, found one that does not fit for {key}'
    ),
    'server.ratings.side': (
        'expected a side of a result as a mapping with the key of its team, found {side}'
    ),
    'server.ratings.source': 'expected the source of a result, one of {sources}, found {source}',
    'server.ratings.batched': (
        'expected models that play in one batch, the built-in AI alone, for a batched rating run, '
        'found another kind, which plays on demand'
    ),
    'server.ratings.changed': (
        'expected the files of the team {team} as they were when the rating run began, found them '
        'changed, so start a new run for the new checkpoint'
    ),
    'server.ratings.config': 'expected the settings of the ratings, {reason}',
    'server.ratings.ended': 'expected a rating run that still plays, found it {state}',
    'server.ratings.exit': 'the rating run ended with exit code {code}',
    'server.ratings.exit_detail': 'the rating run ended with exit code {code}, {detail}',
    'server.ratings.lacks': '{model} lacks {what}',
    'server.ratings.lacks_anchor': {
        'one': '{count} match against the anchor',
        'other': '{count} matches against the anchor',
    },
    'server.ratings.lacks_matches': {
        'one': '{count} match',
        'other': '{count} matches',
    },
    'server.ratings.lacks_width': (
        'an interval of {most} points or less, where its interval spans {width}'
    ),
    'server.ratings.many': 'expected at most {most} models in one rating run, found {count}',
    'server.ratings.method': 'expected a method of the ratings, one of {methods}, found {method}',
    'server.ratings.model': (
        'expected a model as the identifier of a team of the registry or as builtin, a colon and a '
        'difficulty from 0 to 1, found {model}'
    ),
    'server.ratings.no_match': (
        'expected the number of a match of the rating run that has a result, found {number}'
    ),
    'server.ratings.no_run': 'expected a rating run of the ratings folder, found {identifier}',
    'server.ratings.nobody': (
        'expected at least one model beside the anchor to gather evidence for, found none'
    ),
    'server.ratings.request': (
        'expected a request as a mapping of models, a list of texts, the scenario and optionally '
        'ratings, the settings laid over those of the server'
    ),
    'server.ratings.run': (
        'expected the document of a rating run with an identifier that is safe as a file name, '
        'found {run}'
    ),
    'server.ratings.running': 'expected a rating run that has ended, found it {state}',
    'server.ratings.twice': 'expected each model once in a rating run, found {model} twice',
    'server.ratings.refused': 'the format refused every team that the run measures',
    'server.import.count': 'expected at most {most} files in one import, found {count}',
    'server.import.empty': 'expected at least one step in the dump {path}, found none',
    'server.import.exit_detail': 'the import stopped with the exit code {code} and wrote: {detail}',
    'server.import.kaggle': (
        'expected a Kaggle episode with its steps and the raw observations of its agents in '
        '{path}, found {detail}'
    ),
    'server.import.kind': (
        'expected a GRF dump, a Kaggle episode, an action log of cuju, a recording or a bundle in '
        '{path}, found a file of none of these kinds'
    ),
    'server.import.log': 'expected an action log of cuju in {path}, found {detail}',
    'server.import.log_differs': (
        'the action log {path} plays differently from call {call} on, before any of its episodes '
        'ended, so nothing of it was imported'
    ),
    'server.import.log_empty': (
        'the action log {path} recorded no episode of the chosen environments'
    ),
    'server.import.missing': 'expected a file to import, found no file {path}',
    'server.import.no_observations': (
        'expected the observations of the match in the dump {path}, found none, so it holds no '
        'positions to show'
    ),
    'server.import.not_exact': (
        'expected an exact import of {path}, as the setting fidelity asks, found that {reason}'
    ),
    'server.import.reason.action': (
        'step {step} holds the action {action}, outside the action set {action_set}'
    ),
    'server.import.reason.action_set': (
        'the dump names the action set {action_set}, which GRF does not have'
    ),
    'server.import.reason.chosen': 'the setting fidelity asks for positions',
    'server.import.reason.difference': (
        'the match played again differs from the file at step {step}, in {field}'
    ),
    'server.import.reason.ended': (
        'the match played again ended at step {step}, before the last step of the file, {last}'
    ),
    'server.import.reason.kaggle': (
        'a Kaggle episode holds no engine seed that cuju can confirm, so its match cannot play '
        'again'
    ),
    'server.import.reason.log': 'the action log plays differently from call {call}, {kind}, on',
    'server.import.reason.mid_episode': (
        'the dump starts at step {step} and not at the kick off, so its actions cannot play the '
        'match again'
    ),
    'server.import.reason.no_actions': 'step {step} of the dump lacks the actions of its agents',
    'server.import.reason.no_config': 'the dump holds no configuration of its match',
    'server.import.reason.players': (
        'the dump names players other than agents, {players}, and only agents play again'
    ),
    'server.import.reason.scenario': (
        'cuju cannot play the scenario {scenario} of the dump: {detail}'
    ),
    'server.import.reason.truncated': (
        'the file ends inside a step, so the import keeps the positions of its {steps} whole steps'
    ),
    'server.import.reason.unobserved': {
        'one': (
            'the dump lacks the observation of {count} step, which leaves the check nothing to '
            'compare'
        ),
        'other': (
            'the dump lacks the observations of {count} steps, which leaves the check nothing to '
            'compare'
        ),
    },
    'server.import.refused': (
        'the dump {path} holds {found}, which the reader of dumps refuses, so nothing of the file '
        'ran'
    ),
    'server.import.size': (
        'expected a file of at most {most} bytes to import, found {size} bytes in {path}'
    ),
    'server.import.title': '{name}, imported',
    'server.import.unreadable': (
        'expected the steps of a GRF dump in {path}, found steps that do not read as one, {detail}'
    ),
    'server.import.warning.actions': (
        "an action of the episode lies outside GRF's default action set, so the recording keeps no "
        'actions'
    ),
    'server.import.warning.disagreeing': {
        'one': "the two agents' views of the match differ at {count} step",
        'other': "the two agents' views of the match differ at {count} steps",
    },
    'server.import.warning.limit': (
        'the import kept the first {most} recordings of the file, as the setting max_recordings '
        'allows'
    ),
    'server.import.warning.truncated': 'the file ends inside a step, after {steps} whole steps',
    'server.import.warning.turned_back': {
        'one': (
            "the left agent's view was missing at {count} step, so the right agent's view was "
            'turned back'
        ),
        'other': (
            "the left agent's view was missing at {count} steps, so the right agent's view was "
            'turned back'
        ),
    },
    'server.import.warning.unchecked': 'the check left out {fields}, which the batch does not give',
    'server.queue.owner': 'expected the owner of a queue as run:ID, found {owner}',
    'server.queue.state': 'expected the state of a queue as a mapping, found {state}',
    'server.runs.checkpoint': (
        'expected a checkpoint as an identifier, a mapping of id, label and step, or the path of a '
        'model, found {checkpoint}'
    ),
    'server.runs.exists': 'expected a new run, found the run {identifier} already kept',
    'server.runs.field': 'expected the fields of a run, found {field}',
    'server.runs.identifier': (
        'expected the identifier of a run as a plain file name, found {identifier}'
    ),
    'server.runs.kind': 'expected the kind of a run, one of {kinds}, found {kind}',
    'server.runs.unknown': 'expected a run of the runs folder, found {identifier}',
    'server.runs.value': 'expected the field {field} of a run in its own type, found another',
    'server.export.recording': (
        'expected a recording of the library or a `.cjr` file to export, found {recording}'
    ),
    'server.formats.archive': "the format's archive does not read, {error}",
    'server.formats.broken': 'the format {format} is broken, {reason}',
    'server.formats.builtin': '{identifier} is a built-in format, which stays as it ships',
    'server.formats.choice': (
        "expected a format's identifier or a mapping with its id, found {choice}"
    ),
    'server.formats.contract': 'the format does not follow its contract, {reason}',
    'server.formats.failed': 'the format {format} failed, {reason}',
    'server.formats.folder': 'no folder of a format at {folder}',
    'server.formats.forfeit': '{player} forfeits, {reason}',
    'server.formats.identifier': (
        'expected an identifier of lowercase letters, digits and hyphens, found {identifier}'
    ),
    'server.formats.links': "a format's folder holds no links",
    'server.formats.options': 'the options of {format} do not fit, {reason}',
    'server.formats.requires': 'the format needs {needs}',
    'server.formats.size': "a format's folder holds at most {files} files and {mib} MiB",
    'server.formats.strict': (
        'a match names a submission format and the limits of strict/1 together, name one of them'
    ),
    'server.formats.taken': 'a format named {identifier} exists, replace it to add this one',
    'server.formats.unknown': 'expected a submission format, found {format}',
    'server.formats.version': 'no built-in format {format}, which names another version',
    'server.formats.entrant': (
        'expected per-entrant options for an entrant of the context, found {entrant}'
    ),
    'server.formats.length': (
        '{format} allows more time over {steps} steps than its own limit for a match'
    ),
    'server.checkpoints.entrant': (
        'expected a team, or a team, an at sign and the 16 characters of a checkpoint, found '
        '{entrant}'
    ),
    'server.checkpoints.none': (
        'expected a team with files, found {identifier}, which has no checkpoints'
    ),
    'server.checkpoints.unknown': 'expected a checkpoint of the team {team}, found {checkpoint}',
    'server.checkpoints.same': 'expected files that differ from those the team {team} plays now',
    'server.checkpoints.known': (
        'expected files that differ from the earlier checkpoint {checkpoint}, which the team keeps'
    ),
    'server.checkpoints.label': 'expected a label of at most {most} characters',
    'server.checkpoints.note': 'expected a note of at most {most} characters',
    'server.checkpoints.step': 'expected the training step as a whole number from 0, found {step}',
    'server.checkpoints.fields': (
        'expected label, note, step, pinned, control or current, found {fields}'
    ),
    'server.checkpoints.pinned': 'expected pinned as true or false, found {value}',
    'server.checkpoints.current': (
        'expected current as true, which makes the checkpoint play, found {value}'
    ),
    'server.checkpoints.remove_current': (
        'expected an earlier checkpoint to remove, since the team plays the current one'
    ),
    'server.verdicts.no_files': (
        'expected a team with a submission to check, found {team}, which has none'
    ),
    'server.verdicts.folder': 'expected a submission folder, found {folder}',
    'server.verdicts.size': 'expected the size 5v5 or 11v11, found {size}',
    'server.verdicts.max_steps': 'expected a whole number of steps from 1, found {steps}',
    'server.verdicts.process': 'the check of the team failed, {reason}',
    'server.verdicts.option': 'expected an option as KEY=VALUE, found {text}',
    'server.team_folders.directory': 'expected a folder of submission folders, found {directory}',
    'server.team_folders.duplicate': 'duplicate team name',
    'server.plugins.kind': 'expected a kind of plugin, one of {kinds}, found {kind}',
    'server.plugins.missing': 'expected an available plugin, found {plugin}',
    'server.plugins.unknown': 'no plugin offers the {kind} {name}, the choices are {names}',
    'server.plugins.broken': 'the plugin {plugin} is broken, {reason}',
    'server.plugins.disabled': (
        'the plugin {plugin} is turned off by the setting disabled of plugins'
    ),
    'server.plugins.failed': 'the plugin {plugin} failed in its {kind} {name}, {reason}',
    'server.plugins.folder': 'no folder of a plugin at {folder}',
    'server.plugins.links': 'expected a plugin folder without links',
    'server.plugins.size': "a plugin's folder holds at most {files} files and {mib} MiB",
    'server.plugins.declaration': 'the file plugin.toml does not read, {reason}',
    'server.plugins.identifier': (
        'expected a plugin identifier of lowercase letters, digits and hyphens, found {identifier}'
    ),
    'server.plugins.builtin': 'the plugin {identifier} ships with the arena and stays',
    'server.plugins.entry': (
        'the plugin {identifier} comes from an installed package, which pip changes'
    ),
    'server.plugins.taken': 'a plugin named {identifier} exists, replace it to add this one',
}

MESSAGES: dict[str, str | dict[str, str]] = dict(TABLE)
TABLES: list[dict[str, str | dict[str, str]]] = [TABLE]


def register(
    table: dict[str, str | dict[str, str]], /, *, exit_status: dict[str, int] | None = None
) -> None:
    """Adds the messages of another table to `MESSAGES`, so every reader sees one registry.

    A package that has messages of its own calls this once as it loads, with its table, and its
    language files then hold the ids of every table. Each id lives in exactly one table.

    Raises:
        ValueError: If an id does not start with `server.` or is already in a table.
    """
    for message_id in table:
        if not message_id.startswith('server.'):
            raise ValueError(f'expected an id that starts with server., found {message_id}')
        if message_id in MESSAGES:
            raise ValueError(f'expected an id that no table holds, found {message_id}')
    if exit_status and exit_status.keys() - table.keys():
        raise ValueError('expected an exit status to name an id of the table')
    if exit_status:
        _EXIT_STATUS.update(exit_status)
    MESSAGES.update(table)
    TABLES.append(table)


def text(message_id: str, /, **values: Any) -> str:
    """Returns the English text of a message with its placeholders filled from `values`.

    A value is text, or a label that `label` returns, which reads as its English text.

    Raises:
        KeyError: If the id is unknown, or a placeholder of its template has no value.
    """
    template = plain(form(MESSAGES[message_id], values.get('count')))
    return _PLACEHOLDER.sub(lambda match: _shown(values[match.group(1)]), template)


def plain(template: str) -> str:
    """Returns a template without its marks, the words of **strong** and `code` as they are."""
    return _MARK.sub(lambda match: match.group(1) or match.group(2) or '', template)


def form(template: str | dict[str, str], count: Any) -> str:
    """Returns the English form of a template for a count, as the page picks it.

    A plain template is its own form. A plural one takes the form of the exact count, such as
    `=0`, else `one` for a count of 1 written without decimals, else `other`.
    """
    if isinstance(template, str):
        return template
    shown = _shown(count).strip() if count is not None else ''
    if f'={shown}' in template:
        return template[f'={shown}']
    return template['one'] if shown == '1' and 'one' in template else template['other']


def counted(count: Any, one: str, other: str | None = None) -> str:
    """Returns a count with its noun in English, as `1 recording` or `2 recordings`.

    Args:
        count: The number, which reads as one only when it is 1 without decimals.
        one: The noun for one.
        other: The noun for any other count, by default `one` with an s.
    """
    shown = f'{count:g}' if isinstance(count, float) else str(count)
    return f'{shown} {one if shown == "1" else (other or one + "s")}'


def _shown(value: Any) -> str:
    """Returns the English text of a value of a message, a label or anything else as text."""
    if isinstance(value, dict) and isinstance(value.get('text'), str):
        return value['text']
    return str(value)


def _value(value: Any) -> Any:
    """Returns a value of a message as it travels, a label as it is and anything else as text."""
    if isinstance(value, dict) and isinstance(value.get('text'), str):
        return value
    return str(value)


def label(message_id: str, /, **values: Any) -> dict[str, Any]:
    """Returns a name or a label that cuju makes, such as a round or the built-in AI, as data.

    The label holds the message `id`, its English `text` and its `values`, so the page shows it
    in its own language while the command line and the files read the English text. A value may
    itself be a label, such as the name of an entrant inside the title of a match.

    Raises:
        KeyError: If the id is unknown, or a placeholder of its template has no value.
    """
    kept = {key: _value(value) for key, value in values.items()}
    return {'id': message_id, 'text': text(message_id, **kept), 'values': kept}


def scenario(name: str) -> dict[str, Any]:
    """Returns a scenario as a value of a message, which the page shows by its name there."""
    return {'kind': 'scenario', 'name': str(name), 'text': str(name)}


def named(field: str, value: dict[str, Any] | str | None) -> dict[str, Any]:
    """Returns the fields of a name in an answer, `field` with its English text and, for a label,
    `field_id` and `field_values` beside it, as the page's `serverText` reads them.

    A name that a person gave is text and travels alone.
    """
    if isinstance(value, dict) and isinstance(value.get('text'), str):
        return {
            field: value['text'],
            f'{field}_id': value.get('id'),
            f'{field}_values': dict(value.get('values') or {}),
        }
    return {field: value}


# The kinds of value that the page shows by a name of its own, a scenario.
_KINDS = ('scenario',)


def valid_label(value: Any) -> bool:
    """Returns whether a stored value is a label of a known message, its values text or labels."""
    if not isinstance(value, dict) or not isinstance(value.get('text'), str):
        return False
    if 'kind' in value:
        return value['kind'] in _KINDS and isinstance(value.get('name'), str)
    values = value.get('values')
    return (
        value.get('id') in MESSAGES
        and isinstance(values, dict)
        and all(isinstance(item, str) or valid_label(item) for item in values.values())
        and placeholders(MESSAGES[value['id']]) <= set(values)
    )


def placeholders(template: Any) -> set[str]:
    """Returns the names of the placeholders of a template, of every form of a plural one."""
    if isinstance(template, dict):
        return {name for text in template.values() for name in _PLACEHOLDER.findall(str(text))}
    return set(_PLACEHOLDER.findall(template)) if isinstance(template, str) else set()


# The classes that only add the id of a message to another exception, by the module and name
# they were defined with.
_WRAPPERS: dict[tuple[str, str], type] = {}


def _stamp(cls: type) -> None:
    """Gives a class that only adds an id the module and name of the exception it stands for."""
    for base in cls.__mro__[1:]:
        if base is Message or base.__dict__.get('_wrapper'):
            continue
        cls.__module__, cls.__qualname__, cls.__name__ = (
            base.__module__,
            base.__qualname__,
            base.__name__,
        )
        return


def public_names() -> None:
    """Stamps every class that only adds an id again, after the public classes took their module.

    `cuju.arena` gives its public errors its own name as their module once it has imported
    them, so a class defined before then takes the public name here.
    """
    for cls in _WRAPPERS.values():
        _stamp(cls)


def _rebuild(defined: tuple[str, str], message_id: str, values: dict[str, Any]) -> BaseException:
    """Returns the exception that `Message.__reduce__` pickled, of the class it was defined as."""
    module, name = defined
    importlib.import_module(module)
    cls = _WRAPPERS.get(defined) or getattr(sys.modules[module], name)
    return cls(message_id, **values)


# Exit statuses that differ from a negative answer.
_EXIT_STATUS = {
    'server.checkpoints.step': 2,
    'server.benchmark.tasks': 2,
    'server.benchmark.teams': 2,
    'server.benchmark.unknown_task': 2,
    'server.benchmark.workers': 2,
    'server.evaluate.policy_missing': 2,
    'server.import.kind': 2,
    'server.import.missing': 3,
    'server.ratings.method': 2,
    'server.ratings.twice': 2,
    'server.search.annotation': 2,
    'server.search.bound': 2,
    'server.reference.ambiguous': 2,
    'server.search.date': 2,
    'server.search.filter': 2,
    'server.search.kind': 2,
    'server.search.limit_range': 2,
    'server.search.limit_text': 2,
    'server.search.minutes': 2,
    'server.search.player': 2,
    'server.search.probability': 2,
    'server.search.score': 2,
    'server.search.final_score': 2,
    'server.search.signal_negated': 2,
    'server.search.source': 2,
    'server.search.unknown_filter': 2,
    'server.search.value_name': 2,
    'server.team_folders.directory': 2,
    'server.teams.name_separator': 2,
    'server.teams.name_reserved': 2,
    'server.play.scenario': 2,
    'server.scenario': 2,
    'server.formats.archive': 2,
    'server.formats.broken': 3,
    'server.formats.failed': 3,
    'server.formats.requires': 3,
    'server.plugins.broken': 3,
    'server.plugins.failed': 3,
    'server.preflight.failed': 3,
    'server.preflight.timeout': 3,
    'server.verdicts.process': 3,
}


class Message:
    """A mixin of exceptions whose text is a message of `MESSAGES`.

    The exception reads as the English text, and carries the id in `message_id` and the values of
    its placeholders, each as text, in `values`. A class that only adds the id to another
    exception, which sets `_wrapper`, reads as that exception in a traceback and in its `repr`,
    such as `cuju.errors.ArgumentError`, and an `except` of either class catches it.
    """

    # A class that only adds the id to another exception names that exception in an answer.
    _wrapper = True
    message_id: str
    values: dict[str, str]

    def __init_subclass__(cls, **options: Any) -> None:
        super().__init_subclass__(**options)
        if cls.__dict__.get('_wrapper'):
            cls._defined = (cls.__module__, cls.__qualname__)
            _WRAPPERS[cls._defined] = cls
            _stamp(cls)

    def __init__(self, message_id: str, /, **values: Any) -> None:
        self.message_id = message_id
        self.exit_status = _EXIT_STATUS.get(message_id, 1)
        self.values = {key: _value(value) for key, value in values.items()}
        super().__init__(text(message_id, **self.values))

    def __reduce__(self) -> Any:
        cls = type(self)
        defined = cls.__dict__.get('_defined') or (cls.__module__, cls.__qualname__)
        return (_rebuild, (defined, self.message_id, self.values))


class MessageError(Message, ArgumentError):
    """An `ArgumentError` whose text is a message of `MESSAGES`, which the page translates."""

    _wrapper = True


def argument_error(error: BaseException) -> ArgumentError:
    """Returns an `ArgumentError` with the text of `error`, and its id when it carries one."""
    message_id = getattr(error, 'message_id', None)
    if isinstance(message_id, str) and message_id in MESSAGES:
        return MessageError(message_id, **getattr(error, 'values', {}))
    return ArgumentError(str(error))


def kind(error: BaseException) -> str:
    """Returns the name of the exception that an answer shows, without the classes of ids.

    A `MessageError` reads as the `ArgumentError` it stands for, so an answer of the form
    `ArgumentError: ...` reads as before.
    """
    for cls in type(error).__mro__:
        if cls is Message or cls.__dict__.get('_wrapper'):
            continue
        return cls.__name__
    return type(error).__name__


def described(error: BaseException) -> str:
    """Returns the text `Kind: message` of an error, as answers and reports show it."""
    return f'{kind(error)}: {error}'


def fields(message_id: str, /, **values: Any) -> dict[str, Any]:
    """Returns the error fields of an answer or an entry for a message of `MESSAGES`."""
    shown = {key: _value(value) for key, value in values.items()}
    return {'error': text(message_id, **shown), 'error_id': message_id, 'error_values': shown}


def error_fields(error: BaseException, shown: str | None = None) -> dict[str, Any]:
    """Returns the error fields for an exception, with its id when it carries one.

    Args:
        error: The exception.
        shown: The text of the answer, by default `str(error)`.
    """
    result: dict[str, Any] = {'error': str(error) if shown is None else shown}
    message_id = getattr(error, 'message_id', None)
    if isinstance(message_id, str) and message_id in MESSAGES:
        result['error_id'] = message_id
        result['error_values'] = dict(getattr(error, 'values', {}) or {})
    return result


def id_fields(error: BaseException) -> dict[str, Any]:
    """Returns `error_id` and `error_values` of an exception that carries an id, else nothing."""
    found = error_fields(error)
    found.pop('error')
    return found


def entry_fields(error: BaseException | str | None, shown: str | None = None) -> dict[str, Any]:
    """Returns the error fields of an entry, always all three, `None` where it has none.

    Args:
        error: The exception, or a text without an id, or `None` for no error.
        shown: The text that the entry shows for an exception, by default `str(error)`.
    """
    result: dict[str, Any] = {'error': None, 'error_id': None, 'error_values': None}
    if isinstance(error, BaseException):
        result.update(error_fields(error, shown))
    elif error is not None:
        result['error'] = error
    return result


def exit_fields(message_id: str, code: Any, detail: str = '') -> dict[str, Any]:
    """Returns the error fields of a child process that ended with `code`.

    The message `message_id` names the exit code, and `message_id` with `_detail` added names the
    last lines that the process wrote as well, when it wrote any.
    """
    if detail:
        return fields(f'{message_id}_detail', code=code, detail=detail)
    return fields(message_id, code=code)


def report_fields(report: dict[str, Any]) -> dict[str, Any]:
    """Returns the error fields of an entry from the report of a child process.

    The id and values pass on only when they name a message of `MESSAGES` with a text value for
    each of its placeholders, so a report of another version leaves its error without an id.
    """
    error = report.get('error')
    result: dict[str, Any] = {
        'error': error if isinstance(error, str) else None,
        'error_id': None,
        'error_values': None,
    }
    message_id, values = report.get('error_id'), report.get('error_values')
    if (
        isinstance(error, str)
        and isinstance(message_id, str)
        and message_id in MESSAGES
        and isinstance(values, dict)
        and all(isinstance(value, str) or valid_label(value) for value in values.values())
        and placeholders(MESSAGES[message_id]) <= set(values)
    ):
        result.update(error_id=message_id, error_values=dict(values))
    return result


def compare(messages: dict[str, Any]) -> list[str]:
    """Returns the differences between `MESSAGES` and the `server.` messages of a language file.

    Args:
        messages: The messages of a language file, such as the parsed `web/languages/en.json`.
            For English each text must equal the registry's, and for another language each
            message must hold the same placeholders.

    Returns:
        One line for each id missing from either side, each text that differs from the English,
        and each placeholder that differs, empty when they agree.
    """
    english = messages.get('@language', {}).get('code', 'en') == 'en'
    found = {key: value for key, value in messages.items() if key.startswith('server.')}
    # A translation may lack messages, which the page then shows in English.
    problems = [f'missing {key}' for key in MESSAGES if key not in found and english]
    problems += [f'unknown {key}' for key in found if key not in MESSAGES]
    for key, template in MESSAGES.items():
        if key not in found:
            continue
        value = found[key]
        plural = isinstance(value, dict) and all(isinstance(text, str) for text in value.values())
        if not isinstance(value, str) and not plural:
            problems.append(f'{key} is not a text or an object of plural forms')
        elif isinstance(template, str) and plural and 'count' not in placeholders(template):
            problems.append(f'{key} has plural forms, and the registry has no count')
        elif english and value != template:
            problems.append(f'{key} reads {value!r}, the registry {template!r}')
        elif placeholders(value) != placeholders(template):
            problems.append(f'{key} has the placeholders {sorted(placeholders(value))}')
    return problems
