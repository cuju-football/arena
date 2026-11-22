# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Contract 1, `format.toml` read and checked, the registry of formats, identity and migration."""

import io
import json
import multiprocessing
import os
import shutil
import zipfile
from pathlib import Path

import pytest

import cuju
from cuju.arena._formats import cache as _cache
from cuju.arena._formats import contract as _contract
from cuju.arena._formats import identity as _identity
from cuju.arena._formats import migrate as _migrate
from cuju.arena._formats import registry as _registry
from cuju.arena._formats import side as _side

PROBE = Path(__file__).parents[1] / 'support' / 'formats' / 'probe'
BASE = 'contract = 1\nname = "Test"\nscript = "format.py"\n'


def _spec(text):
    return _contract.parse(BASE + text)


def test_the_smallest_format_toml_and_its_defaults():
    spec = _spec('')
    assert (spec.contract, spec.name, spec.script, spec.version) == (1, 'Test', 'format.py', None)
    process = spec.process
    assert (process.host, process.scope, process.threads, process.gpu) == (
        'format',
        'match',
        1,
        'never',
    )
    assert (process.match_minutes, process.check_minutes) == (60.0, 15.0)
    assert spec.forfeit_score is None and spec.evidence is True and spec.options == {}


def test_every_key_of_contract_1():
    spec = _spec(
        'version = 2\nsummary = "One line."\nrequires = ">=0.1,<0.9"\n'
        '[process]\nscope = "run"\nthreads = 2\ngpu = "allowed"\nmemory_mib = 4096\n'
        'match_minutes = 90\ncheck_minutes = 30\nenv = ["ARENA_KEY"]\n'
        '[results]\nforfeit_score = [0, 3]\nevidence = false\n'
        '[options.step_seconds]\ntype = "float"\ndefault = 0.5\nrange = [0.05, 1.1]\n'
        'bounds = "step"\nunit = "s"\n'
        '[options.member]\ntype = "text"\ndefault = ""\nmax_length = 40\nper_entrant = true\n'
        '[options.check_matches]\ntype = "int"\ndefault = 1\nrange = [1, 3]\nreaches = "check"\n'
        '[options.players]\ntype = "choices"\nchoices = ["one", "control"]\ndefault = "one"\n'
        '[options.timing]\ntype = "bool"\ndefault = true\n'
        '[presets.open]\nlabel = "Open"\nplayers = "control"\ntiming = false\n'
    )
    assert spec.version == '2' and spec.requires == '>=0.1,<0.9'
    assert spec.process.env == ('ARENA_KEY',) and spec.process.memory_mib == 4096
    assert spec.forfeit_score == (0, 3) and spec.evidence is False
    assert spec.options['step_seconds'].bounds == 'step'
    assert spec.options['member'].per_entrant and spec.options['member'].max_length == 40
    values = _contract.options(spec, {'step_seconds': 1}, preset='open')
    assert values == {
        'step_seconds': 1.0,
        'member': '',
        'check_matches': 1,
        'players': 'control',
        'timing': False,
    }
    assert 'check_matches' not in _contract.playing(spec, values)
    assert json.loads(json.dumps(spec.as_dict()))['presets']['open']['label'] == 'Open'


@pytest.mark.parametrize(
    ('text', 'message'),
    [
        ('[process]\nhost = "match"\n', 'built-in'),
        ('[process]\nthreads = 65\n', 'threads'),
        ('[process]\nmatch_minutes = 361\n', 'match_minutes'),
        ('[process]\ncheck_minutes = 121\n', 'check_minutes'),
        ('[process]\nenv = ["PYTHONPATH"]\n', 'env'),
        ('[process]\nmemory_mib = 70000\n', 'memory_mib'),
        (
            '[options.a]\ntype = "float"\ndefault = 1\nrange = [0, 400]\nbounds = "step"\n',
            'ceiling',
        ),
        ('[options.a]\ntype = "text"\ndefault = ""\nmax_length = 201\n', 'max_length'),
        ('[options.a]\ntype = "int"\ndefault = 5\nrange = [0, 3]\n', 'lies from'),
        ('[options.a]\ntype = "bool"\n', 'default'),
        ('[options.a]\ntype = "colour"\ndefault = 1\n', 'type'),
        ('[options.A]\ntype = "bool"\ndefault = true\n', 'lowercase'),
        ('[presets.p]\nnone = 1\n', 'no option'),
        ('[results]\nforfeit_score = [0]\n', 'forfeit_score'),
        ('surprise = 1\n', 'unknown'),
        ('requires = "0.2"\n', 'requires'),
    ],
)
def test_a_format_toml_that_does_not_fit_is_refused(text, message):
    with pytest.raises(_contract.ContractError, match=message):
        _spec(text)


