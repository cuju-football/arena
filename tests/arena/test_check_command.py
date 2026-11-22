# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The commands `check` and `team add-folder` of cuju render, and the route of the Add team tile."""

import json
import shutil
from pathlib import Path

import pytest

from cuju.arena.__main__ import main
from cuju.arena._formats import registry as _registry
from cuju.arena._registry import checkpoints as _checkpoints
from cuju.arena._registry import teams as _teams
from cuju.arena._store import runs as _runs

FOLDERS = Path(__file__).parents[1] / 'support' / 'folders'
SUBMISSIONS = FOLDERS / 'submissions'


@pytest.fixture
def namer(arena_home):
    """The Namer format, a person's format that names a team by its manifest."""
    return _registry.add(FOLDERS / 'format', 'namer')


@pytest.fixture
def submissions(tmp_path):
    """A copy of the folder of sample submissions, which a test may change."""
    target = tmp_path / 'submissions'
    shutil.copytree(SUBMISSIONS, target)
    return target


def _names():
    return sorted(team['name'] for team in _teams.teams() if team['kind'] == 'kaggle')


def test_a_folder_that_the_format_accepts_exits_with_0(arena_home, namer, capsys):
    assert main(['check', str(SUBMISSIONS / 'alpha'), '--format', 'namer']) == 0
    out = capsys.readouterr().out
    assert out.startswith('Accepted, alpha, namer, checkpoint ')
    assert 'The submission states the name Alpha FC, 1 player.' in out
    # Nothing is registered by a check, and no verdict is kept.
    assert _names() == []
    assert not (arena_home / 'verdicts').exists() or not list((arena_home / 'verdicts').iterdir())


def test_a_folder_that_the_format_refuses_exits_with_1_and_says_why(arena_home, namer, capsys):
    assert main(['check', str(SUBMISSIONS / 'broken'), '--format', 'namer']) == 1
    out = capsys.readouterr().out
    assert out.startswith('Refused, broken, namer')
    assert 'error load (manifest): manifest.json does not read' in out


def test_verbose_check_writes_the_submission_output_to_standard_error(namer, monkeypatch, capsys):
    from cuju.arena._cli import check

    monkeypatch.setattr(check, '_tail', lambda outcome: 'Traceback from submission')
    arguments = ['check', str(SUBMISSIONS / 'broken'), '--format', 'namer']
    assert main(arguments) == 1
    out, err = capsys.readouterr()
    assert out.startswith('Refused, broken, namer') and 'Traceback' not in out + err
    assert main([*arguments, '--verbose']) == 1
    verbose, err = capsys.readouterr()
    assert verbose == out and 'Traceback from submission\n' in err


def test_a_format_that_fails_exits_with_3(arena_home, namer, capsys):
    arguments = ['check', str(SUBMISSIONS / 'alpha'), '--format', 'namer', '--option', 'crash=true']
    assert main(arguments) == 3
    assert 'format namer failed' in capsys.readouterr().err
    # The registry remembers the format as failing, as the menu's check does.
    assert _registry.failing('namer')


def test_arguments_that_do_not_fit_exit_with_2(arena_home, namer, capsys):
    folder = str(SUBMISSIONS / 'alpha')
    for arguments in [
        ['check', folder, '--format', 'nothing'],
        ['check', str(SUBMISSIONS / 'nowhere'), '--format', 'namer'],
        ['check', folder, '--format', 'namer', '--option', 'crash'],
        ['check', folder, '--format', 'namer', '--option', 'nothing=1'],
        ['check', folder, '--format', 'namer', '--option', 'crash=maybe'],
        ['check', folder, '--format', 'namer', '--preset', 'nothing'],
        ['check', folder, '--format', 'namer', '--scenario', 'nowhere'],
        ['check', folder, '--format', 'namer', '--max-steps', '0'],
    ]:
        assert main(arguments) == 2, arguments
    assert 'cuju arena check: error: ' in capsys.readouterr().err
    # Parser refusals use the same status.
    for arguments in [
        ['check', folder],
        ['check', folder, '--format', 'namer', '--bogus'],
        ['check', folder, '--format', 'namer', '--max-steps', 'many'],
    ]:
        assert main(arguments) == 2


def test_check_of_a_registered_id_points_to_its_folder(arena_home, namer, capsys):
    team = _teams.add_folder(SUBMISSIONS / 'alpha')['id']
    assert main(['check', team, '--format', 'namer', '--json']) == 2
    out, err = capsys.readouterr()
    answer = json.loads(out)
    assert f'found the registered team {team!r}' in answer['error']
    assert answer['hint'] == f'run cuju arena team show {team} for its folder'
    assert answer['hint'] in err
    assert main(['team', 'show', team]) == 0
    folder = arena_home / 'teams' / team
    assert f'folder {folder}' in capsys.readouterr().out
    assert main(['check', str(folder), '--format', 'namer']) == 0


