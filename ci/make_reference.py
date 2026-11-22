# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Write the arena Python and command-line references from the code.

Run with `--check` to compare the committed pages with the public API and parser.
The text before and after the generated section stays in the guide.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import inspect
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'
BEGIN = (
    '<!-- The part below is written by ci/make_reference.py from the code. '
    'Edit the text above it. -->'
)
END = (
    '<!-- The part above is written by ci/make_reference.py. The text below is written by hand. -->'
)

# The guide that teaches each public name of `cuju.arena`. A name of `__all__` that this table
# lacks, and an entry for a name that `__all__` lacks, stop the script.
GUIDES = {
    'Config': 'configuration.md',
    'Evaluation': 'evaluation.md',
    'Frames': 'recording-format.md',
    'PolicyError': 'teams.md',
    'Recorder': 'recording.md',
    'add_recordings': 'importing.md',
    'add_team': 'teams.md',
    'annotate': 'annotations.md',
    'benchmark': 'evaluate-benchmark.md',
    'captions': 'moments-and-search.md',
    'config': 'configuration.md',
    'delete_group': 'recording.md',
    'evaluate': 'evaluation.md',
    'formats': 'formats.md',
    'frames': 'recording-format.md',
    'home': 'configuration.md#the-two-home-folders',
    'moments': 'moments-and-search.md',
    'play': 'matches.md',
    'plugins': 'plugins.md',
    'rate': 'evaluate-ratings.md',
    'record': 'recording.md',
    'recordings': 'recording.md',
    'restore_group': 'recording.md',
    'runs': 'evaluation.md',
    'search': 'moments-and-search.md',
    'statistics': 'recording.md',
}


MADE_BY = {}


COMMAND_GUIDES = {
    'benchmark': 'evaluate-benchmark.md',
    'captions': 'moments-and-search.md#captions',
    'check': 'formats.md#checking-a-folder-from-the-command-line',
    'checkpoint': 'team-files.md',
    'config': 'configuration.md',
    'evaluation': 'evaluation.md',
    'format': 'formats.md#adding-a-format',
    'play': 'matches.md',
    'plugin': 'plugins.md#the-command',
    'rating': 'evaluate-ratings.md#rating-runs',
    'recording': 'recording.md',
    'sample': 'getting-started.md#samples',
    'search': 'moments-and-search.md#searching-moments',
    'team': 'teams.md',
    'tournament': 'evaluate-tournaments.md',
}


def guide_link(guide: str) -> str:
    """Returns a link from the selected reference page to its guide."""
    path, separator, anchor = guide.partition('#')
    relative = path
    return f'[{title_of(path)}]({relative}{separator}{anchor})'


class PageError(Exception):
    """Raised when the code cannot give a page, such as a public name without a guide."""


def cell(text: str) -> str:
    """Returns a text as one cell of a table, on one line with its bars escaped."""
    return ' '.join(text.split()).replace('|', '\\|')


def table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    """Returns the lines of a table of Markdown."""
    lines = ['| ' + ' | '.join(header) + ' |', '| ' + ' | '.join('---' for _ in header) + ' |']
    lines += ['| ' + ' | '.join(cell(value) for value in row) + ' |' for row in rows]
    return lines


def code(text: str) -> str:
    """Returns a text as a code span."""
    fence = '``' if '`' in text else '`'
    padding = ' ' if fence == '``' else ''
    return f'{fence}{padding}{text}{padding}{fence}'


def first_paragraph(thing: Any) -> str:
    """Returns the first paragraph of the docstring of a thing, on one line, or an empty text."""
    text = inspect.getdoc(thing) or ''
    return ' '.join(text.split('\n\n')[0].split())


def title_of(guide: str) -> str:
    """Returns the title of a guide, its first heading."""
    path = DOCS / guide
    if not path.is_file():
        raise PageError(f'ci/make_reference.py: expected the guide docs/{guide}')
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.startswith('# '):
            return line[2:].strip()
    raise PageError(f'ci/make_reference.py: expected a title in docs/{guide}')


