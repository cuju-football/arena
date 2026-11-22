# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The command line of the arena."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import _cli
from ._cli import _engine, contract
from ._cli import _references as references


def parser() -> contract.Parser:
    """Builds the whole command tree without running a command."""
    from ._cli import _arena
    from ._version import __version__

    parser = contract.Parser(prog='cuju arena', description='Play, judge and record cuju matches.')
    contract.details(
        parser, 'Run cuju arena config show to see resolved settings and their sources.'
    )
    parser.add_argument('-V', '--version', action='version', version=f'cuju-arena {__version__}')
    commands = parser.add_subparsers(dest='command', metavar='COMMAND')
    _arena.register(commands)
    for name, module in _cli.register(commands).items():
        commands.choices[name].set_defaults(_runner=module.run)
    contract.globals_for(parser)
    for command in commands.choices.values():
        contract.globals_for(command)
        for action in command._actions:
            if isinstance(action, argparse._SubParsersAction):
                for verb in action.choices.values():
                    contract.globals_for(verb)
    return parser


def main(arguments: list[str] | None = None) -> int:
    """Runs one command and returns its exit status."""
    command_line = sys.argv[1:] if arguments is None else arguments
    switches = command_line[: command_line.index('--')] if '--' in command_line else command_line
    as_json = '--json' in switches
    verbose = any(flag in switches for flag in ('-v', '--verbose'))
    verbose = verbose and not any(flag in switches for flag in ('-q', '--quiet'))
    try:
        damaged = _engine.damaged_engine('cuju arena', as_json)
    except BrokenPipeError:
        return contract.closed_output()
    if damaged is not None:
        return damaged

    from cuju.errors import ConfigurationError, DeviceError, Error, LibraryError, MissingFileError

    selected = contract.Parser(prog='cuju arena')
    try:
        selected = root = parser()
        options = root.parse_args(command_line)
        selected = options._parser
        if options.command is None:
            root.print_help()
            return contract.flushed(contract.SUCCESS)
        if any(isinstance(action, argparse._SubParsersAction) for action in selected._actions):
            selected.print_help(sys.stderr)
            return contract.fail(
                selected,
                ValueError('expected a command'),
                contract.USAGE,
                as_json=as_json,
                verbose=verbose,
                usage=False,
            )
        if options.quiet and options.verbose:
            selected.error('expected one of --quiet and --verbose')
        if options.json and getattr(options, 'csv', False):
            selected.error('expected one of --json and --csv')
        with contract.invocation(options):
            status = _invoke(options)
        return contract.flushed(status)
    except BrokenPipeError:
        return contract.closed_output()
    except SystemExit as error:
        return contract.flushed(int(error.code or 0))
    except contract.UsageError as error:
        selected = error.parser
        status = contract.USAGE
        failure = error
    except (ConfigurationError, argparse.ArgumentTypeError) as error:
        status = contract.USAGE
        failure = error
    except KeyboardInterrupt as error:
        status = contract.INTERRUPTED
        failure = error
    except (OSError, ImportError, DeviceError, LibraryError, MissingFileError) as error:
        status = contract.ENVIRONMENT
        failure = error
    except (Error, contract.RefusalError) as error:
        status = getattr(error, 'exit_status', contract.NEGATIVE)
        failure = error
    except Exception as error:
        status = contract.INTERNAL
        failure = error
    try:
        return contract.fail(selected, failure, status, as_json=as_json, verbose=verbose)
    except BrokenPipeError:
        return contract.closed_output()


def _invoke(options: argparse.Namespace) -> int:
    from ._config.layers import ARENA
    from ._store import home as _home

    values = {}
    if options.home:
        values[_home.HOME_VARIABLE] = str(Path(options.home).expanduser().resolve())
    with contract.environment(values):
        notices = (
            contextlib.redirect_stderr(io.StringIO()) if options.quiet else contextlib.nullcontext()
        )
        with notices:
            home = _home.home()
        try:
            home.mkdir(parents=True, exist_ok=True)
            if not os.access(home, os.W_OK | os.X_OK):
                raise PermissionError(str(home))
        except OSError as error:
            expected = f'expected a writable arena folder, found {str(home)!r}'
            failure = OSError(expected)
            failure.hint = (
                f'run {options._parser.prog} with --home DIR pointing to a writable folder'
            )
            raise failure from error
        paths = options.config
        if options.command == 'config' and options.action == 'check':
            paths = [*options.files, *paths]
        with ARENA.command_line(paths, options.overrides):
            options.config = []
            options.overrides = []
            runner = getattr(options, '_runner', None)
            if runner is not None:
                return runner(options) or 0
            return _run(options._parser, options)


