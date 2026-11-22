# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the academy tasks, the runs of models on them, and the academy benchmark, its table,
its command and its routes."""

import json
import struct
import zlib

import pytest

import cuju
import cuju.errors
from support.common import (
    _SINGLE,
    _attempt_frames,
    _control,
    _frames,
    _short_tasks,
    _toy_team,
)


def _played(attempts):
    """The results of attempts without the recordings they name, which differ from run to run."""
    return [{key: value for key, value in item.items() if key != 'match'} for item in attempts]


def test_every_academy_scenario_is_a_task_with_fixed_seeds():
    from cuju.arena._evaluate import academy as _academy

    tasks = _academy.tasks()
    academies = {name for name in cuju.bundled_scenarios() if name.startswith('academy_')}
    assert {task['scenario'] for task in tasks} == academies and len(tasks) == 11
    assert [task['id'] for task in tasks] == list(_academy.TASKS)
    for task in tasks:
        assert task['scenario'] == f'academy_{task["id"]}'
        assert task['attempts'] == _academy.ATTEMPTS == len(task['seeds']) == 5
        base = zlib.crc32(task['id'].encode())
        assert task['seeds'] == [(base + 7919 * i) % 2**31 for i in range(5)]
        assert task['seeds'] == _academy.seeds(task['id'], 5)
    with pytest.raises(cuju.errors.ArgumentError, match='expected a task') as caught:
        _academy.find('no_such_drill')
    assert caught.value.message_id == 'server.benchmark.unknown_task'


def test_scoring_reads_the_frames():
    from cuju.arena._evaluate import academy as _academy

    header = {'steps_per_second': 10}
    scored = _academy.score_attempt(header, _attempt_frames(goal_at=24))
    assert scored == {'finished': True, 'goals': [1, 0], 'scored': True, 'seconds': 2.4}
    missed = _academy.score_attempt(header, _attempt_frames())
    assert missed == {'finished': True, 'goals': [0, 0], 'scored': False, 'seconds': None}
    # An attempt that ends before its episode counts its goals against and no goals for.
    quit = _academy.score_attempt(header, _attempt_frames(10, finished=False, against_at=5))
    assert quit == {'finished': False, 'goals': [0, 1], 'scored': False, 'seconds': None}
    assert _academy.score_attempt(header, []) == {
        'finished': False,
        'goals': [0, 0],
        'scored': False,
        'seconds': None,
    }
    # The value counts the attempts that scored, and the rate is its share of the attempts.
    results = [scored, missed, quit, dict(scored, seconds=3.5), scored]
    assert _academy.score_run(results) == {'value': 3, 'rate': 0.6}
    assert _academy.score_run([scored, missed, missed]) == {'value': 1, 'rate': 0.3333}
    assert _academy.score_run([]) == {'value': 0, 'rate': 0.0}


def test_a_run_plays_a_registered_team_on_the_seeds_of_its_task(arena_home, tmp_path, monkeypatch):
    from cuju.arena._evaluate import academy as _academy

    team = _toy_team(tmp_path, 'toy', _SINGLE, '11v11')
    _short_tasks(monkeypatch, 6)
    seen = []
    outcome = _academy.run('empty_goal_close', team['id'], progress=seen.append)
    assert [result['index'] for result in seen] == [0, 1, 2, 3, 4]
    assert outcome['task'] == 'empty_goal_close' and outcome['team'] == team['id']
    assert outcome['name'] == 'toy' and outcome['playing'] is None
    assert outcome['failed_attempts'] == 0 and len(outcome['attempts']) == 5
    assert outcome['value'] == sum(result['scored'] for result in outcome['attempts'])
    assert outcome['rate'] == outcome['value'] / 5
    seeds = _academy.find('empty_goal_close')['seeds']
    for result in outcome['attempts']:
        document, frames = _frames(arena_home / 'matches' / f'{result["match"]}.cjr')
        provenance = document['provenance']
        assert provenance['source'] == 'benchmark' and provenance['task'] == 'empty_goal_close'
        assert provenance['attempt'] == result['index']
        assert provenance['seed'] == seeds[result['index']] == result['seed']
        assert provenance['teams'] == [team['id'], 'builtin']
        assert provenance['agent_counts'] == [1, 0] and provenance['ability'] == [1.0, 0.6]
        assert len(frames) == 7 and struct.unpack_from('<i', frames[-1], 28)[0] & 1
        assert document['teams'][0]['id'] == team['id']
        assert document['title_id'] == 'server.names.benchmark_attempt'
        assert document['title'] == (
            f'academy_empty_goal_close, attempt {result["index"] + 1}, toy'
        )
    for wrong in (('no_such_drill', team['id']), ('corner', 'no-such-team'), ('corner', 7)):
        with pytest.raises(cuju.errors.ArgumentError):
            _academy.run(*wrong)


def test_a_team_of_several_players_plays_a_task_with_the_others_out(
    arena_home, tmp_path, monkeypatch
):
    """A five a side team of four dynamic agents plays the empty goal, whose side holds two
    controllable players, with two players, in its own multi agent form."""
    from cuju.arena._evaluate import academy as _academy

    log = tmp_path / 'calls.jsonl'
    source = (
        'import json\n'
        'def agent(obs):\n'
        '    seen = [(raw["active"], len(raw["left_team"])) for raw in obs["players_raw"]]\n'
        f'    with open({str(log)!r}, "a", encoding="utf-8") as handle:\n'
        '        handle.write(json.dumps([obs["controlled_players"], seen]) + "\\n")\n'
        '    return [12, 0, 0, 0]\n'
    )
    four = _toy_team(tmp_path, 'four', source, '5v5', control=_control(4))
    _short_tasks(monkeypatch, 6)
    outcome = _academy.run('empty_goal_close', four['id'])
    assert outcome['playing'] == [2, 4] and outcome['failed_attempts'] == 0
    calls = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
    # Each call holds the four players of the team's form, and a player out sees what the
    # team's first player sees, over every step of the five attempts.
    assert len(calls) == 30
    assert all(count == 4 and len(seen) == 4 for count, seen in calls)
    assert all(seen[2] == seen[3] == seen[0] != seen[1] for _, seen in calls)
    document, _ = _frames(arena_home / 'matches' / f'{outcome["attempts"][0]["match"]}.cjr')
    assert document['provenance']['agent_counts'] == [2, 0]


def test_a_run_counts_a_failing_attempt_as_failed_and_goes_on(arena_home, tmp_path, monkeypatch):
    from cuju.arena._evaluate import academy as _academy
    from cuju.arena._store import library as _library

    _short_tasks(monkeypatch, 4)
    count = tmp_path / 'calls.txt'
    source = (
        'import pathlib\n\n'
        f'COUNT = pathlib.Path({str(count)!r})\n\n\n'
        'def agent(obs):\n'
        '    calls = int(COUNT.read_text()) + 1 if COUNT.exists() else 1\n'
        '    COUNT.write_text(str(calls))\n'
        '    if calls == 6:\n'
        "        raise RuntimeError('no idea')\n"
        '    return [12]\n'
    )
    flaky = _toy_team(tmp_path, 'flaky', source, '11v11')
    outcome = _academy.run('corner', flaky['id'])
    attempts = outcome['attempts']
    assert [attempt['failed'] for attempt in attempts] == [False, True, False, False, False]
    failed = attempts[1]
    assert 'no idea' in failed['error'] and failed['error_id'] == 'server.policy.failed'
    assert not failed['finished'] and failed['goals'][0] == 0 and not failed['scored']
    assert outcome['failed_attempts'] == 1
    # The failed attempt keeps the frames it played, and its recording says it failed.
    document, frames = _frames(arena_home / 'matches' / f'{failed["match"]}.cjr')
    assert document['provenance']['failed'] is True and len(frames) == 2
    assert not struct.unpack_from('<i', frames[-1], 28)[0] & 1
    # The recordings of the run form one group, the failed attempt among them.
    members = _library.matches(group=outcome['group'])
    assert [member['id'] for member in members] == [attempt['match'] for attempt in attempts]


def test_a_model_that_cannot_load_fails_every_attempt(arena_home, tmp_path, monkeypatch):
    from cuju.arena._evaluate import academy as _academy

    _short_tasks(monkeypatch, 4)
    broken = _toy_team(tmp_path, 'broken', 'raise ImportError("no weights")\n', '11v11')
    outcome = _academy.run('corner', broken['id'])
    assert [attempt['failed'] for attempt in outcome['attempts']] == [True] * 5
    assert all('no weights' in attempt['error'] for attempt in outcome['attempts'])
    assert all(attempt['match'] is None for attempt in outcome['attempts'])
    assert outcome['value'] == 0 and outcome['rate'] == 0.0 and outcome['failed_attempts'] == 5
    assert outcome['group'] is None


def test_a_run_is_reproducible(arena_home, tmp_path, monkeypatch):
    from cuju.arena._evaluate import academy as _academy

    _short_tasks(monkeypatch, 12)
    team = _toy_team(tmp_path, 'steady', _SINGLE, '11v11')
    first = _academy.run('run_to_score', team['id'], keep_recordings=False)
    second = _academy.run('run_to_score', team['id'])
    assert _played(first['attempts']) == _played(second['attempts'])
    assert (first['value'], first['rate']) == (second['value'], second['rate'])
    seeds = _academy.find('run_to_score')['seeds']
    assert [attempt['seed'] for attempt in second['attempts']] == seeds


def _in_process(document, cell, attempt, started=None):
    """Plays a cell in the test's own process, so a shortened episode reaches it."""
    from cuju.arena._evaluate import academy as _academy

    return _academy.run(
        cell['task'],
        cell['team'],
        progress=attempt,
        keep_recordings=document['keep_recordings'],
    )


