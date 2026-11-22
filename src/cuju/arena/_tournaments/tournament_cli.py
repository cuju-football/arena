# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The command `cuju arena tournament`, which makes, lists, shows, runs and changes tournaments."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cuju.errors import ArgumentError, ConfigurationError

from .._cli import _references as references
from .._cli import contract
from .._config import files as _files
from .._evaluate import ratings as _evidence
from .._text.messages import MessageError, counted
from . import tournament as rules
from . import tournament_runner as _tournament_runner

# How `show` names the state of a match still to play, as the page names it.
_MATCH_STATES = {
    'ready': 'in the queue',
    'running': 'playing',
    'playing': 'a person is playing',
    'person': 'ready for a person',
    'failed': 'failed',
}
# How `show` names where the rating of a side that the seeding phase did not measure came from,
# by the `origin` of the file of the tournament.
_ORIGINS = {
    'anchor': 'the anchor of the table',
    'given': 'given in the configuration',
    'known': 'known from the table of the scenario',
    'measured': 'measured in the seeding phase',
    'lacking': 'taken with less evidence than asked',
}


def _stage_format(stage: Any, defaults: dict[str, Any]) -> Any:
    if not isinstance(stage, dict):
        return None
    return stage.get('format', defaults.get('format', rules.STAGE_DEFAULTS['format']))


def _read_config(path: str) -> dict[str, Any]:
    """Returns the configuration of a file, TOML when its name ends in `.toml` and JSON otherwise.

    TOML has no empty value, so in a TOML file the text `none` stands for the null of JSON.
    """
    source = Path(path).expanduser()
    if source.suffix.lower() == '.toml':
        return _files.unset(_files.read(source))
    try:
        config = json.loads(source.read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            f'expected a JSON tournament file, found {path!r} with invalid JSON '
            f'on line {error.lineno}, column {error.colno}'
        ) from error
    except UnicodeDecodeError as error:
        raise ConfigurationError(f'expected a UTF-8 tournament file, found {path!r}') from error
    if not isinstance(config, dict):
        raise ConfigurationError(f'expected a JSON object in {path}')
    return config


def _config(options: argparse.Namespace) -> dict[str, Any]:
    """Returns the configuration that the options of `new` describe."""
    if options.file:
        config = _read_config(options.file)
    else:
        config = {'preset': options.tournament_preset or 'quick_cup'}
    if options.tournament_preset and options.file:
        config['preset'] = options.tournament_preset
    if 'stages' not in config and config.get('preset'):
        base = rules.preset_config(config['preset'], _tournament_runner.presets())
        config = {**base, **config}
    entrants: list[dict[str, Any]] = []
    for kind, value in options.entrants or []:
        if kind in ('ability', 'difficulty'):
            # Before the first entrant it is the standard, after an entrant that entrant's own.
            word = 'standard' if entrants else 'scenario'
            try:
                ability = None if value == word else float(value)
            except ValueError as error:
                raise ArgumentError(
                    f'expected --difficulty from 0 to 1 or {word}, found {value!r}'
                ) from error
            if entrants:
                entrants[-1]['ability'] = ability
            else:
                config['ability'] = ability
        elif kind == 'as':
            # A team entered again takes a free name, such as Rex 2, unless --as gives one.
            if not entrants:
                raise ArgumentError('expected --as after the --team or --person that it names')
            entrants[-1]['name'] = value
        elif kind == 'team':
            entrants.append({'kind': 'team', 'team': value})
        elif kind == 'person':
            entrants.append({'kind': 'person', 'name': value})
        elif kind == 'random':
            entrants.append({'kind': 'random'})
        else:
            entrants.append({'kind': 'builtin', 'ability': value})
    if entrants:
        config['entrants'] = entrants
    if isinstance(config.get('entrants'), list):
        for entrant in config['entrants']:
            if isinstance(entrant, dict) and isinstance(entrant.get('team'), str):
                entrant['team'] = references.team(entrant['team'])
    for key in ('name', 'seed', 'seeding', 'concurrency', 'evidence'):
        # An option that the command line lacks leaves the setting to the configuration.
        if getattr(options, key, None) is not None:
            config[key] = getattr(options, key)
    from .._cli import check

    choice = check.optional_choice(options)
    if choice is not None:
        chosen = {'id': choice.format.label}
        if options.option:
            names = [option.partition('=')[0] for option in options.option]
            chosen['options'] = {name: choice.values[name] for name in names}
        if choice.preset is not None:
            chosen['preset'] = choice.preset
        config['submission_format'] = chosen
    if options.preflight:
        config['preflight'] = True
    defaults = dict(config.get('defaults') or {})
    for key in ('scenario', 'length'):
        if getattr(options, key) is not None:
            defaults[key] = getattr(options, key)
    config['defaults'] = defaults
    if options.rounds is not None:
        stages = config.get('stages') if isinstance(config.get('stages'), list) else []
        rounded = [s for s in stages if _stage_format(s, defaults) in ('swiss', 'ladder')]
        if not rounded:
            raise ArgumentError('expected a Swiss or ladder stage for --rounds, found none')
        for stage in rounded:
            stage['rounds'] = options.rounds
    return config


