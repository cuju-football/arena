# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The format that each context chooses, Kaggle open where it chooses none.

A tournament, a rating run, an evaluation, the academy benchmark, a requested match and a game each
read one choice through `_formats.context`, and every side plays under it. The tests hold the
choice, its per-entrant options and its checks, the default of Kaggle open, the forfeits a
format decides, the ratings filter by format, and a team whose entry the format judges.
"""

import json
import shutil
import types
from pathlib import Path

import pytest

from cuju.arena._evaluate import ratings as _ratings
from cuju.arena._evaluate import runner as _runner
from cuju.arena._format import recording as _recording
from cuju.arena._formats import context as _context
from cuju.arena._formats import cpusets as _cpusets
from cuju.arena._formats import registry as _registry
from cuju.arena._playing import play as _play
from cuju.arena._registry import teams as _teams
from cuju.arena._store import ratings as _store
from cuju.arena._tournaments import tournament_runner as _tournament
from cuju.errors import ArgumentError
from support.common import _toy_team

FOLDERS = Path(__file__).parents[1] / 'support' / 'folders'
_AGENT = 'def agent(obs):\n    return [5]\n'
_CRASH = 'def agent(obs):\n    raise RuntimeError("broke")\n'


@pytest.fixture
def agent_format(arena_home):
    """The Agent file format, a person's format whose entry is agent.py, with no main.py."""
    return _registry.add(FOLDERS / 'agent-format', 'agentfile')


def test_a_side_that_chooses_nothing_plays_kaggle_open(arena_home):
    found = _play.chosen_formats(None)
    assert [choice.format.label for choice in found] == ['kaggle/1', 'kaggle/1']
    assert all(choice.preset == 'open' for choice in found)
    # One side may choose and the other may not, which plays Kaggle open.
    pair = _play.chosen_formats([{'id': 'kaggle/1'}, None])
    assert (pair[0].preset, pair[1].preset) == (None, 'open')
    assert pair[0].values['timing'] is True and pair[1].values['timing'] is False
    # The old path of strict/1 rules keeps no default of its own.
    assert _play.chosen_formats(None, default=None) == [None, None]
    with pytest.raises(ArgumentError):
        _play.chosen_formats([None, None, None])


def test_the_choice_of_a_context_reads_and_checks(arena_home, agent_format):
    assert _context.check(None) is None
    assert _context.check('') is None
    # Kaggle open with nothing of its own plays as no choice.
    assert _context.check({'id': 'kaggle/1', 'preset': 'open'}) is None
    assert _context.is_open({'id': 'kaggle/1', 'preset': 'open'})
    assert not _context.is_open({'id': 'kaggle/1'})
    assert _context.check('strict/1') == {'id': 'strict/1'}
    chosen = {'id': 'agentfile', 'per_entrant': {'rex': {'level': 7}}}
    assert _context.check(chosen, ['rex', 'bolt']) == chosen
    assert _context.for_entrant(chosen, 'rex') == {'id': 'agentfile', 'per_entrant': {'level': 7}}
    assert _context.for_entrant(chosen, 'bolt') == {'id': 'agentfile'}
    # An entrant outside the context, an option of no entrant's own and a bad value are refused.
    with pytest.raises(ArgumentError, match='per-entrant options'):
        _context.check(chosen, ['bolt'])
    with pytest.raises(ArgumentError, match='do not fit'):
        _context.check({'id': 'agentfile', 'per_entrant': {'rex': {'level': 40}}})
    with pytest.raises(ArgumentError):
        _context.check({'id': 'strict/1', 'per_entrant': {'rex': {'timing': True}}})
    with pytest.raises(ArgumentError):
        _context.check({'id': 'kaggle/1', 'colour': 'red'})
    with pytest.raises(ArgumentError):
        _context.check({'id': 'no-such-format'})
    assert _context.label(None) == 'kaggle/1'


def test_settings_name_the_format_of_evaluations_and_the_benchmark(arena_home):
    from cuju.arena._config import layers as _layers

    for table in ('evaluate', 'benchmark'):
        found = _layers.table(table)
        assert (found['format'], found['format_preset'], found['format_options']) == (
            'open',
            None,
            {},
        )
        assert _context.from_settings(found) is None
    chosen = _layers.table(
        'benchmark',
        overrides=['benchmark.format=strict/1', 'benchmark.format_options={step_seconds = 0.2}'],
    )
    assert _context.from_settings(chosen) == {'id': 'strict/1', 'options': {'step_seconds': 0.2}}


