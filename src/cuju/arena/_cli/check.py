# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The command `cuju arena check`, which a student runs on a folder before submitting it.

It runs the pre-check of a submission format on a folder that need not be registered, as the
Submission section of a team does. A status of 0 means the format accepts the submission.
A status of 1 means it refused or the check was cut. A status of 2 means the arguments
are invalid. A status of 3 means the format or the environment could not run the check.

`add_folders` registers the folders accepted by a format for `cuju arena team add --each`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import _references as references
from . import _words as words
from . import contract

ACCEPTED, REFUSED, UNFIT = contract.SUCCESS, contract.NEGATIVE, contract.ENVIRONMENT
FAILED = contract.ENVIRONMENT

_TRUE = ('true', 'yes', 'on', '1')
_FALSE = ('false', 'no', 'off', '0')


def format_options(parser: argparse.ArgumentParser) -> None:
    """Adds options that change a submission format."""
    parser.add_argument(
        '--option',
        action='append',
        default=[],
        metavar='KEY=VALUE',
        help='an override such as players=one, see cuju arena format show NAME, may repeat',
    )
    words.offer(
        parser,
        '--preset',
        completion=('open',),
        metavar='NAME',
        help='a preset of format defaults applied before option overrides',
    )


def verdict_options(parser: argparse.ArgumentParser) -> None:
    """Adds options that change a submission check."""
    words.offer(
        parser,
        '--scenario',
        completion=words.scenarios(),
        metavar='SCENARIO',
        help='the scenario for the check, the scenario of the team size by default',
    )
    parser.add_argument(
        '--full',
        action='store_true',
        help='request the full check, its coverage depends on the format',
    )


def register(commands: Any) -> None:
    """Adds the submission check command."""
    parser = contract.leaf(
        commands,
        'check',
        'check a submission under a format',
        './submission --format kaggle --json',
        './team --format strict --full -o verdict.json',
    )
    contract.details(
        parser,
        'Kaggle checks load the agent. Its match actions are checked in play. '
        'Use format list to find formats and format show to see options.',
    )
    parser.add_argument('folder', metavar='PATH', help='the folder of the submission')
    words.offer(
        parser,
        '--format',
        completion=words.FORMATS,
        required=True,
        metavar='NAME',
        help='the submission format',
    )
    format_options(parser)
    verdict_options(parser)
    parser.add_argument(
        '--max-steps',
        type=int,
        metavar='N',
        help='the most steps if the format plays a check match',
    )
    contract.output_path(parser, help_text='the JSON verdict file, standard output by default')


def optional_choice(namespace: argparse.Namespace) -> Any:
    """Reads the submission format if the command names one."""
    if namespace.format is not None:
        return command_choice(namespace.format, namespace.option, namespace.preset)
    if namespace.option or namespace.preset:
        namespace._parser.error('expected --format with --option or --preset')
    return None


def command_choice(name: str, options: list[str], preset: str | None) -> Any:
    from .._text.messages import MessageError

    try:
        return choice_of(name, options, preset)
    except MessageError as error:
        if error.exit_status == contract.NEGATIVE:
            error.exit_status = contract.USAGE
        raise


def _typed(option: Any, text: str) -> Any:
    """Returns the text of `KEY=VALUE` as the type of the option, a wrong text staying as it is."""
    if option.type == 'bool':
        if text.lower() in _TRUE:
            return True
        if text.lower() in _FALSE:
            return False
        return text
    if option.type in ('int', 'float'):
        try:
            return int(text) if option.type == 'int' else float(text)
        except ValueError:
            return text
    if option.type == 'choices':
        return next((item for item in option.choices if str(item) == text), text)
    return text


def choice_of(name: str, items: list[str], preset: str | None = None) -> Any:
    """Returns the `side.Choice` of a format, its preset and `KEY=VALUE` options.

    Raises:
        ArgumentError: If the format is unknown or broken, or an option or the preset does not fit.
    """
    from .._formats import registry as _registry
    from .._formats import side as _side
    from .._text.messages import MessageError

    found = _registry.choosable(name)
    options: dict[str, Any] = {}
    for text in items:
        key, separator, value = text.partition('=')
        if not separator or not key:
            raise MessageError('server.verdicts.option', text=repr(text))
        if key not in found.spec.options:
            raise MessageError(
                'server.formats.options', format=found.label, reason=f'it has no option {key!r}'
            )
        options[key] = _typed(found.spec.options[key], value)
    return _side.choose({'id': found.label, 'options': options, 'preset': preset})


def _tail(outcome: Any) -> str:
    """Returns the last lines that the check's process printed, for a student to read."""
    from .._formats import terminals as _terminals

    if outcome.terminal is None:
        return ''
    text = _terminals.tail(outcome.terminal['run'], outcome.terminal['side'], 4096)
    lines = [line for line in text.splitlines() if line.strip()]
    return '\n'.join(lines[-20:])


def _status(outcome: Any) -> int:
    if outcome.failed is not None:
        return FAILED
    verdict = outcome.verdict
    if verdict.environment:
        return UNFIT
    return ACCEPTED if verdict.accepted else REFUSED