def _run(parser: argparse.ArgumentParser, options: argparse.Namespace) -> int:
    """Runs one command of the parsed command line."""
    if options.command == 'team':
        return _team(options)
    if options.command == 'checkpoint':
        _team_checkpoints(parser, options)
        return 0
    if options.command == 'sample':
        from ._store import samples as _samples

        if options.action == 'list':
            entries = _samples.listing()
            hint = 'Run cuju arena sample add NAME to add a shipped sample.'
            contract.list_answer(
                'samples',
                entries,
                (
                    (
                        e['id'],
                        e['kind'],
                        'not added' if e['state'] == 'missing' else e['state'],
                        e['title'],
                    )
                    for e in entries
                ),
                hint,
            )
            if not options.json and any(e['state'] == 'missing' for e in entries):
                contract.note(hint)
            return 0
        names = None
        if options.name is not None:
            names = [options.name]
        result = _samples.add(names)
        if options.json:
            contract.emit(result)
            return 0
        for change in result['changes']:
            target = next(e['target'] for e in result['samples'] if e['id'] == change['id'])
            named = f' as {target}' if target != change['id'] else ''
            print(f'{change["change"]} {change["id"]}{named}')
        if not result['changes']:
            contract.note('The library already holds the samples.')
        return 0
    if options.command == 'search':
        return _find(options)
    if options.command == 'captions':
        return _write_captions(options)
    if options.command == 'tournament':
        from ._tournaments.tournament_cli import run_command

        return run_command(parser, options)
    if options.command == 'recording':
        return _recordings(options)
    if options.command == 'play':
        return _play_match(options)
    raise RuntimeError(f'unhandled arena command {options.command}')


def _team_answer(entry: dict[str, Any]) -> dict[str, Any]:
    return {('size' if key == 'format' else key): value for key, value in entry.items()}


def _team(options: argparse.Namespace) -> int:
    from ._cli import check as _check
    from ._registry import teams

    action = options.action
    if action not in ('add', 'list'):
        options.target = references.team(options.target, checkpoint=action == 'show')
    if action == 'list':
        entries = teams.teams()
        contract.list_answer(
            'teams',
            [_team_answer(entry) for entry in entries],
            ((entry['id'], entry['format'], entry['name']) for entry in entries),
            'Run cuju arena team add PATH to register a team.',
        )
    elif action == 'add':
        control = _team_control(options)
        if options.each:
            return _check.add_folders(options, control)
        if options.full:
            options._parser.error('expected --each with --full')
        submission = _check.optional_choice(options)
        entry = teams._add_folder(
            options.target,
            options.name,
            options.size or '11v11',
            control,
            submission_format=submission,
            scenario=options.scenario,
            size_override=options.size,
        )
        _print_team(entry, options.json, 'added')
    elif action == 'show':
        entry = teams.entry(options.target)
        _print_team(entry, options.json, None)
        if not options.json and entry['kind'] == 'kaggle' and 'entrant' not in entry:
            from ._store import home

            print(f'  folder {home.folder("teams") / entry["id"]}')
    elif action == 'set':
        control = _team_control(options)
        if control is not None:
            control = _changed_control(teams, options, control)
        _print_team(
            teams.update(options.target, options.name, None, options.size, control),
            options.json,
            'changed',
        )
    elif action == 'export':
        filename, data = teams.export(options.target, options.all_checkpoints)
        path = Path(options.output or filename).expanduser()
        path.write_bytes(data)
        contract.files([path], options.json)
    else:
        if not teams.remove(options.target):
            raise contract.RefusalError(
                f'expected a team of the registry, found {options.target!r}',
                hint='run cuju arena team list',
            )
        if options.json:
            contract.emit({'removed': options.target})
        else:
            print(f'removed {options.target}')
    return 0


