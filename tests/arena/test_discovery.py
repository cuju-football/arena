# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the modules that the command line and the server find by their names."""

import argparse
import importlib
import subprocess
import sys

import pytest

from cuju.arena import _cli

COMMAND = """
def register(commands):
    commands.add_parser({name!r}, aliases={aliases!r})


def run(namespace):
    return {status}
"""


@pytest.fixture
def package(tmp_path, monkeypatch):
    """Returns a function that writes a package of modules, imports it and returns it."""
    made = []

    def make(name, **sources):
        folder = tmp_path / name
        folder.mkdir()
        (folder / '__init__.py').write_text('', encoding='utf-8')
        for module, source in sources.items():
            (folder / f'{module}.py').write_text(source, encoding='utf-8')
        monkeypatch.syspath_prepend(str(tmp_path))
        importlib.invalidate_caches()
        made.extend([name, *[f'{name}.{module}' for module in sources]])
        return importlib.import_module(name)

    yield make
    for name in made:
        sys.modules.pop(name, None)


def _command(name, status=0, aliases=()):
    return COMMAND.format(name=name, status=status, aliases=list(aliases))


def test_the_command_modules_of_the_package_load_in_order_and_without_pytorch():
    found = _cli.modules()
    names = [module.__name__ for module in found]
    assert names == sorted(names)
    assert all(name.startswith('cuju.arena._cli.') for name in names)
    commands = argparse.ArgumentParser().add_subparsers(dest='command')
    owners = _cli.register(commands)
    assert set(owners.values()) == set(found) and set(owners) == set(commands.choices)
    # The command line loads every command module at its start, so none may bring PyTorch.
    code = "import sys, cuju.arena._cli as c; c.modules(); print('torch' in sys.modules)"
    result = subprocess.run(
        [sys.executable, '-c', code],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=True,
        timeout=120,
    )
    assert result.stdout.split() == ['False']


def test_command_modules_are_found_by_name_in_order(package):
    made = package(
        'cli_found',
        zebra=_command('zebra'),
        alpha=_command('alpha', aliases=['al']),
        _helper='VALUE = 1\n',
    )
    found = _cli.modules(made)
    assert [module.__name__ for module in found] == ['cli_found.alpha', 'cli_found.zebra']
    commands = argparse.ArgumentParser().add_subparsers(dest='command')
    commands.add_parser('serve')
    owners = _cli.register(commands, found)
    assert {name: module.__name__ for name, module in owners.items()} == {
        'alpha': 'cli_found.alpha',
        'al': 'cli_found.alpha',
        'zebra': 'cli_found.zebra',
    }
    assert list(commands.choices) == ['serve', 'alpha', 'al', 'zebra']


def test_a_command_module_that_does_not_fit_is_named(package):
    with pytest.raises(TypeError, match=r'expected the function run in cli_lacking\.half'):
        _cli.modules(package('cli_lacking', half='def register(commands):\n    pass\n'))
    quiet = package('cli_quiet', nothing='def register(commands):\n    pass\n\nrun = print\n')
    commands = argparse.ArgumentParser().add_subparsers(dest='command')
    with pytest.raises(ValueError, match=r'expected cli_quiet\.nothing to add a command'):
        _cli.register(commands, _cli.modules(quiet))
    taken = package('cli_taken', serve=_command('serve'))
    commands.add_parser('serve')
    with pytest.raises(ValueError, match=r'expected cli_taken\.serve to add a new command'):
        _cli.register(commands, _cli.modules(taken))
