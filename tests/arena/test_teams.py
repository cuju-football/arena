# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the team registry, its kits, styles and models."""

import io
import json
import logging
import os
import shutil
import struct

import pytest
import torch

import cuju
import cuju.errors
import cuju.state
from cuju.arena._format import recording as _recording
from cuju.arena._registry import teams as _teams
from cuju.arena._store import home as _home
from cuju.arena._store import library as _library
from support.common import (
    _SEVERAL,
    _SINGLE,
    _control,
    _frames,
    _one_second,
    _short_matches,
    _toy_team,
)


@pytest.mark.parametrize(
    'name', ['D:evil.py', '../out.py', '/etc/x', 'a\\b.py', 'a/../../b.py', 'a\x00b']
)
def test_hostile_member_names_are_refused(name):
    with pytest.raises(cuju.ArgumentError):
        _teams._check_member(name)


_DEVICE_NAMES = [
    'con',
    'com1.txt',
    '.. ',
    'nul',
    'NUL.py',
    'con .txt',
    'aux.tar.gz',
    'com0',
    'Com9.log',
    'lpt1',
    'COM\N{SUPERSCRIPT ONE}',
    'lpt\N{SUPERSCRIPT THREE}.txt',
    'conin$',
    'CONOUT$.log',
    'prn',
    'lib/nul/x.py',
    'main.py.',
    'main.py ',
    'a/.. /b',
]


@pytest.mark.parametrize('name', _DEVICE_NAMES)
def test_members_whose_names_open_a_device_on_windows_are_refused(name):
    """A device name of Windows, with any extension or case, or a part ending in a dot or space."""
    with pytest.raises(cuju.ArgumentError, match='open no device on Windows'):
        _teams._check_member(name)


@pytest.mark.parametrize('name', ['console.py', 'auxiliary.py', 'nul_test.py', 'com10.py', '.a'])
def test_names_close_to_devices_stay_members(name):
    assert str(_teams._check_member(name)) == name


def test_an_archive_with_a_device_member_is_refused_whole(arena_home):
    """The member `nul` of the review would open a device on Windows, so the upload is refused."""
    for member in ('nul', 'cuju-team/con.txt', 'helpers/AUX.py'):
        with pytest.raises(cuju.ArgumentError, match='open no device on Windows'):
            _teams.add('', 'team.zip', _archive({'main.py': _MAIN_PY, member: b'x'}))
    assert list(_home.folder('teams').iterdir()) == []


def test_files_of_a_team_named_as_devices_are_removed(arena_home, tmp_path, caplog):
    """A file of a team's folder named as a device, or ending in a dot or space, is removed."""
    names = ('nul', 'con .txt', 'conin$', 'shirt-front.png.', 'notes.md')
    kept = []
    with caplog.at_level(logging.WARNING, logger='cuju'):
        for name in names:
            path = tmp_path / name
            if os.name == 'nt' and name != 'notes.md':
                # Windows opens a device or drops the dot or space, so no folder holds such a file
                # there and the name alone is checked.
                kept.append(not _teams._device(name))
                continue
            path.write_bytes(b'x')
            kept.append(_teams._kept(path))
    assert kept == [False, False, False, False, True]
    assert sorted(path.name for path in tmp_path.iterdir()) == ['notes.md']
    assert os.name == 'nt' or 'opens a device on Windows' in caplog.text


def test_team_control_is_checked_and_travels(arena_home, tmp_path):
    team = _toy_team(tmp_path, 'several', _SEVERAL, '11v11')
    assert team['control'] == _teams.DEFAULT_CONTROL
    for wrong in (
        {'players': 12},
        {'players': 0},
        {'players': True},
        {'players': 9, 'goalkeeper': 'builtin'},
        {'players': 10, 'goalkeeper': 'nobody'},
        {'players': 2, 'extra': 1},
    ):
        with pytest.raises(cuju.ArgumentError):
            _teams.update(team['id'], control=wrong)
    keeper = {'players': 10, 'goalkeeper': 'builtin'}
    team = _teams.update(team['id'], control=keeper)
    assert _teams.agent_count(team) == 11
    # A change of format that the control does not fit returns it to the default.
    assert _teams.update(team['id'], format='5v5')['control']['players'] == 1
    team = _teams.update(team['id'], format='11v11', control=keeper)
    _, archive = _teams.export(team['id'])
    copy = _teams.add('copy', 'copy.zip', archive)
    assert copy['control'] == _teams.check_control(keeper, '11v11')
    with pytest.raises(cuju.ArgumentError):
        _teams.add('wrong', 'main.py', b'def agent(obs):\n    return [0]\n', '5v5', {'players': 6})