def test_a_missing_package_is_an_environment_problem_and_exits_with_3(
    arena_home, namer, monkeypatch, capsys
):
    from cuju.arena._formats import checks as _checks
    from cuju.arena._formats import verdicts as _verdicts
    from cuju.arena.formats import v1 as formats

    # The refusal that a missing package makes is the format's own answer, as `formats.require`
    # gives it. Here the format says it directly, in the process that the command runs in.
    monkeypatch.setattr(
        _checks,
        'check',
        lambda *a, **k: formats.refuse(
            'load', 'needs torch', detail='environment', environment=True
        ),
    )
    monkeypatch.setattr(_verdicts, '_isolated', _verdicts.run)
    assert main(['check', str(SUBMISSIONS / 'alpha'), '--format', 'namer']) == 3
    assert 'needs torch' in capsys.readouterr().err


def test_the_json_report_holds_the_verdict_and_the_exit_status(arena_home, namer, tmp_path):
    report = tmp_path / 'report.json'
    arguments = ['check', str(SUBMISSIONS / 'broken'), '--format', 'namer', '--output', str(report)]
    assert main(arguments) == 1
    found = json.loads(report.read_text(encoding='utf-8'))
    assert found['exit'] == 1 and found['accepted'] is False and found['format'] == 'namer'
    assert found['problems'][0]['kind'] == 'load' and found['problems'][0]['detail'] == 'manifest'
    assert found['folder'] == str((SUBMISSIONS / 'broken').resolve()) and found['size'] == '11v11'
    assert found['checkpoint'] == _runs.files_digest(SUBMISSIONS / 'broken', ('cuju-team',))[:16]
    assert found['full'] is False and found['max_steps'] is None and found['failed'] is None


def test_a_full_check_cut_by_max_steps_says_so_and_never_passes(
    arena_home, namer, tmp_path, capsys
):
    folder = tmp_path / 'probe-team'
    folder.mkdir()
    (folder / 'main.py').write_text('def agent(obs):\n    return [5]\n', encoding='utf-8')

    def arguments(steps):
        return [
            *('check', str(folder), '--format', 'namer', '--option', 'matches=12'),
            *('--scenario', '5_vs_5', '--full', '--max-steps', str(steps), '--json'),
        ]

    assert main(arguments(5)) == 1
    out = capsys.readouterr().out
    assert 'the check was cut at 5 steps' in out
    found = json.loads(out)
    assert found['cut'] is True and found['accepted'] is False and found['full'] is True
    assert found['size'] == '5v5' and found['scenario'] == '5_vs_5' and found['max_steps'] == 5
    assert [problem['kind'] for problem in found['problems'] if problem['level'] == 'error'] == [
        'cut'
    ]
    assert main(arguments(50)) == 0
    assert json.loads(capsys.readouterr().out)['cut'] is False


# Folders as teams.


def test_every_folder_becomes_a_team_named_by_its_manifest(arena_home, namer, capsys):
    assert main(['team', 'add', '--each', str(SUBMISSIONS), '--format', 'namer']) == 1
    out, err = capsys.readouterr()
    assert _names() == ['Alpha FC', 'Beta United']
    assert out.count('  added  ') == 2
    left = err.splitlines()
    assert len(left) == 3
    assert sum('duplicate team name' in line for line in left) == 2
    assert sum('manifest.json does not read' in line for line in left) == 1
    # A repeated name is never numbered, and the hidden folder and the loose file are no entrants.
    assert 'Twins' not in _names() and not any('.hidden' in line for line in left)


def test_the_report_of_a_folder_names_the_folders_left_out(arena_home, namer, capsys):
    assert main(['team', 'add', '--each', str(SUBMISSIONS), '--format', 'namer', '--json']) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['format'] == 'namer'
    assert [team['name'] for team in report['teams']] == ['Alpha FC', 'Beta United']
    assert {team['action'] for team in report['teams']} == {'added'}
    left = {Path(item['path']).name: item for item in report['excluded']}
    assert set(left) == {'broken', 'twin-one', 'twin-two'}
    assert left['twin-one']['reason'] == 'duplicate team name'
    assert left['twin-one']['reason_id'] == 'server.team_folders.duplicate'
    assert left['twin-one']['same'] == [str(SUBMISSIONS.resolve() / 'twin-two')]
    assert left['broken']['kind'] == 'load' and left['broken']['environment'] is False


def test_a_folder_without_a_repeat_or_a_fault_exits_with_0(arena_home, namer, submissions):
    for name in ('broken', 'twin-one', 'twin-two'):
        shutil.rmtree(submissions / name)
    assert main(['team', 'add', '--each', str(submissions), '--format', 'namer']) == 0
    assert _names() == ['Alpha FC', 'Beta United']


