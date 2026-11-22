# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks the arena wheel, including its own namespace portion, data and dependency ranges."""

import configparser
import re
import sys
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

# The folder of the checkout that holds the package, beside the folder of this script.
SOURCE = Path(__file__).resolve().parents[1] / 'src'
ARENA_URL = 'https://github.com/cuju-football/arena'

LICENSES = ['LICENSE', 'NOTICE']
SOURCE_FILES = {
    *LICENSES,
    'README.md',
    'CHANGELOG.md',
    'SECURITY.md',
    'CONTRIBUTING.md',
    'CODE_OF_CONDUCT.md',
    'pyproject.toml',
    'PKG-INFO',
    'ci/sdist.py',
}
REQUIRED = [
    'cuju/arena/__init__.py',
    'cuju/arena/__main__.py',
    'cuju/arena/_version.py',
    'cuju/arena/defaults/recorder.toml',
    'cuju/arena/defaults/plugins.toml',
    'cuju/arena/_moments/captions.json',
    'cuju/arena/formats/builtin/kaggle/format.toml',
    'cuju/arena/samples/sample-5v5.cjr',
    'cuju/arena/samples/sample-11v11.cjr',
    'cuju/arena/samples/compare/sample-5v5-a.cjr',
    'cuju/arena/samples/compare/sample-5v5-b.cjr',
]

# The files of tools of development that never ship, by name. axe-core, which the page check
# fetches, is under the Mozilla Public License 2.0 and stays out of every build.
DEVELOPMENT_ONLY = ('axe.min.js', 'axe.js')


def development_problems(names):
    """Returns a problem for each name of a build that is a file of a tool of development."""
    return [
        f'unexpected {name}, a file of a tool of development that never ships'
        for name in names
        if name.rsplit('/', 1)[-1] in DEVELOPMENT_ONLY
    ]


def sdist_problems(path):
    """Returns what is wrong with the sdist at `path`, or an empty list."""
    names = set()
    found = []
    root = Path(path).name.removesuffix('.tar.gz') + '/'
    with tarfile.open(path) as sdist:
        for member in sdist:
            if member.isdir():
                continue
            name = member.name.removeprefix(root)
            if not member.isfile() or member.name == name:
                found.append(f'unexpected {member.name}, expected a file under {root}')
                continue
            names.add(name)
        if 'PKG-INFO' in names:
            metadata = sdist.extractfile(root + 'PKG-INFO')
            found += metadata_problems(
                metadata.read().decode(), root.removeprefix('cuju_arena-')[:-1]
            )
    found += [f'missing {name} in the sdist' for name in sorted(SOURCE_FILES - names)]
    found += source_problems(
        [name.removeprefix('src/') for name in names if name.startswith('src/')]
    )
    found += development_problems(names)
    for name in sorted(names):
        parts = name.split('/')
        if name in SOURCE_FILES:
            continue
        if parts[0] != 'docs' and not name.startswith('src/cuju/arena/'):
            found.append(f'unexpected {name} in the sdist')
        elif {'__pycache__', '..', 'AGENTS.md', 'TASK.md'} & set(parts) or name.endswith('.pyc'):
            found.append(f'unexpected {name} in the sdist')
    return found


def metadata_problems(metadata, version):
    """Checks the distribution name and its release metadata."""
    fields = Parser().parsestr(metadata)
    found = []
    required = {
        'Name': 'cuju-arena',
        'Version': version,
        'Description-Content-Type': 'text/markdown',
        'License-Expression': 'Apache-2.0',
    }
    for key, value in required.items():
        if fields.get(key) != value:
            found.append(f'expected {key} {value}, found {fields.get(key)!r}')
    for key in ('Summary', 'Keywords', 'Classifier'):
        if not fields.get(key):
            found.append(f'missing {key} in the metadata')
    if set(fields.get_all('License-File', ())) != set(LICENSES):
        found.append('expected LICENSE and NOTICE as the license files')
    urls = fields.get_all('Project-URL', ())
    expected = {
        f'Homepage, {ARENA_URL}',
        f'Documentation, {ARENA_URL}/tree/main/docs',
        f'Source, {ARENA_URL}',
        f'Issues, {ARENA_URL}/issues',
        f'Changelog, {ARENA_URL}/blob/main/CHANGELOG.md',
    }
    found += [f'missing Project-URL {url}' for url in sorted(expected - set(urls))]
    found += requirement_problems(
        fields.get_all('Requires-Dist', ()), {'cuju-engine': '>=0.3.0,<0.4'}
    )
    return found


def source_problems(names, source=SOURCE):
    """Returns the files of the package under `source` that the wheel lacks, bytecode left out."""
    held = set(names)
    found = []
    for path in sorted((source / 'cuju' / 'arena').rglob('*')):
        name = path.relative_to(source).as_posix()
        if path.is_file() and '__pycache__' not in path.parts and name not in held:
            found.append(f'missing {name}, a file of the package in the checkout')
    return found


def problems(path):
    """Returns what is wrong with the wheel at `path`, or an empty list."""
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        metadata = next(
            (wheel.read(n).decode() for n in names if n.endswith('.dist-info/METADATA')), ''
        )
        entry_points = next(
            (wheel.read(n).decode() for n in names if n.endswith('.dist-info/entry_points.txt')), ''
        )
    found = [
        f'missing {name}'
        for name in LICENSES
        if not any(n.endswith(f'licenses/{name}') for n in names)
    ]
    found += [f'missing {name}' for name in REQUIRED if name not in names]
    found += source_problems(names)
    found += development_problems(names)
    if not any(n.startswith('cuju/arena/scenarios/') and n.endswith('.py') for n in names):
        found.append('missing the arena scenarios')
    for name in names:
        if '.dist-info/' in name.split('/', 1)[0] + '/':
            continue
        if not name.startswith('cuju/arena/'):
            found.append(f'unexpected {name}, the wheel holds cuju/arena/ alone')
        elif '__pycache__' in name or name.endswith('.pyc'):
            found.append(f'unexpected {name}')
    version = Path(path).name.split('-')[1]
    found += metadata_problems(metadata, version)
    registered = configparser.ConfigParser()
    registered.read_string(entry_points)
    entries = {
        ('cuju.commands', 'arena'): 'cuju.arena.__main__:main',
        ('cuju.arena.plugins', 'builtin'): 'cuju.arena._builtin_plugins',
    }
    for (group, name), target in entries.items():
        if registered.get(group, name, fallback=None) != target:
            found.append(f'expected entry point {group} {name} = {target}')
    return found


def requirement_problems(requires, expected):
    """Checks the required family distributions and their exact supported ranges."""
    found = []
    for name, bounds in expected.items():
        rows = [row for row in requires if re.match(rf'{name}(?=[<>=; (]|$)', row)]
        wanted = {bound.strip() for bound in bounds.split(',')}
        if len(rows) != 1 or not rows[0].startswith(name):
            found.append(f'expected one requirement of {name}{bounds}, found {rows}')
        elif set(rows[0][len(name) :].strip(' ()').split(',')) != wanted:
            found.append(f'expected the requirement {name}{bounds}, found {rows[0]}')
    if any(re.match(r'cuju(?=[<>=; (]|$)', row) for row in requires):
        found.append('the wheel depends on the cuju umbrella')
    return found


def main():
    wheels = sys.argv[1:]
    if not wheels:
        sys.exit('usage: python ci/check_wheel.py <wheel or sdist>...')
    failed = False
    for path in wheels:
        found = sdist_problems(path) if str(path).endswith('.tar.gz') else problems(path)
        for problem in found:
            print(f'{path}: {problem}')
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