_FOUR = """
def agent(obs):
    return [5 if obs['step'] % 7 else 12, 0, 0, 0]
"""


def test_a_benchmark_plays_three_models_on_two_tasks(arena_home, tmp_path, monkeypatch):
    from cuju.arena._evaluate import academy as _academy
    from cuju.arena._evaluate import benchmark as _benchmark

    _short_tasks(monkeypatch, 6)
    steady = _toy_team(tmp_path, 'steady', _SINGLE, '11v11')
    crashing = _toy_team(
        tmp_path,
        'crashing',
        "def agent(obs):\n    if obs['step'] == 2:\n        raise ValueError('broke')\n"
        '    return [5]\n',
        '11v11',
    )
    # A team of four players plays the empty goal, whose side holds two controllable players,
    # with its other players out, and the corner with all four.
    four = _toy_team(tmp_path, 'four', _FOUR, '11v11', control=_control(4))
    teams = [steady['id'], crashing['id'], four['id']]
    document = _benchmark.plan(teams, ['corner', 'empty_goal_close'], workers=1)
    # The tasks follow the order of the academy.
    assert document['tasks'] == ['empty_goal_close', 'corner']
    assert [cell['state'] for cell in document['cells']] == ['queued'] * 6
    seen = []
    _benchmark.run(document, play=_in_process, changed=seen.append)
    cells = {(cell['team'], cell['task']): cell for cell in document['cells']}
    assert all(cells[(steady['id'], task)]['state'] == 'done' for task in document['tasks'])
    broken = cells[(crashing['id'], 'empty_goal_close')]
    assert broken['state'] == 'done' and broken['failed_attempts'] == 5
    assert broken['value'] == 0 and broken['rate'] == 0.0
    assert all(result['failed'] for result in broken['results'])
    assert cells[(four['id'], 'empty_goal_close')]['playing'] == [2, 4]
    assert cells[(four['id'], 'corner')]['playing'] is None
    for cell in document['cells']:
        task = _academy.find(cell['task'])
        assert cell['played'] == task['attempts'] == len(cell['results'])
        assert cell['value'] == sum(result['scored'] for result in cell['results'])
        assert cell['rate'] == cell['value'] / 5 and cell['group'] is None
    rows = {row['team']: row for row in document['rows']}
    for team in teams:
        mine = [cell for cell in document['cells'] if cell['team'] == team]
        assert rows[team]['score'] == round(100 * sum(cell['rate'] for cell in mine) / 2, 1)
        assert rows[team]['done'] == 2
    assert sorted(row['place'] for row in document['rows'])[0] == 1
    assert document['progress'] == 1.0 and seen
    lines = _benchmark.csv_text(document).splitlines()
    assert lines[0] == 'model,team,empty_goal_close,corner,score,place'
    assert len(lines) == 4 and lines[3].startswith(f'four,{four["id"]},')
    assert lines[3].split(',')[2] != ''
    assert _benchmark.cell_line(document, broken) == (
        'crashing, empty_goal_close, 0 of 5, 5 failed attempts'
    )
    assert _benchmark.cell_line(document, broken, named=False).startswith('empty_goal_close, ')