def test_contract_and_names_are_checked():
    with pytest.raises(_contract.ContractError, match='contract'):
        _contract.parse('contract = 2\nname = "x"\nscript = "a.py"\n')
    with pytest.raises(_contract.ContractError, match='script'):
        _contract.parse('contract = 1\nname = "x"\nscript = "../a.py"\n')
    with pytest.raises(_contract.ContractError, match='does not read'):
        _contract.parse('contract = ')


def test_requires_names_the_version_it_needs():
    assert _contract.satisfies('0.2.0', '>=0.2.0') and not _contract.satisfies('0.1.0', '>=0.2.0')
    assert _contract.satisfies('0.3.1', '>=0.2,<0.4') and not _contract.satisfies('0.4.0', '<0.4')
    assert _contract.needs('>=0.3.0') == 'cuju-arena 0.3 or later'
    assert _contract.needs('>=0.2,<0.4') == 'cuju-arena >=0.2,<0.4'


def test_a_person_adds_exports_and_removes_a_format(arena_home):
    assert 'with your permissions' in _registry.TRUST
    added = _registry.add(PROBE)
    assert added.id == 'probe' and not added.builtin and added.spec.name == 'Probe'
    # The two built-in formats ship beside the one that a person added.
    assert [found.id for found in _registry.formats()] == ['kaggle', 'strict', 'probe']
    with pytest.raises(cuju.ArgumentError, match='exists'):
        _registry.add(PROBE)
    name, data = _registry.export('probe')
    assert name == 'probe.zip'
    assert 'probe/format.toml' in zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert _registry.remove('probe') and not _registry.remove('probe')
    again = _registry.add(data)
    assert again.id == 'probe' and _registry.digest(again.folder) == _registry.digest(PROBE)
    assert _registry.get('probe').label == 'probe'
    with pytest.raises(cuju.ArgumentError):
        _registry.get('nothing')


def test_a_format_that_does_not_fit_is_never_added(arena_home, tmp_path):
    folder = tmp_path / 'late'
    shutil.copytree(PROBE, folder)
    toml = folder / 'format.toml'
    toml.write_text(toml.read_text(encoding='utf-8').replace('>=0.1', '>=9.0'), encoding='utf-8')
    with pytest.raises(cuju.ArgumentError, match=r'needs cuju-arena 9.0 or later'):
        _registry.add(folder)
    toml.write_text('contract = 1\n', encoding='utf-8')
    with pytest.raises(cuju.ArgumentError, match='contract'):
        _registry.add(folder)


def test_a_broken_format_refuses_nobody_and_no_context_chooses_it(arena_home):
    _registry.add(PROBE)
    (arena_home / 'formats' / 'probe' / 'format.toml').write_text(
        'contract = 1\n', encoding='utf-8'
    )
    found = _registry.get('probe')
    assert found.spec is None and 'name' in found.broken
    with pytest.raises(cuju.ArgumentError, match='broken'):
        _side.choose('probe')
    _registry.mark_failing('probe', 'it broke')
    assert _registry.failing('probe')['message'] == 'it broke'


def _mark_format_when_released(at_replace, release):
    replace = os.replace

    def pause(temporary, path):
        at_replace.set()
        if not release.wait(30):
            raise TimeoutError('the status writer was not released')
        replace(temporary, path)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, 'replace', pause)
        _registry.mark_failing('first', 'first refusal')


def _change_format_status(ready, clear):
    ready.set()
    if clear:
        _registry._clear_failing('earlier')
    else:
        _registry.mark_failing('second', 'second refusal')