def check_lines(report: dict[str, Any]) -> list[str]:
    """Returns the lines that `preflight` prints for the check of one team."""
    name = rules.text_of(report.get('name')) or report.get('team')
    verdict = 'pass' if report.get('ok') else 'fail'
    lines = [f'{verdict}  {name}  {report.get("team")}  {report.get("scenario")}']
    rule = report.get('rule')
    if rule:
        lines.append(f'  {rule.get("kind")}, {rule.get("reason")}')
    for side, found in (report.get('sides') or {}).items():
        timings = found.get('timings') or {}
        lines.append(f'  {side}: {_timing_text(timings)}')
    if not report.get('sides') and report.get('timings'):
        lines.append(f'  {_timing_text(report["timings"])}')
    if report.get('submission_format'):
        lines.append(f'  format {report["submission_format"]}')
    for warning in report.get('warnings') or []:
        lines.append(f'  warning, {rules.text_of(warning)}')
    return lines


def _timing_text(timings: dict[str, Any]) -> str:
    def part(key: str, words: str, unit: str) -> str | None:
        value = timings.get(key)
        return None if value is None else f'{words} {value:g} {unit}'

    parts = [
        part('load_seconds', 'loaded in', 's'),
        part('reset_seconds', 'reset in', 's'),
        part('first_ms', 'first call', 'ms'),
        part('mean_after_first_ms', 'mean after the first', 'ms'),
        part('max_ms', 'largest', 'ms'),
    ]
    calls = timings.get('calls') or 0
    parts.append(counted(calls, 'call'))
    return ', '.join(item for item in parts if item)


def _check_rounds(config: dict[str, Any], rounds: int) -> None:
    """Refuses `--rounds` beyond what a Swiss stage allows, which a preset would bring down."""
    checked = _tournament_runner.check_config(config)
    for index, stage in enumerate(checked['stages']):
        if stage['format'] == 'swiss' and stage['rounds'] != rounds:
            raise ArgumentError(
                f'expected --rounds from 1 to {stage["rounds"]} for the Swiss stage {index + 1}, '
                f'which pairs {stage["entrants"]} entrants without rematches, found {rounds}'
            )


def _stage_title(stage: dict[str, Any]) -> str:
    """Returns a stage's name with its format, the format left out when the name is the format."""
    title = rules._FORMAT_TITLES.get(stage['format'], stage['format'])
    if stage['name'].casefold() == title.casefold():
        return stage['name']
    return f'{stage["name"]}, {title[0].lower()}{title[1:]}'


def _names(config: dict[str, Any]) -> list[str]:
    """Returns the name of each entrant, with its ability where it differs from the standard."""
    names = []
    for entrant in config['entrants']:
        shown = rules.shown_ability(entrant, config.get('ability'))
        names.append(entrant['name'] if shown is None else f'{entrant["name"]} at {shown:.2f}')
    return names