def test_a_benchmark_checks_what_it_is_given(arena_home, tmp_path):
    from cuju.arena._evaluate import academy as _academy
    from cuju.arena._evaluate import benchmark as _benchmark

    for teams, tasks, workers in (
        ([], None, None),
        (['builtin', 'builtin'], None, None),
        ('builtin', None, None),
        (['no-such-team'], None, None),
        (['builtin'], ['corner', 'corner'], None),
        (['builtin'], ['no_such_drill'], None),
        (['builtin'], [], None),
        (['builtin'], None, 0),
        (['builtin'], None, 9),
        (['builtin'], None, True),
    ):
        with pytest.raises(cuju.errors.ArgumentError):
            _benchmark.plan(teams, tasks, workers=workers)
    with pytest.raises(cuju.errors.ArgumentError, match='keep_recordings'):
        _benchmark.plan(['builtin'], keep_recordings='yes')
    every = _benchmark.plan(['builtin'])
    assert every['tasks'] == list(_academy.TASKS)
    assert 1 <= every['workers'] == _benchmark.default_workers() <= 4
    assert not every['keep_recordings'] and every['progress'] == 0.0
    # A pair that cannot play is skipped, with the reason, and the others stay queued.
    wings = _toy_team(
        tmp_path, 'wings', _SINGLE, '11v11', control=_control(2, 'fixed', ['LM', 'RM'])
    )
    planned = _benchmark.plan([wings['id'], 'builtin'], ['empty_goal_close'])
    assert [cell['state'] for cell in planned['cells']] == ['skipped', 'queued']
    assert planned['cells'][0]['error_id'] == 'server.teams.cannot_play'