@pytest.mark.parametrize('clear', [False, True], ids=['mark', 'clear'])
def test_formats_serialize_status_updates_between_processes(arena_home, clear):
    _registry.mark_failing('earlier', 'earlier refusal')
    context = multiprocessing.get_context('spawn')
    at_replace, release, ready = context.Event(), context.Event(), context.Event()
    first = context.Process(target=_mark_format_when_released, args=(at_replace, release))
    second = context.Process(target=_change_format_status, args=(ready, clear))
    first.start()
    try:
        assert at_replace.wait(20)
        second.start()
        assert ready.wait(20)
        second.join(1)
        assert second.exitcode is None
    finally:
        release.set()
        for worker in (first, second):
            if worker.pid is not None:
                worker.join(20)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(5)
    assert first.exitcode == second.exitcode == 0
    status = json.loads((arena_home / 'format-status.json').read_text(encoding='utf-8'))
    expected = {'first': 'first refusal'}
    if not clear:
        expected.update(earlier='earlier refusal', second='second refusal')
    assert {name: failure['message'] for name, failure in status.items()} == expected
    assert list(arena_home.glob('*.part')) == []


def test_a_team_carries_suggested_formats(tmp_path):
    shutil.copytree(PROBE, tmp_path / 'cuju-team' / 'formats' / 'probe')
    found = _registry.suggested(tmp_path, 'rex')
    assert [(item.id, item.suggested_by) for item in found] == [('probe', 'rex')]


def test_a_choice_resolves_its_options_and_presets(arena_home):
    _registry.add(PROBE)
    choice = _side.choose({'id': 'probe', 'preset': 'quiet', 'options': {'check_steps': 4}})
    assert choice.values == {'mode': 'play', 'after': 0, 'check_steps': 4}
    assert choice.playing == {'mode': 'play', 'after': 0}
    assert _side.choose(None) is None
    for bad in ({'id': 'probe', 'options': {'mode': 'dance'}}, {'id': 'probe', 'preset': 'loud'}):
        with pytest.raises(cuju.ArgumentError):
            _side.choose(bad)


def test_identity_joins_what_reaches_play_and_the_libraries_behaviour(arena_home):
    found = _registry.add(PROBE)
    values = _contract.options(found.spec)
    identity = _identity.of(found, values)
    assert identity['format'] == 'probe' and identity['contract'] == 1
    assert identity['digest'] == _registry.digest(found.folder)[:12]
    assert identity['options'] == {'mode': 'play', 'after': 3}
    checked = _identity.of(found, {**values, 'check_steps': 9})
    assert _identity.key(checked) == _identity.key(identity)
    changed = _identity.of(found, {**values, 'after': 4})
    assert _identity.key(changed) != _identity.key(identity)
    assert _identity.key(_identity.of(found, values, revision='r2')) != _identity.key(identity)
    assert _identity.model('rex@ab', None, None) == 'rex@ab'
    assert _identity.model('rex@ab', _identity.DEFAULT, None) == 'rex@ab'
    assert _identity.model('rex@ab', identity, None).startswith('rex@ab~')
    with_players = _identity.model('rex@ab', _identity.DEFAULT, {'players': 1})
    assert with_players != 'rex@ab'
    assert _identity.key({'options': {'a': 30}}) == _identity.key({'options': {'a': 30.0}})


def test_the_verdict_key_holds_everything_a_verdict_depends_on(arena_home):
    found = _registry.add(PROBE)
    identity = _identity.of(found, _contract.options(found.spec))
    base = ('c1', identity, {'check_steps': 0}, None, '5v5', {'players': 1}, False, '0.2.0', False)
    key = _identity.verdict_key(*base)
    assert key == _identity.verdict_key(*base[:3], '5_vs_5', *base[4:])
    for place, other in (
        (0, 'c2'),
        (2, {'check_steps': 1}),
        (3, 'academy_corner'),
        (6, True),
        (7, '0.3.0'),
        (8, True),
    ):
        changed = list(base)
        changed[place] = other
        assert _identity.verdict_key(*changed) != key