def _ability_line(config: dict[str, Any]) -> str:
    """Returns the line that names the standard ability of a tournament."""
    standard = config.get('ability')
    return 'standard ability ' + ("the scenario's" if standard is None else f'{standard:.2f}')


def _played_line(entry: dict[str, Any]) -> str:
    """Returns how many matches a tournament played of those it expects, and those scheduled."""
    played, scheduled = entry['played'], entry['scheduled']
    expected = max(entry.get('expected') or 0, scheduled)
    if expected == scheduled:
        return f'{played} of {scheduled} scheduled matches played'
    return f'{played} of {expected} matches played, {scheduled} scheduled so far'


def _shown_state(entry: dict[str, Any]) -> str:
    if (
        entry['state'] == 'running'
        and not entry['played']
        and not entry.get('owner')
        and not entry.get('running')
        and not entry.get('failed')
    ):
        return 'drawn'
    return entry['state']


def describe(entry: dict[str, Any]) -> list[str]:
    """Returns the lines that `show` prints, the stages with their tables, results and ranking."""
    names = _names(entry['config'])
    awarded = {
        match['key'] for match in entry['matches'] if (match.get('result') or {}).get('forfeit')
    }

    def name(side: Any) -> str:
        if isinstance(side, dict):
            return side.get('from', '')
        return 'a bye' if side is None else names[side]

    state = _shown_state(entry)
    expected = max(entry.get('expected') or 0, entry['scheduled'])
    lines = [
        f'{entry["id"]}  {state}  played {entry["played"]} of {expected}',
        f'{entry["name"]}  seed {entry["seed"]}',
        f'{_played_line(entry)}, {_ability_line(entry["config"])}',
    ]
    if entry.get('error'):
        lines.append(entry['error'])
    seeding = entry.get('seeding')
    if seeding:
        # The seeding phase, with the rating that it measured of each entrant, the best first.
        mode = 'on demand' if seeding['mode'] == 'on_demand' else 'batched'
        lines.append('')
        lines.append(
            f'Seeding phase, {mode}, {seeding["state"]}, {seeding["played"]} of '
            f'{seeding["total"]} matches played'
        )
        elo = entry.get('elo') or {}
        anchor = seeding.get('anchor') or elo.get('anchor')
        rated = sorted(seeding['ratings'].items(), key=lambda item: -item[1]['rating'])
        for key, rating in rated:
            shown = (seeding['entrants'].get(key) or {}).get('name') or key
            if rating['measured']:
                lines.append(
                    f'  {shown}  {rating["rating"]:.0f}, from {rating["low"]:.0f} to '
                    f'{rating["high"]:.0f}, {counted(rating["matches"], "match", "matches")}'
                )
            else:
                kept = ((elo.get('entrants') or {}).get(key) or {}).get('seeding') or {}
                origin = 'anchor' if key == anchor else kept.get('origin')
                words = _ORIGINS.get(origin, 'held where it stood')
                lines.append(f'  {shown}  {rating["rating"]:.0f}, {words}')
    # A cancelled tournament plays nothing more, so its unfinished stages read as cancelled.
    cancelled = entry['state'] == 'cancelled'
    for stage in entry['stages_view']:
        lines.append('')
        state = _STAGE_STATES.get(stage['state'], stage['state'])
        if cancelled and stage['state'] != 'done':
            state = 'cancelled'
        lines.append(f'{_stage_title(stage)}, {state}')
        swiss = stage['format'] == 'swiss'
        for table in stage['tables']:
            lines.append(f'  {table["name"]}')
            head = f'  {"":>3}  {"":<28} Played  Won Drawn Lost   Goals Points'
            lines.append(head + ('  Buchholz Byes' if swiss else ''))
            for row in table['rows']:
                goals = f'{row["for"]}:{row["against"]}'
                line = (
                    f'  {row["position"]:>3}  {names[row["entrant"]]:<28} {row["played"]:>6} '
                    f'{row["won"]:>4} {row["drawn"]:>5} {row["lost"]:>4} {goals:>7} '
                    f'{row["points"]:>6}'
                )
                if swiss:
                    line += f'  {row["buchholz"]:>8} {row["byes"]:>4}'
                lines.append(line)
        for round_ in stage['rounds']:
            extra = ''
            if round_.get('bye') is not None:
                extra += f', bye for {names[round_["bye"]]}'
            if round_.get('king') is not None:
                extra += f', king {names[round_["king"]]}'
            lines.append(f'  {round_["name"]}{extra}')
            for fixture in round_['fixtures']:
                line = _fixture_line(fixture, name, awarded, cancelled)
                if line:
                    lines.append(line)
    waiting = [match for match in entry['matches'] if match.get('state') != 'done']
    if waiting:
        lines += ['', 'Matches not played' if cancelled else 'Matches to play']
        for match in waiting:
            state = _MATCH_STATES.get(match.get('state'), match.get('state'))
            if cancelled:
                state = 'not played, the tournament was cancelled'
            if match.get('error'):
                state = f'{state}, {match["error"]}'
            lines.append(
                f'  {match["key"]}  {match["label"]}, {names[match["home"]]} against '
                f'{names[match["away"]]}, {state}'
            )
    if entry.get('ranking'):
        lines += ['', 'Final ranking']
        lines += [f'  {place + 1:>3}. {names[e]}' for place, e in enumerate(entry['ranking'])]
    if entry['champion'] is not None:
        lines += ['', f'Champion: {entry["champion"]}']
    return lines