def test_a_model_at_an_earlier_checkpoint_names_its_checkpoint(arena_home, tmp_path):
    from cuju.arena._evaluate import benchmark as _benchmark
    from cuju.arena._registry import checkpoints as _checkpoints

    team = _toy_team(tmp_path, 'Ladder', _SINGLE, '11v11')['id']
    first = _checkpoints.current_id(team)
    _checkpoints.add(team, 'main.py', b'def agent(obs):\n    return [3]\n', 'step 2M')
    second = _checkpoints.current_id(team)
    _checkpoints.add(team, 'main.py', b'def agent(obs):\n    return [4]\n')
    labelled, bare, current = f'{team}@{second}', f'{team}@{first}', team
    planned = _benchmark.plan([labelled, bare, current], ['empty_goal_close'])
    names = [model['name'] for model in planned['models']]
    assert names[0] == 'Ladder step 2M'
    assert names[1] == f'Ladder {first[:8]}'
    assert names[2] == 'Ladder'


def test_benchmark_command_keeps_each_model_apart(arena_home, tmp_path, capsys):
    """Each run of the command plays in a process of its own, so no model sees another's state."""
    from cuju.arena.__main__ import main

    marking = _toy_team(
        tmp_path,
        'marking',
        'import builtins\n\n\ndef agent(obs):\n    builtins.cuju_bench_mark = True\n'
        '    return [12]\n',
        '11v11',
    )
    looking = _toy_team(
        tmp_path,
        'looking',
        'import builtins\n\n\ndef agent(obs):\n'
        "    if getattr(builtins, 'cuju_bench_mark', False):\n"
        "        raise RuntimeError('saw another model')\n"
        '    return [12]\n',
        '11v11',
    )
    arguments = ['benchmark', 'run', marking['id'], looking['id']]
    arguments += ['--task', 'empty_goal_close', '--workers', '1']
    assert main([*arguments, '--json']) == 0
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert [cell['state'] for cell in document['cells']] == ['done', 'done'], captured.err
    assert [cell['failed_attempts'] for cell in document['cells']] == [0, 0]
    # The end of each run prints one line on standard error.
    assert 'marking, empty_goal_close, ' in captured.err
    # The two models answer alike, so the same seeds give them the same results.
    first, second = document['cells']
    assert _played(first['results']) == _played(second['results'])
    assert first['value'] == second['value'] and first['rate'] == second['rate']
    assert document['rows'][0]['score'] == document['rows'][1]['score']
    assert all(result['match'] is None for result in first['results'])
    assert main([*arguments, '--csv']) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == 'model,team,empty_goal_close,score,place' and len(lines) == 3


