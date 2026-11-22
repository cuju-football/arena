# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the command `cuju arena`."""

import json
import sys
import zipfile
from pathlib import Path

import pytest

from cuju.arena._store import library as _library


@pytest.mark.parametrize('as_json', [False, True])
def test_arena_reports_an_incomplete_engine(tmp_path, as_json):
    import shutil
    import subprocess

    import cuju.arena
    from cuju import engine_status

    engine = tmp_path / 'cuju'
    shutil.copytree(
        Path(cuju.arena.__file__).parent,
        engine / 'arena',
        ignore=shutil.ignore_patterns('__pycache__'),
    )
    source = Path(engine_status.__file__).parent
    for name in ('engine_status.py', '_cli_contract.py'):
        shutil.copyfile(source / name, engine / name)
    info = tmp_path / 'cuju_engine-0.3.0.dist-info'
    info.mkdir()
    (info / 'METADATA').write_text('Name: cuju-engine\nVersion: 0.3.0\n', encoding='utf-8')

    command = [sys.executable, '-E', '-S', '-m', 'cuju.arena', 'config', 'show']
    if as_json:
        command.append('--json')
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    message = 'the cuju engine installation is damaged, run cuju-repair'
    hint = 'pip install --force-reinstall --no-deps cuju-engine==0.3.0'
    assert result.returncode == 3, result.stderr
    assert result.stderr.splitlines() == [
        f'cuju arena: error: {message}',
        f'cuju arena: hint: {hint}',
    ]
    if as_json:
        assert json.loads(result.stdout) == {
            'schema': 'error/1',
            'error': message,
            'error_id': None,
            'error_values': None,
            'hint': hint,
            'exit': 3,
        }
    else:
        assert not result.stdout


def test_sample_list_names_samples_that_are_not_added(arena_home, capsys):
    from cuju.arena.__main__ import main

    assert main(['sample', 'list']) == 0
    out, err = capsys.readouterr()
    assert 'not added' in out and 'missing' not in out
    assert 'cuju arena sample add NAME' in err
    assert main(['sample', 'list', '--json']) == 0
    out, err = capsys.readouterr()
    assert {item['state'] for item in json.loads(out)['samples']} == {'missing'}
    assert not err


def test_play_help_names_the_sizes_for_academy_scenarios(capsys):
    from cuju.arena.__main__ import main

    assert main(['play', '--help']) == 0
    assert 'Academy scenarios accept either team size when the requested players exist.' in (
        ' '.join(capsys.readouterr().out.split())
    )


def test_recording_show_reads_the_library_summary(arena_home, capsys):
    from cuju.arena.__main__ import main

    _library.add_samples(['sample-5v5'])
    summary = _library.details('sample-5v5')
    arguments = ['recording', 'show', 'sample-5v5']
    assert main([*arguments, '--json']) == 0
    out, err = capsys.readouterr()
    assert not err
    assert json.loads(out) == {
        'schema': 'arena.recording.show/1',
        **{
            key: summary.get(key) for key in ('id', 'title', 'scenario', 'teams', 'score', 'length')
        },
        'date': summary['created'],
        'provenance': summary['provenance'],
    }
    assert main(arguments) == 0
    out, err = capsys.readouterr()
    assert not err
    for key in ('id', 'title', 'scenario', 'teams', 'score', 'length', 'date', 'provenance'):
        assert f'{key.capitalize()} ' in out
    assert main(['recording', 'show', 'missing', '--json']) == 1
    out, err = capsys.readouterr()
    assert json.loads(out)['hint'] == 'run cuju arena recording list'
    assert 'cuju arena recording show: error:' in err


def test_play_reads_explicit_and_embedded_difficulty(arena_home, monkeypatch, capsys):
    """Explicit options and builtin values both give a difficulty."""
    from cuju.arena.__main__ import main
    from cuju.arena._playing import play as _play

    asked = []

    def fake(left, right, scenario, seed, title, device, difficulty, **options):
        asked.append(difficulty)
        path = _library.RecordingPath('match.cjr')
        path.summary = {'score': [2, 1], 'teams': ['Left', 'Right']}
        return path

    monkeypatch.setattr(_play, 'play', fake)
    assert main(['play', '--left-difficulty', '0.3', '--right', 'builtin:0.6']) == 0
    assert main(['play', '--left', 'builtin:0.3', '--right-difficulty', '0.6']) == 0
    assert asked == [(0.3, 0.6), (0.3, 0.6)]
    out, err = capsys.readouterr()
    assert out.splitlines() == ['match.cjr'] * 2
    assert err.splitlines()[-1] == 'played match  Left 2-1 Right'


def test_play_reports_incompatible_scenario_as_usage(arena_home, tmp_path, capsys):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import teams

    agent = tmp_path / 'main.py'
    agent.write_text('def agent(obs): return [0]')
    teams.add_folder(tmp_path, 'Harbour', '11v11')
    arguments = ['play', '--left', 'Harbour', '--right', 'builtin', '--seed', '7', '--json']
    assert main([*arguments, '--scenario', '5_vs_5']) == 2
    out, err = capsys.readouterr()
    answer = json.loads(out)
    assert answer['exit'] == 2
    assert answer['error_id'] == 'server.teams.scenario_format'
    assert err.startswith('usage: cuju arena play')
    assert 'expected a scenario of 11v11 for Harbour, found 5_vs_5' in err
    assert main([*arguments, '--left', 'unknown', '--scenario', '5_vs_5']) == 1
    out, err = capsys.readouterr()
    assert json.loads(out)['error_id'] == 'server.teams.unknown'
    assert not err.startswith('usage:')