def test_a_long_context_holds_its_format_to_its_length(arena_home):
    # Within the longest scenario every format fits, and past it Kaggle's own ranges still do.
    _context.check_length({'id': 'kaggle/1'}, 3000)
    _context.check_length({'id': 'kaggle/1'}, 4000)
    with pytest.raises(ArgumentError, match='steps than its own limit'):
        _context.check_length({'id': 'kaggle/1'}, 40000)


def test_a_format_of_its_own_process_bounds_the_matches_at_once(arena_home, agent_format):
    from cuju.arena._formats import cpusets as _cpusets

    assert _context.threads(None) is None
    # The built-in formats play in the match process and take no CPU set.
    assert _context.threads('strict/1') is None
    assert _context.threads('agentfile') == 1
    config = {'submission_format': {'id': 'agentfile'}}
    assert _tournament._at_once(config) == _cpusets.matches_at_once(1)
    assert _tournament._at_once({}) is None


def test_a_requested_match_keeps_the_format_it_played_under(arena_home, tmp_path):
    team = _toy_team(tmp_path, 'steady', _AGENT, '5v5')
    stats = {}
    path = _play.play(team['id'], 'builtin', '5_vs_5', seed=1, length=60, stats=stats)
    provenance = _recording.read_header(path)['provenance']
    # Kaggle open keeps the provenance of every match before formats.
    assert 'submission_format' not in provenance
    assert stats['formats'][0]['identity']['preset'] == 'open'
    # Kaggle itself, with its timing on, is a format of its own and the recording names it.
    path = _play.play(
        team['id'], 'builtin', '5_vs_5', seed=1, length=60, submission_format=['kaggle/1', None]
    )
    kept = _recording.read_header(path)['provenance']['submission_format']
    assert kept[0]['identity']['format'] == 'kaggle/1' and 'preset' not in kept[0]['identity']
    assert kept[0]['options']['timing'] is True


def test_a_crash_under_kaggle_open_is_the_forfeit_of_its_side(arena_home, tmp_path):
    crash = _toy_team(tmp_path, 'crash', _CRASH, '5v5')
    with pytest.raises(_play.ForfeitError) as caught:
        _play.play('builtin', crash['id'], '5_vs_5', seed=1, length=60)
    assert (caught.value.rule['side'], caught.value.rule['kind']) == ('away', 'crash')
    assert caught.value.rule['step'] == 0
    # A rating's or an evaluation's match keeps the forfeit beside the steps played.
    played = _runner.play_teams(crash['id'], 'builtin', '5_vs_5', 1)
    assert played.forfeit['kind'] == 'crash' and played.lengths == [0]


def test_a_match_of_teams_plays_under_its_format_and_reports_it(arena_home, tmp_path):
    team = _toy_team(tmp_path, 'steady', _AGENT, '5v5')
    played = _runner.play_teams(
        team['id'], 'builtin', '5_vs_5', 1, submission_format=[{'id': 'kaggle/1'}, None]
    )
    assert played.forfeit is None and played.lengths[0] > 0
    assert played.formats[0]['identity']['format'] == 'kaggle/1'
    assert played.formats[1] is None
    # Every answer of the team is its action 5, the built-in AI's side holds none of its own.
    assert set(played.actions[:, 0, 0].tolist()) == {5}


def test_a_rating_run_keeps_its_format_and_each_side_its_options(
    arena_home, agent_format, tmp_path
):
    rex = _toy_team(tmp_path, 'rex', _AGENT, '5v5')
    bolt = _toy_team(tmp_path, 'bolt', _AGENT, '5v5')
    chosen = {'id': 'agentfile', 'per_entrant': {f'team:{rex["id"]}': {'level': 3}}}
    measured, scenario, found = _ratings.request(
        {
            'models': [f'team:{rex["id"]}', bolt['id']],
            'scenario': '5_vs_5',
            'submission_format': chosen,
        }
    )
    document = _ratings.create(measured, scenario, found, submission_format=chosen)
    assert document['submission_format'] == chosen
    assert document['formats'][rex['id']] == {'id': 'agentfile', 'per_entrant': {'level': 3}}
    assert document['formats'][bolt['id']] == {'id': 'agentfile'}
    assert _ratings.view(document)['submission_format'] == chosen
    # A run that chooses nothing keeps no format, and its teams play Kaggle open.
    plain = _ratings.create(measured, scenario, found)
    assert plain['submission_format'] is None and plain['formats'] == {}
    with pytest.raises(ArgumentError):
        _ratings.request({'models': [rex['id']], 'scenario': '5_vs_5', 'strict': True})


