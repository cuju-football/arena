# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Check arena paths, public references and message identifiers."""

import ast
from pathlib import Path

from cuju.arena._text import messages

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'src/cuju/arena'


def problems():
    """Find repeated identifiers and missing message IDs in calls."""
    tree = ast.parse((PACKAGE / '_text/messages.py').read_text(encoding='utf-8'))
    table = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and node.target.id == 'TABLE'
    )
    keys = [entry.value for entry in table.keys]
    if len(keys) != len(set(keys)):
        yield 'messages.py repeats an identifier'
    for path in sorted(PACKAGE.rglob('*.py')):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            first = node.args[0]
            if (
                isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and first.value.startswith('server.')
                and not first.value.endswith('.')
                and first.value not in messages.TABLE
            ):
                yield f'{path.relative_to(ROOT)}:{node.lineno} names missing {first.value}'


def main():
    """Report stale arena references."""
    found = list(problems())
    for problem in found:
        print(problem)
    print(f'{len(found)} problems' if found else 'Arena message references hold')
    return int(bool(found))


if __name__ == '__main__':
    raise SystemExit(main())
