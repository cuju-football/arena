# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks the prose of the docs, the README and the changelog against the rules of the project.

Run it as `python ci/check_prose.py`, and with `--count` to also print how many lines of the
docstrings and comments of the sources break the same rules, which fails nothing.
tests/render/test_stale.py runs the check on the repository.

Prose is written with words, commas and periods. A colon, a semicolon, a bracket, a dash, a
question mark and an exclamation mark are marks of another kind, and each one outside code is a
problem with its file and its line. Code is a fenced block, a code span, an indented block after
a blank line, a table, a link's address, an address on its own, a tag of HTML and a date. The
marks of Markdown itself, such as the hyphen of a list item and the brackets of a link, are no
prose either, and a quoted text, such as the name of a button, keeps its own marks.

A paragraph opens with what a thing does. One that opens with No, Not, Never, Nothing, Without
or Only is a problem with its file and its line.

Two rules of the text of the page and its docs hold beside those. No centered dot joins facts in
the messages of the page or of the server, or in a script or a style sheet of the page, with the
window title as the one exception, since a browser tab is not the page. Facts stand side by side
as chips, pairs or a list instead. Spelling is American in the messages, the docs, the changes
and the Unreleased section of the changelog, so colour, grey, licence, centre, metre, behaviour
and favour are problems with their file and line. A code span, a fenced block and an address
keep their own spelling.
"""

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ('docs', 'changes')
FILES = ('README.md', 'CHANGELOG.md')
SOURCES = ('src', 'tests', 'ci')
PACKAGE = 'src/cuju/arena'
ENGLISH = f'{PACKAGE}/web/languages/en.json'
TABLES = (f'{PACKAGE}/_text/messages.py',)
WEB = f'{PACKAGE}/web'
# The message that keeps a centered dot, the title of the window, since a browser tab is no page.
DOTTED = ('structure.title',)
DOT = ' · '
BRITISH = re.compile(
    r'\b(?:colour|grey|licence|centre|centred|(?:kilo|milli|centi)?metre|behaviour|favour)\w*',
    re.IGNORECASE,
)
JOINED = 'joins facts with a centered dot'
SPELLED = 'the British way, where spelling is American'
PLACEHOLDER = re.compile(r'\{[^}]*\}')
UNRELEASED = '## [Unreleased]'

MARKS = {
    ':': 'a colon',
    ';': 'a semicolon',
    '(': 'a bracket',
    ')': 'a bracket',
    '\u2014': 'a dash',
    '\u2013': 'a dash',
    '?': 'a question mark',
    '!': 'an exclamation mark',
}
RULE = 'where prose keeps to words, commas and periods'
DASH = re.compile(r'(?<=\s)-{1,3}(?=\s)')
NEGATION = re.compile(r'(No|Not|Never|Nothing|Without|Only)\b')

FENCE = re.compile(r'^\s{0,3}(```|~~~)')
RELEASE = re.compile(r'^## \[[^\]]+\]')
SPAN = re.compile(r'(`+)(?:(?!\n[ \t]*\n).)+?\1', re.DOTALL)
QUOTED = re.compile('"[^"\n]*"|\u201c[^\u201d\n]*\u201d')
IMAGE = re.compile(r'!\[')
ADDRESS = re.compile(r'\]\([^)\n]*\)|\]\[[^\]\n]*\]')
DEFINITION = re.compile(r'^ {0,3}\[[^\]\n]+\]:[^\n]*$', re.MULTILINE)
BARE = re.compile(r'<?https?://[^\s<>)]+>?')
TAG = re.compile(r'</?[A-Za-z][^>\n]*>|<!--.*?-->', re.DOTALL)
DATE = re.compile(r'\b\d{4}-\d{2}-\d{2}\b')
TIME = re.compile(r'\b\d{1,2}:\d{2}(?::\d{2})?\b')
START = re.compile(r'^\s*(?:>\s*)*(?:[-*+]\s+|\d+[.)]\s+|#{1,6}\s+)?(?:\*\*|__|\*|_)?')


def _blank(match):
    """Returns the newlines of a matched text, so the lines after it keep their numbers."""
    return '\n' * match.group().count('\n')


def _space(match):
    """Returns a space and the newlines of a matched text, which keeps the words around apart."""
    return ' ' + _blank(match)


def prose_lines(text):
    """Returns the prose of a Markdown text by line, code and the marks of Markdown left out.

    Each item is the number of the line, its prose and whether it opens a paragraph.
    """
    lines = text.split('\n')
    kept = []
    fenced = False
    indented = False
    before = ''
    for line in lines:
        if FENCE.match(line):
            fenced = not fenced
            kept.append('')
        elif fenced or line.lstrip().startswith('|') or RELEASE.match(line):
            kept.append('')
        elif line.startswith(('    ', '\t')) and (indented or not before.strip()):
            # An indented line after a blank line is code, unless a list item goes on.
            indented = not _continues(kept)
            kept.append(line if not indented else '')
        else:
            indented = False
            kept.append(line)
        before = line
    text = '\n'.join(kept)
    for expression in (SPAN, TAG):
        text = expression.sub(_blank, text)
    text = IMAGE.sub('[', text)
    for expression in (DEFINITION, ADDRESS, BARE, QUOTED, DATE, TIME):
        text = expression.sub(_space, text)
    found = []
    opening = True
    for number, line in enumerate(text.split('\n'), 1):
        original = lines[number - 1]
        if not original.strip():
            opening = True
            continue
        item = re.match(r'\s*(?:[-*+]|\d+[.)])\s+|\s{0,3}#{1,6}\s', original)
        prose = START.sub('', line, count=1) if (opening or item) else line
        if re.fullmatch(r'\s*([-*_]\s*){3,}', original) or re.fullmatch(r'\s*(=+|-+)\s*', original):
            prose = ''
        found.append((number, prose, bool(opening or item)))
        opening = False
    return found


def _continues(kept):
    """Returns whether the lines so far end inside a list item, which an indented line goes on."""
    for line in reversed(kept):
        if not line.strip():
            continue
        if re.match(r'\s*(?:[-*+]|\d+[.)])\s+', line):
            return True
        if not line.startswith(('    ', '\t')):
            return False
    return False


def text_problems(text):
    """Returns the problems of a Markdown text as the line and what is wrong there."""
    problems = []
    for number, prose, opens in prose_lines(text):
        marks = dict.fromkeys(MARKS[char] for char in prose if char in MARKS)
        if DASH.search(prose):
            marks['a dash'] = None
        for mark in marks:
            problems.append((number, f'holds {mark}, {RULE}'))
        if opens:
            negation = NEGATION.match(prose.lstrip())
            if negation:
                problems.append((number, f'opens with {negation.group(1)}, a negation'))
    return problems


def prose_files(root):
    """Returns the Markdown files that the check reads, from the root."""
    root = Path(root)
    files = [root / name for name in FILES if (root / name).is_file()]
    for folder in FOLDERS:
        files += sorted((root / folder).rglob('*.md'))
    return files


def _british(text):
    """Returns the British spellings of a text, each once and in lower case."""
    return list(dict.fromkeys(match.group().lower() for match in BRITISH.finditer(text)))


def spelling_problems(text):
    """Returns the lines of a Markdown text that spell a word the British way, code left out."""
    inside = False
    kept = []
    for line in text.split('\n'):
        if FENCE.match(line):
            inside = not inside
            kept.append('')
        else:
            kept.append('' if inside else line)
    text = SPAN.sub(_blank, '\n'.join(kept))
    for expression in (ADDRESS, BARE):
        text = expression.sub(_space, text)
    found = []
    for number, line in enumerate(text.split('\n'), 1):
        for word in _british(line):
            found.append((number, f'spells {word} {SPELLED}'))
    return found


def unreleased(text):
    """Returns a changelog with every line outside its Unreleased section blank."""
    lines = text.split('\n')
    kept = []
    inside = False
    for line in lines:
        if RELEASE.match(line):
            inside = line.startswith(UNRELEASED)
        kept.append(line if inside else '')
    return '\n'.join(kept)


def _messages_of(node, key=''):
    """Returns the id and the text of every message of a mapping, plural forms included."""
    if isinstance(node, str):
        return [(key, node)]
    found = []
    if isinstance(node, dict):
        for name, value in node.items():
            found += _messages_of(value, f'{key}.{name}' if key else name)
    return found


def _id_of(key, starts):
    """Returns the id of a message that holds a text, which a plural form extends."""
    while key and key not in starts and '.' in key:
        key = key.rpartition('.')[0]
    return key


def message_problems(root=ROOT):
    """Returns the problems of the messages of the page and of the server as file, line, problem.

    A message joins no facts with a centered dot, apart from the title of the window, and spells
    every word the American way.
    """
    root = Path(root)
    found = []
    path = root / ENGLISH
    if path.is_file():
        lines = path.read_text(encoding='utf-8').split('\n')
        starts = {}
        for number, line in enumerate(lines, 1):
            match = re.match(r'    "([^"]+)": ', line)
            if match:
                starts[match.group(1)] = number
        for key, text in _messages_of(json.loads('\n'.join(lines))):
            top = _id_of(key, starts)
            number = starts.get(top, 1)
            if DOT in text and top not in DOTTED:
                found.append((ENGLISH, number, JOINED))
            for word in _british(PLACEHOLDER.sub('', text)):
                found.append((ENGLISH, number, f'spells {word} {SPELLED}'))
    for table in TABLES:
        path = root / table
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:
            tree = ast.parse('')
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and ' ' in node.value:
                if DOT in node.value:
                    found.append((table, node.lineno, JOINED))
                for word in _british(PLACEHOLDER.sub('', node.value)):
                    found.append((table, node.lineno, f'spells {word} {SPELLED}'))
    return found


def web_problems(root=ROOT):
    """Returns the lines of the scripts and style sheets of the page that join facts with a dot.

    A line of comment is no text of the page, so it may hold the dot of the window title.
    """
    root = Path(root)
    found = []
    base = root / WEB
    if not base.is_dir():
        return found
    for path in sorted(base.rglob('*')):
        name = path.relative_to(root).as_posix()
        if path.suffix not in ('.js', '.mjs', '.css', '.html') or 'vendor' in name:
            continue
        block = False
        for number, line in enumerate(path.read_text(encoding='utf-8').split('\n'), 1):
            stripped = line.strip()
            if block or stripped.startswith(('//', '/*', '*')):
                block = (block or stripped.startswith('/*')) and '*/' not in stripped
                continue
            if DOT in line:
                found.append((name, number, JOINED))
    return found


def prose_problems(root=ROOT):
    """Returns every problem of the prose of the docs, the README and the changelog.

    The spelling of the docs, the dots and the spelling of the messages, and the dots of the
    scripts of the page add theirs.
    """
    root = Path(root)
    problems = []
    for path in prose_files(root):
        name = path.relative_to(root).as_posix()
        text = path.read_text(encoding='utf-8')
        found = text_problems(text)
        found += spelling_problems(unreleased(text) if name == 'CHANGELOG.md' else text)
        for number, problem in sorted(found):
            problems.append(f'{name}:{number}: {problem}')
    for name, number, problem in message_problems(root) + web_problems(root):
        problems.append(f'{name}:{number}: {problem}')
    return problems


def _comment_texts(text, hash_comments):
    """Returns the comments of a source as its lines of prose."""
    found = []
    if hash_comments:
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
        except (tokenize.TokenError, SyntaxError, IndentationError):
            return found
        found += [token.string.lstrip('#') for token in tokens if token.type == tokenize.COMMENT]
    else:
        for match in re.finditer(r'(?m)^[ \t]*//([^\n]*)$|/\*(.*?)\*/', text, re.DOTALL):
            found += (match.group(1) or match.group(2) or '').split('\n')
    return found


def _docstrings(text):
    """Returns the lines of the docstrings of a module of Python."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    kinds = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, kinds):
            found += (ast.get_docstring(node) or '').split('\n')
    return found