def test_the_ratings_table_filters_and_groups_by_format(arena_home):
    identity = {
        'format': 'strict/1',
        'name': 'Strict',
        'version': '1',
        'digest': None,
        'contract': 1,
        'options': {},
        'per_entrant': {},
        'revision': None,
    }

    def team(key, checkpoint, identity=None):
        side = {'key': key, 'checkpoint': checkpoint, 'kind': 'team', 'name': key}
        if identity is not None:
            side.update(format='abcdef012345', submission_format=identity)
        return side

    anchor = _ratings.anchor_side(_ratings.settings())
    for number, (home, score) in enumerate(
        [(team('rex', 'a' * 16), [2, 0]), (team('bolt', 'b' * 16, identity), [1, 1])]
    ):
        _store.record(
            {
                'scenario': '5_vs_5',
                'home': home,
                'away': _ratings._stored(anchor),
                'score': score,
                'source': 'match',
                'ref': f'm{number}',
                'seed': str(number),
            }
        )
    every = _ratings.table('5_vs_5')
    assert {entry['format'] for entry in every['formats']} == {'kaggle/1', 'strict/1'}
    assert every['format'] is None
    shown = {row['key']: row['submission_format'] for row in every['models']}
    assert shown['rex'] == {**_ratings.KAGGLE_OPEN, 'digest': None}
    assert shown['bolt']['format'] == 'strict/1'
    assert shown[anchor['key']] is None
    strict = _ratings.table('5_vs_5', format='strict/1')
    # The filter keeps the teams of the format and the built-in AI, ranked among themselves.
    assert sorted(row['key'] for row in strict['models']) == sorted(['bolt', anchor['key']])
    assert [row['rank'] for row in strict['models']] == [1, 2]
    kaggle = _ratings.table('5_vs_5', format='kaggle/1')
    assert sorted(row['key'] for row in kaggle['models']) == sorted(['rex', anchor['key']])
    assert {entry['format'] for entry in _ratings.overview()['formats']} == {
        'kaggle/1',
        'strict/1',
    }


def test_a_kaggle_open_row_keeps_no_format(arena_home):
    played = {'identity': {'format': 'kaggle/1', 'preset': 'open'}, 'control': None}
    assert 'submission_format' not in _ratings.format_side(played)
    timed = {'identity': {'format': 'kaggle/1'}, 'control': None}
    assert _ratings.format_side(timed)['submission_format'] == {'format': 'kaggle/1'}


def test_the_benchmark_takes_a_format(arena_home, tmp_path):
    from cuju.arena._evaluate import academy as _academy
    from cuju.arena._evaluate import benchmark as _benchmark

    team = _toy_team(tmp_path, 'steady', _AGENT, '5v5')
    made = _benchmark.plan([team['id']], ['empty_goal_close'], submission_format='strict/1')
    assert made['submission_format'] == {'id': 'strict/1'}
    assert _benchmark.plan([team['id']], ['empty_goal_close'])['submission_format'] is None
    command = _academy.child_command('empty_goal_close', team['id'], False, 'cpu', {'id': 'x'})
    assert command[-2:] == ['--submission-format', '{"id": "x"}']
    with pytest.raises(ArgumentError):
        _benchmark.queue({'teams': [team['id']], 'submission_format': 'no-such-format'})


def test_the_benchmark_plays_its_attempts_under_the_chosen_format(
    arena_home, tmp_path, monkeypatch
):
    import time

    from cuju.arena._evaluate import academy as _academy
    from cuju.arena._formats import launch as _launch

    close = _launch.Process.close

    def delayed_close(process, *args, **kwargs):
        time.sleep(1.2)
        return close(process, *args, **kwargs)

    monkeypatch.setattr(_launch.Process, 'close', delayed_close)

    team = _toy_team(tmp_path, 'steady', _AGENT, '5v5')
    outcome = _academy.run(
        'empty_goal_close', team['id'], keep_recordings=False, submission_format='kaggle/1'
    )
    assert outcome['failed_attempts'] == 0 and len(outcome['attempts']) == 5
    # Each attempt's runner closed its policies, so Kaggle's timed child of every attempt ended
    # and gave its CPU set back.
    assert _cpusets.SETS.held() == {}