def test_old_rows_gain_the_format_they_played_under(tmp_path):
    tournaments = tmp_path / 'tournaments'
    tournaments.mkdir()
    limits = {'threads': 1, 'load_seconds': 30.0}
    (tournaments / 'cup.json').write_text(
        json.dumps({'config': {'strict': limits}}), encoding='utf-8'
    )
    (tournaments / 'open.json').write_text(json.dumps({'config': {}}), encoding='utf-8')
    rows = [
        {'source': 'match', 'home': {'key': 'rex'}, 'away': {'key': 'builtin:0.60'}},
        {'source': 'tournament', 'ref': 'cup/m1', 'home': {'key': 'rex'}, 'away': {'key': 'ada'}},
        {
            'source': 'tournament',
            'ref': 'open/m1',
            'home': {'key': 'rex'},
            'away': {'key': 'random:uniform'},
        },
        {'source': 'evaluation', 'home': {'key': 'policy:mine'}, 'away': {'key': 'ada'}},
    ]
    _, changed = _migrate.rows(rows, tournaments)
    assert changed == 5
    assert (
        rows[0]['home']['submission_format'] == _identity.DEFAULT
        and 'format' not in rows[0]['home']
    )
    assert 'submission_format' not in rows[0]['away'] and 'submission_format' not in rows[3]['home']
    strict = rows[1]['home']['submission_format']
    assert strict['format'] == 'strict/1' and strict['options'] == limits
    assert (
        rows[1]['home']['format']
        == rows[1]['away']['format']
        == _identity.model_suffix(strict, None)
    )
    assert rows[2]['home']['submission_format'] == _identity.DEFAULT
    assert _migrate.rows(rows, tournaments)[1] == 0


def test_the_ratings_store_reads_old_rows_under_their_formats(arena_home):
    from cuju.arena._store import ratings as _store

    (arena_home / 'tournaments').mkdir(parents=True)
    (arena_home / 'tournaments' / 'cup.json').write_text(
        json.dumps({'config': {'strict': {'threads': 1}}}), encoding='utf-8'
    )
    for ref, home in (('cup/m1', 'rex'), ('cup/m2', 'rex')):
        _store.record(
            {
                'scenario': '5_vs_5',
                'home': {'key': home, 'checkpoint': 'c1', 'name': 'Rex'},
                'away': {'key': 'builtin:0.60'},
                'score': [1, 0],
                'source': 'tournament',
                'ref': ref,
            }
        )
    _store.record(
        {
            'scenario': '5_vs_5',
            'home': {'key': 'rex', 'checkpoint': 'c1', 'name': 'Rex'},
            'away': {'key': 'builtin:0.60'},
            'score': [2, 0],
            'source': 'match',
            'ref': 'x',
        }
    )
    rows = _store.results('5_vs_5')
    assert [_store.model_of(row['home']) == 'rex@c1' for row in rows] == [False, False, True]
    strict = _store.model_of(rows[0]['home'])
    assert _store.models()[strict]['name'] == 'Rex'


def test_the_cache_keeps_a_folder_per_identity_and_trims_the_oldest(arena_home):
    first = _cache.folder('a' * 12, 'c1')
    (first / 'model.bin').write_bytes(b'x' * 4000)
    second = _cache.folder('b' * 12, 'c1')
    (second / 'model.bin').write_bytes(b'x' * 4000)
    assert first.is_dir() and second.is_dir() and first.parent.name == 'format-cache'
    assert _cache.trim(5000, keep=second) == [first.name]
    assert not first.exists() and second.exists()


def test_a_ceiling_from_outside_forfeits_the_side_at_its_next_call(arena_home):
    from cuju.arena._formats import host as _host
    from cuju.arena.formats import v1 as formats

    found = _registry.add(PROBE)
    match = formats.Match(
        '5_vs_5',
        10,
        0,
        0,
        'left',
        1,
        (1,),
        {},
        'dynamic',
        'default',
        'match',
        {'mode': 'play', 'after': 3},
        False,
        1,
        arena_home,
    )
    host = _host.ProcessHost(found, match)
    host.ceiling('ceiling.memory', 'the tree held 70,000 MiB', detail='memory')
    with pytest.raises(_host.SideForfeit) as caught:
        host.reset()
    assert (caught.value.kind, caught.value.detail) == ('ceiling.memory', 'memory')
    with pytest.raises(ValueError):
        host.ceiling('memory', 'only ceiling kinds')