def _fixture_line(
    fixture: dict[str, Any], name: Callable[[Any], str], awarded: set[str], cancelled: bool
) -> str | None:
    """Returns the line of a tie in `show`, its score and then how it was settled, in the order
    it happened, or `None` for a tie of two byes, which plays nothing."""
    home, away = fixture['home'], fixture['away']
    if fixture.get('decided_by') == 'bye' or home is None or away is None:
        if home is None and away is None:
            return None
        return f'    {name(away if home is None else home)}, a bye'
    score = fixture.get('score')
    done = fixture.get('state') == 'done'
    if score is not None:
        text = f'  {score[0]} to {score[1]}' + ('' if done else ' so far')
    elif done:
        text = ''
    else:
        text = '  not played' + (', the tournament was cancelled' if cancelled else '')
    earlier = False
    for key, words in (('replay', 'replay'), ('decider', 'deciding matches')):
        if fixture.get(key):
            text += f', {words} {fixture[key][0]} to {fixture[key][1]}'
            earlier = True
    how = fixture.get('decided_by')
    winner = fixture.get('winner')
    if how in _DECIDED and isinstance(winner, int):
        text += ', ' + _DECIDED[how].format(
            winner=name(winner), then='then ' if earlier and how == 'lots' else ''
        )
    if awarded & set(fixture.get('matches', [])):
        text += ', awarded'
    return f'    {name(home)} against {name(away)}{text}'


def preview_lines(preview: dict[str, Any]) -> list[str]:
    """Returns the lines that `check` prints, each stage with its entrants and first draw."""
    config = preview['config']
    names = _names(config)

    def name(side: Any) -> str:
        if isinstance(side, dict):
            return side.get('from', '')
        return 'a bye' if side is None else names[side]

    lines = [
        f'{config["name"]}, seed {config["seed"]}, {counted(len(names), "entrant")}, '
        f'{counted(preview["matches"], "match", "matches")} known before any result, '
        f'{_ability_line(config)}'
    ]
    last = len(config['stages']) - 1
    pairs = zip(config['stages'], preview['stages'], strict=True)
    for index, (settings, stage) in enumerate(pairs):
        parts = [counted(settings['entrants'], 'entrant')]
        if index < last:
            advance = settings['advance']
            parts.append(f'{advance} advances' if advance == 1 else f'{advance} advance')
        parts.append(settings['scenario'])
        if settings['length']:
            parts.append(f'{counted(settings["length"], "step")} a match')
        if settings.get('ability') is not None:
            parts.append(f'ability {settings["ability"]:.2f}')
        lines.append(f'{index + 1}. {_stage_title(settings)}, {", ".join(parts)}')
        if settings['format'] == 'groups':
            for table in stage['tables']:
                members = ', '.join(names[row['entrant']] for row in table['rows'])
                lines.append(f'  {table["name"]}, {members}')
        elif stage['rounds']:
            first = stage['rounds'][0]
            lines.append(f'  {first["name"]}')
            for fixture in first['fixtures']:
                lines.append(f'    {name(fixture["home"])} against {name(fixture["away"])}')
    return lines + evidence_lines(preview.get('evidence'), names, config.get('evidence'))