def test_a_format_that_fails_stops_the_folders_with_3(arena_home, namer, submissions, capsys):
    arguments = [
        'team',
        'add',
        '--each',
        str(submissions),
        '--format',
        'namer',
        '--option',
        'crash=true',
    ]
    assert main(arguments) == 3
    assert 'the format namer failed' in capsys.readouterr().err
    assert _names() == []


def test_the_command_of_a_folder_checks_its_arguments(arena_home, namer, tmp_path, capsys):
    assert main(['team', 'add', '--each', str(tmp_path / 'nowhere'), '--format', 'namer']) == 2
    assert main(['team', 'add', '--each', str(SUBMISSIONS)]) == 2
    assert main(['team', 'add', '--each', str(SUBMISSIONS), '--format', 'nothing']) == 2
    assert main(['team', 'add', '--each']) == 2
    assert main(['team', 'add', str(SUBMISSIONS / 'alpha'), '--full']) == 2
    assert 'expected a folder of submission folders' in capsys.readouterr().err
    assert _names() == []


def test_running_it_again_reuses_the_teams_and_a_changed_folder_is_a_new_checkpoint(
    arena_home, namer, submissions, capsys
):
    for name in ('broken', 'twin-one', 'twin-two'):
        shutil.rmtree(submissions / name)
    assert main(['team', 'add', '--each', str(submissions), '--format', 'namer', '--json']) == 0
    first = {item['name']: item for item in json.loads(capsys.readouterr().out)['teams']}
    assert main(['team', 'add', '--each', str(submissions), '--format', 'namer', '--json']) == 0
    again = {item['name']: item for item in json.loads(capsys.readouterr().out)['teams']}
    assert {item['team'] for item in again.values()} == {item['team'] for item in first.values()}
    assert {item['action'] for item in again.values()} == {'same'}
    assert len(_names()) == 2
    # Changed files make a new checkpoint of the same team.
    team = first['Alpha FC']['team']
    old = _checkpoints.current_id(team)
    script = submissions / 'alpha' / 'main.py'
    original = script.read_bytes()
    script.write_text('def agent(obs):\n    return [9]\n', encoding='utf-8')
    assert main(['team', 'add', '--each', str(submissions), '--format', 'namer', '--json']) == 0
    changed = {item['name']: item for item in json.loads(capsys.readouterr().out)['teams']}
    assert changed['Alpha FC']['team'] == team and changed['Alpha FC']['action'] == 'checkpoint'
    assert changed['Beta United']['action'] == 'same'
    assert _checkpoints.current_id(team) != old
    assert [item['id'] for item in _checkpoints.listing(team)['checkpoints']].count(old) == 1
    # Changing it back makes the earlier checkpoint play again, and no team is added.
    script.write_bytes(original)
    assert main(['team', 'add', '--each', str(submissions), '--format', 'namer', '--json']) == 0
    back = {item['name']: item for item in json.loads(capsys.readouterr().out)['teams']}
    assert back['Alpha FC']['action'] == 'checkpoint' and _checkpoints.current_id(team) == old
    assert len(_names()) == 2


def test_a_moved_folder_finds_its_team_by_its_digest(
    arena_home, namer, submissions, tmp_path, capsys
):
    for name in ('broken', 'twin-one', 'twin-two', 'beta'):
        shutil.rmtree(submissions / name)
    assert main(['team', 'add', '--each', str(submissions), '--format', 'namer', '--json']) == 0
    team = json.loads(capsys.readouterr().out)['teams'][0]['team']
    moved = tmp_path / 'moved'
    submissions.rename(moved)
    assert main(['team', 'add', '--each', str(moved), '--format', 'namer', '--json']) == 0
    found = json.loads(capsys.readouterr().out)['teams']
    assert [(item['team'], item['action']) for item in found] == [(team, 'same')]


def test_the_verdict_of_a_folder_is_kept_for_its_team(arena_home, namer, submissions, capsys):
    for name in ('broken', 'twin-one', 'twin-two'):
        shutil.rmtree(submissions / name)
    assert main(['team', 'add', '--each', str(submissions), '--format', 'namer', '--json']) == 0
    teams = json.loads(capsys.readouterr().out)['teams']
    from cuju.arena._formats import verdicts as _verdicts

    for item in teams:
        rows = _verdicts.listing(item['team'])['formats']
        found = [row for row in rows if row['id'] == 'namer']
        assert [(row['state'], row['kept'], row['stated_name']) for row in found] == [
            ('accepted', True, item['name'])
        ]


def test_a_size_and_a_control_reach_the_teams_of_a_folder(arena_home, namer, submissions, capsys):
    for name in ('broken', 'twin-one', 'twin-two', 'beta'):
        shutil.rmtree(submissions / name)
    arguments = ['team', 'add', '--each', str(submissions), '--format', 'namer', '--size', '5v5']
    assert main([*arguments, '--players', '2', '--json']) == 0
    made = json.loads(capsys.readouterr().out)['teams'][0]['team']
    team = _teams.entry(made)
    assert team['format'] == '5v5' and team['control']['players'] == 2