# The Python reference.


def signature(thing: Any, name: str) -> str:
    """Returns the call of a function or a class with its parameters and defaults, no types."""
    if inspect.isclass(thing) and issubclass(thing, BaseException):
        bases = ', '.join(_qualified(base) for base in thing.__bases__)
        return f'{name}({bases})'
    found = inspect.signature(thing)
    # A parameter whose name starts with `_` is private, as a name of the package is.
    plain = found.replace(
        parameters=[
            parameter.replace(annotation=inspect.Parameter.empty)
            for parameter in found.parameters.values()
            if not parameter.name.startswith('_')
        ],
        return_annotation=inspect.Signature.empty,
    )
    return f'{name}{plain}'


def _qualified(kind: type) -> str:
    module = kind.__module__
    if module == 'builtins':
        return kind.__qualname__
    if module.startswith('cuju.') and not module.startswith('cuju.arena._'):
        return f'{module}.{kind.__qualname__}'
    return kind.__qualname__


ATTRIBUTE = re.compile(r'^    (\w+): (.*)$')


def attributes(kind: type) -> list[tuple[str, str]]:
    """Returns the attributes that the docstring of a class names, each with its first sentence."""
    text = inspect.getdoc(kind) or ''
    found: list[tuple[str, str]] = []
    inside = False
    for line in text.splitlines():
        if line == 'Attributes:':
            inside = True
            continue
        if not inside:
            continue
        if line and not line.startswith(' '):
            break
        match = ATTRIBUTE.match(line)
        if match:
            found.append((match.group(1), match.group(2)))
        elif line.startswith('        ') and found:
            name, words = found[-1]
            found[-1] = (name, f'{words} {line.strip()}')
    return found


def members(kind: type) -> list[tuple[str, str]]:
    """Returns the public members of a class, each as its code and its description."""
    rows: list[tuple[str, str]] = []
    named: set[str] = set()
    for name, words in attributes(kind):
        named.add(name)
        rows.append((code(name), words))
    for name, value in vars(kind).items():
        if name.startswith('_') or name in named:
            continue
        if isinstance(value, (property, _cached_property())):
            rows.append((code(name), first_paragraph(value)))
        elif isinstance(value, (staticmethod, classmethod)):
            function = value.__func__
            rows.append((code(signature(function, name)), first_paragraph(function)))
        elif inspect.isfunction(value):
            call = signature(value, name).replace('(self, ', '(').replace('(self)', '()')
            rows.append((code(call), first_paragraph(value)))
    return rows


def _cached_property() -> type:
    import functools

    return functools.cached_property


def python_page() -> list[str]:
    """Returns the generated lines of `docs/reference.md`."""
    import cuju.arena as arena

    names = list(arena.__all__)
    missing = [name for name in names if name not in GUIDES]
    if missing:
        raise PageError(
            f'ci/make_reference.py: expected a guide in GUIDES for {", ".join(missing)}'
        )
    unknown = [name for name in GUIDES if name not in names]
    if unknown:
        raise PageError(
            f'ci/make_reference.py: GUIDES names {", ".join(unknown)}, which __all__ lacks'
        )
    rows = []
    classes = []
    for name in sorted(names, key=lambda name: (name.lower(), name)):
        thing = getattr(arena, name)
        guide = GUIDES[name]
        rows.append(
            (
                code(name),
                f'made by {code(MADE_BY[name])}'
                if name in MADE_BY
                # A public module, such as the library of formats, reads as its import.
                else code(f'import cuju.arena.{name}')
                if inspect.ismodule(thing)
                else code(signature(thing, name)),
                first_paragraph(thing),
                guide_link(guide),
            )
        )
        if inspect.isclass(thing):
            classes.append((name, thing))
    lines = ['## Names', '']
    lines += table(('Name', 'Call', 'Description', 'Guide'), rows)
    lines += ['', '## Classes']
    for name, thing in classes:
        lines += ['', f'### {code(name)}', '']
        found = members(thing)
        if found:
            lines += table(('Member', 'Description'), found)
        else:
            lines.append(f'{code(name)} adds no public member of its own.')
    return lines