def test_single_player_team_plays_as_before(arena_home, tmp_path, monkeypatch):
    """A team of one controlled player plays the match of one agent, frame for frame."""
    from cuju.kaggle import KaggleAgent

    _short_matches(monkeypatch, 80)
    team = _toy_team(tmp_path, 'single', _SINGLE, '5v5')
    path = cuju.arena.play(team['id'], 'builtin', '5_vs_5', seed=9)
    _, frames = _frames(path)
    batch = cuju.BatchEnvironment(
        '5_vs_5_l1_r0',
        environment_count=1,
        representation='raw',
        seed=9,
        auto_reset=False,
        device='cpu',
    )
    player = KaggleAgent(_teams.load_agent(team['id']), batch, 0)
    reader = _recording.StateReader(10)
    batch.reset()
    expected = [reader.frame(batch.get_state(0))]
    for step in range(80):
        batch.step(player.act().reshape(1, 1))
        expected.append(reader.frame(batch.get_state(0), 1 if step == 79 else 0))
    assert frames == expected
    batch.close()


@pytest.mark.parametrize(
    ('scenario', 'format', 'control', 'active', 'engine_count', 'side', 'opponent'),
    [
        ('5_vs_5', '5v5', _control(2), None, 2, 0, False),
        (
            '11_vs_11_kaggle',
            '11v11',
            _control(3, 'fixed', ['CF', 'CB', 'CB'], 'builtin'),
            [9, 2, 3],
            11,
            0,
            True,
        ),
        ('11_vs_11_kaggle', '11v11', _control(2, 'fixed', [0, 9], 'agent'), [0, 9], 11, 1, True),
        (
            'benchmark_full_game_11_vs_11_hard',
            '11v11',
            _control(10, 'fixed', list(range(1, 11))),
            list(range(1, 11)),
            10,
            0,
            False,
        ),
        ('5_vs_5', '5v5', _control(2, action_set='v2'), None, 2, 1, True),
        (
            'academy_3_vs_1_with_keeper',
            '11v11',
            _control(2, 'fixed', [3, 1], 'builtin'),
            [3, 1],
            4,
            0,
            True,
        ),
        (
            'academy_3_vs_1_with_keeper',
            '11v11',
            _control(1, 'fixed', ['CB'], 'builtin'),
            [1],
            2,
            1,
            False,
        ),
    ],
)
def test_team_assignments_match_engine(
    arena_home,
    tmp_path,
    monkeypatch,
    scenario,
    format,
    control,
    active,
    engine_count,
    side,
    opponent,
):
    """Toy actions reach their selected players in either side of a mixed match."""
    from cuju.raw import grf_observation, team_sizes

    steps = 80
    _short_matches(monkeypatch, steps)
    source = f"""
expected = {active!r}
count = {control['players']}
hand = {control['action_set'] == 'v2'}

def agent(obs):
    players = [raw['active'] for raw in obs['players_raw']]
    assert obs['controlled_players'] == count
    assert expected is None or players == expected
    return [19 if hand and k == 0 else (3 if player % 2 else 7)
            for k, player in enumerate(players)]
"""
    team = _toy_team(tmp_path, 'selected', source, format, control)
    other = _toy_team(tmp_path, 'single', 'def agent(obs):\n    return [-1]\n', format)
    sides = [team['id'], other['id'] if opponent else 'builtin']
    if side:
        sides.reverse()
    path = cuju.arena.play(*sides, scenario, seed=7, device='cpu')
    document, frames = _frames(path)
    counts = [engine_count, int(opponent)]
    if side:
        counts.reverse()
    extended = control['assignment'] == 'fixed' or control['action_set'] == 'v2'
    asset = str(_teams.scenario_path(scenario)) if scenario.startswith('benchmark_') else scenario
    batch = cuju.BatchEnvironment(
        asset,
        environment_count=1,
        representation='raw',
        seed=7,
        auto_reset=False,
        device='cpu',
        agent_counts=[tuple(counts)],
        action_set='v2' if extended else 'default',
    )
    try:
        batch.reset()
        reader = _recording.StateReader(len(document['players']))
        expected = [reader.frame(batch.get_state(0))]
        first = 0 if side == 0 else counts[0]
        for step in range(steps):
            raw = batch.raw()
            columns = list(range(first, first + engine_count))
            by_player = {
                int(
                    grf_observation(raw, 0, column, *team_sizes(batch, 0, column))['active']
                ): column
                for column in columns
            }
            # A step on which GRF hands the players out again leaves them to the built-in AI,
            # as the runner does.
            complete = len(by_player) == len(columns) and all(i in by_player for i in active or ())
            chosen = (
                columns
                if active is None
                else ([by_player[index] for index in active] if complete else [])
            )
            actions = torch.zeros((1, sum(counts)), dtype=torch.int32)
            if opponent:
                actions[0, counts[0] if side == 0 else 0] = 18
            for column in columns:
                actions[0, column] = 19
            for k, column in enumerate(chosen):
                player = int(
                    grf_observation(raw, 0, column, *team_sizes(batch, 0, column))['active']
                )
                actions[0, column] = (
                    19 if control['action_set'] == 'v2' and k == 0 else 3 if player % 2 else 7
                )
            _, _, done, _ = batch.step(actions)
            final = bool(done[0]) or step == steps - 1
            expected.append(reader.frame(batch.get_state(0), int(final)))
            if final:
                break
        assert frames == expected
    finally:
        batch.close()


