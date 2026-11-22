# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Folds the fragments of parallel work into the files that every change shares.

Usage: `python ci/fragments.py check`, `python ci/fragments.py fold` and
`python ci/fragments.py fold --in-place`, each with `--root FOLDER` for another tree.

A change writes its English strings to `strings/<id>.json` and its changelog entries to
`changes/<id>.md`, at the root of the repository, and leaves the shared files alone.

A file of strings is a JSON object of message ids, each with its English text or an object of the
plural forms `one` and `other` and of exact counts such as `=0`. Every id goes into
`web/languages/en.json`. An id that starts with `server.` also goes into one of the two tables of
messages, `TABLE` of `_text/messages.py` or `TABLE` of `_text/server_messages.py`, the one that
holds its group, the first two parts of the id, else the second. An id that starts with `caption.`
or `describe.` also goes into `_moments/captions.json`. A new id stands after the last id of its
group, the first part of a page id and the first two parts of a server id, or at the end.

A file of changes holds list items under the headings `### Added`, `### Changed`, `### Fixed` and
`### Removed`, as the changelog writes them. Each item goes to the end of the section of that name
under `## [Unreleased]` of `CHANGELOG.md`, which gains the section when it lacks it.

`check` names what stops a fold, an id that two places give, an id that the shared files hold with
another text, a malformed text and an unknown section. `fold` checks, folds and deletes the
fragments. `fold --in-place` keeps them, for a copy of the tree that the checks run on, and
folding the same fragments again changes nothing.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STRINGS = 'strings'
CHANGES = 'changes'
ENGLISH = None
MESSAGES = 'src/cuju/arena/_text/messages.py'
SERVER_MESSAGES = 'src/cuju/arena/_text/server_messages.py'
# The tables of messages in the order a new id looks for its group, and the English captions.
TABLES = (MESSAGES,)
CAPTIONS = 'src/cuju/arena/_moments/captions.json'
CAPTION_IDS = ('caption.', 'describe.')
CHANGELOG = 'CHANGELOG.md'
SECTIONS = ('Added', 'Changed', 'Fixed', 'Removed')
UNRELEASED = '## [Unreleased]'
# The longest line that `ruff format` leaves in `messages.py`.
WIDTH = 100

_ID = re.compile(r'[A-Za-z0-9_.-]+')
_EXACT = re.compile(r'=\d+')
# The plural forms of English, which every counted text holds.
_FORMS = ('one', 'other')
_HEADING = re.compile(r'### (.+)')
_WORD = re.compile(r'\S+\s*|\s+')
# The line that ends the section of unreleased changes, a release or the links of the file.
_AFTER = re.compile(r'## |\[[^\]]+\]: ')


class _Pairs(list):
    """The pairs of a JSON object in the order of the file, with every repeated key."""


def _load(text: str) -> Any:
    """Parses JSON and keeps each object as its pairs, so a repeated key shows."""
    return json.loads(text, object_pairs_hook=_Pairs)


def _plain(value: Any) -> Any:
    """Returns a parsed value with each object as a dictionary."""
    if isinstance(value, _Pairs):
        return {key: _plain(item) for key, item in value}
    return value


def _fragments(root: Path, folder: str, suffix: str) -> list[Path]:
    """Returns the fragments of a folder in the order of their names."""
    return sorted((root / folder).glob(f'*{suffix}'), key=lambda path: path.name)


