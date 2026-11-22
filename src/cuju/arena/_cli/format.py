# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The command `cuju arena format`, which keeps the submission formats of the renderer."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import _words as words
from . import contract


def register(commands: Any) -> None:
    """Adds the format commands."""
    actions = contract.group(commands, 'format', 'manage submission formats')
    contract.leaf(actions, 'list', 'list the available formats', '--json')
    show = contract.leaf(actions, 'show', 'show a format and its details', 'kaggle')
    words.offer(
        show,
        'identifier',
        completion=words.FORMATS,
        metavar='ID',
        help='the identifier from format list',
    )
    add = contract.leaf(actions, 'add', 'add a format you trust', './docs/formats/bot')
    contract.details(
        add,
        'A format can run code from its folder. Add a format from a source '
        'you trust, then use format show and check with its identifier.',
    )
    add.add_argument(
        'source', metavar='PATH', help='a folder or zip archive containing format.toml'
    )
    add.add_argument(
        '--id',
        dest='identifier',
        metavar='ID',
        help='the identifier to register, the manifest identifier by default',
    )
    add.add_argument(
        '--replace', action='store_true', help='replace the format of the same identifier'
    )
    export = contract.leaf(
        actions, 'export', 'export a format as a zip archive', 'kaggle -o kaggle.zip'
    )
    words.offer(
        export, 'identifier', completion=words.FORMATS, metavar='ID', help='the format to export'
    )
    contract.output_path(
        export, help_text='the zip file, the format identifier with .zip by default'
    )
    remove = contract.leaf(actions, 'remove', 'remove an added format', 'example')
    remove.add_argument('identifier', metavar='ID', help='an added identifier from format list')


def lines(found: Any) -> list[str]:
    """Returns a format in lines of English, as `show` prints it."""
    head = f'{found.label}, {found.name}'
    if found.builtin:
        head += ', built in'
    out = [head]
    if found.spec is None:
        out.append(f'Broken: {found.broken}')
        return out
    spec = found.spec
    if spec.summary:
        out.append(spec.summary)
    process = spec.process
    out.append(
        f'Plays in {"the match process" if process.host == "match" else "a process of its own"}, '
        f'{process.threads} threads, GPU {process.gpu}, a match within '
        f'{process.match_minutes:g} minutes, a check within {process.check_minutes:g}.'
    )
    for option in spec.options.values():
        span = '' if option.range is None else f' from {option.range[0]:g} to {option.range[1]:g}'
        unit = f' {option.unit}' if option.unit else ''
        out.append(f'Option {option.key}, {option.type}{span}{unit}, default {option.default!r}.')
    for key, preset in spec.presets.items():
        out.append(f'Preset {key}, {preset["label"]}.')
    return out


def run(namespace: argparse.Namespace) -> int | None:
    """Runs one of the commands of `cuju arena format`."""
    from .._formats import registry as _registry

    command = namespace.action
    if command == 'list':
        found = _registry.formats()
        contract.list_answer(
            'formats',
            [item.as_dict() for item in found],
            (
                (
                    item.label,
                    item.name,
                    'broken' if item.spec is None else 'built in' if item.builtin else 'added',
                )
                for item in found
            ),
            'Run cuju arena format add PATH to add a submission format.',
        )
        return 0
    if command == 'show':
        found = _registry.get(namespace.identifier)
        if namespace.json:
            contract.emit(found.as_dict())
        else:
            print('\n'.join(lines(found)))
        return 0
    if command == 'add':
        from cuju.errors import MissingFileError

        path = Path(namespace.source).expanduser()
        if not path.exists():
            raise MissingFileError(
                f'expected a format folder or zip archive, found no file at {str(path)!r}'
            )
        contract.note(_registry.TRUST)
        source = path.read_bytes() if path.is_file() else path
        found = _registry.add(source, namespace.identifier, namespace.replace)
        if namespace.json:
            contract.emit(found.as_dict())
        else:
            print('\n'.join(lines(found)))
        return 0
    if command == 'export':
        name, data = _registry.export(namespace.identifier)
        target = Path(namespace.output or name).expanduser()
        target.write_bytes(data)
        contract.files([target], namespace.json)
        return 0
    if not _registry.remove(namespace.identifier):
        raise contract.RefusalError(
            f'expected a format, found {namespace.identifier!r}',
            hint='run cuju arena format list',
        )
    if namespace.json:
        contract.emit({'removed': namespace.identifier})
    else:
        print(f'removed {namespace.identifier}')
    return 0