def test_earlier_team_control_migrates(arena_home, tmp_path):
    """Earlier metadata selects the same players after loading and export."""
    team = _toy_team(tmp_path, 'earlier', _SEVERAL, '11v11')
    meta = _teams._meta_path(team['id'])
    saved = json.loads(meta.read_text(encoding='utf-8'))
    saved['control'] = {'players': 10, 'goalkeeper': 'builtin'}
    meta.write_text(json.dumps(saved), encoding='utf-8')
    control = _teams.entry(team['id'])['control']
    assert control == _control(10, 'fixed', list(range(1, 11)), 'builtin')
    _, archive = _teams.export(team['id'])
    assert _teams.add('copy', 'copy.zip', archive)['control'] == control
    saved['control'] = {'players': 2, 'goalkeeper': 'agent'}
    meta.write_text(json.dumps(saved), encoding='utf-8')
    assert _teams.entry(team['id'])['control'] == _control(2)


def test_a_submission_folder_leaves_its_links_out(arena_home, tmp_path, caplog):
    """A link in a submission folder, to a file or a folder, is never followed into the team."""
    secrets = tmp_path / 'secrets'
    secrets.mkdir()
    (secrets / 'id_ed25519').write_bytes(b'private key')
    folder = tmp_path / 'submission'
    (folder / 'lib' / '__pycache__').mkdir(parents=True)
    (folder / 'main.py').write_bytes(_MAIN_PY)
    (folder / 'lib' / 'helper.py').write_bytes(b'VALUE = 1\n')
    (folder / 'lib' / '__pycache__' / 'helper.pyc').write_bytes(b'cache')
    (folder / 'key').symlink_to(secrets / 'id_ed25519')
    (folder / 'lib' / 'keys').symlink_to(secrets, target_is_directory=True)
    with caplog.at_level(logging.WARNING, logger='cuju'):
        team = _teams.add_folder(folder, 'Linked')
    stored = _home.folder('teams') / team['id']
    assert sorted(p.relative_to(stored).as_posix() for p in stored.rglob('*') if p.is_file()) == [
        'cuju-team/team.json',
        'lib/helper.py',
        'main.py',
    ]
    assert 'key' in caplog.text and 'lib/keys' in caplog.text
    assert b'private key' not in _teams.export(team['id'])[1]


