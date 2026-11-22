# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Read the engine and renderer dependencies of the arena."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'cuju.arena'
SOURCE = Path('src/cuju/arena')


def module_name(root, path):
    """Returns the dotted name of a file of the package."""
    parts = list(path.relative_to(root).with_suffix('').parts)
    if parts[-1] == '__init__':
        parts = parts[:-1]
    return '.'.join([PACKAGE, *parts])


def _files(repo):
    root = Path(repo) / SOURCE
    return root, [
        path
        for path in sorted(root.rglob('*.py'))
        if '__pycache__' not in path.parts and path.relative_to(root).parts[0] != 'web'
    ]


def modules_of(repo=ROOT):
    """Returns the dotted names of the modules of the package, the page folder left out."""
    root, files = _files(repo)
    return sorted(module_name(root, path) for path in files)


def _private(parts):
    """Returns whether a name of the engine is private, one that starts with a single underscore."""
    return any(part.startswith('_') and not part.startswith('__') for part in parts)


def engine_uses(repo=ROOT):
    """Returns the private names of the engine that the package imports, sorted.

    The answer lists (importing module, what it imports, line). What it imports is the module, or
    `module:name` for a private name taken from a public module or read as an attribute of one.
    The engine is `cuju` and its submodules apart from the packages of this repository,
    `cuju.render` and `cuju.arena`. Only the public names of the engine may be used, so a module or
    a name that starts with a single underscore counts.
    """
    root, files = _files(repo)
    found = set()
    for path in files:
        name = module_name(root, path)
        tree = ast.parse(path.read_text(encoding='utf-8'))
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split('.')[0] == 'cuju' and alias.name.split('.')[1:2] not in (
                        ['render'],
                        ['arena'],
                    ):
                        aliases[alias.asname or alias.name.split('.')[0]] = alias.name
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                if node.module.split('.')[0] == 'cuju' and node.module.split('.')[1:2] not in (
                    ['render'],
                    ['arena'],
                ):
                    for alias in node.names:
                        aliases[alias.asname or alias.name] = f'{node.module}.{alias.name}'
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in aliases
                and _private([node.attr])
            ):
                found.add((name, f'{aliases[node.value.id]}:{node.attr}', node.lineno))
            targets = []
            if isinstance(node, ast.ImportFrom) and not node.level and node.module:
                targets = [(node.module, alias.name) for alias in node.names]
            elif isinstance(node, ast.Import):
                targets = [(alias.name, None) for alias in node.names]
            elif (
                isinstance(node, ast.Call)
                and getattr(node.func, 'attr', None) == 'import_module'
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and not node.args[0].value.startswith('.')
            ):
                targets = [(node.args[0].value, None)]
            for module, imported in targets:
                parts = module.split('.')
                if parts[0] != 'cuju' or parts[1:2] in (['render'], ['arena']):
                    continue
                if _private(parts[1:]):
                    found.add((name, module, node.lineno))
                elif imported and _private([imported]):
                    found.add((name, f'{module}:{imported}', node.lineno))
    return sorted(found)


def render_uses(repo=ROOT):
    """Find imports and module names that reach the renderer."""
    root, files = _files(repo)
    found = set()
    for path in files:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                names = [node.module, *(f'{node.module}.{alias.name}' for alias in node.names)]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names = [node.value]
            for name in names:
                if (
                    name != 'cuju.render.draw'
                    and (name == 'cuju.render' or name.startswith('cuju.render.'))
                    and not any(char.isspace() for char in name)
                ):
                    found.add((module_name(root, path), name, node.lineno))
    return sorted(found)