class _Shooter:
    """A policy that runs right and shoots every ninth step, the same on every machine."""

    def __call__(self, observation, batch):
        import torch

        # The batch itself counts its steps, since the id of a batch that is gone may name the
        # next one.
        step = getattr(batch, 'policy_steps', 0)
        batch.policy_steps = step + 1
        shape = (batch.environment_count, batch.agents)
        return torch.full(shape, 12 if step % 9 == 8 else 5, dtype=torch.int32)


def test_a_policy_plays_the_attempts_of_a_task_at_once_on_the_seeds_of_the_catalogue(arena_home):
    import numpy as np
    import torch

    from cuju.arena._evaluate import academy as _academy
    from cuju.arena._evaluate import benchmark as _benchmark
    from cuju.arena._evaluate import replay as _replay
    from cuju.arena._evaluate import runner as _runner
    from cuju.arena._store import runs as _runs

    tasks = ['empty_goal_close', 'run_to_score_with_keeper']
    seen = []
    found = _benchmark.benchmark(
        _Shooter(),
        {'benchmark': {'tasks': tasks}, 'evaluate': {'name': 'Shooter'}},
        progress=seen.append,
    )
    assert found.kind == 'benchmark' and found.state == 'done' and found.name == 'Shooter'
    summary = found.summary
    assert [task['task'] for task in summary['tasks']] == tasks
    assert all(task['state'] == 'done' and task['attempts'] == 5 for task in summary['tasks'])
    assert summary['score'] == round(100 * sum(t['rate'] for t in summary['tasks']) / 2, 1)
    assert summary['value'] == sum(task['value'] for task in summary['tasks'])
    episodes = found.episodes
    assert len(episodes) == 10
    # Attempt i of a task plays the seed of the catalogue, as a team's run does.
    for index, task in enumerate(tasks):
        own = [episode for episode in episodes if episode['task'] == task]
        assert [episode['own_seed'] for episode in own] == _academy.find(task)['seeds']
        assert [episode['index'] for episode in own] == [0, 1, 2, 3, 4]
        assert summary['tasks'][index]['value'] == sum(e['goals_for'] > 0 for e in own)
    # The attempt of the batch is the episode that a game of one environment plays on that seed.
    kept = _runner.episodes_of(found.id)
    episode = episodes[7]
    actions = kept[episode['number']]['actions']
    with cuju.BatchEnvironment(
        'academy_run_to_score_with_keeper_l1_r0',
        agent_counts=[(1, 0)],
        environment_count=1,
        seed=episode['own_seed'],
        auto_reset=False,
        device='cpu',
    ) as batch:
        batch.reset()
        goals, steps, done = [0, 0], 0, False
        while not done:
            step = torch.as_tensor(np.asarray(actions[steps], dtype=np.int32)).reshape(1, 1)
            _, _, finished, information = batch.step(step)
            score = int(information['score_reward'][0])
            if score:
                goals[0 if score > 0 else 1] += 1
            steps += 1
            done = bool(finished[0])
    assert [episode['goals_for'], episode['goals_against'], episode['length']] == [*goals, steps]
    assert _replay.check(found.id) == {'id': found.id, 'checked': 10, 'same': True, 'problems': []}
    # The run is a benchmark of the manifest in the form that the list of benchmarks reads.
    document = _runs.get(found.id)
    assert document['kind'] == 'benchmark' and document['state'] == 'done'
    table = found.to_dict()['benchmark']
    assert table['tasks'] == tasks and table['teams'] == ['policy:shooter']
    assert table['rows'][0]['score'] == summary['score'] and table['rows'][0]['name'] == 'Shooter'
    assert [cell['state'] for cell in table['cells']] == ['done', 'done']
    assert _benchmark._restored(document)['rows'] == table['rows']
    assert _benchmark.csv_text(table).splitlines()[0] == (
        'model,team,empty_goal_close,run_to_score_with_keeper,score,place'
    )
    # The progress counts the tasks, and names the one that plays and those that wait.
    assert seen[0]['playing'] == [{'home': 'policy:shooter', 'away': 'empty_goal_close'}]
    assert [entry['away'] for entry in seen[0]['next']] == ['run_to_score_with_keeper']
    assert seen[-1]['played'] == seen[-1]['total'] == 2 and seen[-1]['phase'] == 'benchmark'
    assert found.scalars()['eval/score'] == summary['score']
    assert found.scalars()['eval/empty_goal_close/rate'] == summary['tasks'][0]['rate']
    # A benchmark that keeps its recordings has every attempt drawn, in one group.
    kept_run = _benchmark.benchmark(
        'builtin',
        {
            'benchmark': {'tasks': ['empty_goal_close'], 'keep_recordings': True},
            'evaluate': {'record': {'background': False}},
        },
    )
    assert len(kept_run.recordings) == 5 and kept_run.name == 'Built-in AI'
    path = arena_home / 'matches' / f'{kept_run.recordings[2]}.cjr'
    header, frames = _frames(path)
    assert header['title'] == 'academy_empty_goal_close, attempt 3, Built-in AI'
    assert header['title_id'] == 'server.names.benchmark_attempt'
    assert len(frames) == kept_run.episodes[2]['length'] + 1
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _benchmark.benchmark('builtin', {'benchmark': {'tasks': ['no_such_drill']}})
    assert caught.value.message_id == 'server.benchmark.unknown_task'