def _name(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _text_problem(key: str, value: Any) -> str | None:
    """Returns what is malformed about the English text of an id, or None."""
    if not _ID.fullmatch(key) or '.' not in key:
        return 'expected an id of parts joined by periods, in letters, digits, - and _'
    if isinstance(value, str):
        return None
    if not isinstance(value, dict) or not value:
        return 'expected a text or an object of plural forms'
    if not all(isinstance(form, str) for form in value.values()):
        return 'expected each plural form as a text'
    if key.startswith('caption.'):
        return 'expected a text, since captions have no plural forms'
    unknown = [form for form in value if form not in _FORMS and not _EXACT.fullmatch(form)]
    if unknown:
        return f'expected the plural forms one, other and exact counts, found {", ".join(unknown)}'
    lacking = [form for form in _FORMS if form not in value]
    if lacking:
        return f'expected the plural forms one and other, the object lacks {", ".join(lacking)}'
    return None


def read_strings(root: Path) -> tuple[dict[str, Any], list[str]]:
    """Reads the files of strings of a tree.

    Returns:
        The texts by id in the order of the files and of their lines, and the problems, each a
        line that starts with the path of its file.
    """
    strings, _, problems = _read_strings(root)
    return strings, problems


def _read_strings(root: Path) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """Reads the files of strings of a tree, also the path of the file of each id."""
    strings: dict[str, Any] = {}
    owners: dict[str, str] = {}
    problems = []
    for path in _fragments(root, STRINGS, '.json'):
        name = _name(root, path)
        try:
            document = _load(path.read_text(encoding='utf-8'))
        except (UnicodeDecodeError, ValueError) as error:
            problems.append(f'{name}: expected JSON, {error}')
            continue
        if not isinstance(document, _Pairs):
            problems.append(f'{name}: expected a JSON object of message ids')
            continue
        seen = set()
        for key, raw in document:
            value = _plain(raw)
            if key in seen:
                problems.append(f'{name}: {key} stands twice in the file')
                continue
            seen.add(key)
            forms = [form for form, _ in raw] if isinstance(raw, _Pairs) else []
            if len(forms) != len(set(forms)):
                problems.append(f'{name}: {key} gives a plural form twice')
                continue
            problem = _text_problem(key, value)
            if problem:
                problems.append(f'{name}: {key}, {problem}')
            elif key in owners:
                problems.append(f'{name}: {key} also stands in {owners[key]}')
            else:
                owners[key] = name
                strings[key] = value
    return strings, owners, problems


def read_changes(root: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Reads the files of changes of a tree.

    Returns:
        The entries by section in the order of the files, each entry its lines joined, and the
        problems, each a line that starts with the path of its file.
    """
    changes: dict[str, list[str]] = {section: [] for section in SECTIONS}
    problems = []
    for path in _fragments(root, CHANGES, '.md'):
        name = _name(root, path)
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except UnicodeDecodeError as error:
            problems.append(f'{name}: expected UTF-8 text, {error}')
            continue
        found, wrong = _entries(lines)
        problems += [f'{name}: {problem}' for problem in wrong]
        if not wrong and not any(found.values()):
            problems.append(f'{name}: expected at least one entry')
        for section, entries in found.items():
            for entry in entries:
                if entry in changes[section]:
                    problems.append(f'{name}: an entry of {section} stands twice, {entry[:60]!r}')
                else:
                    changes[section].append(entry)
    return changes, problems


def _entries(lines: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Returns the entries of the lines of a changelog section by heading, and their problems."""
    found: dict[str, list[str]] = {}
    problems = []
    section = None
    for number, raw in enumerate(lines, 1):
        line = raw.rstrip()
        if not line:
            continue
        heading = _HEADING.fullmatch(line)
        if line.startswith('#'):
            section = heading.group(1) if heading else None
            if section not in SECTIONS:
                expected = ', '.join(f'### {name}' for name in SECTIONS)
                problems.append(f'line {number}, expected one of {expected}, found {line!r}')
                section = None
            else:
                found.setdefault(section, [])
        elif section is None:
            problems.append(f'line {number}, expected an entry under a heading, found {line!r}')
        elif line.startswith('- '):
            found[section].append(line)
        elif line[0] in ' \t' and found[section]:
            found[section][-1] += '\n' + line
        else:
            problems.append(f'line {number}, expected a list item or its next line, found {line!r}')
    return found, problems


def _registry(source: str) -> tuple[ast.Dict, dict[str, Any]]:
    """Returns the dictionary `TABLE` of the source of a table of messages and its value."""
    for node in ast.parse(source).body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(target, ast.Name) and target.id == 'TABLE':
            if isinstance(node.value, ast.Dict):
                return node.value, ast.literal_eval(node.value)
    raise ValueError('expected the dictionary TABLE in a table of messages')


def _tables(root: Path) -> dict[str, dict[str, Any]]:
    """Returns the messages of each table of a tree by its path."""
    return {name: _registry((root / name).read_text(encoding='utf-8'))[1] for name in TABLES}


def _captions(root: Path) -> dict[str, Any]:
    """Returns the English captions of a tree."""
    return json.loads((root / CAPTIONS).read_text(encoding='utf-8'))


def check(root: Path = ROOT) -> list[str]:
    """Returns what stops a fold of the fragments of a tree, or an empty list."""
    strings, owners, problems = _read_strings(root)
    _, wrong = read_changes(root)
    problems += wrong
    if strings:
        places = [(_captions(root), CAPTIONS)]
        places += [(held, name) for name, held in _tables(root).items()]
        for key, value in strings.items():
            for held, place in places:
                if key in held and held[key] != value:
                    problems.append(
                        f'{owners[key]}: {key} reads {value!r}, and {place} holds {held[key]!r}'
                    )
    if _fragments(root, CHANGES, '.md'):
        lines = (root / CHANGELOG).read_text(encoding='utf-8').split('\n')
        if UNRELEASED not in lines:
            problems.append(f'{CHANGELOG}: expected the heading {UNRELEASED}')
        else:
            start, end = _unreleased(lines)
            found = [_HEADING.fullmatch(line.rstrip()) for line in lines[start:end]]
            names = [heading.group(1) for heading in found if heading]
            for name in sorted({name for name in names if names.count(name) > 1}):
                problems.append(f'{CHANGELOG}: ### {name} stands twice under {UNRELEASED}')
    return problems


def _anchor(keys: list[str], key: str) -> int:
    """Returns where a new id goes among the ids, after the last one of its group or at the end."""
    parts = key.split('.')
    for size in (2, 1) if parts[0] == 'server' else (1,):
        group = '.'.join(parts[:size])
        for index in range(len(keys) - 1, -1, -1):
            if keys[index] == group or keys[index].startswith(group + '.'):
                return index + 1
    return len(keys)


def fold_english(text: str, strings: dict[str, Any]) -> str:
    """Returns the text of the English file with the new ids of `strings` in their places."""
    english = _plain(_load(text))
    keys = list(english)
    for key, value in strings.items():
        if key not in english:
            keys.insert(_anchor(keys, key), key)
            english[key] = value
    return json.dumps({key: english[key] for key in keys}, ensure_ascii=False, indent=4) + '\n'


def _literal(text: str) -> str:
    """Returns a text as the string literal that `ruff format` keeps, in single quotes unless
    double quotes save escapes."""
    quote = '"' if text.count("'") > text.count('"') else "'"
    shown = []
    for character in text:
        if character in ('\\', quote):
            shown.append('\\' + character)
        elif character == '\n':
            shown.append('\\n')
        elif character == '\t':
            shown.append('\\t')
        elif character.isprintable():
            shown.append(character)
        else:
            shown.append(character.encode('unicode_escape').decode('ascii'))
    return quote + ''.join(shown) + quote


def _pieces(text: str, room: int) -> list[str]:
    """Returns a text as literals of at most `room` characters, cut after the spaces of words."""
    pieces = ['']
    for word in _WORD.findall(text):
        if pieces[-1] and len(_literal(pieces[-1] + word)) > room:
            pieces.append('')
        pieces[-1] += word
    return [_literal(piece) for piece in pieces]


def entry_lines(key: str, value: Any, indent: int = 4) -> list[str]:
    """Returns the lines of an entry of `MESSAGES` as `ruff format` writes them."""
    pad = ' ' * indent
    head = f'{pad}{_literal(key)}: '
    if isinstance(value, dict):
        forms = [entry_lines(form, text, indent + 4) for form, text in value.items()]
        return [head + '{', *[line for lines in forms for line in lines], pad + '},']
    line = f'{head}{_literal(value)},'
    if len(line) <= WIDTH:
        return [line]
    inner = ' ' * (indent + 4)
    pieces = _pieces(value, WIDTH - len(inner))
    return [head + '(', *[inner + piece for piece in pieces], pad + '),']


def table_of(key: str, tables: dict[str, dict[str, Any]]) -> str:
    """Returns the path of the table that takes a server id, the one that holds it or its group.

    A group is the first two parts of the id. When no table holds it, the id goes to the table of
    the page and its server, `_text/server_messages.py`.
    """
    for name, held in tables.items():
        if key in held:
            return name
    group = '.'.join(key.split('.')[:2])
    for name, held in tables.items():
        if any(other == group or other.startswith(group + '.') for other in held):
            return name
    return MESSAGES


def fold_captions(text: str, strings: dict[str, Any]) -> str:
    """Returns the text of the English captions with the new ids of `strings` in their places."""
    captions = json.loads(text)
    keys = list(captions)
    for key, value in strings.items():
        if key.startswith(CAPTION_IDS) and key not in captions:
            keys.insert(_anchor(keys, key), key)
            captions[key] = value
    return json.dumps({key: captions[key] for key in keys}, ensure_ascii=False, indent=4) + '\n'


def fold_messages(source: str, strings: dict[str, Any]) -> str:
    """Returns the source of a table of messages with the new server ids of `strings` in it.

    The caller picks the ids of the table, as `table_of` does.
    """
    wanted = {key: value for key, value in strings.items() if key.startswith('server.')}
    _, expected = _registry(source)
    for key, value in wanted.items():
        node, registry = _registry(source)
        if key in registry:
            continue
        lines = source.split('\n')
        keys = list(registry)
        index = _anchor(keys, key)
        if index < len(keys):
            # The entry ends where the next one starts, above the comments that lead it.
            row = node.keys[index].lineno - 1
            while lines[row - 1].lstrip().startswith('#'):
                row -= 1
        else:
            row = node.end_lineno - 1
        lines[row:row] = entry_lines(key, value)
        source = '\n'.join(lines)
        expected[key] = value
    if _registry(source)[1] != expected:
        raise ValueError('the fold of a table of messages lost or changed a message')
    return source


def fold_changelog(text: str, changes: dict[str, list[str]]) -> str:
    """Returns the changelog with the new entries of `changes` under `## [Unreleased]`."""
    lines = text.split('\n')
    start, end = _unreleased(lines)
    # The lines above the first heading, then the lines of each section in the order of the file.
    names: list[str] = []
    blocks: dict[str, list[str]] = {'': []}
    for line in lines[start:end]:
        heading = _HEADING.fullmatch(line.rstrip())
        if heading:
            names.append(heading.group(1))
        blocks.setdefault(names[-1] if names else '', []).append(line)
    folded = False
    for section in SECTIONS:
        present = _entries(blocks.get(section, []))[0].get(section, [])
        new = [entry for entry in changes.get(section, []) if entry not in present]
        if not new:
            continue
        folded = True
        if section not in blocks:
            earlier = SECTIONS[: SECTIONS.index(section)]
            later = [name for name in names if name not in earlier]
            names.insert(names.index(later[0]) if later else len(names), section)
            blocks[section] = [f'### {section}', '', *new]
        else:
            blocks[section] = [*_trimmed(blocks[section]), *new]
    if not folded:
        return text
    body = _trimmed(blocks[''])
    for name in names:
        body += ['', *_trimmed(blocks[name])]
    return '\n'.join([*lines[:start], *body, '', *lines[end:]])


def _unreleased(lines: list[str]) -> tuple[int, int]:
    """Returns the rows of the lines under `## [Unreleased]`, the first and the one after."""
    start = lines.index(UNRELEASED) + 1
    end = next((row for row in range(start, len(lines)) if _AFTER.match(lines[row])), len(lines))
    return start, end


def _trimmed(lines: list[str]) -> list[str]:
    """Returns lines without the blank ones at their end."""
    kept = list(lines)
    while kept and not kept[-1].strip():
        kept.pop()
    return kept


def _write(path: Path, text: str) -> bool:
    """Writes a text file with LF line ends when its text changes, and returns whether it did."""
    if path.read_text(encoding='utf-8') == text:
        return False
    path.write_text(text, encoding='utf-8', newline='\n')
    return True


def fold(root: Path = ROOT, in_place: bool = False) -> list[str]:
    """Folds the fragments of a tree into its shared files.

    Args:
        root: The root of the tree.
        in_place: Whether the fragments stay, as for a copy of the tree that the checks run on.
            Otherwise the fold deletes them, with their folders once empty.

    Returns:
        The problems that stopped the fold before it wrote anything, or an empty list.
    """
    problems = check(root)
    if problems:
        return problems
    strings, _ = read_strings(root)
    changes, _ = read_changes(root)
    if strings:
        tables = _tables(root)
        for name in TABLES:
            held = {key: value for key, value in strings.items() if table_of(key, tables) == name}
            table = root / name
            _write(table, fold_messages(table.read_text(encoding='utf-8'), held))
        captions = root / CAPTIONS
        _write(captions, fold_captions(captions.read_text(encoding='utf-8'), strings))
    if any(changes.values()):
        changelog = root / CHANGELOG
        _write(changelog, fold_changelog(changelog.read_text(encoding='utf-8'), changes))
    if not in_place:
        for folder, suffix in ((STRINGS, '.json'), (CHANGES, '.md')):
            for path in _fragments(root, folder, suffix):
                path.unlink()
            if (root / folder).is_dir() and not any((root / folder).iterdir()):
                (root / folder).rmdir()
    return []


def main(arguments: list[str] | None = None) -> int:
    """Runs `check` or `fold` and returns the exit status, 1 when a problem stops it."""
    parser = argparse.ArgumentParser(
        prog='ci/fragments.py', description='Folds the fragments of parallel work.'
    )
    parser.add_argument('command', choices=('check', 'fold'))
    parser.add_argument(
        '--in-place',
        action='store_true',
        help='keep the fragments after a fold, as for a copy of the tree that the checks run on',
    )
    parser.add_argument('--root', type=Path, default=ROOT, help='the root of the tree to read')
    options = parser.parse_args(arguments)
    if options.in_place and options.command != 'fold':
        parser.error('expected --in-place with fold')
    root = options.root.resolve()
    strings, _ = read_strings(root)
    changes, _ = read_changes(root)
    problems = check(root) if options.command == 'check' else fold(root, options.in_place)
    for problem in problems:
        print(problem)
    if problems:
        return 1
    done = 'checked' if options.command == 'check' else 'folded'
    print(f'{done} {len(strings)} strings and {sum(map(len, changes.values()))} changelog entries')
    return 0


if __name__ == '__main__':
    sys.exit(main())