def test_team_names_from_files_print_no_escapes_or_turned_text(arena_home, capsys):
    """The name of an imported team file, or of one edited by hand, keeps only printing text."""
    from cuju.arena.__main__ import main

    escape, bell = chr(27), chr(7)
    turned = '\N{RIGHT-TO-LEFT OVERRIDE}\N{LEFT-TO-RIGHT ISOLATE}\N{ZERO WIDTH SPACE}'
    hostile = f'{escape}[2J{escape}[31mRed{turned} FC{escape}]0;title{bell}\N{LINE SEPARATOR}'
    files = {'main.py': _MAIN_PY, 'cuju-team/team.json': json.dumps({'name': hostile}).encode()}
    team = _teams.add('', 'team.zip', _archive(files))
    assert team['name'].isprintable() and escape not in team['name']
    assert team['name'].startswith('[2J [31mRed') and 'FC' in team['name']
    meta = _teams._meta_path(team['id'])
    meta.write_text(
        json.dumps({**json.loads(meta.read_text(encoding='utf-8')), 'name': hostile}),
        encoding='utf-8',
    )
    builtin = _teams._meta_path(_teams.BUILTIN)
    builtin.parent.mkdir(parents=True)
    builtin.write_text(json.dumps({'name': hostile}), encoding='utf-8')
    assert all(entry['name'].isprintable() for entry in _teams.teams())
    assert main(['team', 'list']) == 0
    printed = capsys.readouterr().out
    assert 'Red' in printed and all(line.isprintable() for line in printed.splitlines())


def test_a_submission_reads_by_its_own_source_encoding(arena_home):
    """A main.py reads as UTF-8, or by its encoding line, whatever the code page of the system."""
    name = 'Ação 中文 FC'
    utf8 = f'NAME = {name!r}\ndef agent(obs):\n    return [len(NAME)]\n'.encode()
    latin = b'# -*- coding: latin-1 -*-\nNAME = "\xe7"\ndef agent(obs):\n    return [NAME]\n'
    team = _teams.add('', 'team.zip', _archive({'main.py': utf8}))
    assert _teams.load_agent(team['id'])({}) == [len(name)]
    team = _teams.add('', 'team.zip', _archive({'main.py': latin}))
    assert _teams.load_agent(team['id'])({}) == ['ç']


def test_team_identifiers_are_unique_within_a_second(arena_home, monkeypatch):
    _one_second(monkeypatch)
    source = b'def agent(obs):\n    return [0]\n'
    added = [_teams.add(f'team {k}', 'main.py', source) for k in range(6)]
    added.append(_teams.duplicate(added[0]['id']))
    identifiers = [team['id'] for team in added]
    assert len(set(identifiers)) == 7
    assert sorted(team['id'] for team in _teams.teams() if team['kind'] != 'builtin') == sorted(
        identifiers
    )
    assert _teams.entry(identifiers[-1])['name'] == 'team 0 copy'


def _archive(files):
    """Returns a zip of the files, by their paths inside it."""
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


_MAIN_PY = b'def agent(obs):\n    return 0\n'


@pytest.mark.parametrize(
    'team_json',
    [
        b'{"format": []}',
        b'{"id": 5}',
        b'{"created": 5, "name": 7}',
        b'{"style": [1]}',
        b'{"style": {"players": 3}, "control": {"players": []}}',
        b'{"format": {"5v5": 1}, "model": {"joints": 4}}',
        b'[' * 100000 + b']' * 100000,
        b'{"a": ' * 5000 + b'1' + b'}' * 5000,
        b'{"name": "' + b'x' * (2 * 1024 * 1024) + b'"}',
    ],
)
def test_crafted_team_files_never_break_the_list(arena_home, team_json):
    """A team file of an archive with values of the wrong type adds a team or fails cleanly."""
    data = _archive({'main.py': _MAIN_PY, 'cuju-team/team.json': team_json})
    try:
        team = _teams.add('', 'team.zip', data)
    except cuju.ArgumentError:
        team = None
    listed = _teams.teams()
    folders = sorted(path.name for path in _home.folder('teams').iterdir())
    if team is None:
        assert folders == [] and [t['id'] for t in listed] == ['builtin']
    else:
        assert folders == [team['id']]
        assert [t['id'] for t in listed] == ['builtin', team['id']]
        assert isinstance(team['name'], str) and team['format'] in _teams.FORMATS