def _team_checkpoints(parser: argparse.ArgumentParser, options: argparse.Namespace) -> None:
    """Adds a checkpoint to a team or lists its checkpoints, for `cuju arena team`."""
    from ._registry import checkpoints as _checkpoints
    from ._registry import teams as _teams

    options.target = references.team(options.target, checkpoint=False)
    if '@' in options.target:
        raise contract.UsageError(
            parser,
            f'expected a team reference without a checkpoint selector, found {options.target!r}',
            hint='use a team identifier, prefix or name by itself',
        )
    if options.action == 'add':
        path = Path(options.path).expanduser()
        filename = path.name
        if path.is_dir():
            if not (path / 'main.py').is_file():
                parser.error(f'expected main.py in the folder {str(path)!r}')
            files = [
                (item.relative_to(path).as_posix(), item.read_bytes())
                for item in sorted(path.rglob('*'))
                if item.is_file() and '__pycache__' not in item.parts and not item.is_symlink()
            ]
            data = _teams.folder_bundle(files)
        elif path.is_file():
            data = path.read_bytes()
        else:
            parser.error(f'expected a submission folder or file, found nothing at {str(path)!r}')
        found = _checkpoints.add(
            options.target, filename, data, options.label or '', options.step, options.note or ''
        )
    else:
        _teams.entry(options.target)
        found = _checkpoints.listing(options.target)
    if options.json:
        contract.emit(found)
        return
    contract.listing(
        (
            item['id'],
            ', '.join(
                field
                for field in (
                    'current' if item['current'] else '',
                    f'step {item["step"]}' if item['step'] is not None else '',
                    'pinned' if item['pinned'] else '',
                )
                if field
            )
            or 'kept',
            f'{item["files"]} files, {item["bytes"]} bytes',
            item['label'],
        )
        for item in found['checkpoints']
    )


def _team_control(options: argparse.Namespace) -> dict[str, Any] | None:
    """Returns the control that the options of `cuju arena team` name, or `None` for none."""
    control: dict[str, Any] = {}
    if options.players is not None:
        control['players'] = options.players
    if options.selection is not None:
        parts = [part.strip() for part in options.selection.split(',') if part.strip()]
        control['selection'] = [int(part) if part.isdigit() else part for part in parts]
        control['assignment'] = 'fixed'
        control.setdefault('players', len(parts))
    if options.assignment is not None:
        control['assignment'] = options.assignment
    if options.goalkeeper is not None:
        control['goalkeeper'] = options.goalkeeper
    if options.action_set is not None:
        control['action_set'] = options.action_set
    return control or None


def _changed_control(teams: Any, options: argparse.Namespace, control: dict[str, Any]) -> Any:
    """Returns a team's control with the changes of `cuju arena team set` applied to it."""
    from cuju.errors import Error

    current = teams.entry(options.target)
    try:
        known = teams.check_control(current.get('control'), options.size or current['format'])
    except Error:
        known = dict(teams.DEFAULT_CONTROL)
    if control.get('assignment', known['assignment']) == 'dynamic':
        # Dynamic assignment keeps no selection, and the goalkeeper goes back to the scenario.
        known['selection'] = []
        if known['goalkeeper'] == 'builtin':
            known['goalkeeper'] = 'scenario'
    return {**known, **control}


def _print_team(entry: dict[str, Any], as_json: bool, change: str | None) -> None:
    """Prints a team of the registry, as JSON or as lines of its name, format and control."""
    if as_json:
        contract.emit(_team_answer(entry))
        return
    head = f'{change} ' if change else ''
    print(f'{head}{entry["id"]} {entry["name"]}')
    control = entry.get('control')
    if entry.get('kind') != 'builtin' and isinstance(control, dict):
        selection = ','.join(str(item) for item in control.get('selection') or []) or 'none'
        print(
            f'  size {entry["format"]}, {control["players"]} controlled, '
            f'assignment {control["assignment"]}, selection {selection}\n'
            f'  goalkeeper {control["goalkeeper"]}, action set {control["action_set"]}'
        )