def test_a_policy_runner_closes_its_policies_on_their_own_thread():
    """Closing a runner ends its policies, after the call on their thread, and policies whose
    load ran past its time close once the load ends."""
    import threading
    import time

    closed = []

    def made():
        found = object.__new__(_play.Policies)
        found.close = lambda: closed.append(threading.current_thread().name)
        return found

    runner = _play.PolicyRunner()
    runner.load(made, 5)
    runner.close(5)
    assert closed == ['cuju-policies']

    def slow():
        time.sleep(0.5)
        return made()

    late = _play.PolicyRunner()
    with pytest.raises(_play.PolicyError):
        late.load(slow, 0.05)
    late.close(5)
    assert closed == ['cuju-policies', 'cuju-policies']


def test_a_tournament_plays_each_side_with_its_own_options(arena_home, agent_format, tmp_path):
    rex = _toy_team(tmp_path, 'rex', _AGENT, '5v5')
    bolt = _toy_team(tmp_path, 'bolt', _AGENT, '5v5')
    config = {
        'entrants': [{'team': rex['id']}, {'team': bolt['id']}],
        'stages': [{'format': 'league', 'scenario': '5_vs_5'}],
        'submission_format': {'id': 'agentfile', 'per_entrant': {rex['id']: {'level': 4}}},
    }
    checked = _tournament.check_config(config)
    assert checked['submission_format']['per_entrant'] == {rex['id']: {'level': 4}}
    match = {'home': 0, 'away': 1}
    sides = _tournament.side_formats(checked, match)
    assert sides[0]['per_entrant'] == {'level': 4} and 'per_entrant' not in sides[1]
    assert sides[0]['options'] == sides[1]['options']
    with pytest.raises(ArgumentError):
        _tournament.check_config(
            {**config, 'submission_format': {'id': 'agentfile', 'per_entrant': {'zed': {}}}}
        )
    name = _tournament.terminal_run('cup-1', 's0/r1/m2')
    assert name == 'cup-1-s0-r1-m2'


def test_a_team_under_a_format_needs_the_entry_of_that_format(arena_home, agent_format, tmp_path):
    folder = tmp_path / 'arena-like'
    folder.mkdir()
    (folder / 'agent.py').write_text(_AGENT, encoding='utf-8')
    # With no format, Kaggle open keeps the rule of a main.py at the top.
    with pytest.raises(ArgumentError, match=r'main\.py'):
        _teams.add_folder(folder, 'Arena', '5v5')
    made = _teams.add_folder(folder, 'Arena', '5v5', submission_format='agentfile')
    assert (arena_home / 'teams' / made['id'] / 'agent.py').is_file()
    # A file of its own keeps its name under a format, and is Kaggle's main.py without one.
    single = _teams.add('Single', 'agent.py', _AGENT.encode(), '5v5', submission_format='agentfile')
    assert (arena_home / 'teams' / single['id'] / 'agent.py').is_file()
    # A directory of such folders registers every folder its format accepts.
    directory = tmp_path / 'class'
    shutil.copytree(folder, directory / 'ana')
    (directory / 'bea').mkdir()
    (directory / 'bea' / 'main.py').write_text(_AGENT, encoding='utf-8')
    choice = _play.chosen_formats('agentfile')[0]
    found = _teams.add_folders(directory, choice, None, '5v5', None, False, isolate=False)
    assert [team['path'] for team in found['teams']] == [str(directory / 'ana')]
    assert found['excluded'][0]['reason'] == 'agent.py is missing'


def test_a_rating_run_plays_under_its_format_and_records_it(arena_home, tmp_path, monkeypatch):
    import numpy as np

    from cuju.arena._evaluate import evaluate as _evaluate

    rex = _toy_team(tmp_path, 'rex', _AGENT, '5v5')
    measured, scenario, found = _ratings.request(
        {'models': [rex['id']], 'scenario': '5_vs_5', 'submission_format': 'kaggle/1'}
    )
    document = _ratings.create(measured, scenario, found, submission_format='kaggle/1')
    engine = _ratings._Engine(document)
    pairing = {'home': rex['id'], 'away': document['anchor'], 'meeting': 0, 'number': 0}
    _, setup = engine._start(pairing)
    captured = {}

    def child(owner, spec):
        captured.update(spec)
        return _runner.Played([[1, 0]], [10], np.zeros((10, 1, 1), 'uint8'))

    monkeypatch.setattr(_evaluate._Play, '_child', staticmethod(child))
    engine._one(pairing, setup)
    # The spec of a team's match carries the format of each side and the name of its terminals.
    assert captured['submission_format'] == [{'id': 'kaggle/1'}, None]
    assert captured['kind'] == 'rating'
    assert captured['terminal'] == f'{document["id"]}-m0'