# The command line.


def command_parser() -> argparse.ArgumentParser:
    """Returns the whole command tree of cuju arena."""
    return importlib.import_module('cuju.arena.__main__').parser()


def _subcommands(parser: argparse.ArgumentParser) -> Any:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _helps(commands: Any) -> dict[str, str]:
    return {choice.dest: choice.help or '' for choice in commands._choices_actions}


def _argument(action: argparse.Action, formatter: argparse.HelpFormatter) -> str:
    if action.choices is not None and action.metavar is None:
        # The values of a choice have a column of their own, and the argument shows its name.
        action = copy.copy(action)
        action.metavar = action.dest.upper()
    if action.option_strings:
        if action.nargs == 0:
            return ', '.join(action.option_strings)
        value = action.metavar or action.dest.upper()
        shown = formatter._format_args(action, value)
        return ', '.join(f'{option} {shown}' for option in action.option_strings)
    value = action.metavar or action.dest.upper()
    return formatter._format_args(action, value)


def _default(action: argparse.Action) -> str:
    value = action.default
    if value is None or value is False or value == [] or value == argparse.SUPPRESS:
        return ''
    if action.nargs == 0 and value is True:
        return ''
    return code(str(value))


def arguments(parser: argparse.ArgumentParser) -> list[tuple[str, str, str, str]]:
    """Returns the arguments and options of a parser that its help shows, as rows of a table."""
    formatter = parser._get_formatter()
    rows = []
    for action in parser._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        if action.help == argparse.SUPPRESS:
            continue
        values = ', '.join(code(str(choice)) for choice in action.choices or ())
        helped = formatter._expand_help(action) if action.help else ''
        rows.append((code(_argument(action, formatter)), values, _default(action), helped))
    return rows


def commands(parser: argparse.ArgumentParser, prefix: str) -> list[dict[str, Any]]:
    """Returns every command under a parser, each with its name, aliases, help and parser."""
    found = []
    subcommands = _subcommands(parser)
    if subcommands is None:
        return found
    helps = _helps(subcommands)
    seen: dict[int, dict[str, Any]] = {}
    for name, child in subcommands.choices.items():
        if id(child) in seen:
            seen[id(child)]['aliases'].append(name)
            continue
        entry = {
            'name': f'{prefix} {name}',
            'aliases': [],
            'help': helps.get(name, ''),
            'parser': child,
        }
        seen[id(child)] = entry
        found.append(entry)
        found += commands(child, entry['name'])
    return found


def cli_page() -> list[str]:
    """Returns the generated lines of `docs/cli.md`."""
    parser = command_parser()
    found = commands(parser, 'cuju arena')
    top = [entry['name'].split(' ')[2] for entry in found if entry['name'].count(' ') == 2]
    missing = [name for name in top if name not in COMMAND_GUIDES]
    if missing:
        raise PageError(
            f'ci/make_reference.py: expected a guide in COMMAND_GUIDES for {", ".join(missing)}'
        )
    unknown = [name for name in COMMAND_GUIDES if name not in top]
    if unknown:
        raise PageError(
            f'ci/make_reference.py: COMMAND_GUIDES names {", ".join(unknown)}, '
            'which the parser lacks'
        )
    lines = ['## Arena root options', '']
    lines += table(('Option', 'Values', 'Default', 'Help'), arguments(parser))
    lines += ['', '## Commands', '']
    rows: list[tuple[str, ...]] = []
    for entry in found:
        guide = COMMAND_GUIDES[entry['name'].split(' ')[2]]
        rows.append(
            (
                f'[{code(entry["name"])}](#{_anchor(entry["name"])})',
                entry['help'],
                guide_link(guide),
            )
        )
    lines += table(('Command', 'Does', 'Guide'), rows)
    for entry in found:
        lines += ['', f'### {code(entry["name"])}', '']
        child = entry['parser']
        rows = []
        if entry['aliases']:
            parent = entry['name'].rsplit(' ', 1)[0]
            rows.append(
                ('Also', ', '.join(code(f'{parent} {alias}') for alias in entry['aliases']))
            )
        if entry['help']:
            rows.append(('Does', entry['help']))
        if child.description:
            rows.append(('Description', child.description))
        if child.epilog:
            kind = 'Example'
            for line in child.epilog.splitlines()[1:]:
                if line.strip() == 'Filters':
                    kind = 'Filter example'
                elif line.strip():
                    rows.append((kind, code(line.strip())))
        if rows:
            lines += table(('Command', code(entry['name'])), rows)
        found_arguments = arguments(child)
        if found_arguments:
            lines += [''] if rows else []
            lines += table(('Argument', 'Values', 'Default', 'Help'), found_arguments)
    return lines


