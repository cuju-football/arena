# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of evaluation runs, their results, their recordings, their check, their command and
their routes, and of the ratings across tournaments, evaluations and recorded matches."""

import json
import time

import numpy as np
import pytest
import torch

import cuju
import cuju.errors
from cuju.arena._evaluate import evaluate as _evaluate
from cuju.arena._evaluate import replay as _replay
from cuju.arena._evaluate import runner as _runner
from cuju.arena._format import attachments as _attachments
from cuju.arena._format import recording as _recording
from cuju.arena._store import library as _library
from cuju.arena._store import ratings as _ratings
from cuju.arena._store import runs as _runs

_SCENARIO = 'academy_run_to_score_with_keeper'
_OPPONENTS = [{'kind': 'builtin', 'difficulty': 0.2}, {'kind': 'builtin', 'difficulty': 0.9}]


def _settings(**evaluate):
    record = {'keep': 'nothing', 'background': False, **evaluate.pop('record', {})}
    return {
        'evaluate': {
            'scenario': _SCENARIO,
            'episodes': 4,
            'opponents': _OPPONENTS,
            'record': record,
            **evaluate,
        }
    }


class _Policy:
    """A policy whose actions come from integer arithmetic alone, the same on every machine,
    with a value and probabilities as its extras."""

    def __init__(self, extras=True):
        self.extras = extras

    def __call__(self, observation, batch):
        # The batch itself counts its steps, since the id of a batch that is gone may name the
        # next one.
        step = getattr(batch, 'policy_steps', 0)
        batch.policy_steps = step + 1
        count, agents = batch.environment_count, batch.agents
        rows = [
            [(step * 7 + match * 3 + agent * 5) % batch.action_count for agent in range(agents)]
            for match in range(count)
        ]
        actions = torch.tensor(rows, dtype=torch.int32, device=observation.device)
        if not self.extras:
            return actions
        value = observation[..., 0].float() + step / 1000
        probabilities = torch.full((count, agents, batch.action_count), 0.01)
        probabilities[..., 3] = 1 - 0.01 * (batch.action_count - 1)
        return actions, {'values': {'value': value}, 'probabilities': probabilities}


def _results(evaluation):
    """The results of the matches without what differs from run to run."""
    keys = (
        'number',
        'opponent',
        'index',
        'side',
        'goals_for',
        'goals_against',
        'outcome',
        'length',
    )
    return [{key: episode[key] for key in keys} for episode in evaluation.episodes]


def test_the_shipped_keys_of_the_keeping_rule_and_the_intervals():
    assert _evaluate.keep_rule('losses') == ('losses', None)
    assert _evaluate.keep_rule('first:8') == ('first', 8)
    assert _evaluate.keep_rule(None) == ('nothing', None)
    for wrong in ('none', 'first', 'first:0', 'worst:x', 'losses:3', 7):
        with pytest.raises(cuju.errors.ArgumentError) as caught:
            _evaluate.keep_rule(wrong)
        assert caught.value.message_id == 'server.evaluate.keep'
    episodes = [
        {'number': number, 'outcome': outcome, 'goals_for': own, 'goals_against': other}
        for number, (outcome, own, other) in enumerate(
            [('win', 2, 0), ('loss', 0, 3), ('draw', 0, 0), ('loss', 1, 2), (None, None, None)]
        )
    ]
    assert _evaluate.kept_episodes(episodes, 'all', 32) == [0, 1, 2, 3]
    assert _evaluate.kept_episodes(episodes, 'nothing', 32) == []
    assert _evaluate.kept_episodes(episodes, 'losses', 32) == [1, 3]
    assert _evaluate.kept_episodes(episodes, 'losses', 1) == [1]
    assert _evaluate.kept_episodes(episodes, 'draws', 32) == [2]
    assert _evaluate.kept_episodes(episodes, 'wins', 32) == [0]
    assert _evaluate.kept_episodes(episodes, 'goals', 32) == [0, 1, 3]
    assert _evaluate.kept_episodes(episodes, 'first:2', 32) == [0, 1]
    assert _evaluate.kept_episodes(episodes, 'worst:2', 32) == [1, 3]
    # The Wilson interval of 8 wins in 10 matches at 95 percent, as its formula gives it.
    assert _evaluate.wilson(8, 10) == [0.4902, 0.9433]
    assert _evaluate.wilson(0, 0) == [0.0, 1.0] and _evaluate.wilson(0, 5)[0] == 0.0
    values = [1.0, -2.0, 0.0, 3.0, 1.0, 0.0, -1.0, 2.0]
    low, high = _evaluate.bootstrap(values, 0.95, 1000, 5)
    assert [low, high] == [-0.5, 1.5]
    assert low < sum(values) / len(values) < high
    assert _evaluate.bootstrap(values, 0.95, 1000, 5) == [low, high]
    assert _evaluate.bootstrap(values, 0.95, 1000, 6) == [-0.625, 1.625]
    assert _evaluate.bootstrap([], 0.95, 1000, 5) == [0.0, 0.0]
    assert _evaluate.bootstrap([2.0], 0.95, 1000, 5) == [2.0, 2.0]


def test_an_evaluation_of_the_built_in_ai_gives_one_table_on_either_device(arena_home, device):
    """The built-in AI against itself, and a policy of fixed actions, give the same results on
    the CPU and on CUDA, match for match."""
    tables = {}
    for name in dict.fromkeys(('cpu', device)):
        found = _evaluate.evaluate('builtin', _settings(), [f'evaluate.device={name}'])
        assert found.state == 'done' and len(found.episodes) == 8
        own = _evaluate.evaluate(
            _Policy(extras=False),
            _settings(agents=2, sides='both'),
            [f'evaluate.device={name}'],
        )
        assert len(own.episodes) == 16
        tables[name] = (found.summary, _results(found), own.summary, _results(own))
    assert tables[device] == tables['cpu']
    summary, results = tables['cpu'][:2]
    overall = summary['overall']
    assert overall['episodes'] == 8 and overall['failed'] == 0
    assert overall['wins'] + overall['draws'] + overall['losses'] == 8
    assert overall['win_rate'] == overall['wins'] / 8
    assert overall['win_rate_interval'] == _evaluate.wilson(overall['wins'], 8)
    assert overall['goals_for'] == sum(result['goals_for'] for result in results)
    difference = overall['goal_difference']
    assert difference['low'] <= difference['mean'] <= difference['high']
    assert [entry['key'] for entry in summary['opponents']] == ['builtin:0.20', 'builtin:0.90']
    assert all(entry['episodes'] == 4 for entry in summary['opponents'])
    # Match k against any opponent is index k of the seed rule, from the left.
    assert [(r['opponent'], r['index'], r['side']) for r in results] == [
        (opponent, index, 'left') for opponent in (0, 1) for index in range(4)
    ]


def test_an_evaluation_is_a_run_of_the_manifest_with_its_settings(arena_home):
    seen = []
    found = _evaluate.evaluate(
        _Policy(),
        _settings(name='Fixed', run='training', checkpoint={'id': 'c-12', 'step': 1200}),
        progress=seen.append,
    )
    document = _runs.get(found.id)
    assert document['kind'] == 'evaluation' and document['state'] == 'done'
    assert document['name'] == found.name == 'Fixed' and found.run == 'training'
    assert found.checkpoint == {'id': 'c-12', 'step': 1200}
    assert document['scenarios'] == [_SCENARIO] and document['seeds'] == [1000003]
    assert [opponent['key'] for opponent in document['opponents']] == [
        'builtin:0.20',
        'builtin:0.90',
    ]
    config = found.config
    assert config['mode'] == 'batched' and config['sides'] == ['left']
    assert config['policy'] == {
        'kind': 'function',
        'text': None,
        'team': None,
        'key': 'policy:fixed',
        'name': 'Fixed',
    }
    assert config['evaluate']['episodes'] == 4 and config['evaluate']['seed'] == 1000003
    assert config['evaluate']['policy'] is None and config['versions']['cuju']
    assert [setup['agents'] for setup in config['setups']] == [[1, 0], [1, 0]]
    assert config['setups'][1]['difficulty'] == [None, 0.9]
    # The progress says at every change what was played, what plays and what comes next.
    assert seen[0]['played'] == 0 and seen[0]['total'] == 8 and seen[0]['mode'] == 'batched'
    assert len(seen[0]['playing']) == 8 and seen[0]['next'] == []
    assert [entry['played'] for entry in seen] == [0, 4, 8, 8]
    last = seen[-1]
    assert last == found.progress and last['state'] == 'done' and last['done']
    assert last['phase'] == 'evaluation' and last['settled'] is False
    assert set(last['ratings']) == {'policy:fixed', 'builtin:0.20', 'builtin:0.90'}
    for rating in last['ratings'].values():
        assert rating['low'] < rating['rating'] < rating['high'] and rating['measured']
    assert last['ratings']['policy:fixed']['matches'] == 8
    # Every side starts at 1000, where the anchor of the tables stands, and a match moves both
    # sides by the same points, so the ratings keep that mean.
    assert seen[0]['ratings']['policy:fixed']['rating'] == 1000.0
    mean = sum(rating['rating'] for rating in last['ratings'].values()) / len(last['ratings'])
    assert mean == pytest.approx(1000.0, abs=0.05)
    assert [entry['number'] for entry in last['history']] == list(range(8))
    first = last['history'][0]
    assert first['home'] == 'policy:fixed' and first['away'] == 'builtin:0.20'
    assert set(first['ratings']) == {'policy:fixed', 'builtin:0.20'}
    assert last['entrants']['policy:fixed'] == {'name': 'Fixed', 'kind': 'policy', 'index': 0}
    assert last['entrants']['builtin:0.90']['name'] == 'Built-in AI 0.90'
    assert found.queue == {
        key: last[key] for key in ('playing', 'next', 'played', 'most', 'settled')
    }
    # A policy with a name is a model of the table of its scenario, at its checkpoint.
    rows = _ratings.results(_SCENARIO)
    assert [row['ref'] for row in rows] == [f'{found.id}/{number}' for number in range(8)]
    assert all(row['source'] == 'evaluation' and row['phase'] == 'evaluation' for row in rows)
    assert all(row['home'] == {'key': 'policy:fixed', 'checkpoint': 'c-12'} for row in rows)
    assert rows[3]['actions'] == {'run': found.id, 'number': 3}
    assert _ratings.models()['policy:fixed@c-12']['name'] == 'Fixed'
    # The list holds the evaluation, and those of its series alone when asked.
    listed = _evaluate.evaluations()
    assert [entry['id'] for entry in listed] == [found.id]
    assert listed[0]['played'] == listed[0]['total'] == 8 and listed[0]['run'] == 'training'
    assert listed[0]['overall'] == found.summary['overall']
    assert _evaluate.evaluations('training') == listed and _evaluate.evaluations('other') == []
    assert found.to_dict()['summary'] == found.summary
    assert repr(found) == f"Evaluation('{found.id}', done, 8 matches)"
    # A policy without a name of the settings moves no rating.
    nameless = _evaluate.evaluate(_Policy(extras=False), _settings())
    assert nameless.config['policy']['key'] is None and nameless.name == '_Policy'
    assert set(nameless.progress['ratings']) == {'policy', 'builtin:0.20', 'builtin:0.90'}
    assert len(_ratings.results(_SCENARIO)) == 8
    off = _evaluate.evaluate(_Policy(extras=False), _settings(name='Fixed', rate=False, seed=77))
    assert off.state == 'done' and len(_ratings.results(_SCENARIO)) == 8


def test_the_kept_matches_are_drawn_as_a_recorder_at_play_would(arena_home, monkeypatch):
    """A match drawn again from its seed and actions equals the recording that a recorder
    attached during play writes, bit for bit, and carries the extras as annotations."""
    from cuju.arena._watch.recorder import Recorder

    real = _runner.play_batch
    held = []

    def attached(*arguments, **options):
        if options.get('policy') is not None and not held:

            def attach(batch):
                held.append(Recorder(batch, 2, version=2, keep='all', every=1))

            options['attach'] = attach
        return real(*arguments, **options)

    monkeypatch.setattr(_runner, 'play_batch', attached)
    found = _evaluate.evaluate(
        _Policy(), _settings(agents=2, record={'keep': 'all', 'background': False})
    )
    assert found.to_dict()['record']['state'] == 'done'
    recordings = found.recordings
    assert len(recordings) == 8 and len(set(recordings)) == 8
    (at_play,) = held[0].saved
    episode = next(e for e in found.episodes if e['opponent'] == 0 and e['index'] == 2)
    drawn = _library.path_of(episode['recording'])
    assert list(_recording.frames(drawn)) == list(_recording.frames(at_play))
    first, second = _recording.sections(drawn), _recording.sections(at_play)
    for tag in ('PLYR', 'ACTS', 'STAT'):
        assert first[tag] == second[tag], tag
    assert len(list(_recording.frames(drawn))) == episode['length'] + 1
    header = _recording.read_header(drawn)
    assert header['run_key'] == _recording.read_header(at_play)['run_key']
    assert header['provenance']['run'] == {'id': found.id, 'episode': 0}
    assert header['provenance']['batch_seed'] == 1000003
    assert header['title_id'] == 'server.names.evaluation_match'
    assert header['title'] == '_Policy against Built-in AI 0.20, match 3'
    assert [team['name'] for team in header['teams']] == ['_Policy', 'Built-in AI 0.20']
    # Every step carries the value, the action and the probabilities of each agent.
    notes = _attachments.annotations(drawn)
    assert len(notes) == 2 * episode['length']
    actions = _recording.actions(drawn)
    for note in notes[:6]:
        # A record holds its frame, its values, its action and its likely actions by short keys.
        assert set(note['v']) == {'value'} and note['k'][0][0] == 3
        assert note['a'] in actions[note['f'] + 1].tolist()
    # The recordings form one group of the library, and each match holds its statistics.
    summary = next(entry for entry in _library.matches() if entry['id'] == episode['recording'])
    group = summary['group']
    assert group['kind'] == 'evaluation' and group['attempts'] == 8
    assert group['title'] == 'Evaluation of _Policy' and group['attempt'] == episode['number']
    assert len(_library.matches(group=group['id'])) == 8
    assert all(e['stats'] and e['stats']['score'] is not None for e in found.episodes)
    overall = found.summary['overall']
    assert overall['stats_episodes'] == 8 and 0 <= overall['possession'] <= 1
    assert abs(sum(overall['actions'].values()) - 1) < 0.01


def test_evaluation_keeps_both_checkpoint_snapshots(arena_home, tmp_path, monkeypatch, capsys):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import checkpoints
    from support.common import _SINGLE, _toy_team

    team = _toy_team(tmp_path, 'Greens', _SINGLE, '11v11')['id']
    other = _toy_team(tmp_path, 'Blues', _SINGLE + '\n', '11v11')['id']
    first, second = checkpoints.current_id(team), checkpoints.current_id(other)
    checkpoints.change(team, first, label='First', step=10)
    checkpoints.change(other, second, label='Second', step=20)
    expected = [
        {'checkpoint': {'id': first, 'label': 'First', 'step': 10}},
        {'checkpoint': {'id': second, 'label': 'Second', 'step': 20}},
    ]
    settings = _settings(
        opponents=[{'kind': 'team', 'team': other}],
        episodes=1,
        sides='both',
        checkpoint={'id': 'training', 'label': 'Series', 'step': 100},
        record={'keep': 'all'},
    )
    monkeypatch.setattr(_evaluate, '_draw', lambda *_: None)
    evaluation = _evaluate.evaluate(f'team:{team}', settings)
    checkpoints.change(team, first, label='Edited', step=50)
    checkpoints.change(other, second, label='Edited again', step=60)
    assert main(['evaluation', 'show', evaluation.id, '--json']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['schema'] == 'arena.evaluation.show/1'
    assert payload['checkpoint'] == settings['evaluate']['checkpoint']
    assert {ep['side']: ep['sides'] for ep in payload['episodes']} == {
        'left': expected,
        'right': expected[::-1],
    }
    _replay.run(evaluation.id)
    for episode in evaluation.episodes:
        recording = arena_home / 'matches' / f'{episode["recording"]}.cjr'
        assert _recording.read_header(recording)['provenance']['sides'] == episode['sides']


def test_the_keeping_rule_draws_only_its_matches(arena_home):
    found = _evaluate.evaluate(
        'builtin', _settings(record={'keep': 'first:3', 'limit': 2, 'background': True})
    )
    # The call returns while a process of its own draws the matches, and the list waits for it.
    assert found.wait(120)
    assert [episode['keep'] for episode in found.episodes] == [True, True, *[False] * 6]
    assert len(found.recordings) == 2
    assert [bool(episode['recording']) for episode in found.episodes] == [True, True, *[False] * 6]
    assert found.summary['overall']['stats_episodes'] == 2
    # With statistics of every match, each is drawn, and only the kept ones stay.
    every = _evaluate.evaluate(
        'builtin',
        _settings(episodes=2, record={'keep': 'first:1'}, stats={'episodes': 'all'}),
    )
    assert len(every.recordings) == 1 and every.summary['overall']['stats_episodes'] == 4
    assert len([entry for entry in _library.matches()]) == 3
    assert _evaluate.evaluate('builtin', _settings()).recordings == []


def test_check_confirms_an_evaluation_and_finds_a_changed_action_file(arena_home):
    found = _evaluate.evaluate(_Policy(extras=False), _settings(sides='both'))
    same = _replay.check(found.id)
    assert same == {'id': found.id, 'checked': 16, 'same': True, 'problems': []}
    folder = _runner.actions_folder(found.id)
    files = sorted(path.name for path in folder.glob('*.npz'))
    assert files == ['o0-left-0.npz', 'o0-right-0.npz', 'o1-left-0.npz', 'o1-right-0.npz']
    # One changed action leaves another digest, whatever it does to the score.
    path = folder / 'o1-left-0.npz'
    with np.load(path) as data:
        arrays = {name: data[name].copy() for name in data.files}
    numbers = arrays['numbers'].tolist()
    arrays['actions'][3, 1, 0] = (arrays['actions'][3, 1, 0] + 1) % 19
    np.savez_compressed(path, **arrays)
    changed = _replay.check(found.id)
    assert not changed['same'] and changed['checked'] == 16
    assert [problem['number'] for problem in changed['problems']] == [numbers[1]]
    assert changed['problems'][0]['problem'] == 'changed'
    # A file that is gone leaves its matches without actions.
    path.unlink()
    missing = _replay.check(found.id)
    assert [problem['problem'] for problem in missing['problems']] == ['missing'] * 4
    assert sorted(problem['number'] for problem in missing['problems']) == sorted(numbers)
    # A match whose kept result differs from its replay is found by its score or its length.
    other = _evaluate.evaluate(_Policy(extras=False), _settings())
    document = _runs.get(other.id)
    episodes = [dict(episode) for episode in document['episodes']]
    episodes[0]['goals_for'] += 1
    episodes[5]['length'] += 2
    _runner.keep_episodes(other.id, episodes)
    wrong = _replay.check(other.id)
    assert [(problem['number'], problem['problem']) for problem in wrong['problems']] == [
        (0, 'score'),
        (5, 'length'),
    ]
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _replay.check('e-none')
    assert caught.value.message_id == 'server.evaluate.unknown'


def test_the_settings_refuse_what_does_not_fit(arena_home):
    with pytest.raises(cuju.errors.ChoiceError, match=r'expected a key of \[evaluate\]'):
        _evaluate.evaluate('builtin', {'evaluate': {'episode': 3}})
    with pytest.raises(cuju.errors.ConfigurationError, match=r'evaluate\.episodes'):
        _evaluate.evaluate('builtin', _settings(), ['evaluate.episodes=0'])
    for policy, settings, message in (
        (None, _settings(), 'server.evaluate.policy_missing'),
        (7, _settings(), 'server.evaluate.policy'),
        ('nothing', _settings(), 'server.evaluate.policy'),
        ('team:t-none', _settings(), 'server.teams.unknown'),
        ('no_such_module:make', _settings(), 'server.evaluate.policy_load'),
        ('builtin', _settings(opponents=[]), 'server.evaluate.opponents'),
        ('builtin', _settings(opponents=[*_OPPONENTS, _OPPONENTS[0]]), 'server.evaluate.opponents'),
        (
            'builtin',
            _settings(opponents=[{'kind': 'team', 'team': 't-none'}]),
            'server.teams.unknown',
        ),
        ('builtin', _settings(record={'keep': 'best'}), 'server.evaluate.keep'),
    ):
        with pytest.raises(cuju.errors.ArgumentError) as caught:
            _evaluate.evaluate(policy, settings)
        assert caught.value.message_id == message, (policy, message)
    assert _evaluate.evaluations() == []
    # A policy that answers another shape than the batch's ends the run as failed.
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _evaluate.evaluate(lambda observation, batch: torch.zeros(3), _settings())
    assert caught.value.message_id == 'server.evaluate.actions_shape'
    (failed,) = _evaluate.evaluations()
    assert failed['state'] == 'failed'
    assert 'expected the actions of a policy' in _evaluate.get(failed['id']).to_dict()['error']
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _evaluate.evaluate(
            lambda observation, batch: (torch.zeros((4, 1)), {'value': observation}), _settings()
        )
    assert caught.value.message_id == 'server.evaluate.extras'
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _evaluate.remove(failed['id'] + 'x') or _evaluate.get('e-none')
    assert caught.value.message_id == 'server.evaluate.unknown'
    assert _evaluate.remove(failed['id']) and not _evaluate.remove(failed['id'])


def test_a_module_and_a_function_name_a_policy(arena_home, tmp_path, monkeypatch):
    source = (
        'import torch\n\n\n'
        'def make(action=5):\n'
        '    def policy(observation, batch):\n'
        '        shape = (batch.environment_count, batch.agents)\n'
        '        return torch.full(shape, action, dtype=torch.int32)\n\n'
        '    return policy\n\n\n'
        'def kaggle(shot=12):\n'
        '    def agent(obs):\n'
        "        return [shot if obs['step'] % 9 == 8 else 5]\n\n"
        '    return agent\n'
    )
    (tmp_path / 'policies_for_test.py').write_text(source, encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    by_module = _evaluate.evaluate(
        'policies_for_test:make', _settings(policy_options={'action': 5})
    )
    assert by_module.name == 'policies_for_test:make'
    assert by_module.config['evaluate']['policy'] == 'policies_for_test:make'
    by_file = _evaluate.evaluate(
        None, _settings(policy=str(tmp_path / 'policies_for_test.py') + ':make')
    )
    assert _results(by_file) == _results(by_module)
    document = _runs.get(by_module.id)
    folder = _runner.actions_folder(by_module.id)
    with np.load(folder / document['episodes'][0]['actions']) as data:
        assert set(np.unique(data['actions']).tolist()) == {5}
    # A Kaggle agent is called for each match, and plays in a batch all the same.
    agent = _evaluate.evaluate(
        'policies_for_test:kaggle', _settings(interface='kaggle', episodes=2)
    )
    assert agent.config['mode'] == 'batched' and len(agent.episodes) == 4
    assert agent.config['setups'][0]['options'] == {
        'representation': 'simple115v2',
        'with_raw': True,
    }
    assert _replay.check(agent.id)['same']
    with np.load(_runner.actions_folder(agent.id) / 'o0-left-0.npz') as data:
        assert set(np.unique(data['actions'][:8]).tolist()) == {5} and 12 in data['actions']


class _Writer:
    def __init__(self):
        self.scalars = {}

    def add_scalar(self, tag, value, step=None):
        self.scalars[tag] = (value, step)


class _Run:
    def __init__(self):
        self.logged = []

    def log(self, values, step=None):
        self.logged.append((dict(values), step))


def test_log_writes_scalars_to_a_writer_or_a_run_by_what_it_can_do(arena_home):
    found = _evaluate.evaluate(
        'builtin', _settings(checkpoint={'id': 'c-9', 'step': 900}, episodes=2)
    )
    overall = found.summary['overall']
    writer = _Writer()
    found.log(writer, step=40)
    assert writer.scalars['eval/win_rate'] == (overall['win_rate'], 40)
    assert writer.scalars['eval/win_rate_low'][0] == overall['win_rate_interval'][0]
    assert writer.scalars['eval/goal_difference'][0] == overall['goal_difference']['mean']
    assert writer.scalars['eval/episodes'] == (4.0, 40)
    assert writer.scalars['eval/builtin_0.20/win_rate'][0] is not None
    assert writer.scalars == {tag: (value, 40) for tag, value in found.scalars().items()}
    # Without a step the step of the checkpoint serves, and a run of W&B takes all at once.
    run = _Run()
    found.log(run, prefix='held')
    ((values, step),) = run.logged
    assert step == 900 and values == found.scalars('held')
    assert values['held/episodes'] == 4.0 and all(tag.startswith('held/') for tag in values)
    bare = _evaluate.evaluate('builtin', _settings(episodes=2))
    bare.log(run)
    assert run.logged[-1][1] is None
    # A whole number as the checkpoint is its step.
    numbered = _evaluate.evaluate('builtin', _settings(episodes=2), ['evaluate.checkpoint=1500'])
    assert numbered.checkpoint == {'id': '1500', 'step': 1500}
    numbered.log(run)
    assert run.logged[-1][1] == 1500
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        found.log(object())
    assert caught.value.message_id == 'server.evaluate.target'
    assert 'torch.utils.tensorboard' not in __import__('sys').modules
    assert 'wandb' not in __import__('sys').modules


def test_the_server_takes_up_an_evaluation_that_a_stop_left(arena_home, monkeypatch):
    """An evaluation of the server that was waiting or under way plays on from its manifest."""
    settings = _evaluate.request_settings({**_settings()['evaluate'], 'policy': 'builtin'})
    made = _evaluate.plan(None, settings)
    identifier = _evaluate._create(made, settings, 'server', 'running')
    # A function given from Python is no evaluation of the server, so the server leaves it.
    other = _evaluate._create(made, settings, 'python', 'running')
    started = []

    class _Done:
        stderr = None
        returncode = 0

        def wait(self):
            return 0

        def kill(self):
            pass

    def starter(run):
        started.append(run)
        _evaluate.resume(run)
        return _Done()

    jobs = _evaluate._Evaluations(starter=starter)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and _runs.get(identifier)['state'] != 'done':
        time.sleep(0.1)
    assert started == [identifier] and _runs.get(identifier)['state'] == 'done'
    assert len(_runs.get(identifier)['episodes']) == 8
    assert _runs.get(other)['state'] == 'running'
    assert not jobs.cancel(identifier) and jobs.cancel(other)
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _evaluate.resume(identifier)
    assert caught.value.message_id == 'server.evaluate.ended'


def test_evaluation_show_prints_the_match_counts(arena_home, capsys):
    from cuju.arena.__main__ import main

    identifier = 'e-20261120-012345-6789'
    _runs.create(
        'evaluation',
        identifier,
        opponents=[{'name': 'reds'}, {'name': 'blues'}],
        episodes=[
            {
                'number': index,
                'opponent': index,
                'outcome': 'win',
                'goals_for': 1,
                'goals_against': 0,
                'length': 100,
            }
            for index in range(2)
        ],
    )
    assert main(['evaluation', 'show', 'last']) == 0
    out, _ = capsys.readouterr()
    assert 'against reds, 1 match, 1 won' in out
    assert 'against blues, 1 match, 1 won' in out
    assert 'overall, 2 matches, 2 won' in out


@pytest.mark.parametrize('state', ['cached', 'legacy', 'running'])
def test_show_of_an_evaluation_reads_its_summary_without_numpy(arena_home, state):
    import subprocess
    import sys

    from cuju.arena._store import runs

    identifier = 'e-20261120-012345-6789'
    episodes = [
        {'number': 0, 'outcome': 'win', 'goals_for': 1, 'goals_against': 0, 'length': 100},
        {'number': 1, 'outcome': 'win', 'goals_for': 2, 'goals_against': 0, 'length': 200},
    ]
    document = runs.create('evaluation', identifier, episodes=episodes)
    expected = _evaluate.summarize(document)
    if state != 'running':
        aggregates = {'record': {'state': 'done'}}
        if state == 'cached':
            aggregates['summary'] = expected
        runs.finish(identifier, aggregates)
    else:
        runs.update(identifier, state='running')
    probe = """
import contextlib
import io
import json
import sys
from cuju.arena.__main__ import main
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert main(['evaluation', 'show', 'last', '--json']) == 0
print(json.dumps(json.loads(out.getvalue())['summary']))
assert 'numpy' not in sys.modules
assert 'torch' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, '-c', probe], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
