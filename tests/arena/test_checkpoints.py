# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the checkpoints of a team, the entrants that name them and the home scenario."""

import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

import cuju.errors
from cuju.arena.__main__ import main
from cuju.arena._evaluate import ratings as _ratings
from cuju.arena._registry import checkpoints as _checkpoints
from cuju.arena._registry import teams as _teams
from cuju.arena._store import home as _home
from cuju.arena._store import runs as _runs
from cuju.arena._tournaments import tournament as _tournament
from support.common import _SINGLE, _toy_team

OLD = Path(__file__).parents[1] / 'support' / 'old-teams'
_SECOND = """
def agent(obs):
    return [3]
"""
_THIRD = """
def agent(obs):
    return [4]
"""


def _zip(source):
    """Returns a zip that holds `main.py` with the source."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('main.py', source)
    return buffer.getvalue()


def _team(tmp_path, name='toy', source=_SINGLE, format='11v11'):
    return _toy_team(tmp_path, name, source, format)


def _answer(identifier):
    """Returns what the agent of an entrant answers for the first step."""
    return _teams.load_agent(identifier)({'step': 1})


def test_a_team_of_0_1_0_keeps_its_checkpoint_id(arena_home):
    shutil.copytree(OLD / 'plain', _home.folder('teams') / 't-plain')
    folder = _home.folder('teams') / 't-plain'
    before = _runs.files_digest(folder, ('cuju-team',))
    assert _runs.team_checkpoint('t-plain')['id'] == before[:16]
    listing = _checkpoints.listing('t-plain')
    assert [item['id'] for item in listing['checkpoints']] == [before[:16]]
    assert listing['current'] == before[:16] and listing['checkpoints'][0]['current'] is True
    # The list lives in the folder of the look, so writing it leaves the id as it was.
    assert (folder / 'cuju-team' / 'checkpoints.json').is_file()
    assert _runs.team_checkpoint('t-plain')['id'] == before[:16]
    assert _ratings.checkpoint_of('t-plain') == before[:16]
    assert listing['checkpoints'][0]['digest'] == before


def test_the_ground_is_recorded_before_the_files_are_replaced(arena_home):
    shutil.copytree(OLD / 'plain', _home.folder('teams') / 't-plain')
    folder = _home.folder('teams') / 't-plain'
    first = _runs.files_digest(folder, ('cuju-team',))
    _checkpoints.add('t-plain', 'main.py', _SECOND.encode())
    assert _teams.entry('t-plain')['ground'] == first
    assert _teams.first_checkpoint('t-plain') == first


def test_an_entrant_with_a_digest_plays_the_earlier_files(arena_home, tmp_path):
    team = _team(tmp_path)
    first = _checkpoints.current_id(team['id'])
    _checkpoints.add(team['id'], 'main.py', _SECOND.encode())
    entrant = f'{team["id"]}@{first}'
    assert _answer(team['id']) == [3]
    assert _answer(entrant) == [5]
    found = _checkpoints.resolve(entrant)
    assert (found['team'], found['checkpoint'], found['current']) == (team['id'], first, False)
    assert (
        found['folder'] == _home.folder('teams') / team['id'] / 'cuju-team' / 'checkpoints' / first
    )
    bare = _checkpoints.resolve(team['id'])
    assert bare['entrant'] == team['id'] and bare['current'] and bare['folder'].name == team['id']
    # Every context reads an entrant the same way.
    entry = _teams.entry(entrant)
    assert (entry['entrant'], entry['checkpoint'], entry['id']) == (entrant, first, team['id'])
    side = _ratings.side_of(f'team:{entrant}')
    assert (side['key'], side['checkpoint'], side['team']) == (team['id'], first, entrant)
    assert _ratings.team_side(team['id'])['checkpoint'] == _checkpoints.current_id(team['id'])
    assert _teams.header_team(entrant)['checkpoint'] == first
    assert 'checkpoint' not in _teams.header_team(team['id'])
    assert _ratings.checkpoint_of(entrant) == first


def test_entrants_of_other_shapes_are_refused(arena_home, tmp_path):
    team = _team(tmp_path)
    for text in (
        f'{team["id"]}@',
        f'{team["id"]}@XYZ',
        f'{team["id"]}@{"0" * 16}',
        'builtin@' + '0' * 16,
    ):
        with pytest.raises(cuju.errors.ArgumentError):
            _teams.entry(text)
    assert _checkpoints.split('a@' + 'ab' * 8) == ('a', 'ab' * 8)
    assert _checkpoints.split('a') == ('a', None)
    assert _checkpoints.entrant('a', None) == 'a' and _checkpoints.entrant('a', 'b') == 'a@b'
    assert _checkpoints.resolve('builtin')['checkpoint'] is None
    assert _ratings.checkpoint_of('builtin') is None


def test_the_keeping_rule_drops_the_oldest_unpinned_checkpoint(arena_home, tmp_path, monkeypatch):
    monkeypatch.setenv('CUJU_ARENA_CONFIG', str(_config(tmp_path, 'checkpoints = 3')))
    team = _team(tmp_path)
    ids = [_checkpoints.current_id(team['id'])]
    for number in range(4):
        listing = _checkpoints.add(team['id'], 'main.py', f'# {number}\n{_SECOND}'.encode())
        ids.append(listing['current'])
        if number == 0:
            _checkpoints.change(team['id'], ids[0], pinned=True)
    kept = [item['id'] for item in _checkpoints.listing(team['id'])['checkpoints']]
    # The pinned first checkpoint stays, then the oldest unpinned ones drop, current stays.
    assert ids[0] in kept and ids[-1] in kept and len(kept) == 3
    assert ids[1] not in kept and ids[2] not in kept
    folder = _home.folder('teams') / team['id'] / 'cuju-team' / 'checkpoints'
    assert sorted(path.name for path in folder.iterdir()) == sorted(set(kept) - {ids[-1]})
    # A dropped checkpoint plays no more, and its entrant is refused.
    with pytest.raises(cuju.errors.ArgumentError):
        _teams.entry(f'{team["id"]}@{ids[1]}')


def test_the_size_limit_drops_checkpoints_too(arena_home, tmp_path, monkeypatch):
    monkeypatch.setenv('CUJU_ARENA_CONFIG', str(_config(tmp_path, 'checkpoint_mib = 2')))
    team = _team(tmp_path)
    first = _checkpoints.current_id(team['id'])
    big = b'# ' + b'x' * (700 * 1024) + b'\n' + _SECOND.encode()
    for number in range(3):
        listing = _checkpoints.add(team['id'], 'main.py', big + f'# {number}\n'.encode())
    # Three of 700 KiB pass 2 MiB, so the two oldest go and the newest two stay.
    assert len(listing['checkpoints']) == 2 and listing['checkpoints'][0]['current']
    assert first not in [item['id'] for item in listing['checkpoints']]
    assert sum(item['bytes'] for item in listing['checkpoints']) <= 2 * 1024 * 1024


def _config(tmp_path, line):
    path = tmp_path / 'keep.toml'
    path.write_text(f'[teams]\n{line}\n', encoding='utf-8')
    return path


def test_the_same_files_and_a_known_checkpoint_are_refused(arena_home, tmp_path):
    team = _team(tmp_path)
    first = _checkpoints.current_id(team['id'])
    original = (tmp_path / 'toy' / 'main.py').read_bytes()
    with pytest.raises(cuju.errors.ArgumentError) as same:
        _checkpoints.add(team['id'], 'main.py', original)
    assert same.value.message_id == 'server.checkpoints.same'
    _checkpoints.add(team['id'], 'main.py', _SECOND.encode())
    with pytest.raises(cuju.errors.ArgumentError) as known:
        _checkpoints.add(team['id'], 'main.py', original)
    assert known.value.message_id == 'server.checkpoints.known' and first in str(known.value)
    assert _answer(team['id']) == [3]


def test_hostile_and_broken_uploads_leave_the_team_as_it_was(arena_home, tmp_path):
    team = _team(tmp_path)
    folder = _home.folder('teams') / team['id']
    before = {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()
    }
    hostile = io.BytesIO()
    with zipfile.ZipFile(hostile, 'w') as archive:
        archive.writestr('main.py', _SECOND)
        archive.writestr('../outside.py', 'x')
    for filename, data in [
        ('x.zip', hostile.getvalue()),
        ('x.zip', b'not a zip'),
        ('x.txt', b'text'),
        ('x.zip', _zip('').replace(b'main.py', b'other.py')),
    ]:
        with pytest.raises(cuju.errors.ArgumentError):
            _checkpoints.add(team['id'], filename, data)
    with pytest.raises(cuju.errors.ArgumentError):
        _checkpoints.add(team['id'], 'main.py', b'x' * (_checkpoints.MAXIMUM_BYTES + 1))
    after = {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()
    }
    assert {k: v for k, v in after.items() if k != 'cuju-team/checkpoints.json'} == before
    assert not (folder / 'cuju-team' / _checkpoints.INCOMING).exists()
    assert not (folder.parent / 'outside.py').exists()


def test_a_zip_a_tar_a_folder_and_an_entry_file_each_make_a_checkpoint(arena_home, tmp_path):
    import tarfile

    team = _team(tmp_path)
    kinds = []
    kinds.append(('a.zip', _zip(_SECOND)))
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode='w:gz') as archive:
        info = tarfile.TarInfo('main.py')
        data = _THIRD.encode()
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    kinds.append(('a.tar.gz', tar.getvalue()))
    kinds.append(('main.py', b'def agent(obs):\n    return [6]\n'))
    kinds.append(
        (
            'folder',
            _teams.folder_bundle(
                [('main.py', b'def agent(obs):\n    return [7]\n'), ('w/a.bin', b'1')]
            ),
        )
    )
    for filename, data in kinds:
        _checkpoints.add(team['id'], filename, data)
    assert len(_checkpoints.listing(team['id'])['checkpoints']) == 5
    assert _answer(team['id']) == [7]
    assert (_home.folder('teams') / team['id'] / 'w' / 'a.bin').read_bytes() == b'1'


def test_control_belongs_to_each_checkpoint(arena_home, tmp_path):
    team = _team(tmp_path)
    first = _checkpoints.current_id(team['id'])
    four = _teams.check_control({'players': 4, 'goalkeeper': 'scenario'}, '11v11')
    _teams.update(team['id'], control=four)
    listing = _checkpoints.add(team['id'], 'main.py', _SECOND.encode())
    # A new checkpoint starts with the control of the one before.
    second = listing['current']
    assert listing['checkpoints'][0]['control'] == four
    one = _teams.check_control({'players': 2, 'goalkeeper': 'scenario'}, '11v11')
    found = _checkpoints.change(team['id'], first, control=one)
    assert found['control'] == one and _teams.entry(team['id'])['control'] == four
    assert _teams.entry(f'{team["id"]}@{first}')['control'] == one
    # The team's own control follows the checkpoint that plays.
    _teams.update(team['id'], control=_teams.check_control({'players': 3}, '11v11'))
    assert _checkpoints.get(team['id'], second)['control']['players'] == 3
    changed = _checkpoints.change(team['id'], first, current=True)
    assert changed['current'] is True and _checkpoints.current_id(team['id']) == first
    assert _teams.entry(team['id'])['control'] == one
    assert _answer(team['id']) == [5]
    assert _answer(f'{team["id"]}@{second}') == [3]


def test_labels_notes_steps_and_pins_are_checked(arena_home, tmp_path):
    team = _team(tmp_path)
    first = _checkpoints.current_id(team['id'])
    found = _checkpoints.change(team['id'], first, label='  base ', note='n', step=10, pinned=True)
    assert (found['label'], found['note'], found['step'], found['pinned']) == (
        'base',
        'n',
        10,
        True,
    )
    for fields in (
        {'label': 'x' * 81},
        {'note': 'x' * 501},
        {'step': -1},
        {'step': True},
        {'pinned': 'yes'},
        {'current': False},
        {'colour': 'red'},
    ):
        with pytest.raises(cuju.errors.ArgumentError):
            _checkpoints.change(team['id'], first, **fields)
    with pytest.raises(cuju.errors.ArgumentError):
        _checkpoints.change(team['id'], '0' * 16, label='x')
    with pytest.raises(cuju.errors.ArgumentError) as current:
        _checkpoints.remove(team['id'], first)
    assert current.value.message_id == 'server.checkpoints.remove_current'


def test_an_earlier_checkpoint_is_removed_with_its_files(arena_home, tmp_path):
    team = _team(tmp_path)
    first = _checkpoints.current_id(team['id'])
    _checkpoints.add(team['id'], 'main.py', _SECOND.encode())
    assert _checkpoints.remove(team['id'], first) is True
    assert _checkpoints.remove(team['id'], first) is False
    assert not (_home.folder('teams') / team['id'] / 'cuju-team' / 'checkpoints' / first).exists()
    assert [i['id'] for i in _checkpoints.listing(team['id'])['checkpoints']] == [
        _checkpoints.current_id(team['id'])
    ]


def test_files_changed_by_hand_become_a_new_checkpoint(arena_home, tmp_path):
    team = _team(tmp_path)
    first = _checkpoints.current_id(team['id'])
    _checkpoints.change(team['id'], first, label='base', pinned=True)
    folder = _home.folder('teams') / team['id']
    (folder / 'main.py').write_text(_SECOND, encoding='utf-8')
    listing = _checkpoints.listing(team['id'])
    (only,) = listing['checkpoints']
    assert only['id'] == _checkpoints.current_id(team['id']) != first and only['current']
    assert only['label'] == ''


def test_export_writes_the_current_checkpoint_and_all_checkpoints_adds_the_rest(
    arena_home, tmp_path
):
    team = _team(tmp_path)
    first = _checkpoints.current_id(team['id'])
    _checkpoints.add(team['id'], 'main.py', _SECOND.encode(), 'later')
    _, plain = _teams.export(team['id'])
    _, everything = _teams.export(team['id'], all_checkpoints=True)
    with zipfile.ZipFile(io.BytesIO(plain)) as archive:
        names = archive.namelist()
        assert archive.read('main.py').decode() == _SECOND
    assert not any('checkpoints' in name for name in names)
    with zipfile.ZipFile(io.BytesIO(everything)) as archive:
        assert f'cuju-team/checkpoints/{first}/main.py' in archive.namelist()
        assert 'cuju-team/checkpoints.json' in archive.namelist()
    again = _teams.add('Again', 'again.zip', everything)
    listing = _checkpoints.listing(again['id'])
    assert [i['id'] for i in listing['checkpoints']][1] == first
    assert _answer(f'{again["id"]}@{first}') == [5] and _answer(again['id']) == [3]
    # A copy takes the current checkpoint alone.
    copy = _teams.duplicate(team['id'])
    assert len(_checkpoints.listing(copy['id'])['checkpoints']) == 1


def test_a_crafted_list_never_breaks_the_team(arena_home, tmp_path):
    team = _team(tmp_path)
    first = _checkpoints.current_id(team['id'])
    path = _home.folder('teams') / team['id'] / 'cuju-team' / 'checkpoints.json'
    for text in (
        '',
        '[]',
        '{"checkpoints": 3}',
        '{"current": "x", "checkpoints": [{}]}',
        '\x00',
        '{' * 5000,
    ):
        path.write_text(text, encoding='utf-8')
        listing = _checkpoints.listing(team['id'])
        assert [item['id'] for item in listing['checkpoints']] == [first]
    document = json.loads(path.read_text(encoding='utf-8'))
    assert document['identity'] == 1 and document['current'] == first


def test_the_listing_gives_the_limits_and_the_trust_line(arena_home, tmp_path):
    team = _team(tmp_path)
    listing = _checkpoints.listing(team['id'])
    assert listing['limits'] == {
        'checkpoints': 8,
        'checkpoint_mib': 500,
        'upload_bytes': 100 * 1024 * 1024,
        'idle_seconds': 60,
    }
    assert listing['trust']['text'] == (
        'A submission is Python that runs with your permissions. Add only teams you trust.'
    )
    with pytest.raises(cuju.errors.ArgumentError):
        _checkpoints.listing(_teams.BUILTIN)
    with pytest.raises(cuju.errors.ArgumentError):
        _checkpoints.listing('t-none')


def test_a_tournament_names_an_earlier_checkpoint_as_an_entrant(arena_home, tmp_path):
    team = _team(tmp_path)
    first = _checkpoints.current_id(team['id'])
    _checkpoints.add(team['id'], 'main.py', _SECOND.encode())
    registry = _teams.teams()
    entrants = _tournament.check_entrants(
        [{'team': team['id']}, {'team': f'{team["id"]}@{first}'}], registry
    )
    assert [e['team'] for e in entrants] == [team['id'], f'{team["id"]}@{first}']
    assert [e['key'] for e in entrants] == [team['id'], team['id']]
    assert entrants[1]['checkpoint'] == first and 'checkpoint' not in entrants[0]
    assert entrants[0]['name'] != entrants[1]['name']
    sides = [_ratings.entrant_side({**e, 'checkpoint': None}) for e in entrants]
    assert sides[1]['checkpoint'] == first and sides[0]['checkpoint'] != first
    for text in (f'{team["id"]}@{"0" * 16}', f'{team["id"]}@zz'):
        with pytest.raises(_tournament.TournamentError):
            _tournament.check_entrants([{'team': team['id']}, {'team': text}], registry)
    assert _tournament.entrant_key({'team': f'{team["id"]}@{first}'}) == team['id']


def test_an_evaluation_of_an_earlier_checkpoint_names_its_files(arena_home, tmp_path):
    from cuju.arena._evaluate import evaluate as _evaluate

    team = _team(tmp_path)
    first = _checkpoints.current_id(team['id'])
    _checkpoints.add(team['id'], 'main.py', _SECOND.encode())
    found = _evaluate._checkpoint({}, {'kind': 'team', 'team': f'{team["id"]}@{first}'})
    assert found['id'] == first and found['label'] == team['id']
    assert _evaluate._checkpoint({}, {'kind': 'team', 'team': team['id']})['id'] != first


def test_the_home_scenario_falls_back_when_the_team_cannot_play_it(arena_home, tmp_path, caplog):
    team = _team(tmp_path)
    assert team['scenario'] == '11_vs_11_kaggle'
    small = _team(tmp_path, 'small', _SINGLE, '5v5')
    assert small['scenario'] == '5_vs_5'
    chosen = _teams.update(team['id'], scenario='benchmark_full_game_11_vs_11_easy')
    assert chosen['scenario'] == 'benchmark_full_game_11_vs_11_easy' and chosen['set_aside'] == []
    path = _home.folder('teams') / team['id'] / 'cuju-team' / 'team.json'
    assert (
        json.loads(path.read_text(encoding='utf-8'))['scenario']
        == 'benchmark_full_game_11_vs_11_easy'
    )
    # The scenario of a full game of the other size and an unknown one are refused.
    for scenario in ('5_vs_5', 'no_such_scenario', 3):
        with pytest.raises(cuju.errors.ArgumentError):
            _teams.update(team['id'], scenario=scenario)
    # A control that drives the keeper cannot play the benchmark game, so the scenario falls back.
    control = _teams.check_control({'players': 11, 'goalkeeper': 'agent'}, '11v11')
    fell = _teams.update(team['id'], control=control)
    assert fell['scenario'] == '11_vs_11_kaggle' and fell['set_aside'] == ['scenario']
    document = json.loads(path.read_text(encoding='utf-8'))
    assert 'scenario' not in document
    assert document['kept']['refused']['scenario'] == 'benchmark_full_game_11_vs_11_easy'
    # A stored scenario that the team cannot play reads as the default of its size.
    document['scenario'] = '5_vs_5'
    document['kept'] = {}
    path.write_text(json.dumps(document), encoding='utf-8')
    read = _teams.entry(team['id'])
    assert read['scenario'] == '11_vs_11_kaggle' and 'scenario' in read['set_aside']
    assert path.read_text(encoding='utf-8') == json.dumps(document)
    # A value that is no scenario name is refused as a value of the file.
    document['scenario'] = ['5_vs_5']
    path.write_text(json.dumps(document), encoding='utf-8')
    assert _teams.entry(team['id'])['scenario'] == '11_vs_11_kaggle'


def test_the_scenarios_a_team_can_play_are_computed(arena_home, tmp_path):
    team = _team(tmp_path)
    found = _teams.playable_scenarios(team)
    assert '11_vs_11_kaggle' in found and '5_vs_5' not in found
    assert 'benchmark_full_game_11_vs_11_easy' in found
    narrow = _teams.update(
        team['id'], control=_teams.check_control({'players': 11, 'goalkeeper': 'agent'}, '11v11')
    )
    assert 'benchmark_full_game_11_vs_11_easy' not in _teams.playable_scenarios(narrow)
    assert _teams.size_default_scenario('5v5') == '5_vs_5'


def test_the_command_line_adds_and_lists_checkpoints(arena_home, tmp_path, capsys):
    team = _team(tmp_path)
    folder = tmp_path / 'trained'
    folder.mkdir()
    (folder / 'main.py').write_text(_SECOND, encoding='utf-8')
    (folder / 'weights.bin').write_bytes(b'1234')
    assert (
        main(['checkpoint', 'add', team['id'], str(folder), '--label', 'trained', '--step', '9'])
        == 0
    )
    out = capsys.readouterr().out
    assert 'step 9' in out and 'trained' in out and '2 files, ' in out
    assert main(['checkpoint', 'list', team['id'], '--json']) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing['checkpoints'][0]['label'] == 'trained' and len(listing['checkpoints']) == 2
    assert main(['checkpoint', 'list', team['id']]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[0] for line in lines] == [item['id'] for item in listing['checkpoints']]
    assert 'current, step 9' in lines[0]
    assert 'kept' in lines[1] and 'current' not in lines[1]
    assert (
        main(
            [
                'team',
                'export',
                team['id'],
                '--all-checkpoints',
                '--output',
                str(tmp_path / 'all.zip'),
            ]
        )
        == 0
    )
    with zipfile.ZipFile(tmp_path / 'all.zip') as archive:
        assert any('checkpoints/' in name for name in archive.namelist())
    assert main(['checkpoint', 'add', team['id']]) == 2
    assert main(['checkpoint', 'add', team['id'], str(tmp_path / 'none')]) == 2