def test_the_benchmark_of_a_team_from_python_plays_its_runs_in_child_processes(
    arena_home, tmp_path
):
    from cuju.arena._evaluate import benchmark as _benchmark
    from cuju.arena._evaluate import replay as _replay

    team = _toy_team(tmp_path, 'toy', _SINGLE, '11v11')
    found = _benchmark.benchmark(
        f'team:{team["id"]}', {'benchmark': {'tasks': ['empty_goal_close'], 'workers': 1}}
    )
    assert found.state == 'done' and found.name == 'toy'
    (task,) = found.summary['tasks']
    assert task['state'] == 'done' and task['attempts'] == 5
    assert len(found.episodes) == 5 and found.recordings == []
    assert [episode['own_seed'] for episode in found.episodes] == _shared_seeds('empty_goal_close')
    # A run of a team keeps no actions, so nothing of it plays again.
    assert _replay.check(found.id)['checked'] == 0


def _shared_seeds(task):
    from cuju.arena._evaluate import academy as _academy

    return _academy.find(task)['seeds']


def test_the_command_plays_the_policy_of_the_settings(arena_home, capsys):
    from cuju.arena.__main__ import main

    options = ['--set', 'evaluate.policy=builtin', '--task', 'empty_goal_close']
    assert main(['benchmark', 'run', *options, '--json']) == 0
    document = json.loads(capsys.readouterr().out)
    assert document['kind'] == 'benchmark' and document['state'] == 'done'
    assert len(document['episodes']) == 5 and document['benchmark']['tasks'] == ['empty_goal_close']
    assert main(['benchmark', 'run', *options]) == 0
    printed = capsys.readouterr()
    lines = printed.out.splitlines()
    assert lines[0].endswith(', Built-in AI, done, 5 matches, 0 recordings')
    assert lines[1].startswith('    empty_goal_close, ') and lines[1].endswith(' of 5')
    assert lines[2].startswith('score ') and '1 of 1 tasks played' in printed.err
    assert main(['benchmark', 'run', *options, '--csv']) == 0
    assert capsys.readouterr().out.splitlines()[0] == 'model,team,empty_goal_close,score,place'
    # Without a team and without a policy of the settings, the command names what it lacks.
    assert main(['benchmark', 'run']) == 2
    missing = capsys.readouterr()
    assert missing.out == ''
    assert 'expected a list of distinct teams' in missing.err
    assert 'run cuju arena team list' in missing.err
    assert main(['evaluation', 'show', document['id']]) == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith('score ')