def source_counts(root=ROOT):
    """Returns how many lines of docstrings and of comments break the rules, and how many exist.

    The count reads the sources of the package, the tests and the checks, the code of others left
    out. It tells where the comments stand and fails nothing.
    """
    root = Path(root)
    counts = {'docstrings': [0, 0], 'comments': [0, 0]}
    for folder in SOURCES:
        for path in sorted((root / folder).rglob('*')):
            name = path.relative_to(root).as_posix()
            if path.suffix not in ('.py', '.js', '.mjs', '.css') or 'vendor' in name:
                continue
            text = path.read_text(encoding='utf-8')
            python = path.suffix == '.py'
            groups = [('comments', _comment_texts(text, python))]
            if python:
                groups.append(('docstrings', _docstrings(text)))
            for kind, lines in groups:
                for line in lines:
                    if not line.strip():
                        continue
                    counts[kind][1] += 1
                    # The line of the licence, a heading of a docstring such as Args and an
                    # entry under it are structure, which the rules leave alone.
                    if re.match(r'\s*[\w-]+: ', line) or re.match(r'\s*\w+:$', line):
                        continue
                    if text_problems(line):
                        counts[kind][0] += 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--count', action='store_true', help='count docstrings and comments too')
    parser.add_argument('--root', default=str(ROOT), help='another tree to check')
    options = parser.parse_args(argv)
    problems = prose_problems(options.root)
    for problem in problems:
        print(problem)
    print(f'{len(problems)} problems' if problems else 'The prose keeps to the rules')
    if options.count:
        for kind, (breaking, total) in source_counts(options.root).items():
            print(f'{kind}: {breaking} of {total} lines hold another mark')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