# What the tournament does with entrants that lack evidence, by its key `evidence`.
_EVIDENCE_WAYS = {
    'refuse': 'As it stands the tournament is refused.',
    'collect': 'The tournament gathers it in a seeding phase before the draw.',
    'proceed': 'The draw takes the ratings as they stand.',
}


def evidence_lines(report: Any, names: list[str], evidence: Any) -> list[str]:
    """Returns the lines that `check` prints of the evidence that a draw by rating asks.

    They name the table and what it asks, then each entrant that lacks evidence with what it
    misses, and the two ways on, `--evidence collect` and `--evidence proceed`. A tournament
    that ratings do not seed asks no evidence and has no such lines.
    """
    if not isinstance(report, dict) or not report.get('seeded'):
        return []
    required = report['required']
    asked = [
        counted(required['matches'], 'match', 'matches'),
        f'{counted(required["anchor_matches"], "match", "matches")} against the anchor',
    ]
    if required.get('width') is not None:
        asked.append(f'an interval of {required["width"]:.0f} points or less')
    lines = [
        '',
        f'Evidence on the table of {report["scenario"]}, by {report["method"]}, with '
        f'{report["anchor"]} at 1000, each entrant needs {", ".join(asked)}',
    ]
    if report.get('complete'):
        lines.append('  Every entrant holds the evidence that the draw by rating asks.')
        return lines
    for entry in report['lacking']:
        shown = names[entry['entrant']] if 0 <= entry['entrant'] < len(names) else entry['key']
        row = {'id': entry['model'], 'name': shown, 'lacking': entry['lacking']}
        lines.append('  ' + _evidence.lacking_text({'required': required, 'models': [row]}))
    lines.append(
        '  Run with --evidence collect to gather the evidence in a seeding phase first, or with '
        '--evidence proceed to seed the draw with the ratings as they stand.'
    )
    lines.append('  ' + _EVIDENCE_WAYS.get(evidence, _EVIDENCE_WAYS['refuse']))
    return lines


def _plays_it(identifier: str) -> str:
    return f'Run cuju arena tournament run {identifier} to play the tournament.'


def _plays_on(entry: dict[str, Any]) -> str:
    """Returns who plays a tournament on after a change, the process that holds it when one does."""
    if entry.get('owner'):
        return f'and process {entry["owner"]}, which holds it, plays it on'
    return _plays_it(entry['id'])


def run_command(parser: argparse.ArgumentParser, options: argparse.Namespace) -> int:
    """Runs one action of `cuju arena tournament` and returns the exit status."""
    try:
        return _run_command(parser, options)
    except ArgumentError as error:
        if options.action in ('new', 'check') and getattr(error, 'exit_status', 1) == 1:
            raise contract.UsageError(parser, str(error)) from error
        raise
    except rules.TournamentError as error:
        failure = contract.UsageError(parser, str(error))
        raise failure from error


