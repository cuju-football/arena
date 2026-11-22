# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The commands of `cuju arena` that live in modules of their own, found by their names.

A module of this folder whose name starts with a letter is a command. It holds
`register(commands)`, which adds its parser, or several, to the subparsers of `cuju arena`, and
`run(namespace)`, which runs the parsed command and returns its exit status. A name that starts
with an underscore is a helper and is left alone.

The modules load in the order of their names each time the command line starts, after the
commands of `__main__.py`, so a module imports its heavy parts inside `run`. `run` starts once
`--home` is applied and the home folder exists, and an `Error` of cuju or an `OSError` that it
raises prints as the errors of the other commands do, as JSON under an option `--json`.
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys
from types import ModuleType
from typing import Any


def modules(package: ModuleType | None = None) -> list[ModuleType]:
    """Returns the command modules of a package, by default this one, in the order of their names.

    Raises:
        TypeError: If a module lacks `register` or `run`.
    """
    package = sys.modules[__name__] if package is None else package
    names = sorted(
        info.name
        for info in pkgutil.iter_modules(package.__path__)
        if info.name[:1] != '_' and info.name != 'contract'
    )
    found = [importlib.import_module(f'{package.__name__}.{name}') for name in names]
    for module in found:
        for name in ('register', 'run'):
            if not callable(getattr(module, name, None)):
                raise TypeError(f'expected the function {name} in {module.__name__}')
    return found


def register(commands: Any, found: list[ModuleType] | None = None) -> dict[str, ModuleType]:
    """Adds the parsers of the command modules to the subparsers of `cuju arena`.

    Args:
        commands: The subparsers, as `ArgumentParser.add_subparsers` returns them.
        found: The command modules, by default those of `modules()`.

    Returns:
        The module that runs each command, by the name of the command and by each of its aliases.

    Raises:
        ValueError: If a module adds no command, or one whose name another command holds.
    """
    owners: dict[str, ModuleType] = {}
    for module in modules() if found is None else found:
        before = dict(commands.choices)
        try:
            module.register(commands)
        except argparse.ArgumentError as error:
            raise ValueError(f'expected {module.__name__} to add a new command, {error}') from None
        added = [name for name, made in commands.choices.items() if before.get(name) is not made]
        if not added:
            raise ValueError(f'expected {module.__name__} to add a command')
        for name in added:
            if name in before:
                raise ValueError(f'expected {module.__name__} to add a new command, found {name}')
            owners[name] = module
    return owners