def test_a_forfeit_in_a_rating_run_is_no_evidence(arena_home, tmp_path):
    import numpy as np

    rex = _toy_team(tmp_path, 'rex', _AGENT, '5v5')
    measured, scenario, found = _ratings.request({'models': [rex['id']], 'scenario': '5_vs_5'})
    document = _ratings.create(measured, scenario, found)
    engine = _ratings._Engine(document)
    pairing = {'home': rex['id'], 'away': document['anchor'], 'meeting': 0, 'number': 0}
    index, _ = engine._start(pairing)
    engine.schedule.due()
    forfeit = {'side': 'home', 'kind': 'crash', 'reason': 'rex forfeits, broke', 'at': [0, 0]}
    played = _runner.Played([[0, 0]], [3], np.zeros((3, 1, 1), 'uint8'))
    played.forfeit = forfeit
    rows = len(_store.results('5_vs_5'))
    engine._count(pairing, index, played, None)
    episode = document['episodes'][-1]
    assert episode['forfeit'] == forfeit and episode['score'] is None
    assert episode['error'] == 'rex forfeits, broke'
    assert len(_store.results('5_vs_5')) == rows


def test_a_tournament_keeps_the_terminals_of_its_forfeits(arena_home):
    from cuju.arena._formats import terminals as _terminals

    name = _tournament.terminal_run('cup', 's0/m1')
    terminal = _terminals.Run(name).open('left', pipe=False)
    terminal.write_str('thinking hard\n')
    terminal.close()
    # A forfeit pins its terminal for nobody, and the tournament claims it after the match.
    assert _terminals.pin(name, 'left', _terminals.UNOWNED)
    _tournament.Tournaments._pin_terminals('cup', 's0/m1')
    assert 'tournament:cup' in _terminals.summary(name, 'left')['pinned']
    record = {'id': 'cup'}
    _tournament.Tournaments._seal(object(), record)
    assert 'thinking hard' in record['terminals'][f'{name}/left']
    assert 'tournament:cup' not in _terminals.summary(name, 'left')['pinned']


def test_two_checkpoints_of_one_team_enter_by_their_checkpoints(arena_home, tmp_path):
    from cuju.arena._registry import checkpoints as _checkpoints
    from cuju.arena._tournaments import tournament as _rules

    team = _toy_team(tmp_path, 'Ladder', _AGENT, '5v5')['id']
    first = _checkpoints.current_id(team)
    _checkpoints.add(team, 'main.py', b'def agent(obs):\n    return [3]\n', 'step 2M')
    second = _checkpoints.current_id(team)
    _checkpoints.add(team, 'main.py', b'def agent(obs):\n    return [4]\n')
    entrants = _rules.check_entrants(
        [{'team': f'{team}@{first}'}, {'team': f'{team}@{second}'}, {'team': team}],
        _teams.teams(),
    )
    # Never Ladder 2, the rules name each checkpoint by its label, else by its id.
    assert [entrant['name'] for entrant in entrants] == [
        f'Ladder {first[:8]}',
        'Ladder step 2M',
        'Ladder',
    ]


def test_team_add_takes_the_submission_format_of_the_folder(arena_home, agent_format, tmp_path):
    from cuju.arena.__main__ import main

    folder = tmp_path / 'arena-like'
    folder.mkdir()
    (folder / 'agent.py').write_text(_AGENT, encoding='utf-8')
    # Without a format, Kaggle open keeps the rule of a main.py at the top.
    assert main(['team', 'add', str(folder), '--size', '5v5']) != 0
    assert [team for team in _teams.teams() if team['name'] == 'arena-like'] == []
    code = main(['team', 'add', str(folder), '--size', '5v5', '--format', 'agentfile'])
    assert code in (0, None)
    added = [team for team in _teams.teams() if team['name'] == 'arena-like']
    assert len(added) == 1 and (arena_home / 'teams' / added[0]['id'] / 'agent.py').is_file()


def test_play_hands_its_submission_format_to_both_sides(arena_home, monkeypatch, capsys):
    from cuju.arena.__main__ import main

    seen = {}

    def play(*arguments, **options):
        seen.update(options)
        return types.SimpleNamespace(stem='made', summary={'score': [0, 0]})

    monkeypatch.setattr(_play, 'play', play)
    code = main(['play', '--format', 'strict/1', '--option', 'load_seconds=40', '--json'])
    assert code in (0, None)
    chosen = seen['submission_format']
    assert chosen.format.label == 'strict/1' and chosen.values['load_seconds'] == 40
    assert json.loads(capsys.readouterr().out)['match'] == 'made'
    seen.clear()
    main(['play', '--json'])
    assert seen['submission_format'] is None