def _anchor(heading: str) -> str:
    """Returns the anchor that GitHub gives a heading that is one code span."""
    return re.sub(r'[^a-z0-9 _-]', '', heading.lower()).replace(' ', '-')


# The routes of the server.


PAGES: dict[str, Callable[[], list[str]]] = {
    'docs/reference.md': python_page,
    'docs/cli.md': cli_page,
}


def written(path: str, root: Path = ROOT, generated: list[str] | None = None) -> str:
    """Returns a page as the script writes it, its text by hand kept and the rest from the code."""
    page = root / path
    if not page.is_file():
        raise PageError(f'{path}: expected the page, with its lead and the lines BEGIN and END')
    text = page.read_text(encoding='utf-8')
    if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(BEGIN) > text.index(END):
        raise PageError(f'{path}: expected the line BEGIN once and the line END once after it')
    lead = text[: text.index(BEGIN)].rstrip('\n')
    notes = text[text.index(END) + len(END) :].strip('\n')
    lines = PAGES[path]() if generated is None else generated
    body = [lead, '', BEGIN, '', *lines, '', END]
    if notes:
        body += ['', notes]
    return '\n'.join(body) + '\n'


def difference(path: str, found: str, wanted: str) -> str | None:
    """Returns the first line that differs between a page and what the script writes, or None."""
    if found == wanted:
        return None
    have = found.splitlines()
    want = wanted.splitlines()
    for number, (line, expected) in enumerate(zip(have, want, strict=False), start=1):
        if line != expected:
            return f'{path}:{number}: expected {expected!r}, found {line!r}'
    number = min(len(have), len(want)) + 1
    if len(want) > len(have):
        return f'{path}:{number}: expected {want[number - 1]!r}, found the end of the page'
    if len(have) > len(want):
        return f'{path}:{number}: expected the end of the page, found {have[number - 1]!r}'
    return f'{path}:{number}: expected the page to end in one line break'


def check(root: Path = ROOT) -> list[str]:
    """Returns the first difference of each page from what the script writes."""
    problems = []
    for path in PAGES:
        try:
            wanted = written(path, root)
        except PageError as error:
            problems.append(str(error))
            continue
        found = (root / path).read_text(encoding='utf-8')
        problem = difference(path, found, wanted)
        if problem:
            problems.append(problem)
    return problems


def main(arguments: list[str] | None = None) -> int:
    """Writes the pages, or checks them with `--check`, and returns the exit status."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument(
        '--check', action='store_true', help='compare the pages with the code and write nothing'
    )
    options = parser.parse_args(arguments)
    try:
        if options.check:
            problems = check()
            for problem in problems:
                print(problem)
            return 1 if problems else 0
        for path in PAGES:
            text = written(path)
            (ROOT / path).write_text(text, encoding='utf-8', newline='\n')
            print(f'wrote {path}')
    except PageError as error:
        print(error)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