def test_a_failed_import_removes_its_folder(arena_home, monkeypatch):
    """Any failure after the folder of a new team is made removes the folder again."""
    data = _archive({'main.py': _MAIN_PY, 'cuju-team/team.json': b'{"id": "../../../.ssh"}'})

    def broken(*_):
        raise RuntimeError('a check broke')

    monkeypatch.setattr(_teams, '_import', broken)
    with pytest.raises(RuntimeError):
        _teams.add('', 'team.zip', data)
    assert list(_home.folder('teams').iterdir()) == []
    monkeypatch.setattr(_teams, '_import', lambda *_: (_ for _ in ()).throw(TypeError('x')))
    with pytest.raises(cuju.ArgumentError):
        _teams.add('', 'team.zip', data)
    assert list(_home.folder('teams').iterdir()) == []
    assert [t['id'] for t in _teams.teams()] == ['builtin']


def _bounded_refusal(filename, data, match):
    """Adds an archive that must be refused, and returns the peak memory and seconds it took."""
    import time
    import tracemalloc

    tracemalloc.start()
    started = time.monotonic()
    try:
        with pytest.raises(cuju.ArgumentError, match=match):
            _teams.add('', filename, data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert list(_home.folder('teams').iterdir()) == []
    return peak, time.monotonic() - started


def _tar_header(name, size=0, kind=None):
    import tarfile

    member = tarfile.TarInfo(name)
    member.size = size
    if kind is not None:
        member.type = kind
    return member.tobuf(tarfile.USTAR_FORMAT)


@pytest.mark.parametrize('codec', ['bz2', 'gzip', 'lzma'])
def test_a_small_tar_of_a_great_many_members_is_refused_early(arena_home, codec):
    """The bomb of the review, 200,000 empty members in a few kB, stops at the 10,001st member."""
    import importlib

    compress = importlib.import_module(codec).compress
    # Streams joined end to end decompress as one, so the archive takes one compression.
    data = compress(_tar_header('a') * 1000) * 200
    assert len(data) < 1_000_000
    peak, seconds = _bounded_refusal('team.tar', data, '10,000 files')
    assert peak < 32 * 1024 * 1024 and seconds < 30


def test_a_tar_header_that_claims_a_gigabyte_is_refused_early(arena_home):
    """A pax header of a gigabyte of zeros is refused after a megabyte, never held whole."""
    import bz2
    import tarfile

    data = bz2.compress(_tar_header('././@PaxHeader', 1 << 30, tarfile.XHDTYPE))
    data += bz2.compress(bytes(1 << 20)) * 1024
    peak, seconds = _bounded_refusal('team.tar.gz', data, '10,000 files')
    assert peak < 32 * 1024 * 1024 and seconds < 30
    # A member that claims more than the whole limit is refused before anything moves past it.
    data = bz2.compress(_tar_header('main.py', 600 * 1024 * 1024))
    _bounded_refusal('team.tgz', data, '500 MiB')


def _many_entry_zip(count, declared):
    """Returns a zip of `count` entries in its central directory, whose end declares `declared`."""
    local = struct.pack('<4s5H3L2H', b'PK\x03\x04', 20, 0, 0, 0, 0, 0, 0, 0, 1, 0) + b'a'
    entry = struct.pack(
        '<4s6H3L5H2L', b'PK\x01\x02', 20, 20, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0
    )
    directory = (entry + b'a') * count
    end = struct.pack(
        '<4s4H2LH', b'PK\x05\x06', 0, 0, declared, declared, len(directory), len(local), 0
    )
    return local + directory + end


@pytest.mark.parametrize('declared', [0xFFFF, 1])
def test_a_zip_of_a_great_many_entries_is_refused_before_zipfile_reads_them(arena_home, declared):
    """A zip whose directory holds 200,000 entries is refused before a list of them is built."""
    import zipfile

    data = _many_entry_zip(200_000, declared)
    # zipfile itself reads every entry of the directory, whatever the end record declares.
    assert len(zipfile.ZipFile(io.BytesIO(_many_entry_zip(20, 1))).infolist()) == 20
    peak, seconds = _bounded_refusal('team.zip', data, '10,000 files')
    assert peak < 16 * 1024 * 1024 and seconds < 10


@pytest.mark.parametrize('codec', [None, 'gzip', 'bz2', 'lzma'])
def test_tar_submissions_of_every_compression_still_import(arena_home, codec):
    """A tar archive, plain or compressed, with a pax header of a long name, imports whole."""
    import importlib
    import tarfile

    long_name = 'lib/' + 'x' * 150 + '.py'
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name, data in (('main.py', _MAIN_PY), (long_name, b'VALUE = 1\n')):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    data = buffer.getvalue()
    if codec is not None:
        data = importlib.import_module(codec).compress(data)
    team = _teams.add('', 'team.tar.gz', data)
    folder = _home.folder('teams') / team['id']
    assert (folder / 'main.py').read_bytes() == _MAIN_PY
    assert (folder / long_name).read_bytes() == b'VALUE = 1\n'


# Python 3.12 and later warn of a tar extracted without a filter, which this test makes.
@pytest.mark.filterwarnings('ignore::DeprecationWarning')
def test_a_tar_without_the_data_filter_writes_its_members_plain(arena_home, monkeypatch):
    """Python before 3.10.12 has no data filter, and the members land without their mode bits."""
    import tarfile

    monkeypatch.delattr(tarfile, 'data_filter', raising=False)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        member = tarfile.TarInfo('main.py')
        member.size, member.mode = len(_MAIN_PY), 0o6777
        archive.addfile(member, io.BytesIO(_MAIN_PY))
    team = _teams.add('', 'team.tar', buffer.getvalue())
    main = _home.folder('teams') / team['id'] / 'main.py'
    assert main.read_bytes() == _MAIN_PY
    assert main.stat().st_mode & 0o7000 == 0


def test_samples_add_restore_and_register_the_starter_team(arena_home):
    from cuju.arena._store import samples as _samples

    listed = {entry['id']: entry for entry in _samples.listing()}
    assert list(listed) == ['sample-5v5', 'sample-11v11', 'starter-team']
    assert {entry['state'] for entry in listed.values()} == {'missing'}
    added = _samples.add(['sample-5v5'])
    assert added['changes'] == [{'id': 'sample-5v5', 'change': 'added'}]
    assert _library.trash('sample-5v5')
    assert {e['id']: e['state'] for e in _samples.listing()}['sample-5v5'] == 'trash'
    assert _samples.add(['sample-5v5'])['changes'] == [{'id': 'sample-5v5', 'change': 'restored'}]
    team = _samples.add(['starter-team'])
    entry = next(item for item in team['samples'] if item['id'] == 'starter-team')
    assert team['changes'] == [{'id': 'starter-team', 'change': 'added'}]
    registered = _teams.entry(entry['target'])
    assert (registered['name'], registered['format'], entry['state']) == (
        'Starter team',
        '11v11',
        'library',
    )
    assert (arena_home / 'teams' / entry['target'] / 'main.py').is_file()
    # A sample that the library holds stays as it is, and a removed team comes back as new.
    assert _samples.add(['starter-team', 'sample-5v5'])['changes'] == []
    assert _teams.remove(entry['target'])
    assert {e['id']: e['state'] for e in _samples.listing()}['starter-team'] == 'missing'
    every = _samples.add()
    assert {change['id'] for change in every['changes']} == {'sample-11v11', 'starter-team'}
    with pytest.raises(cuju.errors.ArgumentError):
        _samples.add(['sample-3v3'])


def test_cli_team_control_show_set_and_json(arena_home, tmp_path, capsys):
    from cuju.arena.__main__ import main

    folder = tmp_path / 'submission'
    folder.mkdir()
    (folder / 'main.py').write_text('def agent(obs):\n    return [0]\n', encoding='utf-8')
    assert main(['team', 'add', str(folder), '--size', '5v5', '--selection', '1,2', '--json']) == 0
    added = json.loads(capsys.readouterr().out)
    assert added['size'] == '5v5' and 'format' not in added
    assert added['control'] == {
        'players': 2,
        'assignment': 'fixed',
        'selection': [1, 2],
        'goalkeeper': 'scenario',
        'action_set': 'default',
    }
    assert main(['team', 'set', added['id'], '--assignment', 'dynamic', '--players', '3']) == 0
    assert 'changed' in capsys.readouterr().out
    control = _teams.entry(added['id'])['control']
    assert (control['players'], control['assignment'], control['selection']) == (3, 'dynamic', [])
    assert main(['team', 'set', added['id'], '--name', 'Renamed', '--action-set', 'v2']) == 0
    capsys.readouterr()
    assert main(['team', 'show', added['id']]) == 0
    shown = capsys.readouterr().out
    assert 'Renamed' in shown and 'action set v2' in shown and '3 controlled' in shown
    assert main(['team', 'list', '--json']) == 0
    listed = json.loads(capsys.readouterr().out)['teams']
    assert [team['id'] for team in listed] == ['builtin', added['id']]
    assert main(['sample', 'list', '--json']) == 0
    assert [entry['id'] for entry in json.loads(capsys.readouterr().out)['samples']][:2] == [
        'sample-5v5',
        'sample-11v11',
    ]
    assert main(['team', 'remove', added['id']]) == 0
    assert f'removed {added["id"]}' in capsys.readouterr().out
    assert main(['team', 'add', str(tmp_path / 'nowhere')]) == 1
    assert 'found nothing at' in capsys.readouterr().err


def test_names_of_the_built_in_ai_travel_as_a_label_until_renamed(arena_home):
    builtin = _teams.teams()[0]
    assert builtin['name'] == "GRF's built-in AI"
    assert builtin['name_id'] == 'server.names.builtin' and builtin['name_values'] == {}
    header = _teams.header_team(_teams.BUILTIN)
    assert header['name_id'] == 'server.names.builtin'
    # The page sends the name back with every change of the style, which keeps the label.
    kept = _teams.update(_teams.BUILTIN, name="GRF's built-in AI")
    assert kept['name_id'] == 'server.names.builtin'
    renamed = _teams.update(_teams.BUILTIN, name='House AI')
    assert renamed['name'] == 'House AI' and 'name_id' not in renamed
    assert 'name_id' not in _teams.entry(_teams.BUILTIN)
    stored = json.loads(_teams._meta_path(_teams.BUILTIN).read_text(encoding='utf-8'))
    assert stored['name'] == 'House AI' and 'name_id' not in stored
    _teams.update(_teams.BUILTIN, name="GRF's built-in AI")
    assert _teams.entry(_teams.BUILTIN)['name_id'] == 'server.names.builtin'


def test_names_of_scenarios_in_team_messages_travel_as_scenarios(arena_home):
    from cuju.arena._text import messages as _messages

    team = {'id': 't', 'name': 'Multi four', 'kind': 'submission', 'format': '5v5'}
    with pytest.raises(_messages.MessageError) as raised:
        _teams.check_scenario([team, _teams.entry(_teams.BUILTIN)], '11_vs_11_kaggle')
    error = raised.value
    assert error.message_id == 'server.teams.scenario_format'
    assert error.values['scenario'] == _messages.scenario('11_vs_11_kaggle')
    # The English text of the command line stays as it was.
    assert str(error) == 'expected a scenario of 5v5 for Multi four, found 11_vs_11_kaggle'
    assert _messages.valid_label(
        {'id': error.message_id, 'text': str(error), 'values': error.values}
    )


def test_names_of_games_and_benchmark_attempts(arena_home):
    from cuju.arena._playing import play as _play
    from cuju.arena._text import messages as _messages

    builtin = _messages.label('server.names.builtin')
    title = _play.game_title([_messages.label('server.names.you'), builtin], None)
    assert title['text'] == "You against GRF's built-in AI"
    assert title['values']['right'] == builtin
    # The built-in AI under its own name is a label, and a renamed team stays text.
    assert _teams.shown_name(_teams.entry(_teams.BUILTIN)) == builtin
    assert _teams.shown_name({'name': 'House AI'}) == 'House AI'
    attempt = _messages.label(
        'server.names.benchmark_attempt',
        task=_messages.scenario('academy_empty_goal_close'),
        attempt=5,
        competitor='Rule bot',
    )
    assert attempt['text'] == 'academy_empty_goal_close, attempt 5, Rule bot'
    assert _messages.valid_label(attempt)


def test_a_team_folder_named_as_a_random_side_is_left_out(arena_home, tmp_path, caplog):
    from cuju.arena._playing import draw as _draw

    assert _draw.RANDOM in _teams.RESERVED
    kept = _toy_team(tmp_path, 'kept', 'def agent(obs):\n    return [0]\n', '5v5')
    folders = arena_home / 'teams'
    shutil.copytree(folders / kept['id'], folders / _draw.RANDOM)
    with caplog.at_level(logging.WARNING):
        identifiers = [team['id'] for team in _teams.teams()]
    # The team by its own folder stays, and the folder that a request would read as a random side
    # is left out with a word on how to keep it.
    assert identifiers == ['builtin', kept['id']]
    assert 'rename the folder' in caplog.text