def test_benchmark_command_lists_its_tasks_and_refuses_what_does_not_fit(arena_home, capsys):
    from cuju.arena import __main__ as command
    from cuju.arena._evaluate import academy as _academy

    assert command.main(['benchmark', 'tasks']) == 0
    listed = capsys.readouterr().out
    row = next(line for line in listed.splitlines() if line.startswith('empty_goal_close'))
    assert row.split() == ['empty_goal_close', 'academy_empty_goal_close', '5', 'attempts']
    assert len(listed.splitlines()) == len(_academy.TASKS) == 11
    assert command.main(['benchmark', 'tasks', '--json']) == 0
    tasks = json.loads(capsys.readouterr().out)['tasks']
    assert [task['id'] for task in tasks] == list(_academy.TASKS)
    assert all(len(task['seeds']) == task['attempts'] == 5 for task in tasks)
    assert (
        command.main(
            [
                'benchmark',
                'run',
                'builtin:0.3',
                'random',
                '--task',
                'empty_goal_close',
                '--workers',
                '1',
                '--json',
                '--quiet',
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    found = json.loads(output.out)
    assert found['teams'] == ['builtin:0.3', 'random']
    assert found['playable'] == 2
    assert [cell['state'] for cell in found['cells']] == ['done', 'done']
    assert found['models'][0]['name'] == 'Built-in AI 0.30'
    assert not output.err
    assert command.main(['benchmark', 'run', 'builtin', '--task', 'nothing']) == 2
    assert 'expected a task' in capsys.readouterr().err
    assert command.main(['benchmark', 'run']) == 2
    assert 'expected a list of distinct teams' in capsys.readouterr().err
    assert command.main(['benchmark', 'run', 'builtin', '--workers', '9']) == 2
    assert 'runs at once' in capsys.readouterr().err


@pytest.mark.parametrize('configured', [False, True])
def test_benchmark_reports_zero_playable_cells(arena_home, tmp_path, capsys, configured):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import teams

    (tmp_path / 'main.py').write_text('def agent(obs): return [0]')
    team = teams.add_folder(
        tmp_path,
        'Greens',
        '11v11',
        control={'players': 3, 'assignment': 'fixed', 'selection': ['CB', 'CM', 'CF']},
    )
    selection = ['--set', f'evaluate.policy=team:{team["id"]}'] if configured else [team['id']]
    assert main(['benchmark', 'run', *selection, '--task', 'corner', '--json']) == 0
    out, err = capsys.readouterr()
    answer = json.loads(out)
    assert answer['playable'] == 0
    benchmark = answer['benchmark'] if configured else answer
    assert benchmark['playable'] == 0
    assert [cell['state'] for cell in benchmark['cells']] == ['skipped']
    assert err.splitlines() == ['No task fits this team and nothing was played.']


def test_benchmark_reports_zero_playable_when_format_refuses(arena_home, monkeypatch, capsys):
    from cuju.arena.__main__ import main
    from cuju.arena._evaluate import academy
    from cuju.arena._text.messages import MessageError, id_fields

    def refused(command, report, started):
        error = MessageError('server.benchmark.refused', team='Built-in AI', reason='test refusal')
        report({'error': str(error), **id_fields(error)})
        return 1, ''

    monkeypatch.setattr(academy, 'play_child', refused)
    assert main(['benchmark', 'run', 'builtin', '--task', 'empty_goal_close', '--json']) == 0
    out, err = capsys.readouterr()
    answer = json.loads(out)
    assert answer['playable'] == 0 and answer['cells'][0]['state'] == 'failed'
    assert err.splitlines() == ['No task fits this team and nothing was played.']


def test_failing_commands_under_json_answer_with_json_and_a_human_error(arena_home, capsys):
    from cuju.arena.__main__ import main

    for arguments, status in (
        (['play', '--left', 'no-such-team', '--scenario', '5_vs_5', '--json'], 1),
        (['benchmark', 'run', 'builtin', '--task', 'no_such_task', '--json'], 2),
        (['search', 'nope:1', '--json'], 2),
        (['tournament', 'new', '--tournament-preset', 'no_such_preset', '--json'], 2),
    ):
        assert main(arguments) == status, arguments
        out, err = capsys.readouterr()
        answer = json.loads(out)
        assert set(answer) == {'schema', 'error', 'error_id', 'error_values', 'hint', 'exit'}
        assert answer['schema'] == 'error/1' and answer['error'] and ': error: ' in err
    # A tournament error keeps the message of the tournament's own error.
    main(['tournament', 'new', '--tournament-preset', 'no_such_preset', '--json'])
    assert json.loads(capsys.readouterr().out)['error_id'] == 'server.tournament.preset'
    # Arguments that do not fit end with status 2 and the error as JSON as well.
    for arguments, words, status in (
        (['tournament', 'show', 'no-such-cup', '--json'], 'no-such-cup', 1),
        (['tournament', 'forfeit', '--json'], 'required', 2),
    ):
        assert main(arguments) == status
        out, err = capsys.readouterr()
        answer = json.loads(out)
        assert words in answer['error'] and answer['error_id'] is None and ': error: ' in err
    # Without --json the error stays a line of standard error.
    assert main(['search', 'nope:1']) == 2
    out, err = capsys.readouterr()
    assert out == '' and err.startswith('usage: cuju arena search')
    assert 'cuju arena search: error: expected a filter' in err


def test_command_line_reports_errors(arena_home, capsys):
    from cuju.arena.__main__ import main

    assert main(['team', 'export', 'no-such-team']) == 1
    assert 'expected a team of the registry' in capsys.readouterr().err
    assert main(['team', 'remove']) == 2


@pytest.mark.parametrize(
    'arguments',
    [
        ['team', 'export'],
        ['team', 'remove'],
        ['team', 'unknown'],
        ['team', 'add', '.', '--size', 'bad'],
        ['team', 'add'],
        ['play', '--seed', 'bad'],
        ['play', '--left-ability', 'bad'],
        ['play', '--right-ability', 'bad'],
        ['play', '--left-difficulty', 'bad'],
        ['play', '--right-difficulty', 'bad'],
        ['tournament', 'check', '--evidence', 'bad'],
    ],
)
def test_cli_argument_refusals(arguments):
    """Invalid command arguments produce an error exit."""
    from cuju.arena.__main__ import main

    assert main(arguments) == 2


def test_team_sound_is_refused_and_pack_is_gone(arena_home, capsys):
    """A team has no sounds and the renderer has no pack command, so the parser refuses both."""
    from cuju.arena.__main__ import main

    for command in (['team', 'sound', 'builtin'], ['pack', 'check', '.']):
        assert main(command) == 2
    assert "cuju arena team: error: expected a command, found 'sound'" in capsys.readouterr().err


@pytest.mark.parametrize(
    ('arguments', 'suggestion'),
    [
        (['teem'], 'cuju arena team --help'),
        (['play', '--scenaro', '5_vs_5'], 'cuju arena play --help for --scenario'),
        (['plugin', 'show', 'missing'], 'cuju arena plugin list'),
        (['tournament', 'show', 'missing'], 'cuju arena tournament list'),
    ],
)
def test_errors_name_a_command_to_try(arguments, suggestion, capsys):
    from cuju.arena.__main__ import main

    assert main([*arguments, '--json']) in (1, 2)
    out, err = capsys.readouterr()
    assert json.loads(out)['hint'] == f'run {suggestion}'
    assert [line.split(': hint: ')[1] for line in err.splitlines() if ': hint: ' in line] == [
        f'run {suggestion}'
    ]


@pytest.mark.parametrize(
    ('arguments', 'key', 'next_command'),
    [
        (['recording', 'list', '--group', 'missing'], 'recordings', 'sample add'),
        (['evaluation', 'list'], 'evaluations', 'evaluation run TEAM'),
        (['rating', 'list'], 'runs', 'rating run TEAM --scenario SCENARIO'),
        (['tournament', 'list'], 'tournaments', 'tournament new --help'),
    ],
)
def test_empty_listings_keep_guidance_off_standard_output(arguments, key, next_command, capsys):
    from cuju.arena.__main__ import main

    assert main(arguments) == 0
    out, err = capsys.readouterr()
    assert out == '' and f'cuju arena {next_command}' in err
    assert main([*arguments, '--json', '--quiet']) == 0
    out, err = capsys.readouterr()
    assert json.loads(out)[key] == [] and err == ''


def test_listings_separate_full_columns_with_two_spaces(capsys, monkeypatch):
    from cuju.arena._cli import contract

    title = 'Long ' + '界' * 90
    rows = [('short', '5v5', title), ('longer', '11v11', 'Other')]
    for terminal in (False, True):
        monkeypatch.setattr(sys.stdout, 'isatty', lambda terminal=terminal: terminal)
        contract.listing(rows)
        assert capsys.readouterr().out.splitlines() == [
            f'short  5v5  {title}',
            'longer  11v11  Other',
        ]


def test_quiet_home_adoption_moves_the_data_without_a_notice(capsys, monkeypatch, tmp_path):
    from cuju.arena.__main__ import main

    old = tmp_path / 'earlier'
    (old / 'teams').mkdir(parents=True)
    monkeypatch.setenv('CUJU_RENDER_HOME', str(old))
    monkeypatch.delenv('CUJU_ARENA_HOME')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    assert main(['team', 'list', '--quiet']) == 0
    assert (tmp_path / '.cuju' / 'arena' / 'teams').is_dir()
    assert capsys.readouterr().err == ''


def test_tournament_preflight_distinguishes_environment_from_a_refusal(capsys, monkeypatch):
    from cuju.arena.__main__ import main
    from cuju.arena._tournaments import tournament_runner

    monkeypatch.setattr(
        tournament_runner,
        'preflight',
        lambda *args: {'ok': False, 'environment': True, 'rule': {'reason': 'needs torch'}},
    )
    assert main(['tournament', 'preflight', 'greens', '--json']) == 3
    out, err = capsys.readouterr()
    report = json.loads(out)
    assert report['schema'] == 'error/1' and report['exit'] == 3 and report['environment']
    assert 'cuju arena tournament preflight: error: needs torch' in err


def test_search_command_prints_the_moment_title_and_a_page_fragment(arena_home, capsys):
    from cuju.arena.__main__ import main

    _library.add_samples()
    assert main(['search', 'goal', '--json']) == 0
    results = json.loads(capsys.readouterr().out)['results']
    assert main(['search', 'goal']) == 0
    printed, notes = capsys.readouterr()
    for result in results:
        assert f'{result["match"]}  {result["title"]}' in printed
        assert f't={result["t"]:g}  {result["text"]}' in printed
        assert f'{result["url"]}' in printed
    assert any(result['t'] != result['seek'] for result in results)
    assert 'cuju render open' in notes


def test_search_help_shows_shell_quoting_for_negation_and_multiword_values(arena_home, capsys):
    from cuju.arena.__main__ import main

    assert main(['search', '--help']) == 0
    out = capsys.readouterr().out
    for example in (
        "cuju arena search 'card -yellow'",
        'cuju arena search --json -- pass -team:left',
        """cuju arena search 'team:"Harbour Rovers"'""",
    ):
        assert example in out

    _library.add_samples()
    assert main(['search', '--json', '--', 'pass', '-team:left']) == 0
    separated = json.loads(capsys.readouterr().out)
    assert main(['search', '--json', 'pass -team:left']) == 0
    quoted = json.loads(capsys.readouterr().out)
    for field in ('query', 'total', 'results'):
        assert separated[field] == quoted[field]
    assert quoted['total'] > 0
    assert all(row['team'] != 0 for row in quoted['results'])


def test_captions_stdout_matches_the_guide(arena_home, capsys, monkeypatch, tmp_path):
    from cuju.arena.__main__ import main

    _library.add_samples(['sample-5v5'])
    monkeypatch.chdir(tmp_path)
    guide = Path(__file__).resolve().parents[2] / 'docs' / 'moments-and-search.md'
    command = 'captions sample-5v5 -o sample-5v5.vtt'
    expected = guide.read_text(encoding='utf-8').split(f'$ cuju arena {command}\n')[1]
    assert main(command.split()) == 0
    assert capsys.readouterr() == (expected.splitlines()[0] + '\n', '')
    assert (tmp_path / 'sample-5v5.vtt').read_text(encoding='utf-8').startswith('WEBVTT')


def test_search_counts_matching_recordings_separately_from_the_library(arena_home, capsys):
    from cuju.arena.__main__ import main

    _library.add_samples()
    arguments = ['search', 'pass match:sample-5v5', '--limit', '1']
    assert main([*arguments, '--json']) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer['recordings'] == 2
    assert answer['matched_recordings'] == 1
    assert len(answer['results']) == 1 and answer['total'] > 1
    assert main(arguments) == 0
    assert capsys.readouterr().out.splitlines()[-1] == (
        f'{answer["total"]} moments in 1 of 2 recordings'
    )


def test_search_accepts_kept_references(arena_home, capsys):
    import shutil

    from cuju.arena.__main__ import main

    cups = []
    for _ in range(2):
        assert main(['tournament', 'new', '--team', 'builtin', '--team', 'builtin', '--json']) == 0
        cups.append(json.loads(capsys.readouterr().out)['id'])

    (sample,) = _library.add_samples(['sample-5v5'])
    summary = json.loads(sample.with_suffix('.json').read_text())
    summary['created'] = '2000-01-01T00:00:00'
    sample.with_suffix('.json').write_text(json.dumps(summary))
    ids = ('m-20261119-012345-6789', 'm-20261120-012345-abcd')
    for index, (identifier, cup) in enumerate(zip(ids, cups, strict=True)):
        path = sample.with_name(f'{identifier}.cjr')
        shutil.copyfile(sample, path)
        document = {
            **summary,
            'id': identifier,
            'created': f'2026-11-{19 + index}T01:23:45',
            'title': f'Cup game {index}',
            'provenance': {'tournament': {'id': cup, 'name': 'Spring Cup', 'stage': 'Final'}},
        }
        path.with_suffix('.json').write_text(json.dumps(document))

    def matches(query):
        assert main(['search', query, '--json']) == 0
        answer = json.loads(capsys.readouterr().out)
        assert answer['query'] == query
        return {row['match'] for row in answer['results']}

    for key, identifier in (('match', ids[1]), ('tournament', cups[1])):
        for reference in (identifier, identifier[:-1], identifier[:-1].partition('-')[2], 'last'):
            assert matches(f'pass {key}:{reference}') == {ids[1]}
    assert matches('pass -match:last match:"Cup game"') == {ids[0]}
    assert matches('pass tournament:Spring') == set(ids)

    for key, candidates, prefix in (('match', ids, 'm-'), ('tournament', cups, 'cup-')):
        assert main(['search', f'{key}:{prefix}2026', '--json']) == 2
        out, err = capsys.readouterr()
        assert all(identifier in json.loads(out)['hint'] for identifier in candidates)
        assert err.startswith('usage: cuju arena search')


def test_search_accepts_references_to_the_team_credited_with_a_moment(
    arena_home, monkeypatch, capsys
):
    from cuju.arena.__main__ import main
    from cuju.arena._format import recording
    from cuju.arena._registry import teams

    left, right = 't-20261119-012345-aaaa', 't-20261120-012345-bbbb'
    entries = [{'id': left, 'name': 'Harbour Renamed'}, {'id': right, 'name': 'Green'}]
    monkeypatch.setattr(teams, 'teams', lambda: entries)
    (path,) = _library.add_samples(['sample-5v5'])
    header = recording.read_header(path)
    header['teams'][0].update(id=left, name='Old Harbour')
    header['teams'][1].update(id=right, name='Green')
    frames = list(recording.frames(path))
    recording.write(path, recording._encode_header(header), frames, header['version'])

    def moments(query):
        assert main(['search', '--json', '--', query]) == 0
        answer = json.loads(capsys.readouterr().out)
        assert answer['query'] == query
        return answer['results']

    credited = moments('team:left')
    assert credited
    for reference in (left, left[:-1], left[2:-1], '"haRBOUr Renamed"', 'Old'):
        assert moments(f'team:{reference}') == credited
    other = moments(f'team:{right}')
    assert other and all(row['team'] == 1 for row in other)
    assert not set(row['id'] for row in moments(f'-team:{left}')) & {row['id'] for row in credited}
    for prefix in ('t-2026', '2026'):
        assert main(['search', f'team:{prefix}', '--json']) == 2
        out, err = capsys.readouterr()
        assert json.loads(out)['hint'] == f'use one of {left}, {right}'
        assert err.startswith('usage: cuju arena search')


@pytest.mark.parametrize('query', ['date:2026-02-30', 'date:2025-02-29', 'date:2026-13'])
def test_search_refuses_an_impossible_day(arena_home, capsys, query):
    from cuju.arena.__main__ import main

    assert main(['search', query, '--json']) == 2
    out, err = capsys.readouterr()
    assert json.loads(out)['error_id'] == 'server.search.date'
    assert err.startswith('usage: cuju arena search')


@pytest.mark.parametrize(
    ('key', 'value', 'error_id', 'options'),
    [
        ('player', '²', 'player', ()),
        ('player', '9' * 4301, 'player', ()),
        ('score', '9' * 4301 + '-0', 'score', ()),
        ('final', '0-' + '9' * 4301, 'final_score', ()),
        ('limit', '²', 'limit_text', ('--limit', '5')),
    ],
    ids=['copied-digit', 'long-player', 'long-score', 'long-final', 'overridden-limit'],
)
def test_search_refuses_invalid_numbers(arena_home, capsys, key, value, error_id, options):
    from cuju.arena.__main__ import main

    assert main(['search', f'{key}:{value}', *options, '--json']) == 2
    out, err = capsys.readouterr()
    answer = json.loads(out)
    assert answer['exit'] == 2 and answer['error_id'] == f'server.search.{error_id}'
    assert repr(value) in answer['error']
    assert err.startswith('usage: cuju arena search')
    assert f'cuju arena search: error: {answer["error"]}' in err


@pytest.mark.parametrize(
    'query',
    ['after:nan', 'before:inf', 'after:+NaNm', 'before:-INFINITYs', 'after:60:NaN', 'before:1e308'],
)
def test_search_refuses_nonfinite_clocks(arena_home, capsys, query):
    from cuju.arena.__main__ import main

    assert main(['search', 'pass', query, '--limit', '5', '--json']) == 2
    out, err = capsys.readouterr()
    answer = json.loads(out)
    assert answer['exit'] == 2 and answer['error_id'] == 'server.search.minutes'
    assert 'finite' in answer['error']
    assert err.startswith('usage: cuju arena search')
    assert f'cuju arena search: error: {answer["error"]}' in err


def test_tournament_new_prints_both_ways_to_run_it(arena_home, capsys):
    from cuju.arena.__main__ import main

    assert main(['tournament', 'new', '--team', 'builtin:0.3', '--team', 'builtin:0.7']) == 0
    out, err = capsys.readouterr()
    identifier = out.split()[0]
    assert f'cuju arena tournament run {identifier}' in err
    assert 'A render server serving the same arena home plays new tournaments by itself.' in err


@pytest.mark.parametrize('source', ['drawn', 'flag', 'file', 'quiet'])
def test_tournament_new_reports_a_drawn_seed(arena_home, capsys, tmp_path, source):
    from cuju.arena.__main__ import main

    arguments = ['tournament', 'new', '--json', '--team', 'builtin:0.3', '--team', 'builtin:0.6']
    if source == 'flag':
        arguments.extend(['--seed', '0'])
    elif source == 'file':
        path = tmp_path / 'cup.toml'
        path.write_text('preset = "quick_cup"\nseed = 0\n', encoding='utf-8')
        arguments.insert(2, str(path))
    elif source == 'quiet':
        arguments.append('--quiet')
    assert main(arguments) == 0
    out, err = capsys.readouterr()
    seed = json.loads(out)['seed']
    if source == 'drawn':
        assert f'drew seed {seed}' in err.splitlines()
    else:
        assert 'drew seed' not in err
        if source != 'quiet':
            assert seed == 0


def test_finished_tournament_cancel_points_to_remove(arena_home, capsys):
    from cuju.arena.__main__ import main

    assert (
        main(
            [
                'tournament',
                'new',
                '--team',
                'builtin:0.3',
                '--team',
                'builtin:0.7',
                '--scenario',
                'academy_empty_goal_close',
                '--length',
                '50',
                '--seed',
                '7',
                '--json',
            ]
        )
        == 0
    )
    identifier = json.loads(capsys.readouterr().out)['id']
    assert main(['tournament', 'run', identifier, '--quiet']) == 0
    capsys.readouterr()
    assert main(['tournament', 'cancel', identifier, '--json']) == 1
    out, err = capsys.readouterr()
    answer = json.loads(out)
    assert 'found it done' in answer['error']
    assert answer['hint'] == f'run cuju arena tournament remove {identifier}'
    assert answer['hint'] in err


def test_team_references_resolve_names_prefixes_and_checkpoint_selectors(
    arena_home, tmp_path, monkeypatch, capsys
):
    from cuju.arena.__main__ import main
    from cuju.arena._playing import play
    from cuju.arena._registry import checkpoints, teams

    def fake(left, right, scenario, seed, *args, **options):
        path = _library.RecordingPath('match.cjr')
        path.summary = {
            'score': [2, 1],
            'teams': ['Harbour', 'Built-in AI'],
            'provenance': {
                'sides': [{'checkpoint': checkpoints.snapshot(s)} for s in (left, right)]
            },
        }
        seen.append((left, right, scenario, seed))
        return path

    seen = []
    monkeypatch.setattr(play, 'play', fake)
    folder = tmp_path / 'harbour'
    folder.mkdir()
    (folder / 'main.py').write_text('def agent(obs):\n    return [0]\n')
    identifier = teams.add_folder(folder, 'Harbour')['id']
    found = checkpoints.add(identifier, 'main.py', b'def agent(obs): return [1]', 'baseline', 3000)
    digest = found['current']
    assert main(['team', 'show', 'hArBOUr', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['id'] == identifier
    assert main(['team', 'show', identifier[:-1], '--json']) == 0
    assert json.loads(capsys.readouterr().out)['id'] == identifier
    settings = ['--set', 'play.scenario=academy_empty_goal_close', '--set', 'play.seed=7']
    settings += ['--set', 'play.right=builtin:0.3']
    for reference in (digest, digest[:4], 'baseline', 'step:3000', 'latest'):
        assert main(['play', '--left', f'Harbour@{reference}', *settings, '--json']) == 0
        out, err = capsys.readouterr()
        answer = json.loads(out)
        assert answer['left_checkpoint'] == {'id': digest, 'label': 'baseline', 'step': 3000}
        assert answer['right_checkpoint'] is None
        assert err.splitlines()[-1] == 'played match  Harbour 2-1 Built-in AI'
    assert seen == [(f'{identifier}@{digest}', 'builtin', 'academy_empty_goal_close', 7)] * 5
    original = next(row for row in found['checkpoints'] if row['id'] != digest)
    checkpoints.change(identifier, original['id'], current=True)
    assert main(['play', '--left', 'Harbour@latest', *settings, '--json']) == 0
    assert json.loads(capsys.readouterr().out)['left_checkpoint']['id'] == digest
    assert (
        main(
            [
                'play',
                '--left',
                'random',
                '--pool',
                'Harbour@baseline',
                '--scenario',
                'academy_empty_goal_close',
                '--seed',
                '7',
                '--json',
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)['left'] == f'{identifier}@{digest}'
    assert (
        main(
            [
                'play',
                '--left',
                'Harbour',
                '--right',
                'random-actions',
                '--seed',
                '9',
                '--scenario',
                '5_vs_5',
                *settings,
            ]
        )
        == 0
    )
    assert seen[-1] == (identifier, 'random-actions', '5_vs_5', 9)
    assert capsys.readouterr().out == 'match.cjr\n'
    assert main(['checkpoint', 'add', 'Harbour', str(folder), '--step', '-1', '--json']) == 2
    out, err = capsys.readouterr()
    assert json.loads(out)['error_id'] == 'server.checkpoints.step'
    assert err.startswith('usage: cuju arena checkpoint add')


def _legacy_team_name(identifier, name):
    from cuju.arena._registry import teams

    path = teams._meta_path(identifier)
    manifest = json.loads(path.read_text())
    manifest['name'] = name
    path.write_text(json.dumps(manifest))


@pytest.mark.parametrize(
    'name', ['BuIlTiN', 'RANDOM', 'Random-Actions', 'BUILTIN:0.05', 'Builtin:Scenario']
)
@pytest.mark.parametrize('verb', ['add', 'set'])
def test_team_names_refuse_builtin_identifiers(arena_home, tmp_path, capsys, name, verb):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import teams

    (tmp_path / 'main.py').write_text('def agent(obs): return [0]')
    target = str(tmp_path) if verb == 'add' else teams.add_folder(tmp_path, 'Greens')['id']
    before = teams.teams()
    assert main(['team', verb, target, '--name', name, '--json']) == 2
    out, err = capsys.readouterr()
    answer = json.loads(out)
    assert answer['exit'] == 2 and repr(name) in answer['error']
    assert answer['hint'] and 'hint:' in err
    assert teams.teams() == before


@pytest.mark.parametrize('source', ['explicit', 'folder', 'file', 'archive', 'each', 'rename'])
def test_team_names_refuse_checkpoint_separator(arena_home, tmp_path, capsys, source):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import teams

    name = 'East@West'
    agent = 'def agent(obs): return [0]'
    (tmp_path / 'main.py').write_text(agent)
    command = 'set' if source == 'rename' else 'add'
    if source == 'explicit':
        arguments = [str(tmp_path), '--name', name]
    elif source in ('folder', 'each'):
        submission = tmp_path / name
        submission.mkdir()
        (submission / 'main.py').write_text(agent)
        arguments = (
            [str(submission)]
            if source == 'folder'
            else [str(tmp_path), '--each', '--format', 'kaggle']
        )
    elif source == 'file':
        submission = tmp_path / f'{name}.py'
        submission.write_text(agent)
        arguments = [str(submission)]
    elif source == 'archive':
        archive = tmp_path / 'export.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('main.py', agent)
            bundle.writestr('cuju-team/team.json', json.dumps({'name': name, 'format': '11v11'}))
        arguments = [str(archive)]
    else:
        team = teams.add_folder(tmp_path, 'East West')
        arguments = [team['id'], '--name', name]

    before = teams.teams()
    assert main(['team', command, *arguments, '--json']) == 2
    out, err = capsys.readouterr()
    answer = json.loads(out)
    assert answer['exit'] == 2 and repr(name) in answer['error']
    assert answer['hint'] == '@ separates a team from its checkpoint'
    assert err.startswith(f'usage: cuju arena team {command}')
    assert err.splitlines()[-1] == f'cuju arena team {command}: hint: {answer["hint"]}'
    assert teams.teams() == before
    if source == 'archive':
        assert main(['team', 'add', *arguments, '--name', 'East West', '--json']) == 0
        assert json.loads(capsys.readouterr().out)['name'] == 'East West'


def test_checkpoint_list_expects_the_team_reference(arena_home, tmp_path, capsys):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import checkpoints, teams

    (tmp_path / 'main.py').write_text('def agent(obs): return [0]')
    identifier = teams.add_folder(tmp_path, 'Harbour')['id']
    found = checkpoints.add(identifier, 'main.py', b'def agent(obs): return [1]', 'baseline', 3000)
    assert main(['checkpoint', 'list', 'hArBoUr', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['team'] == identifier
    for selector in ('latest', 'baseline', 'step:3000', found['current']):
        reference = f'Harbour@{selector}'
        assert main(['checkpoint', 'list', reference, '--json']) == 2
        out, err = capsys.readouterr()
        assert json.loads(out)['exit'] == 2
        assert err.startswith('usage: cuju arena checkpoint list')
        assert 'expected a team reference without a checkpoint selector' in err
        assert 'hint: use a team identifier, prefix or name by itself' in err
        assert main(['team', 'show', reference, '--json']) == 0
        capsys.readouterr()
    _legacy_team_name(identifier, 'Harbour@latest')
    assert main(['checkpoint', 'list', 'Harbour@latest', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['team'] == identifier
    assert main(['checkpoint', 'list', '--help']) == 0
    assert 'Use TEAM by itself' in capsys.readouterr().out


def test_checkpoint_label_collisions_offer_exact_references(arena_home, tmp_path, capsys):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import teams

    (tmp_path / 'main.py').write_text('def agent(obs): return [0]')
    team = teams.add_folder(tmp_path, 'LabelProbe')['id']
    points = []
    for step, label in ((1000, 'latest'), (2000, 'step:3000'), (3000, 'step:4000')):
        path = tmp_path / f'{step}.py'
        path.write_text(f'def agent(obs): return [{step // 1000}]')
        args = ['LabelProbe', str(path), '--step', str(step), '--label', label, '--json']
        assert main(['checkpoint', 'add', *args]) == 0
        points.append(json.loads(capsys.readouterr().out)['current'])

    for selector, labelled in zip(('latest', 'step:3000'), points[:2], strict=True):
        reference = f'LabelProbe@{selector}'
        assert main(['team', 'show', reference, '--json']) == 2
        out, err = capsys.readouterr()
        answer = json.loads(out)
        assert answer['exit'] == 2 and 'found 2' in answer['error']
        assert err.startswith('usage: cuju arena team show')
        assert set(answer['hint'].removeprefix('use one of ').split(', ')) == {
            f'{team}@{labelled}',
            f'{team}@{points[2]}',
        }
    for selector, expected in zip(
        (*points, 'step:1000', 'step:2000', 'step:4000'), points * 2, strict=True
    ):
        assert main(['team', 'show', f'LabelProbe@{selector}', '--json']) == 0
        assert json.loads(capsys.readouterr().out)['entrant'] == f'{team}@{expected}'


def test_checkpoint_labels_and_prefixes_share_one_match_set(arena_home, tmp_path, capsys):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import checkpoints, teams

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'main.py').write_text('def agent(obs): return [0]')
    team = teams.add_folder(source, 'LabelProbe')['id']
    first = checkpoints.current_id(team)
    label = first[:4]
    second = tmp_path / 'second.py'
    second.write_text('def agent(obs): return [1]')
    assert main(['checkpoint', 'add', team, str(second), '--label', label, '--json']) == 0
    latest = json.loads(capsys.readouterr().out)['current']
    assert not latest.startswith(label)

    reference = f'LabelProbe@{label}'
    assert main(['team', 'show', reference, '--json']) == 2
    out, err = capsys.readouterr()
    answer = json.loads(out)
    assert answer['exit'] == 2 and repr(reference) in answer['error']
    assert err.startswith('usage: cuju arena team show')
    assert set(answer['hint'].removeprefix('use one of ').split(', ')) == {
        f'{team}@{first}',
        f'{team}@{latest}',
    }
    for selector, expected in ((first, first), (first[:5], first), (latest, latest)):
        assert main(['team', 'show', f'LabelProbe@{selector}', '--json']) == 0
        assert json.loads(capsys.readouterr().out)['entrant'] == f'{team}@{expected}'


def test_team_name_and_checkpoint_selector_share_one_match_set(arena_home, tmp_path, capsys):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import checkpoints, teams

    (tmp_path / 'main.py').write_text('def agent(obs): return [0]')
    team = teams.add_folder(tmp_path, 'Harbour')['id']
    found = checkpoints.add(team, 'main.py', b'def agent(obs): return [1]', 'snapshot', 3000)
    point = found['current']
    literal = teams.add_folder(tmp_path, 'Other')['id']
    _legacy_team_name(literal, 'Harbour@snapshot')
    reference = 'hArBoUr@snapshot'
    assert main(['team', 'show', reference, '--json']) == 2
    out, err = capsys.readouterr()
    answer = json.loads(out)
    assert answer['exit'] == 2 and repr(reference) in answer['error']
    assert err.startswith('usage: cuju arena team show')
    assert set(answer['hint'].removeprefix('use one of ').split(', ')) == {
        literal,
        f'{team}@{point}',
    }
    assert f'cuju arena team show: hint: {answer["hint"]}' in err
    assert main(['team', 'show', f'{team}@{point}', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['entrant'] == f'{team}@{point}'
    assert main(['team', 'show', literal, '--json']) == 0
    assert json.loads(capsys.readouterr().out)['id'] == literal
    assert main(['checkpoint', 'list', reference, '--json']) == 0
    assert json.loads(capsys.readouterr().out)['team'] == literal
    for name in ('Harbour@unknown', 'builtin@latest'):
        _legacy_team_name(literal, name)
        assert main(['team', 'show', name, '--json']) == 0
        assert json.loads(capsys.readouterr().out)['id'] == literal
    _legacy_team_name(literal, f'{team}@{point}')
    assert main(['team', 'show', f'{team}@{point}', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['entrant'] == f'{team}@{point}'


def test_checkpoint_step_reference_handles_long_decimal_input(arena_home, tmp_path, capsys):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import checkpoints, teams

    (tmp_path / 'main.py').write_text('def agent(obs): return [0]')
    team = teams.add_folder(tmp_path, 'Harbour')['id']
    found = checkpoints.add(team, 'main.py', b'def agent(obs): return [1]', 'snapshot', 3000)
    reference = 'Harbour@step:' + '9' * 4301
    assert main(['team', 'show', reference, '--json']) == 1
    out, err = capsys.readouterr()
    answer = json.loads(out)
    assert answer['exit'] == 1 and answer['error_id'] == 'server.checkpoints.unknown'
    assert (
        err.splitlines()[-1] == f'cuju arena team show: hint: run cuju arena checkpoint list {team}'
    )
    padded = 'Harbour@step:' + '0' * 4301 + '3000'
    assert main(['team', 'show', padded, '--json']) == 0
    assert json.loads(capsys.readouterr().out)['entrant'] == f'{team}@{found["current"]}'


@pytest.mark.parametrize(
    ('command', 'arguments'),
    [
        (('team', 'show'), ()),
        (('play',), ('--left',)),
        (('tournament', 'preflight'), ()),
    ],
)
def test_empty_checkpoint_selector_is_a_usage_error(
    arena_home, tmp_path, capsys, command, arguments
):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import teams

    (tmp_path / 'main.py').write_text('def agent(obs): return [0]')
    team = teams.add_folder(tmp_path, 'LabelProbe')['id']
    for reference in ('LabelProbe@', 'Unknown@'):
        assert main([*command, *arguments, reference, '--json']) == 2
        out, err = capsys.readouterr()
        answer = json.loads(out)
        assert answer['exit'] == 2 and repr(reference) in answer['error']
        assert err.startswith(f'usage: cuju arena {" ".join(command)}')
        assert 'TEAM@CHECKPOINT' in answer['hint'] and answer['hint'] in err

    assert main(['team', 'show', 'LabelProbe@no-such-label', '--json']) == 1
    out, err = capsys.readouterr()
    assert json.loads(out)['exit'] == 1
    assert err.startswith('cuju arena team show: error:')
    _legacy_team_name(team, 'LabelProbe@')
    assert main(['team', 'show', 'LabelProbe@', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['id'] == team


def test_ambiguous_references_list_at_most_five_candidates(arena_home, monkeypatch, capsys):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import teams

    entries = [{'id': f't-20261120-121010-{index:04}', 'name': 'Harbour'} for index in range(6)]
    monkeypatch.setattr(teams, 'teams', lambda: entries)
    for reference in ('harbour', 't-2026', '2026'):
        assert main(['team', 'show', reference, '--json']) == 2
        out, err = capsys.readouterr()
        hint = json.loads(out)['hint']
        assert hint == 'use one of ' + ', '.join(entry['id'] for entry in entries[:5])
        assert err.startswith('usage: cuju arena team show')


def test_recording_date_last_and_final_score(arena_home, monkeypatch, capsys):
    from datetime import date, timedelta

    from cuju.arena.__main__ import main
    from cuju.arena._moments import search

    _library.add_samples(['sample-5v5'])
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    path = arena_home / 'matches' / 'sample-5v5.json'
    summary = json.loads(path.read_text())
    summary.update(created=f'{yesterday}T12:00:00', score=[2, 1])
    path.write_text(json.dumps(summary))
    assert main(['recording', 'list', '--date', 'today']) == 0
    assert capsys.readouterr().out == ''
    assert main(['recording', 'list', '--date', 'yesterday']) == 0
    identifier, day, teams, score, title = capsys.readouterr().out.strip().split('  ')
    assert (identifier, day, score) == ('sample-5v5', yesterday, '2-1')
    assert teams and title
    assert main(['recording', 'list', '--date', today, '--json']) == 0
    assert json.loads(capsys.readouterr().out)['recordings'] == []
    assert main(['recording', 'show', 'last', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['id'] == 'sample-5v5'
    assert main(['recording', 'list', '--date', '2026-02-30']) == 2
    assert capsys.readouterr().err.startswith('usage: cuju arena recording list')
    assert search.search('date:yesterday final:2-1')['total'] > 0
    assert search.search('date:today final:2-1')['total'] == 0
    assert search.search('date:yesterday final:2-1 score:0-0')['total'] > 0


@pytest.mark.parametrize(
    'series',
    ['night', '42', 'true', '[1, 2]', '  Camp "North" \\ \U0001f989\x7f  ', 'two\nlines\r\u2028'],
)
def test_series_and_recent_evaluation(arena_home, monkeypatch, capsys, series):
    from cuju.arena.__main__ import main
    from cuju.arena._evaluate import evaluate
    from cuju.arena._store import runs

    identifier = 'e-20261120-012345-6789'

    def fake(policy, *, progress, overrides):
        name = evaluate._layers.table('evaluate', overrides=overrides)['run']
        assert name == series
        runs.create('evaluation', identifier, name='AI', run=name, state='done')
        progress({'id': identifier, 'played': 2, 'total': 2})
        return evaluate.get(identifier)

    monkeypatch.setattr(evaluate, 'evaluate', fake)
    assert (
        main(
            [
                'evaluation',
                'run',
                'builtin',
                '--series',
                series,
                '--set',
                'evaluate.run=day',
                '--json',
            ]
        )
        == 0
    )
    out, err = capsys.readouterr()
    assert json.loads(out)['id'] == identifier and identifier in err
    for reference in ('last', identifier[:-1]):
        assert main(['evaluation', 'show', reference, '--json']) == 0
        assert json.loads(capsys.readouterr().out)['id'] == identifier
    assert main(['evaluation', 'list', '--series', series, '--json']) == 0
    listed = json.loads(capsys.readouterr().out)['evaluations']
    assert [(entry['id'], entry['run']) for entry in listed] == [(identifier, series)]
    assert main(['evaluation', 'list', '--series', 'typo', '--json']) == 0
    out, err = capsys.readouterr()
    assert json.loads(out)['evaluations'] == []
    assert err.splitlines() == [f'Known series are {series!r}.']


def test_evaluation_episode_option_overrides_settings(arena_home, tmp_path, monkeypatch, capsys):
    from cuju.arena.__main__ import main
    from cuju.arena._evaluate import evaluate
    from cuju.arena._store import runs

    settings = tmp_path / 'evaluation.toml'
    settings.write_text('[evaluate]\nepisodes = 3\n')
    identifier = 'e-20261120-012345-6789'

    def fake(policy, *, progress, overrides):
        count = evaluate._layers.table('evaluate', overrides=overrides)['episodes']
        assert count == 5
        runs.create('evaluation', identifier, name='AI', state='done')
        return evaluate.get(identifier)

    monkeypatch.setattr(evaluate, 'evaluate', fake)
    assert (
        main(
            [
                'evaluation',
                'run',
                'builtin',
                '--config',
                str(settings),
                '--set',
                'evaluate.episodes=4',
                '--episodes',
                '5',
                '--json',
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)['id'] == identifier


def test_static_team_completion_includes_documented_difficulties():
    from cuju.arena._cli._words import TEAMS

    assert {
        'builtin:0.05',
        'builtin:0.1',
        'builtin:0.3',
        'builtin:0.5',
        'builtin:0.6',
        'builtin:0.60',
        'builtin:0.7',
        'builtin:0.8',
        'builtin:1',
    } <= set(TEAMS)


@pytest.mark.parametrize(
    ('command', 'position'),
    [
        (('recording', 'show'), 'target'),
        (('recording', 'export'), 'target'),
        (('captions',), 'recording'),
        *[(('evaluation', verb), 'target') for verb in ('show', 'check', 'remove')],
        *[
            (('tournament', verb), 'target')
            for verb in (
                'show',
                'run',
                'resume',
                'cancel',
                'remove',
                'forfeit',
                'replay',
                'export',
                'legs',
            )
        ],
    ],
)
def test_kept_references_offer_last_in_shell_completion(command, position):
    import argparse

    from cuju.arena.__main__ import parser

    tree = parser()
    for name in command:
        group = next(
            action for action in tree._actions if isinstance(action, argparse._SubParsersAction)
        )
        tree = group.choices[name]
    subject = next(action for action in tree._actions if action.dest == position)
    assert subject.completion == ('last',)
    assert subject.choices is None


@pytest.mark.parametrize('verb', ['new', 'check'])
def test_tournament_completes_difficulty_keywords(verb):
    import argparse

    from cuju.arena.__main__ import parser

    tree = parser()
    for name in ('tournament', verb):
        group = next(
            action for action in tree._actions if isinstance(action, argparse._SubParsersAction)
        )
        tree = group.choices[name]
    action = next(action for action in tree._actions if '--difficulty' in action.option_strings)
    assert set(action.completion) == {'scenario', 'standard'}
    for value in ('scenario', 'standard', '0.05'):
        assert tree.parse_args(['--difficulty', value]).entrants == [('difficulty', value)]


def test_tournament_show_draw_and_cancel_outcome(arena_home, monkeypatch, capsys):
    from cuju.arena.__main__ import main
    from cuju.arena._tournaments import tournament_runner

    setup = ['--difficulty', '0.8', '--team', 'builtin', '--team', 'builtin:0.3']
    assert main(['tournament', 'check', *setup]) == 0
    assert 'standard ability 0.80' in capsys.readouterr().out.splitlines()[0]
    assert main(['tournament', 'new', *setup, '--json']) == 0
    identifier = json.loads(capsys.readouterr().out)['id']
    assert main(['tournament', 'show', 'last']) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f'{identifier}  drawn  played 0 of 1'
    assert any('standard ability 0.80' in line for line in lines[1:])

    def cancel(self, target, report):
        assert target == identifier
        self.cancel(target)
        return 'cancelled'

    monkeypatch.setattr(tournament_runner.Tournaments, 'run', cancel)
    assert main(['tournament', 'run', identifier[:-1], '--json']) == 0
    out, err = capsys.readouterr()
    assert json.loads(out)['outcome'] == 'cancelled' and err == 'cancelled\n'

    def interrupt(self, target, report):
        raise KeyboardInterrupt

    monkeypatch.setattr(tournament_runner.Tournaments, 'run', interrupt)
    assert main(['tournament', 'run', 'last', '--json']) == 130
    assert json.loads(capsys.readouterr().out)['exit'] == 130


def test_help_lists_score_filters_and_default_settings(capsys):
    from cuju.arena.__main__ import main

    assert main(['search', '--help']) == 0
    help_text = capsys.readouterr().out
    assert 'Quote' in help_text
    assert all(f"'{term}'" in help_text for term in ('ann:value<0.5', 'entropy:>1.5', 'reward:>0'))
    assert all(word in help_text for word in ('date:today', 'score:0-0', 'final:2-1', 'ann:'))
    assert main(['play', '--help']) == 0
    help_text = capsys.readouterr().out
    assert all(word in help_text for word in ('TEAM@CHECKPOINT', 'play.seed', 'play.right'))
    assert main(['play', '--scenario', 'academy_empty_gaol_close', '--json']) == 2
    out, err = capsys.readouterr()
    assert json.loads(out)['hint'] == 'use --scenario academy_empty_goal_close'
    assert err.startswith('usage: cuju arena play')


def test_read_only_commands_defer_numerical_imports(arena_home):
    import subprocess

    probe = """
import sys
from cuju.arena.__main__ import main
commands = [
    ['--help'], ['team', 'list'], ['recording', 'list'], ['evaluation', 'list'],
    ['tournament', 'list'], ['rating', 'list'], ['sample', 'list'], ['format', 'list'],
    ['plugin', 'list'], ['plugin', 'show', 'builtin'], ['benchmark', 'tasks'],
]
for command in commands:
    assert main([*command, '--quiet'] if command != ['--help'] else command) == 0, command
    assert not {'torch', 'numpy', 'cuju._batch.environment'} & sys.modules.keys(), command
"""
    result = subprocess.run(
        [sys.executable, '-c', probe], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_team_add_size_overrides_imported_identity(arena_home, tmp_path, capsys):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import teams

    folder = tmp_path / 'starter'
    folder.mkdir()
    (folder / 'main.py').write_text('def agent(obs): return [0]')
    original = teams.add_folder(folder)
    registered = arena_home / 'teams' / original['id']
    assert (
        main(
            [
                'team',
                'add',
                str(registered),
                '--name',
                '5v5 copy',
                '--size',
                '5v5',
                '--format',
                'kaggle',
                '--json',
            ]
        )
        == 0
    )
    copied = json.loads(capsys.readouterr().out)
    assert copied['size'] == '5v5' and len(copied['roster']) == 5
    assert teams.entry(copied['id'])['format'] == '5v5'
    assert teams.entry(original['id'])['format'] == '11v11'


def test_kaggle_full_check_reports_load_coverage(tmp_path, capsys):
    from cuju.arena.__main__ import main

    (tmp_path / 'main.py').write_text('def agent(obs): return [99]')
    args = ['check', str(tmp_path), '--format', 'kaggle', '--full', '--max-steps', '50']
    assert main([*args, '--json']) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer['coverage'] == 'load_only' and answer['accepted']
    assert answer['full'] and answer['max_steps'] == 50
    assert main(args) == 0
    assert 'Coverage, agent load.' in capsys.readouterr().out


def test_root_and_leaf_help_explain_defaults_and_output(capsys):
    from cuju.arena.__main__ import main

    cases = [
        ([], ('CUJU_ARENA_HOME', '~/.cuju/arena', 'config show', 'play.seed=7')),
        (['team', 'set'], ('current count', 'zero-based indices')),
        (['team', 'add'], ('requires --each',)),
        (['play'], ('left_checkpoint', 'right_checkpoint', 'label', 'step')),
        (['rating', 'table'], ('checkpoint identifier', 'label', 'training step')),
        (['recording', 'show'], ('Provenance', 'checkpoint digest')),
        (['check'], ('Kaggle checks load the agent', 'verdict.json')),
        (['captions'], ('WebVTT', 'standard output by default')),
        (['benchmark', 'run'], ('five fixed seeds', 'benchmark.format=NAME')),
        (['evaluation', 'run'], ('100 episodes per opponent', 'evaluate.episodes=1')),
        (['rating', 'draw'], ('zero-based match number', 'rating show --json')),
    ]
    for command, terms in cases:
        assert main([*command, '--help']) == 0
        output = ' '.join(capsys.readouterr().out.split())
        assert all(term in output for term in terms), (command, output)


def test_tournament_interrupt_keeps_played_results_and_releases_the_lock(
    arena_home, monkeypatch, capsys
):
    from cuju.arena.__main__ import main
    from cuju.arena._tournaments import tournament_runner

    entrants = ['--team', 'builtin', '--team', 'builtin:0.4', '--team', 'builtin:0.8']
    assert (
        main(['tournament', 'new', '--tournament-preset', 'round_robin', *entrants, '--json']) == 0
    )
    identifier = json.loads(capsys.readouterr().out)['id']
    runner = tournament_runner.Tournaments

    def interrupt(self):
        record = self._records[identifier]
        match = self._view(record)['matches'][0]
        self._finish(record, match, {'score': [1, 0]})
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, 'tick', interrupt)
    assert main(['tournament', 'run', identifier]) == 130
    capsys.readouterr()
    assert main(['tournament', 'show', identifier, '--json']) == 0
    entry = json.loads(capsys.readouterr().out)
    assert entry['state'] == 'stopped'
    assert entry['played'] == 1
    assert entry['running'] == 0 and entry['owner'] is None
    assert not (arena_home / 'tournaments' / f'{identifier}.lock').exists()
