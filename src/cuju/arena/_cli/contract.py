# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Arguments, output and errors shared by the command line."""

from __future__ import annotations

import argparse
import contextlib
import errno
import json
import os
import stat
import sys
import textwrap
import traceback
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextvars import ContextVar
from difflib import get_close_matches
from pathlib import Path
from typing import Any

SUCCESS, NEGATIVE, USAGE, ENVIRONMENT, INTERNAL, INTERRUPTED = 0, 1, 2, 3, 70, 130


class UsageError(Exception):
    """An argument rejected by a particular parser."""

    def __init__(
        self, parser: argparse.ArgumentParser, message: str, *, hint: str | None = None
    ) -> None:
        super().__init__(message)
        self.parser = parser
        self.hint = hint


class RefusalError(Exception):
    """A negative answer with a message for the caller."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class Parser(argparse.ArgumentParser):
    """Reports usage through the same channel as other failures."""

    def __init__(self, **keywords: Any) -> None:
        super().__init__(
            allow_abbrev=False, formatter_class=argparse.RawDescriptionHelpFormatter, **keywords
        )
        self.set_defaults(_parser=self)

    def error(self, message: str) -> Any:
        raise UsageError(self, message)

    def _check_value(self, action: argparse.Action, value: Any) -> None:
        if isinstance(action, argparse._SubParsersAction) and value not in action.choices:
            matches = get_close_matches(value, action.choices, n=1)
            hint = f'run {self.prog} {matches[0]} --help' if matches else None
            raise UsageError(self, f'expected a command, found {value!r}', hint=hint)
        super()._check_value(action, value)

    def parse_args(self, args=None, namespace=None):
        options, extra = self.parse_known_args(args, namespace)
        if extra:
            selected = options._parser
            word = extra[0].partition('=')[0]
            matches = get_close_matches(word, selected._option_string_actions, n=1)
            hint = None
            if word.startswith('--') and matches:
                hint = f'run {selected.prog} --help for {matches[0]}'
            raise UsageError(
                selected,
                f'expected an option or argument of this command, found {extra[0]!r}',
                hint=hint,
            )
        for key in ('json', 'quiet', 'verbose', 'home', 'config', 'overrides'):
            entries = [
                vars(options).pop(f'_global_{depth}_{key}')
                for depth in range(3)
                if f'_global_{depth}_{key}' in vars(options)
            ]
            if key in ('config', 'overrides'):
                value = [entry for group in entries for entry in group]
            else:
                value = entries[-1] if entries else getattr(options, key, None)
            setattr(options, key, value)
        return options


def globals_for(parser: Parser, *, with_json: bool = True) -> None:
    """Adds options at one level and keeps repeated values in command line order."""
    depth = len(parser.prog.split()) - 2

    def add(*flags: str, key: str | None = None, **options: Any) -> None:
        name = key or flags[-1][2:].replace('-', '_')
        if not any(flag in parser._option_string_actions for flag in flags):
            parser.add_argument(
                *flags, dest=f'_global_{depth}_{name}', default=argparse.SUPPRESS, **options
            )

    if with_json:
        add('--json', action='store_true', help='print the answer as JSON')
    levels = parser.add_mutually_exclusive_group()
    for short, name, help_text in (
        ('-q', 'quiet', 'print only errors on standard error'),
        ('-v', 'verbose', 'print diagnostic lines and tracebacks'),
    ):
        levels.add_argument(
            short,
            f'--{name}',
            dest=f'_global_{depth}_{name}',
            action='store_true',
            default=argparse.SUPPRESS,
            help=help_text,
        )
    add('--home', metavar='DIR', help='the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena')
    add(
        '--config', action='append', metavar='FILE', help='a TOML or JSON settings file, may repeat'
    )
    add(
        '--set',
        key='overrides',
        action='append',
        metavar='KEY=VALUE',
        help='a final setting such as play.seed=7, may repeat',
    )


def group(commands: Any, noun: str, summary: str) -> Any:
    """Adds a group of commands."""
    parser = commands.add_parser(noun, help=summary, description=summary.capitalize() + '.')
    return parser.add_subparsers(dest='action', metavar='COMMAND')


def leaf(commands: Any, word: str, summary: str, *examples: str) -> Parser:
    """Adds a command with its summary and complete examples."""
    parser = commands.add_parser(word, help=summary, description=summary.capitalize() + '.')
    parser.epilog = '\n'.join(
        ['Examples', *(f'  {parser.prog} {example}'.rstrip() for example in examples)]
    )
    return parser


def details(parser: Parser, prose: str) -> None:
    """Adds a paragraph to command help."""
    parser.description += '\n\n' + textwrap.fill(prose, width=76, break_on_hyphens=False)


def output_path(
    parser: Parser, metavar: str = 'FILE', *, help_text: str = 'the path of the written product'
) -> None:
    """Adds the option that names the product."""
    parser.add_argument('-o', '--output', metavar=metavar, help=help_text)


def device(parser: Parser, *, default: str | None = 'cpu') -> None:
    """Adds the option that names the device."""
    default_help = default if default is not None else 'the device of the settings'
    action = parser.add_argument(
        '--device',
        type=device_name,
        default=default,
        metavar='DEVICE',
        help=f'cpu, cuda or cuda:N, {default_help} by default',
    )
    action.completion = ('cpu', 'cuda', 'cuda:0')


def device_name(value: str) -> str:
    if value in ('cpu', 'cuda') or (
        value.startswith('cuda:') and value[5:].isascii() and value[5:].isdigit()
    ):
        return value
    raise argparse.ArgumentTypeError(f'expected cpu, cuda or cuda:N, found {value!r}')


def team_value(value: str) -> tuple[str, float | None]:
    """Reads a built-in difficulty or returns the named team."""
    kind, separator, suffix = value.partition(':')
    if kind != 'builtin' or not separator:
        return value, None
    try:
        difficulty = float(suffix)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f'expected a built-in difficulty from 0 to 1, found {suffix!r}'
        ) from error
    if not 0 <= difficulty <= 1:
        raise argparse.ArgumentTypeError(
            f'expected a built-in difficulty from 0 to 1, found {suffix!r}'
        )
    return kind, difficulty


def team_name(value: str) -> str:
    """Checks a team argument and retains its spelling."""
    team_value(value)
    return value


_current: ContextVar[argparse.Namespace] = ContextVar('cli_options')


@contextlib.contextmanager
def invocation(options: argparse.Namespace) -> Iterator[None]:
    token = _current.set(options)
    try:
        yield
    finally:
        _current.reset(token)


def schema() -> str:
    options = _current.get()
    return '.'.join(options._parser.prog.split()[1:]) + '/1'


def encode(payload: Mapping[str, Any], name: str) -> str:
    return json.dumps({'schema': name, **payload}, indent=2, ensure_ascii=True)


def emit(payload: Mapping[str, Any]) -> None:
    """Prints one versioned answer."""
    print(encode(payload, schema()))


def files(paths: list[Path], as_json: bool) -> None:
    """Prints the paths of written products."""
    if as_json:
        emit({'files': [str(path) for path in paths]})
    else:
        for path in paths:
            print(path)


def note(message: str) -> None:
    """Prints a note unless the caller has selected quiet output."""
    if not _current.get().quiet:
        print(message, file=sys.stderr)


def listing(rows: Iterable[Sequence[Any]]) -> None:
    """Prints one row per item, with two spaces between columns."""
    for row in rows:
        print('  '.join(' '.join(str(value).split()) for value in row))


def list_answer(
    key: str, entries: Sequence[Any] | Mapping[str, Any], rows: Iterable[Sequence[Any]], empty: str
) -> None:
    """Prints a listing and offers a next command when it is empty."""
    if _current.get().json:
        emit({key: entries})
    else:
        listing(rows)
    if not entries:
        note(empty)


class Progress:
    """Writes finished units to standard error and clears a terminal line on close."""

    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet
        self.terminal = sys.stderr.isatty() and not quiet
        self.width = 0

    def __call__(self, text: str) -> None:
        if self.quiet:
            return
        if self.terminal:
            print('\r' + text.ljust(self.width), end='', file=sys.stderr, flush=True)
            self.width = len(text)
        else:
            print(text, file=sys.stderr, flush=True)

    def close(self) -> None:
        if self.width:
            print('\r' + ' ' * self.width + '\r', end='', file=sys.stderr, flush=True)

    def __enter__(self) -> Progress:
        return self

    def __exit__(self, *_error: Any) -> None:
        self.close()


_NEXT_COMMAND = {
    'server.benchmark.teams': 'team list',
    'server.benchmark.team': 'team list',
    'server.benchmark.unknown_task': 'benchmark tasks',
    'server.evaluate.unknown': 'evaluation list',
    'server.evaluate.policy_missing': 'evaluation run --help',
    'server.checkpoints.none': 'team add --help',
    'server.formats.unknown': 'format list',
    'server.formats.version': 'format list',
    'server.library.unknown': 'recording list',
    'server.plugins.missing': 'plugin list',
    'server.ratings.no_run': 'rating list',
    'server.search.target': 'recording list',
    'server.teams.unknown': 'team list',
    'server.tournament.entrants': 'tournament new --help',
    'server.tournament.preset': 'tournament presets',
}


def fail(
    parser: argparse.ArgumentParser,
    error: BaseException,
    status: int,
    *,
    as_json: bool,
    verbose: bool,
    hint: str | None = None,
    usage: bool = True,
) -> int:
    """Prints a usage, environment, refusal or internal error."""
    if status == USAGE and usage:
        parser.print_usage(sys.stderr)
    text = str(error) or ('interrupted' if status == INTERRUPTED else type(error).__name__)
    if status == INTERNAL:
        text = f'{type(error).__name__}, {text}'
    text = ' '.join(text.splitlines())
    source = error
    if not hasattr(source, 'message_id') and hasattr(error.__cause__, 'message_id'):
        source = error.__cause__
    message_id = getattr(source, 'message_id', None)
    if hint is None:
        hint = getattr(error, 'hint', None)
    if hint is None and message_id in _NEXT_COMMAND:
        hint = f'run cuju arena {_NEXT_COMMAND[message_id]}'
    if hint is None:
        hint = f'run {parser.prog} --help'
        if status == INTERNAL:
            hint = f'run {parser.prog} with --verbose and report the error'
        elif status == INTERRUPTED:
            hint = f'run {parser.prog} again to try again'
    if verbose:
        traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
    print(f'{parser.prog}: error: {text}', file=sys.stderr)
    print(f'{parser.prog}: hint: {hint}', file=sys.stderr)
    if as_json:
        payload = getattr(error, 'payload', {})
        print(
            encode(
                {
                    'error': text,
                    'error_id': message_id,
                    'error_values': getattr(source, 'values', None),
                    'hint': hint,
                    'exit': status,
                    **{key: value for key, value in payload.items() if key != 'exit'},
                },
                'error/1',
            ),
            flush=True,
        )
    return status


def closed_output() -> int:
    """Closes the output quietly after its reader leaves."""
    with contextlib.suppress(OSError, AttributeError, ValueError):
        with open(os.devnull, 'wb') as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
    return SUCCESS


def flushed(status: int) -> int:
    """Flushes the command answer before returning its status."""
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        return closed_output()
    except OSError as error:
        if os.name == 'nt' and error.errno == errno.EINVAL:
            with contextlib.suppress(OSError, AttributeError, ValueError):
                if stat.S_ISFIFO(os.fstat(sys.stdout.fileno()).st_mode):
                    return closed_output()
        raise
    return status


@contextlib.contextmanager
def environment(values: dict[str, str]) -> Iterator[None]:
    """Applies environment variables for the duration of one command."""
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