def _run(options: argparse.Namespace) -> int:
    """Runs a tournament in this process and prints how it stopped, or the whole of it as JSON."""
    tournaments = _tournament_runner.Tournaments(only=options.target, start=False)
    try:
        if tournaments.get(options.target) is None:
            available = _tournament_runner.Tournaments(start=False)
            try:
                options.target = references.kept(options.target, available.entries(), 'cup-')
            finally:
                available.close()
            tournaments.close()
            tournaments = _tournament_runner.Tournaments(only=options.target, start=False)
        if tournaments.get(options.target) is None:
            raise contract.RefusalError(
                f'expected a tournament, found {options.target!r}',
                hint='run cuju arena tournament list',
            )
        with contract.Progress(options.quiet) as progress:
            state = tournaments.run(options.target, report=progress)
        entry = tournaments.get(options.target)
    except KeyboardInterrupt:
        tournaments.interrupt(options.target)
        raise
    finally:
        tournaments.close()
    if state == 'cancelled':
        contract.note('cancelled')
    if options.json:
        contract.emit({'outcome': state, 'tournament': entry})
    elif state != 'cancelled':
        print(_RUN_MESSAGES[state])
    return 0 if state in ('done', 'people', 'cancelled') else 1


def _preflight(options: argparse.Namespace) -> int:
    """Checks one team as a tournament's pre-flight does, exit status 0 for a pass and 1 else."""
    from .._cli import check

    choice = check.optional_choice(options)
    try:
        report = _tournament_runner.preflight(
            references.team(options.target),
            options.scenario,
            None if choice is None else choice.as_dict(),
        )
    except MessageError as error:
        if error.message_id == 'server.teams.unknown':
            error.exit_status = contract.USAGE
        raise
    if report.get('environment'):
        rule = report.get('rule') or {}
        error = contract.RefusalError(rule.get('reason') or 'the submission could not be checked')
        error.exit_status = contract.ENVIRONMENT
        error.payload = report
        raise error
    if options.json:
        contract.emit(report)
    else:
        print('\n'.join(check_lines(report)))
    return 0 if report.get('ok') else 1