def _find(options: argparse.Namespace) -> int:
    """Prints the moments found in the library."""
    from ._moments import search as _search
    from ._text.messages import counted

    found = _search.search(' '.join(options.query), options.limit)
    if not found['recordings']:
        contract.note('Run cuju arena sample add or cuju arena recording add PATH to add a match.')
    if options.json:
        contract.emit(found)
        return 0
    match = None
    for result in found['results']:
        if result['match'] != match:
            match = result['match']
            print(f'{match}  {result["title"]}')
        print(f'  {result["minute"]:>6}  t={result["t"]:g}  {result["text"]}')
        print(f'          {result["url"]}')
    moments = counted(found['total'], 'moment')
    print(
        f'{moments} in {found["matched_recordings"]} of {counted(found["recordings"], "recording")}'
    )
    if found['results']:
        address = found['results'][0]['url']
        contract.note(f'Open the first result at its seek time with cuju render open {address!r}.')
    return 0


def _write_captions(options: argparse.Namespace) -> int:
    """Writes or prints the captions of a recording."""
    from ._moments import search
    from ._store import library

    if not Path(options.recording).expanduser().is_file():
        options.recording = references.kept(options.recording, library.matches(), 'm-')
    if options.output:
        path = Path(options.output).expanduser()
        path.write_text(search.captions_vtt(options.recording), encoding='utf-8', newline='\n')
        contract.files([path], options.json)
    elif options.json:
        contract.emit({'cues': search.captions(options.recording)})
    else:
        print(search.captions_vtt(options.recording), end='')
    return 0


def _play_match(options: argparse.Namespace) -> int:
    """Plays a match between two registered teams and prints the recording it saved.

    What the submissions print goes to standard error, so standard output holds the result alone.
    """
    import secrets
    from difflib import get_close_matches

    from ._cli import _words as words
    from ._cli import check as _check
    from ._config import layers
    from ._playing import draw as _draw
    from ._text.messages import MessageError

    defaults = layers.table('play')
    left, left_difficulty = contract.team_value('builtin' if options.left is None else options.left)
    right, right_difficulty = contract.team_value(
        options.right if options.right is not None else defaults['right']
    )
    left, right = references.team(left), references.team(right)
    difficulties = []
    for side, embedded, explicit in (
        ('left', left_difficulty, options.left_difficulty),
        ('right', right_difficulty, options.right_difficulty),
    ):
        if embedded is not None and explicit is not None:
            options._parser.error(f'expected one difficulty for the {side} team')
        if explicit is not None and not 0 <= explicit <= 1:
            options._parser.error(f'expected the {side} difficulty from 0 to 1, found {explicit!r}')
        difficulties.append(embedded if explicit is None else explicit)

    scenario = defaults['scenario'] if options.scenario is None else options.scenario
    if scenario != 'random' and scenario not in words.scenarios():
        close = get_close_matches(scenario, words.scenarios(), n=1)
        raise contract.UsageError(
            options._parser,
            f'expected a shipped scenario, found {scenario!r}',
            hint=f'use --scenario {close[0]}' if close else None,
        )
    requested_seed = defaults['seed'] if options.seed is None else options.seed
    seed = secrets.randbelow(2**31) if requested_seed is None else requested_seed
    pool = None
    if options.pool is not None:
        pool = [references.team(item.strip()) for item in options.pool.split(',') if item.strip()]
    draw = None
    if _draw.RANDOM in (left, right, scenario):
        drawn = _draw.draw_request(left, right, scenario, seed, pool, options.same_team)
        left, right, scenario = drawn['left'], drawn['right'], drawn['scenario']
        draw = drawn['draw']
    submission = _check.optional_choice(options)
    if requested_seed is None or draw is not None:
        contract.note(f'{left} on the left, {right} on the right, {scenario}, seed {seed}')
    from ._playing.play import play

    output = open(os.devnull, 'w') if options.quiet else contextlib.nullcontext(sys.stderr)
    try:
        with (
            contract.Progress(options.quiet) as progress,
            output as submission_output,
            contextlib.redirect_stdout(submission_output),
        ):
            path = play(
                left,
                right,
                scenario,
                seed,
                None,
                options.device,
                tuple(difficulties),
                progress=(lambda share: progress(f'playing, {share:.0%}'))
                if progress.terminal
                else None,
                policy_errors=True,
                draw=draw,
                submission_format=submission,
            )
    except MessageError as error:
        if error.message_id == 'server.teams.scenario_format':
            raise contract.UsageError(options._parser, str(error)) from error
        raise
    summary = path.summary or {}
    home, away = summary.get('teams') or (left, right)
    a, b = summary['score']
    contract.note(f'played {path.stem}  {home} {a}-{b} {away}')
    if options.json:
        sides = (summary.get('provenance') or {}).get('sides') or [
            {'checkpoint': None},
            {'checkpoint': None},
        ]
        contract.emit(
            {
                'files': [str(path)],
                'match': path.stem,
                'path': str(path),
                'score': summary.get('score'),
                'summary': summary,
                'left': left,
                'right': right,
                'scenario': scenario,
                'seed': seed,
                'draw': draw,
                'sides': sides,
                'left_checkpoint': sides[0]['checkpoint'],
                'right_checkpoint': sides[1]['checkpoint'],
            }
        )
    else:
        print(path)
    return 0


