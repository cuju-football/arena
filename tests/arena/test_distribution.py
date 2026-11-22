# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks which distributions the arena workflows build, test and publish."""

import re
from pathlib import Path

import pytest
import yaml

from cuju.arena import __version__

ROOT = Path(__file__).resolve().parents[2]
PUBLISH = 'pypa/gh-action-pypi-publish@release/v1'


def workflow(name):
    return yaml.load(
        (ROOT / '.github' / 'workflows' / f'{name}.yml').read_text(encoding='utf-8'),
        Loader=yaml.BaseLoader,
    )


@pytest.mark.parametrize('name', ['ci', 'release'])
def test_workflow_builds_and_checks_both_distributions(name):
    jobs = workflow(name)['jobs']
    job = jobs['arena' if name == 'ci' else 'build']
    commands = '\n'.join(step.get('run', '') for step in job['steps'])
    assert re.search(r'(?m)^python -m build(?:\s+--outdir|$)', commands)
    assert '--wheel' not in commands and '--sdist' not in commands
    assert 'python -m twine check --strict dist/*' in commands
    assert 'python ci/check_wheel.py dist/*.whl dist/*.tar.gz' in commands


@pytest.mark.parametrize('name', ['ci', 'release'])
def test_workflow_tests_the_local_wheel_with_the_engine_from_pypi(name):
    jobs = workflow(name)['jobs']
    job = jobs['arena' if name == 'ci' else 'test']
    commands = '\n'.join(step.get('run', '') for step in job['steps'])
    wheel = commands.index('wheel=(dist/cuju_arena-*.whl)')
    install = commands.index(
        '--index-url https://pypi.org/simple \'cuju-engine>=0.3.0,<0.4\' "${wheel[0]}[test]"'
    )
    assert wheel < install < commands.index('-m pip check') < commands.index('-m pytest')
    if name == 'release':
        assert job['needs'] == 'build'
        assert any(
            step.get('uses', '').startswith('actions/download-artifact@') for step in job['steps']
        )


def test_publishing_requires_a_dispatch_and_checks_the_tag_before_upload():
    release = workflow('release')
    assert release['on']['push']['tags'] == ['v*']
    assert release['on']['workflow_dispatch']['inputs']['publish']['default'] == 'false'
    publish = release['jobs']['publish']
    assert ' '.join(publish['if'].split()) == (
        "github.event_name == 'workflow_dispatch' && inputs.publish "
        "&& startsWith(github.ref, 'refs/tags/v')"
    )
    assert publish['environment'] == 'pypi'
    assert set(publish['needs']) >= {'build', 'test'}
    assert publish['permissions']['id-token'] == 'write'
    assert 'id-token' not in release['permissions']
    for name, job in release['jobs'].items():
        uploads = [
            step for step in job['steps'] if 'gh-action-pypi-publish' in step.get('uses', '')
        ]
        if name != 'publish':
            assert not uploads and 'id-token' not in job.get('permissions', {})
    upload = next(i for i, step in enumerate(publish['steps']) if step.get('uses') == PUBLISH)
    check = next(
        step['run']
        for step in publish['steps'][:upload]
        if 'tag names the version' in step.get('name', '')
    )
    assert 'src/cuju/arena/_version.py' in check
    assert 'test -n "$version" && test "$GITHUB_REF_NAME" = "v$version"' in check


def test_readme_links_and_images_use_absolute_urls():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    addresses = re.findall(r'\]\(([^)]+)\)|^\[[^\]]+\]:\s+(\S+)', readme, re.M)
    assert addresses and all(
        (inline or definition).startswith('https://') for inline, definition in addresses
    )
    assert 'pip install cuju-arena' in readme and 'pip install cuju-render' in readme
    assert '[family]: https://pypi.org/project/cuju/' in readme


def test_current_version_has_a_changelog_entry():
    changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    assert re.search(rf'^## \[{re.escape(__version__)}\] \d{{4}}-\d{{2}}-\d{{2}}$', changelog, re.M)
