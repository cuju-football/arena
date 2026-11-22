# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Shows and checks the settings of the arena."""

from __future__ import annotations

import argparse
from typing import Any

from . import contract


def register(commands: Any) -> None:
    """Adds the arena settings commands."""
    actions = contract.group(commands, 'config', 'inspect the settings of the arena')
    contract.leaf(
        actions,
        'show',
        'show the resolved arena settings',
        '--json',
        '--config arena.toml --set play.seed=7',
    )
    check = contract.leaf(actions, 'check', 'check files of arena settings', './arena.toml')
    check.add_argument('files', nargs='*', metavar='FILE', help='a TOML or JSON file of settings')


def run(namespace: argparse.Namespace) -> int:
    """Prints the current settings or the layers that passed a check."""
    from .._config import layers

    settings = layers.config()
    if namespace.action == 'check':
        if namespace.json:
            contract.emit({'ok': True, 'layers': list(settings.layers)})
        else:
            for layer in settings.layers:
                print(f'checked {layers.place_of(layer)}')
            print('the settings fit')
    elif namespace.json:
        contract.emit(
            {
                'config': settings.to_dict(),
                'sources': settings.sources(),
                'layers': list(settings.layers),
            }
        )
    else:
        print(f'# The layers, the lowest first, {", ".join(settings.layers)}.')
        print()
        print(settings.to_toml(sources=True), end='')
    return 0