def report_of(outcome: Any, folder: Path, status: int, output: str) -> dict[str, Any]:
    """Returns the verdict of a check as the JSON document that `--json` writes."""
    verdict = outcome.verdict
    return {
        'folder': str(folder),
        'checkpoint': outcome.subject.checkpoint,
        'name': outcome.subject.name,
        'size': outcome.subject.size,
        'format': outcome.format,
        'options': outcome.options,
        'preset': outcome.preset,
        'scenario': outcome.scenario,
        'full': outcome.full,
        'coverage': 'load_only' if outcome.format == 'kaggle/1' else None,
        'max_steps': outcome.max_steps,
        'exit': status,
        'accepted': None if verdict is None else verdict.accepted,
        'environment': None if verdict is None else verdict.environment,
        'cut': outcome.cut,
        'failed': outcome.failed,
        'players': None if verdict is None else verdict.players,
        'stated_name': None if verdict is None else verdict.name,
        'deterministic': None if verdict is None else verdict.deterministic,
        'problems': [] if verdict is None else [found.as_dict() for found in verdict.problems],
        'output': output,
    }


def lines(outcome: Any, status: int, output: str) -> list[str]:
    """Returns a check in lines of English, as the command prints it."""
    verdict = outcome.verdict
    where = f'{outcome.subject.name}, {outcome.format}, checkpoint {outcome.subject.checkpoint}'
    if outcome.failed is not None:
        found = [f'The format {outcome.format} failed, {outcome.failed}', where]
    else:
        word = {ACCEPTED: 'Accepted', REFUSED: 'Refused'}.get(status, 'Not checked')
        found = [f'{word}, {where}']
        if outcome.format == 'kaggle/1':
            found.append('Coverage, agent load. Play a match to check the actions it returns.')
        stated = []
        if verdict.name:
            stated.append(f'the name {verdict.name}')
        if verdict.players is not None:
            stated.append(f'{verdict.players} {"player" if verdict.players == 1 else "players"}')
        if verdict.deterministic is not None:
            stated.append('deterministic' if verdict.deterministic else 'not deterministic')
        if stated:
            found.append('The submission states ' + ', '.join(stated) + '.')
        for problem in verdict.problems:
            detail = f' ({problem.detail})' if problem.detail else ''
            found.append(f'{problem.level} {problem.kind}{detail}: {problem.message}')
    if output:
        found.append('Last lines of the output of the check:')
        found += [f'  {line}' for line in output.splitlines()]
    return found


def run(namespace: argparse.Namespace) -> int:
    """Runs `cuju arena check` and returns its exit status."""
    from cuju.errors import ArgumentError

    from .._formats import verdicts
    from .._registry import teams
    from .._text.messages import MessageError

    choice = command_choice(namespace.format, namespace.option, namespace.preset)
    steps = namespace.max_steps
    if steps is not None and steps < 1:
        error = MessageError('server.verdicts.max_steps', steps=repr(steps))
        error.exit_status = contract.USAGE
        raise error
    folder = Path(namespace.folder).expanduser()
    try:
        scenario = verdicts.check_scenario(namespace.scenario)
        subject = verdicts.subject_of_folder(folder, scenario)
    except ArgumentError as error:
        identifier = namespace.folder
        if getattr(error, 'message_id', None) == 'server.verdicts.folder':
            identifier = references.team(identifier, checkpoint=False)
        if getattr(error, 'message_id', None) == 'server.verdicts.folder' and any(
            team['id'] == identifier and team['kind'] == 'kaggle' for team in teams.teams()
        ):
            raise contract.UsageError(
                namespace._parser,
                f'expected a submission folder, found the registered team {namespace.folder!r}',
                hint=f'run cuju arena team show {identifier} for its folder',
            ) from None
        error.exit_status = contract.USAGE
        raise
    outcome = verdicts.check(
        subject, choice, scenario, namespace.full, steps, isolate=True, keep=False
    )
    status = _status(outcome)
    output = _tail(outcome) if status != ACCEPTED and namespace.verbose else ''
    if output:
        contract.note('Last lines of the output of the check.\n' + output)
    report = report_of(outcome, folder.resolve(), status, output)
    if namespace.output:
        path = Path(namespace.output).expanduser()
        path.write_text(contract.encode(report, contract.schema()) + '\n', encoding='utf-8')
        if status != UNFIT:
            contract.files([path], namespace.json)
    elif status != UNFIT:
        if namespace.json:
            contract.emit(report)
        else:
            print('\n'.join(lines(outcome, status, '')))
    if status == UNFIT:
        problem = (
            next((item.message for item in outcome.verdict.problems), None)
            if outcome.verdict
            else None
        )
        reason = (
            f'the format {outcome.format} failed, {outcome.failed}' if outcome.failed else problem
        )
        error = contract.RefusalError(reason or 'the submission could not be checked')
        error.exit_status = status
        error.payload = report
        raise error
    return status


def add_folders(namespace: argparse.Namespace, control: dict[str, Any] | None) -> int:
    """Checks and registers each submission in a directory."""
    from .._registry import teams

    if not namespace.format:
        namespace._parser.error('expected --format with --each')
    choice = command_choice(namespace.format, namespace.option, namespace.preset)
    made = teams.add_folders(
        namespace.target,
        choice,
        namespace.scenario,
        namespace.size,
        control,
        namespace.full,
    )
    if any(left['environment'] for left in made['excluded']):
        error = contract.RefusalError('one or more submissions could not be checked')
        error.exit_status = contract.ENVIRONMENT
        error.payload = made
        raise error
    if namespace.json:
        contract.emit(made)
    else:
        for team in made['teams']:
            print(f'{team["team"]}  {team["name"]}  {team["action"]}  {team["path"]}')
        for left in made['excluded']:
            print(f'{left["path"]}  {left["reason"]}', file=sys.stderr)
    return REFUSED if made['excluded'] else ACCEPTED