def _run_command(parser: argparse.ArgumentParser, options: argparse.Namespace) -> int:
    action = options.action
    if action == 'presets':
        presets = _tournament_runner.presets()
        contract.list_answer(
            'presets',
            presets,
            ((key, preset['title']) for key, preset in presets.items()),
            'Run cuju arena tournament new --help for ways to create a tournament.',
        )
        return 0
    if action == 'ratings':
        teams, models = _evidence.latest_rows()
        ordered = sorted(models.values(), key=lambda row: -row['rating'])
        if options.json:
            contract.emit({'ratings': teams, 'models': ordered})
        else:
            contract.listing(
                (
                    value['id'],
                    value['name'],
                    f'{value["rating"]:.1f}',
                    counted(value.get('matches', 0), 'match', 'matches'),
                )
                for value in ordered
            )
        if not ordered:
            contract.note('Run cuju arena tournament run ID to earn ratings from played matches.')
        return 0
    if action == 'check':
        config = _config(options)
        if options.rounds is not None:
            _check_rounds(config, options.rounds)
        preview = _tournament_runner.preview(config)
        if options.json:
            contract.emit(preview)
        else:
            print('\n'.join(preview_lines(preview)))
        return 0
    if action == 'run':
        return _run(options)
    if action == 'preflight':
        return _preflight(options)
    tournaments = _tournament_runner.Tournaments(start=False)
    try:
        if action == 'new':
            config = _config(options)
            if options.rounds is not None:
                _check_rounds(config, options.rounds)
            entry = tournaments.create(config)
            if config.get('seed') is None:
                contract.note(f'drew seed {entry["seed"]}')
            if options.json:
                contract.emit(entry)
            else:
                print(f'{entry["id"]}  {entry["name"]}')
            contract.note(
                _plays_it(entry['id']) + ' A render server serving the same arena home '
                'plays new tournaments by itself.'
            )
        elif action == 'list':
            entries = tournaments.entries()
            contract.list_answer(
                'tournaments',
                entries,
                (
                    (
                        e['id'],
                        _shown_state(e),
                        f'{e["played"]} played',
                        e['name'] + (f', won by {e["champion"]}' if e['champion'] else ''),
                    )
                    for e in entries
                ),
                'Run cuju arena tournament new --help to create a tournament.',
            )
        else:
            options.target = references.kept(options.target, tournaments.entries(), 'cup-')
            if tournaments.get(options.target) is None:
                raise contract.RefusalError(
                    f'expected a tournament, found {options.target!r}',
                    hint='run cuju arena tournament list',
                )
            if action == 'show':
                entry = tournaments.get(options.target)
                if options.json:
                    contract.emit(entry)
                else:
                    print('\n'.join(describe(entry)))
            elif action == 'forfeit':
                entry = tournaments.forfeit(options.target, options.match, options.winner)
                if options.json:
                    contract.emit(entry)
                    return 0
                match = next(m for m in entry['matches'] if m['key'] == options.match)
                side = match['home'] if options.winner == 'home' else match['away']
                print(f'awarded {match["label"]} to {entry["config"]["entrants"][side]["name"]}')
                if entry['state'] == 'running':
                    print(f'the tournament runs, {_plays_on(entry)}')
                elif entry['error']:
                    print(f'the tournament stays {entry["state"]}, {entry["error"]}')
            elif action == 'resume':
                entry = tournaments.resume(options.target)
                if options.json:
                    contract.emit(entry)
                    return 0
                print(f'resumed {entry["id"]}, {_plays_on(entry)}')
            elif action == 'export':
                files = tournaments.files(options.target)
                target = Path(options.output or '.').expanduser()
                target.mkdir(parents=True, exist_ok=True)
                for name, text in files.items():
                    (target / name).write_text(text, encoding='utf-8')
                contract.files([target / name for name in files], options.json)
            elif action == 'legs':
                entry = tournaments.add_legs(options.target, options.legs)
                if options.json:
                    contract.emit(entry)
                else:
                    print(f'added {counted(options.legs, "leg")}, {_played_line(entry)}')
                    if entry['state'] == 'running':
                        print(f'the tournament runs, {_plays_on(entry)}')
            elif action == 'replay':
                found = _tournament_runner.replay_match(
                    options.target, options.match, device=options.device
                )
                if options.json:
                    contract.emit(found)
                else:
                    again, kept = found['replay'], found['kept']
                    print(
                        f'{options.match} replays in {again["steps"]} steps to '
                        f'{again["score"][0]} to {again["score"][1]}, '
                        + ('as it played' if found['same'] else f'though it played {kept}')
                    )
                return 0 if found['same'] else 1
            elif action == 'remove':
                tournaments.remove(options.target)
                if options.json:
                    contract.emit({'id': options.target, 'removed': True})
                else:
                    print(f'removed {options.target}, its recordings stay in the library')
            else:
                try:
                    tournaments.cancel(options.target)
                except MessageError as error:
                    if (
                        error.message_id == 'server.tournament.cancel_state'
                        and error.values['state'] == 'done'
                    ):
                        error.hint = f'run cuju arena tournament remove {options.target}'
                    raise
                if options.json:
                    contract.emit(tournaments.get(options.target))
                else:
                    print(f'cancelled {options.target}')
    finally:
        tournaments.close()
    return 0


_RUN_MESSAGES = {
    'done': 'the tournament is complete',
    'stopped': 'the tournament stopped, award the failed matches with cuju arena '
    'tournament forfeit, or run it again to retry them',
    'people': 'the tournament waits for people, who play their matches on the Play page',
}
# How `show` names the state of a stage and how a tie was decided.
_STAGE_STATES = {
    'waiting': 'waiting for the stage before it',
    'ready': 'ready',
    'playing': 'playing',
    'done': 'finished',
}
# Each follows the score, the replay and the deciding matches, so it names only who went on.
_DECIDED = {
    'away goals': '{winner} wins on away goals',
    'replay': '{winner} wins',
    'decider': '{winner} wins',
    'lots': '{then}{winner} wins by lots',
    'holder': '{winner} keeps the place on a draw',
}
