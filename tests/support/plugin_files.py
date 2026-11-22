# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Writes plugins of a third party for the tests, a folder and an installed distribution."""

import sys
import textwrap
from pathlib import Path

from cuju.arena import plugins as _plugins


def folder(base, identifier='acme', *, toml=None, code=None, name='double'):
    """Writes a folder plugin whose module provides the probe `name` and returns the folder."""
    folder = Path(base) / identifier
    folder.mkdir(parents=True)
    declaration = toml or (
        f'id = "{identifier}"\nversion = "1.2"\nrequires = ">=0.1.0"\n'
        f'[provides]\n"probe/1" = ["{name}"]\n'
    )
    (folder / 'plugin.toml').write_text(declaration, encoding='utf-8')
    (folder / 'plugin.py').write_text(
        textwrap.dedent(
            code
            or f"""
            class Double:
                def run(self, value):
                    return value * 2

            def register(registry):
                registry.provide('probe/1', '{name}', Double())
            """
        ),
        encoding='utf-8',
    )
    return folder


def entry(tmp_path, monkeypatch, name='orbit', *, module=None, plugin=None):
    """Installs a distribution that names a plugin module as an entry point of the group."""
    root = tmp_path / 'site'
    root.mkdir(exist_ok=True)
    (root / f'{name}_mod.py').write_text(
        textwrap.dedent(
            module
            or f"""
            PLUGIN = {plugin or {'id': name, 'provides': {'probe/1': ['triple']}}!r}

            class Triple:
                def run(self, value):
                    return value * 3

            def register(registry):
                registry.provide('probe/1', 'triple', Triple())
            """
        ),
        encoding='utf-8',
    )
    info = root / f'{name}-1.0.dist-info'
    info.mkdir()
    (info / 'METADATA').write_text(
        f'Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n', encoding='utf-8'
    )
    (info / 'entry_points.txt').write_text(
        f'[{_plugins.GROUP}]\n{name} = {name}_mod\n', encoding='utf-8'
    )
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.delitem(sys.modules, f'{name}_mod', raising=False)
    _plugins.refresh()
    return root


def declared(identifier, provides):
    """Returns the text of a `plugin.toml` for a plugin that provides names of some kinds."""
    lines = [f'id = "{identifier}"', 'requires = ">=0.1.0"', '', '[provides]']
    for kind, names in provides.items():
        lines.append(f'"{kind}" = {list(names)!r}'.replace("'", '"'))
    return '\n'.join(lines) + '\n'


def written(base, identifier, provides, code):
    """Writes a folder plugin of any kinds, from its `provides` and the source of its module."""
    return folder(base, identifier, toml=declared(identifier, provides), code=code)


def installed(tmp_path, monkeypatch, identifier, provides, code):
    """Installs a distribution of a plugin of any kinds, from its `provides` and its module."""
    module = f'PLUGIN = {{"id": {identifier!r}, "provides": {provides!r}}}\n' + textwrap.dedent(
        code
    )
    return entry(tmp_path, monkeypatch, identifier, module=module)