def _recordings(options: argparse.Namespace) -> int:
    """Runs one action on the recording library."""
    from ._store import library
    from ._text.messages import counted

    action = options.action
    if action in ('export', 'show') and library.path_of(options.target) is None:
        options.target = references.kept(options.target, library.matches(), 'm-')
    if action == 'export':
        target = Path(options.output or f'{options.target}.cjz').expanduser()
        target.write_bytes(library.bundle(options.target))
        contract.files([target], options.json)
    elif action == 'show':
        _show_recording(library.details(options.target), options.json)
    elif action == 'add':
        from ._import import core

        entries = core.add_recordings(*options.paths)
        if options.json:
            contract.emit({'recordings': entries, 'files': [entry['path'] for entry in entries]})
        else:
            for entry in entries:
                print(entry['path'])
                if not options.quiet:
                    print(core.imported_line(entry), file=sys.stderr)
    elif action in ('remove', 'restore'):
        restore = action == 'restore'
        changed = library.trash_group(options.group, restore=restore)
        where = 'the trash' if restore else 'the library'
        if not changed:
            listing = 'recording list --trash' if restore else 'recording list'
            raise contract.RefusalError(
                f'expected a group in {where}, found {options.group!r}',
                hint=f'run cuju arena {listing}',
            )
        if options.json:
            contract.emit({'group': options.group, 'restored' if restore else 'trashed': changed})
        else:
            print(f'{options.group}  {counted(len(changed), "recording")} moved')
    else:
        from ._store import dates

        day = None
        if options.date is not None:
            try:
                day = dates.day(options.date)
            except ValueError:
                options._parser.error(
                    f'expected today, yesterday or a day as YYYY-MM-DD, found {options.date!r}'
                )
        entries = library.matches(options.trash, options.group)
        if day is not None:
            entries = [entry for entry in entries if (entry.get('created') or '')[:10] == day]
        if not entries:
            if day is not None:
                contract.note('No recordings match this day. Run cuju arena recording list.')
            elif options.trash:
                contract.note('Run cuju arena recording remove --group ID to move a group here.')
            else:
                contract.note('Run cuju arena sample add or cuju arena recording add PATH.')
        if options.json:
            contract.emit({'recordings': entries})
        else:
            contract.listing(
                (
                    entry['id'],
                    (entry.get('created') or '')[:10] or 'unknown',
                    ' and '.join(name or 'unknown' for name in entry.get('teams') or ())
                    or 'unknown',
                    '-'.join(map(str, entry.get('score') or ())) or 'unknown',
                    entry['title'],
                )
                for entry in entries
            )
    return 0


def _show_recording(summary: dict[str, Any], as_json: bool) -> None:
    """Prints a recording's details from its library summary."""
    answer = {
        **{
            key: summary.get(key) for key in ('id', 'title', 'scenario', 'teams', 'score', 'length')
        },
        'date': summary.get('created'),
        'provenance': summary.get('provenance'),
    }
    if as_json:
        contract.emit(answer)
        return
    for key, value in answer.items():
        if key == 'teams' and value:
            value = ' and '.join(name or 'unknown' for name in value)
        elif key == 'score' and isinstance(value, list):
            value = ' to '.join(map(str, value))
        elif key == 'provenance' and value:
            value = json.dumps(value, ensure_ascii=False)
        print(f'{key.capitalize()} {value if value or value == 0 else "unknown"}')


if __name__ == '__main__':
    sys.exit(main())
