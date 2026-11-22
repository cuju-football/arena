# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the plugin mechanism, its discovery, its declarations, its failures and its command.

The mechanism is held to a kind of its own, `probe`, so none of these tests reads a standard.
"""

import json
import os
import py_compile
import sys
from pathlib import Path

import pytest

import cuju.errors
from cuju.arena import plugins as _plugins
from cuju.arena.__main__ import main
from support.plugin_files import entry as _entry
from support.plugin_files import folder as _folder


class Probe:
    """An implementation of the kind `probe` that doubles its argument."""

    def run(self, value):
        return value * 2


@pytest.fixture(autouse=True)
def probe(monkeypatch, arena_home):
    monkeypatch.setitem(_plugins.KINDS, 'probe', _plugins.Kind('probe', 1, ('run',), 'a probe'))
    _plugins.refresh()
    yield
    _plugins.refresh()


def test_the_standards_are_the_plugin_builtin_and_the_group_is_the_arena_s(monkeypatch):
    found = _plugins.plugins()
    assert found[0].id == 'builtin' and found[0].builtin and found[0].broken is None
    assert _plugins.GROUP == 'cuju.arena.plugins'
    assert _plugins.KINDS['rating_method'].major == 1
    assert set(_plugins.KINDS) >= {'rating_method', 'evidence', 'seeding', 'pairing', 'attachment'}


def test_the_package_declares_its_standards_as_its_own_entry_point():
    import tomllib

    root = Path(__file__).resolve().parents[2]
    declared = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    group = declared['project']['entry-points'][_plugins.GROUP]
    assert group == {'builtin': _plugins.BUILTIN_MODULE}


def test_a_folder_plugin_is_found_named_and_called(arena_home):
    _folder(arena_home / 'plugins')
    found = _plugins.plugin('acme')
    assert found.source == 'folder' and found.broken is None and found.version == '1.2'
    assert found.names('probe') == ['acme.double']
    assert _plugins.names('probe') == ['acme.double']
    assert _plugins.get('probe', 'acme.double').run(4) == 8
    assert _plugins.call('probe', 'acme.double', 'run', 5) == 10
    shown = found.as_dict()
    assert shown['provides'] == {'probe/1': ['acme.double']} and shown['disabled'] is False


def test_an_entry_point_plugin_is_found_by_its_module(tmp_path, monkeypatch):
    _entry(tmp_path, monkeypatch)
    found = _plugins.plugin('orbit')
    assert found.source == 'entry point' and found.broken is None
    assert _plugins.call('probe', 'orbit.triple', 'run', 2) == 6


def test_plugin_show_completes_installed_names_without_loading_them(
    arena_home, tmp_path, monkeypatch, capsys
):
    from cuju.arena.__main__ import parser

    _entry(tmp_path, monkeypatch)
    _folder(arena_home / 'plugins')
    options = parser().parse_args(['plugin', 'show', 'orbit'])
    identifier = next(action for action in options._parser._actions if action.dest == 'identifier')
    assert {'builtin', 'orbit'} <= set(identifier.completion)
    assert 'acme' not in identifier.completion
    assert 'orbit_mod' not in sys.modules
    assert main(['plugin', 'show', 'orbit', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['id'] == 'orbit'
    assert main(['plugin', 'show', 'acme', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['id'] == 'acme'


def test_the_builtin_entry_point_is_not_read_twice(monkeypatch):
    class Entry:
        name = 'builtin'
        value = _plugins.BUILTIN_MODULE

        def load(self):
            raise AssertionError('the standards load by name, never through the entry point')

    monkeypatch.setattr(_plugins.importlib.metadata, 'entry_points', lambda group=None: [Entry()])
    _plugins.refresh()
    assert [found.id for found in _plugins.plugins()] == ['builtin']


def test_a_bare_name_means_the_standards_and_a_dotted_one_a_plugin(arena_home):
    _folder(arena_home / 'plugins')
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _plugins.get('probe', 'double')
    assert caught.value.message_id == 'server.plugins.unknown'
    assert 'acme.double' in str(caught.value)
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _plugins.get('probe', 'acme.missing')
    assert caught.value.message_id == 'server.plugins.unknown'
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _plugins.get('probe', 'nobody.double')
    assert caught.value.message_id == 'server.plugins.unknown'
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _plugins.get('nothing', 'double')
    assert caught.value.message_id == 'server.plugins.kind'


@pytest.mark.parametrize(
    ('toml', 'reason'),
    [
        ('id = "acme"\n[provides]\n"probe/2" = ["double"]\n', 'protocol major 1, found 2'),
        ('id = "acme"\n[provides]\n"nothing/1" = ["double"]\n', 'one of'),
        ('id = "acme"\nrequires = ">=9.0.0"\n[provides]\n"probe/1" = ["double"]\n', 'needs'),
        ('id = "acme"\nrequires = "soon"\n[provides]\n"probe/1" = ["double"]\n', 'range'),
        ('id = "other"\n[provides]\n"probe/1" = ["double"]\n', "expected the id 'acme'"),
        ('id = "acme"\n[provides]\n"probe/1" = ["Double"]\n', 'lowercase'),
        ('id = "acme"\n[provides]\n"probe/1" = ["a", "a"]\n', 'once each'),
        ('id = "acme"\nmodule = "../x.py"\n[provides]\n"probe/1" = ["double"]\n', 'module'),
        ('id = "acme"\n[provides\n', 'does not read'),
    ],
)
def test_a_plugin_that_misses_its_declaration_is_broken_and_cannot_be_chosen(
    arena_home, toml, reason
):
    _folder(arena_home / 'plugins', toml=toml)
    found = _plugins.plugin('acme')
    assert found.broken is not None and reason in found.broken
    assert found.provided == {} and found.names() == []
    assert _plugins.names('probe') == []
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _plugins.get('probe', 'acme.double')
    assert caught.value.message_id == 'server.plugins.broken'
    assert reason in str(caught.value)


@pytest.mark.parametrize(
    ('code', 'reason'),
    [
        ('def register(registry):\n    raise RuntimeError("no luck")\n', 'RuntimeError, no luck'),
        ('x = 1\n', 'expected a function register'),
        ('def register(registry):\n    pass\n', 'did not provide double of probe/1'),
        ('import nothing_of_this_name\n', 'ModuleNotFoundError'),
        (
            'class Bare:\n    pass\n\n'
            'def register(registry):\n    registry.provide("probe/1", "double", Bare())\n',
            'to have run',
        ),
        (
            'class One:\n    def run(self, value):\n        return value\n\n'
            'def register(registry):\n    registry.provide("probe/1", "other", One())\n',
            'lists under probe/1',
        ),
        (
            'class One:\n    def run(self, value):\n        return value\n\n'
            'def register(registry):\n    registry.provide("probe/3", "double", One())\n',
            'the plugin declares',
        ),
    ],
)
def test_a_plugin_whose_module_fails_to_register_is_broken(arena_home, code, reason):
    _folder(arena_home / 'plugins', code=code)
    found = _plugins.plugin('acme')
    assert found.broken is not None and reason in found.broken
    assert found.provided == {} and _plugins.names('probe') == []


def test_a_plugin_that_raises_while_it_runs_is_marked_failing(arena_home):
    code = """
        class Hurt:
            def run(self, value):
                raise RuntimeError('out of ideas')

        def register(registry):
            registry.provide('probe/1', 'double', Hurt())
    """
    _folder(arena_home / 'plugins', code=code)
    assert _plugins.failing('acme') is None
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _plugins.call('probe', 'acme.double', 'run', 1)
    assert caught.value.message_id == 'server.plugins.failed'
    assert 'acme' in str(caught.value) and 'out of ideas' in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)
    found = _plugins.failing('acme')
    assert found is not None and 'out of ideas' in found['message']
    assert _plugins.plugin('acme').as_dict()['failing'] == found
    # The plugin stays choosable, and a replacement clears the mark.
    assert _plugins.names('probe') == ['acme.double']
    _plugins.add(_folder(arena_home / 'next'), replace=True)
    assert _plugins.failing('acme') is None
    assert _plugins.call('probe', 'acme.double', 'run', 1) == 2


def test_an_answer_that_the_check_refuses_marks_the_plugin_failing(arena_home):
    _folder(arena_home / 'plugins')

    def positive(answer):
        if answer < 0:
            raise ValueError('expected a positive answer')
        return answer

    assert _plugins.call('probe', 'acme.double', 'run', 2, check=positive) == 4
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _plugins.call('probe', 'acme.double', 'run', -2, check=positive)
    assert caught.value.message_id == 'server.plugins.failed'
    assert 'positive' in _plugins.failing('acme')['message']


def test_a_failing_standard_is_a_defect_of_the_package_and_passes_through(monkeypatch):
    class Broken:
        def run(self, value):
            raise KeyError('inside the package')

    monkeypatch.setattr(_plugins._builtin(), 'provided', {('probe', 'bad'): Broken()})
    with pytest.raises(KeyError):
        _plugins.call('probe', 'bad', 'run', 1)
    assert _plugins.failing('builtin') is None


def test_the_setting_disabled_turns_a_plugin_off_and_the_standards_stay_on(arena_home):
    _folder(arena_home / 'plugins')
    assert _plugins.disabled() == []
    off = ['plugins.disabled=["acme", "builtin"]']
    assert _plugins.disabled(overrides=off) == ['acme']
    assert _plugins.names('probe', overrides=off) == []
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _plugins.get('probe', 'acme.double', overrides=off)
    assert caught.value.message_id == 'server.plugins.disabled'
    assert _plugins.plugin('acme').as_dict()['disabled'] is False
    # A home file turns it off for every call that names no settings of its own.
    (arena_home / 'arena.toml').write_text('[plugins]\ndisabled = ["acme"]\n', encoding='utf-8')
    assert _plugins.disabled() == ['acme'] and _plugins.names('probe') == []
    assert _plugins.plugin('acme').as_dict()['disabled'] is True


def test_the_setting_refuses_what_is_no_list_of_names():
    with pytest.raises(cuju.errors.ConfigurationError):
        _plugins.disabled(overrides=['plugins.disabled=3'])


def test_two_plugins_of_one_identifier_leave_the_second_broken(tmp_path, monkeypatch, arena_home):
    _entry(tmp_path, monkeypatch, name='acme')
    _folder(arena_home / 'plugins')
    found = {item.id: item for item in _plugins.plugins() if item.id == 'acme'}
    assert len([item for item in _plugins.plugins() if item.id == 'acme']) == 2
    sources = {item.source: item for item in _plugins.plugins() if item.id == 'acme'}
    assert sources['entry point'].broken is None
    assert 'taken' in sources['folder'].broken
    assert found['acme'] is not None


def test_an_entry_point_with_the_wrong_declaration_is_broken(tmp_path, monkeypatch):
    _entry(tmp_path, monkeypatch, plugin={'id': 'someone', 'provides': {'probe/1': ['triple']}})
    found = _plugins.plugin('orbit')
    assert found.broken is not None and "expected the id 'orbit'" in found.broken
    _plugins.refresh()


def test_an_entry_point_that_fails_to_import_is_broken(tmp_path, monkeypatch):
    _entry(tmp_path, monkeypatch, module='raise ImportError("not here")\n')
    found = _plugins.plugin('orbit')
    assert found.broken is not None and 'not here' in found.broken


def test_the_identifier_builtin_belongs_to_the_standards(tmp_path, monkeypatch):
    _entry(tmp_path, monkeypatch, name='builtin', plugin={'id': 'builtin', 'provides': {}})
    others = [item for item in _plugins.plugins() if item.id == 'builtin' and not item.builtin]
    assert len(others) == 1 and 'taken' in others[0].broken


def test_add_copies_a_folder_reads_it_and_shows_the_trust_line(tmp_path, arena_home):
    source = _folder(tmp_path / 'src')
    found = _plugins.add(source)
    assert found.id == 'acme' and found.broken is None and found.source == 'folder'
    assert (arena_home / 'plugins' / 'acme' / 'plugin.toml').is_file()
    assert 'permissions' in _plugins.TRUST
    assert _plugins.call('probe', 'acme.double', 'run', 3) == 6
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _plugins.add(source)
    assert caught.value.message_id == 'server.plugins.taken'
    assert _plugins.add(source, replace=True).id == 'acme'


def test_add_refuses_what_does_not_fit_and_leaves_nothing_behind(tmp_path, arena_home):
    def refused(folder):
        with pytest.raises(cuju.errors.ArgumentError) as caught:
            _plugins.add(folder)
        return caught.value.message_id

    assert refused(tmp_path / 'nowhere') == 'server.plugins.folder'
    bad = _folder(tmp_path / 'a', code='def register(registry):\n    raise RuntimeError("x")\n')
    assert refused(bad) == 'server.plugins.broken'
    shouting = _folder(tmp_path / 'b', identifier='acme')
    (shouting / 'plugin.toml').write_text('id = "Acme!"\n[provides]\n', encoding='utf-8')
    assert refused(shouting) == 'server.plugins.identifier'
    named = _folder(tmp_path / 'c')
    (named / 'plugin.toml').write_text('id = "builtin"\n[provides]\n', encoding='utf-8')
    assert refused(named) == 'server.plugins.builtin'
    garbled = _folder(tmp_path / 'd')
    (garbled / 'plugin.toml').write_text('id = [\n', encoding='utf-8')
    assert refused(garbled) == 'server.plugins.declaration'
    assert not (arena_home / 'plugins').exists() or not list((arena_home / 'plugins').iterdir())


def test_add_refuses_a_folder_that_is_too_large_or_holds_a_link(tmp_path, monkeypatch):
    big = _folder(tmp_path / 'big')
    monkeypatch.setattr(_plugins, 'MAXIMUM_FILES', 1)
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _plugins.add(big)
    assert caught.value.message_id == 'server.plugins.size'
    monkeypatch.undo()
    linked = _folder(tmp_path / 'linked')
    try:
        (linked / 'again.py').symlink_to(linked / 'plugin.py')
    except OSError:
        pytest.skip('this machine makes no links')
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _plugins.add(linked)
    assert caught.value.message_id == 'server.plugins.links'


def test_add_refuses_the_identifier_of_an_installed_package(tmp_path, monkeypatch):
    _entry(tmp_path, monkeypatch, name='acme')
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _plugins.add(_folder(tmp_path / 'src'))
    assert caught.value.message_id == 'server.plugins.entry'
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _plugins.remove('acme')
    assert caught.value.message_id == 'server.plugins.entry'


def test_remove_deletes_the_folder_and_the_mark_and_keeps_the_standards(tmp_path, arena_home):
    _plugins.add(_folder(tmp_path / 'src'))
    _plugins.mark_failing('acme', 'it hurt')
    assert _plugins.remove('acme') is True
    assert not (arena_home / 'plugins' / 'acme').exists()
    assert _plugins.failing('acme') is None
    assert [item.id for item in _plugins.plugins()] == ['builtin']
    assert _plugins.remove('acme') is False and _plugins.remove('Bad Name') is False
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _plugins.remove('builtin')
    assert caught.value.message_id == 'server.plugins.builtin'


def test_a_folder_plugin_may_import_its_own_modules(arena_home):
    folder = _folder(
        arena_home / 'plugins',
        code='from .helper import FACTOR\n'
        'class Scale:\n    def run(self, value):\n        return value * FACTOR\n\n'
        'def register(registry):\n    registry.provide("probe/1", "double", Scale())\n',
    )
    (folder / 'helper.py').write_text('FACTOR = 7\n', encoding='utf-8')
    assert _plugins.call('probe', 'acme.double', 'run', 2) == 14


def test_a_changed_folder_is_read_again_without_a_restart(arena_home):
    folder = _folder(arena_home / 'plugins')
    script = folder / 'plugin.py'
    second = 1_700_000_000_000_000_000
    os.utime(script, ns=(second, second))
    py_compile.compile(str(script), doraise=True)
    assert _plugins.call('probe', 'acme.double', 'run', 2) == 4
    signature = _plugins.signature()
    script.write_text(
        script.read_text(encoding='utf-8').replace('value * 2', 'value * 5'), encoding='utf-8'
    )
    os.utime(script, ns=(second, second + 500_000_000))
    assert _plugins.signature() != signature
    assert _plugins.call('probe', 'acme.double', 'run', 2) == 10


def test_the_command_lists_shows_adds_and_removes(tmp_path, arena_home, capsys):
    assert main(['plugin', 'list']) == 0
    assert capsys.readouterr().out.splitlines()[0].split('  ')[:2] == ['builtin', 'built in']
    source = _folder(tmp_path / 'src')
    assert main(['plugin', 'add', str(source)]) == 0
    captured = capsys.readouterr()
    assert _plugins.TRUST in captured.err and 'acme, folder, version 1.2' in captured.out
    assert 'Offers probe/1: acme.double.' in captured.out
    assert main(['plugin', 'show', 'acme', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['provides'] == {'probe/1': ['acme.double']}
    assert main(['plugin', 'list', '--json']) == 0
    assert [item['id'] for item in json.loads(capsys.readouterr().out)['plugins']] == [
        'builtin',
        'acme',
    ]
    assert main(['plugin', 'remove', 'acme']) == 0
    assert main(['plugin', 'remove', 'acme']) == 1
    assert "expected an available plugin, found 'acme'" in capsys.readouterr().err


def test_the_command_names_a_broken_and_a_failing_plugin(arena_home, capsys):
    _folder(arena_home / 'plugins', code='x = 1\n')
    assert main(['plugin', 'list']) == 0
    output = capsys.readouterr().out.splitlines()
    assert [line.split('  ') for line in output] == [
        ['builtin', 'built in', '39 offered'],
        ['acme', 'broken', '0 offered'],
    ]
    assert main(['plugin', 'show', 'acme']) == 0
    assert 'Broken: expected a function register' in capsys.readouterr().out
    _folder(arena_home / 'plugins', identifier='other', name='twice')
    _plugins.mark_failing('other', 'it hurt')
    assert main(['plugin', 'show', 'other']) == 0
    assert 'Failing: it hurt' in capsys.readouterr().out


def test_the_modules_the_mechanism_reads_stay_importable_on_their_own():
    import subprocess

    done = subprocess.run(
        [sys.executable, '-c', 'import cuju.arena.plugins as p; print(len(p.KINDS))'],
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=True,
    )
    assert int(done.stdout) >= 4
