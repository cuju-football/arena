# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The command `cuju arena plugin`, which keeps the plugins of the arena."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import _words as words
from . import contract


def register(commands: Any) -> None:
    """Adds the plugin commands."""
    from .. import plugins

    actions = contract.group(commands, 'plugin', 'manage arena plugins')
    contract.leaf(actions, 'list', 'list the available plugins', '--json')
    show = contract.leaf(actions, 'show', 'show a plugin and its details', 'builtin')
    words.offer(
        show,
        'identifier',
        completion=plugins._completion_ids(),
        metavar='ID',
        help='the identifier from plugin list',
    )
    add = contract.leaf(actions, 'add', 'add a plugin you trust', './docs/plugins/goal-rating')
    contract.details(
        add,
        'A plugin runs code when loaded. Its manifest names its identifier. '
        'Use plugin show to find the qualified names of its providers.',
    )
    add.add_argument('source', metavar='PATH', help='a folder containing plugin.toml')
    add.add_argument(
        '--replace', action='store_true', help='replace the plugin of the same identifier'
    )
    remove = contract.leaf(actions, 'remove', 'remove an added plugin', 'example')
    remove.add_argument('identifier', metavar='ID', help='an added identifier from plugin list')


def lines(found: dict[str, Any]) -> list[str]:
    """Returns a plugin in lines of English, as `show` prints it."""
    state = 'built in' if found['builtin'] else found['source']
    head = f'{found["id"]}, {state}'
    if found['version']:
        head += f', version {found["version"]}'
    out = [head]
    if found['requires']:
        out.append(f'Needs the arena {found["requires"]}.')
    if found['disabled']:
        out.append('Turned off by [plugins] disabled.')
    if found['broken']:
        out.append(f'Broken: {found["broken"]}')
    if found['failing']:
        out.append(f'Failing: {found["failing"]["message"]}')
    for kind, names in found['provides'].items():
        out.append(f'Offers {kind}: {", ".join(names)}.')
    return out


def _state(item: dict[str, Any]) -> str:
    if item['broken']:
        return 'broken'
    if item['disabled']:
        return 'off'
    if item['failing']:
        return 'failing'
    return 'built in' if item['builtin'] else item['source']


def run(namespace: argparse.Namespace) -> int | None:
    """Runs one of the commands of `cuju arena plugin`."""
    from .. import plugins as _plugins

    command = namespace.action
    if command == 'list':
        found = _plugins._descriptions()
        contract.list_answer(
            'plugins',
            found,
            (
                (
                    item['id'],
                    _state(item),
                    f'{sum(len(names) for names in item["provides"].values())} offered',
                )
                for item in found
            ),
            'Run cuju arena plugin add PATH to add a plugin.',
        )
        return 0
    if command == 'show':
        descriptions = _plugins._descriptions()
        found = next((item for item in descriptions if item['id'] == namespace.identifier), None)
        if found is None:
            from .._text.messages import MessageError

            raise MessageError('server.plugins.missing', plugin=repr(namespace.identifier))
        if namespace.json:
            contract.emit(found)
        else:
            print('\n'.join(lines(found)))
        return 0
    if command == 'add':
        from cuju.errors import MissingFileError

        path = Path(namespace.source).expanduser()
        if not path.is_dir():
            if not path.exists():
                raise MissingFileError(f'expected a plugin folder, found no file at {str(path)!r}')
            namespace._parser.error(f'expected a plugin folder, found a file at {str(path)!r}')
        contract.note(_plugins.TRUST)
        found = _plugins.add(path, namespace.replace).as_dict()
        if namespace.json:
            contract.emit(found)
        else:
            print('\n'.join(lines(found)))
        return 0
    if not _plugins.remove(namespace.identifier):
        from .._text.messages import MessageError

        raise MessageError('server.plugins.missing', plugin=repr(namespace.identifier))
    if namespace.json:
        contract.emit({'removed': namespace.identifier})
    else:
        print(f'removed {namespace.identifier}')
    return 0
