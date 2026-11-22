# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The library of submission formats, `cuju.arena.formats.v1`, held to gfootball and to cuju."""

import io
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
import torch

import cuju
from cuju.arena._formats import blame as _blame
from cuju.arena._formats import frames as _frames
from cuju.arena.formats import v1 as formats
from cuju.arena.formats.v1 import _context
from cuju.gfootball import env as _gfootball
from cuju.kaggle import observation as _kaggle_observation
from cuju.raw import grf_observation, team_sizes

PACKAGE = Path(cuju.arena.__file__).parent
PROBE = Path(__file__).parents[1] / 'support' / 'formats' / 'probe'


def _batch(asset, counts, steps=5, seed=4):
    batch = cuju.BatchEnvironment(
        asset,
        agent_counts=[counts],
        environment_count=1,
        representation='raw',
        seed=seed,
        auto_reset=False,
        device='cpu',
    )
    batch.reset()
    for _ in range(steps):
        batch.step(torch.full((1, sum(counts)), 5, dtype=torch.int32))
    return batch


@pytest.mark.parametrize(
    ('asset', 'counts'), [('5_vs_5_l1_r1', (1, 1)), ('11_vs_11_kaggle_l2_r1', (2, 1))]
)
def test_the_fixed_table_equals_grf_observation(asset, counts):
    """The frames carry every key of GRF's raw observation in GRF's own types, both sides."""
    batch = _batch(asset, counts)
    try:
        raw = batch.raw()
        for agent in range(sum(counts)):
            found = grf_observation(raw, 0, agent, *team_sizes(batch, 0, agent))
            assert list(found) == list(_frames.TABLE)
            for key, (kind, rank) in _frames.TABLE.items():
                value = found[key]
                if kind == 'int':
                    assert type(value) is int, key
                elif kind == 'score':
                    assert type(value) is list and len(value) == 2, key
                else:
                    assert (value.dtype.name, value.ndim) == (kind, rank), key
            _frames.typed(found)
            back = _frames.read(io.BytesIO(_frames.encode('call', [found, found])))['value']
            for decoded in back:
                assert list(decoded) == list(found)
                for key, value in found.items():
                    if isinstance(value, np.ndarray):
                        assert (
                            decoded[key].dtype == value.dtype and decoded[key].shape == value.shape
                        )
                        assert decoded[key].tobytes() == value.tobytes()
                        assert decoded[key].flags.writeable
                    else:
                        assert type(decoded[key]) is type(value) and decoded[key] == value
    finally:
        batch.close()


def test_frames_carry_json_and_arrays_and_refuse_everything_else():
    value = {'a': [1, 2.5, None, True, 'x'], 'b': np.arange(6, dtype=np.int32).reshape(2, 3)}
    back = _frames.read(io.BytesIO(_frames.encode('answer', value, extra=1)))
    assert back['kind'] == 'answer' and back['extra'] == 1 and back['contract'] == 1
    assert back['value']['a'] == [1, 2.5, None, True, 'x']
    assert back['value']['b'].dtype == np.int32 and back['value']['b'].tolist() == [
        [0, 1, 2],
        [3, 4, 5],
    ]
    for bad in ({1, 2}, object(), {'$array': 0}, np.array(['text']), {1: 'key'}):
        with pytest.raises(_frames.FrameError):
            _frames.encode('answer', bad)
    frame = _frames.encode('answer', 1)
    other = frame.replace(b'"contract":1', b'"contract":9')
    with pytest.raises(_frames.FrameError, match='contract'):
        _frames.read(io.BytesIO(other))
    with pytest.raises(EOFError):
        _frames.read(io.BytesIO(frame[:-1]))


def test_no_format_module_pickles_anything():
    """Nothing is pickled in either direction, so no module of formats imports a pickler."""
    for root in (PACKAGE / 'formats', PACKAGE / '_formats'):
        for path in root.rglob('*.py'):
            text = path.read_text(encoding='utf-8')
            assert not re.search(
                r'^\s*(import|from)\s+(pickle|marshal|multiprocessing|dill|cloudpickle)\b|\bpickle\.loads?\b',
                text,
                re.MULTILINE,
            ), path


def test_kaggle_observation_equals_cujus_own():
    batch = _batch('11_vs_11_kaggle_l2_r1', (2, 1))
    try:
        raw = batch.raw()
        for agents in ([0], [0, 1], [2]):
            expected = _kaggle_observation(
                batch, 0, agents if len(agents) > 1 else agents[0], step=4
            )
            observations = [grf_observation(raw, 0, a, *team_sizes(batch, 0, a)) for a in agents]
            found = formats.kaggle_observation(observations, step=4)
            assert found == expected
            assert json.dumps(found) == json.dumps(expected)
            assert found.step == 4 and found.remainingOverageTime == 60
            with pytest.raises(AttributeError):
                found.missing  # noqa: B018
    finally:
        batch.close()


def test_kaggle_agent_takes_the_last_callable_and_runs_from_its_folder(tmp_path):
    folder = tmp_path / 'team'
    folder.mkdir()
    (folder / 'mine.py').write_text('VALUE = 7\n', encoding='utf-8')
    (folder / 'data.txt').write_text('12', encoding='utf-8')
    (folder / 'main.py').write_text(
        'import mine\n'
        'def helper(obs):\n    return [0]\n'
        'def agent(obs):\n    return [mine.VALUE + int(open("data.txt").read())]\n',
        encoding='utf-8',
    )
    agent = formats.kaggle_agent(folder)
    assert agent({}) == [19]
    assert 'mine' not in sys.modules
    assert agent.__name__ == 'agent'
    (folder / 'main.py').write_text('VALUE = 1\n', encoding='utf-8')
    with pytest.raises(formats.Forfeit) as caught:
        formats.kaggle_agent(folder)
    assert caught.value.kind == 'load'
    with pytest.raises(formats.Forfeit, match='missing'):
        formats.kaggle_agent(tmp_path / 'nowhere')


def test_load_module_gives_a_name_of_its_own(tmp_path):
    (tmp_path / 'bot.py').write_text('def decide(o):\n    return "left"\n', encoding='utf-8')
    module = formats.load_module(tmp_path, 'bot.py')
    assert module.decide({}) == 'left'
    assert module.__name__.startswith('cuju_submission_')
    with pytest.raises(formats.Forfeit):
        formats.load_module(tmp_path, '../bot.py')


@pytest.mark.parametrize(
    ('value', 'count', 'action_set', 'negative', 'expected'),
    [
        (5, 1, 'default', False, [5]),
        ([5], 1, 'default', False, [5]),
        ([[3], [4]], 2, 'default', False, [3, 4]),
        (np.array([1, 2]), 2, 'default', False, [1, 2]),
        (np.int64(7), 1, 'default', False, [7]),
        (torch.tensor([2, 3]), 2, 'default', False, [2, 3]),
        (-1, 1, 'default', True, [18]),
        (19, 1, 'v2', False, [19]),
        (32, 1, 'full', False, [32]),
    ],
)
def test_as_actions_takes_every_common_answer(value, count, action_set, negative, expected):
    assert formats.as_actions(value, count, action_set, negative) == expected


@pytest.mark.parametrize(
    ('value', 'count', 'action_set'),
    [
        (True, 1, 'default'),
        (19, 1, 'default'),
        (-1, 1, 'default'),
        ([1, 2], 1, 'default'),
        (1.0, 1, 'default'),
        ('3', 1, 'default'),
        ([1], 2, 'default'),
    ],
)
def test_as_actions_refuses_what_gfootball_would_not_play(value, count, action_set):
    with pytest.raises(formats.Invalid):
        formats.as_actions(value, count, action_set)


def test_verdicts_problems_and_forfeits():
    note = formats.problem('note', '3 to 1', 'note', detail='match.builtin')
    verdict = formats.accept(players=2, name='  Rex ', deterministic=True, problems=[note])
    assert verdict.accepted and not verdict.environment and verdict.name == 'Rex'
    assert formats.Verdict.from_dict(json.loads(json.dumps(verdict.as_dict()))) == verdict
    refused = formats.refuse('load', 'no GPU', detail='gpu', environment=True)
    assert not refused.accepted and refused.environment
    assert len(formats.problem('rule', 'x' * 500).message) == 300
    with pytest.raises(ValueError):
        formats.problem('bad name!', 'x')
    with pytest.raises(ValueError):
        formats.accept(players=12)
    forfeit = formats.Forfeit('timeout', 'the bank ran out', detail='overage')
    assert forfeit.as_dict() == {
        'kind': 'timeout',
        'message': 'the bank ran out',
        'detail': 'overage',
    }
    for kind in ('ceiling.answer', 'error', 'stall'):
        with pytest.raises(ValueError):
            formats.Forfeit(kind, 'only the renderer records a ceiling')


def test_read_manifest_reads_toml_json_and_yaml(tmp_path):
    (tmp_path / 'a.toml').write_text('team = "rex"\nplayers = 2\n', encoding='utf-8')
    (tmp_path / 'a.json').write_text('{"team": "rex"}', encoding='utf-8')
    (tmp_path / 'a.yml').write_text('team: rex\ncontrolled_players: 1\n', encoding='utf-8')
    (tmp_path / 'list.json').write_text('[1]', encoding='utf-8')
    (tmp_path / 'bad.toml').write_text('team = ', encoding='utf-8')
    assert formats.read_manifest(tmp_path, 'a.toml') == {'team': 'rex', 'players': 2}
    assert formats.read_manifest(tmp_path, 'a.json') == {'team': 'rex'}
    try:
        import yaml  # noqa: F401
    except ImportError:
        with pytest.raises(formats.ManifestError, match='needs PyYAML'):
            formats.read_manifest(tmp_path, 'a.yml')
    else:
        assert formats.read_manifest(tmp_path, 'a.yml') == {'team': 'rex', 'controlled_players': 1}
    for name in ('list.json', 'bad.toml', 'missing.toml', '../a.toml'):
        with pytest.raises(formats.ManifestError):
            formats.read_manifest(tmp_path, name)


def test_require_and_seed_globals():
    assert formats.require('json') is json
    assert formats.require('json', 're') == (json, re)
    with pytest.raises(formats.Missing, match='install it') as caught:
        formats.require('json', 'no_such_module_here', hint='install it')
    assert caught.value.modules == ['no_such_module_here']
    formats.seed_globals(5)
    first = (__import__('random').random(), np.random.random(), torch.rand(1).item())
    formats.seed_globals(5)
    assert first == (__import__('random').random(), np.random.random(), torch.rand(1).item())


@pytest.mark.parametrize('representation', ['simple115', 'simple115v2', 'extracted'])
@pytest.mark.parametrize('stacked', [False, True])
def test_the_observer_gives_what_gfootball_gives(representation, stacked):
    """Two gfootball environments of one seed, raw and the representation, step alike."""
    options = {'game_engine_random_seed': 11}
    made = _gfootball.create_environment(
        env_name='5_vs_5',
        representation=representation,
        stacked=stacked,
        other_config_options=options,
    )
    plain = _gfootball.create_environment(
        env_name='5_vs_5', representation='raw', other_config_options=options
    )
    observer = formats.Observer(representation, stacked=stacked)
    try:
        expected = made.reset()
        found = observer.observe(plain.reset())
        for step in range(12):
            assert np.array_equal(found, expected), step
            assert found.dtype == expected.dtype
            action = (step * 5) % 19
            expected = made.step(action)[0]
            found = observer.observe(plain.step(action)[0])
        assert len(observer.per_player(plain.step(0)[0])) == 1
    finally:
        made.close()
        plain.close()


def test_the_observer_refuses_what_gfootball_cannot_make():
    for representation, stacked in (('pixels', False), ('raw', True)):
        with pytest.raises(ValueError):
            formats.Observer(representation, stacked=stacked)


def test_blame_follows_the_innermost_owned_frame(tmp_path):
    team = tmp_path / 'team'
    own = tmp_path / 'format'
    team.mkdir()
    own.mkdir()
    (team / 'mine.py').write_text('def boom():\n    raise ValueError("team")\n', encoding='utf-8')
    (own / 'theirs.py').write_text('def call(f):\n    return f()\n', encoding='utf-8')
    mine = formats.load_module(team, 'mine.py')
    theirs = formats.load_module(own, 'theirs.py')
    with pytest.raises(ValueError) as caught:
        theirs.call(mine.boom)
    assert _blame.blame(caught.value, team, own) == 'team'
    with pytest.raises(ZeroDivisionError) as caught:
        theirs.call(lambda: 1 / 0)
    assert _blame.blame(caught.value, team, own) == 'format'
    try:
        compile('def (', str(team / 'bad.py'), 'exec')
    except SyntaxError as error:
        assert _blame.blame(error, team, own) == 'team'
    try:
        int('x')
    except ValueError as error:
        assert _blame.blame(error, team, None) == 'format'


def test_overlapping_format_calls_keep_their_own_context(tmp_path):
    initial = _context.current()
    outer = _context.Context(format_folder=tmp_path / 'outer')
    other = _context.Context(format_folder=tmp_path / 'other')

    def in_other_thread():
        assert _context.current() is initial
        with _context.using(other):
            assert _context.current() is other
        assert _context.current() is initial

    with ThreadPoolExecutor(max_workers=1) as worker, _context.using(outer):
        worker.submit(in_other_thread).result(timeout=10)
        assert _context.current() is outer
    assert _context.current() is initial


def test_a_child_answers_calls_and_maps_its_failures(tmp_path):
    folder = tmp_path / 'submission'
    folder.mkdir()
    (folder / 'mine.py').write_text('def boom():\n    raise KeyError("team")\n', encoding='utf-8')
    with _context.using(_context.Context(format_folder=PROBE, submission=folder)):
        child = formats.Child(folder, 'inner.py', 'Echo', load_timeout=60)
        try:
            assert child.load_seconds > 0
            value, seconds = child.call('echo', {'a': np.arange(3)}, timeout=30)
            assert value['a'].tolist() == [0, 1, 2] and seconds > 0
            assert Path(child.call('where')[0]).resolve() == folder.resolve()
            with pytest.raises(formats.ChildCrash, match='KeyError'):
                child.call('team')
            with pytest.raises(formats.Forfeit) as caught:
                child.call('team', forfeits=True)
            assert caught.value.kind == 'crash'
            with pytest.raises(RuntimeError, match='the format failed'):
                child.call('fail')
            with pytest.raises(formats.Forfeit) as caught:
                child.call('slow', 30, timeout=0.5, forfeits=True)
            assert caught.value.kind == 'timeout'
        finally:
            child.close()
        with pytest.raises(FileNotFoundError):
            formats.Child(folder, '../elsewhere/inner.py', 'Echo')
    with pytest.raises(RuntimeError, match='only from the code of a format'):
        formats.Child(folder, 'inner.py', 'Echo')


def test_test_match_plays_only_inside_a_check():
    with pytest.raises(RuntimeError, match='inside accept'):
        formats.test_match()


def test_the_library_keeps_its_behaviour_and_contract():
    assert formats.CONTRACT == 1 and formats.BEHAVIOUR >= 1
    assert set(formats.__all__) <= set(dir(formats))
    assert formats.KINDS == ('load', 'timeout', 'crash', 'invalid', 'rule')
    from cuju.arena import formats as library

    assert library.CURRENT == 1 and library.CONTRACTS == (1,) and library.v1 is formats


def test_imports_of_reads_the_modules_a_folder_imports_without_running_it(tmp_path):
    (tmp_path / 'main.py').write_text(
        'import numpy as np\nimport gfootball.env.wrappers\n'
        'from torch import nn\nfrom . import helper\n'
        'raise SystemExit("never run")\n',
        encoding='utf-8',
    )
    (tmp_path / 'lib').mkdir()
    (tmp_path / 'lib' / 'more.py').write_text('from os.path import join\n', encoding='utf-8')
    (tmp_path / 'lib' / 'broken.py').write_text('import (\n', encoding='utf-8')
    (tmp_path / '__pycache__').mkdir()
    (tmp_path / '__pycache__' / 'cached.py').write_text('import hidden\n', encoding='utf-8')
    (tmp_path / 'notes.txt').write_text('import text\n', encoding='utf-8')
    assert formats.imports_of(tmp_path) == {'numpy', 'gfootball', 'torch', 'os'}
    assert formats.imports_of(str(tmp_path / 'lib')) == {'os'}


def test_folder_size_counts_every_file_but_the_caches_of_python(tmp_path):
    (tmp_path / 'a.bin').write_bytes(bytes(1000))
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'sub' / 'b.bin').write_bytes(bytes(24))
    (tmp_path / '__pycache__').mkdir()
    (tmp_path / '__pycache__' / 'c.pyc').write_bytes(bytes(5000))
    assert formats.folder_size(tmp_path) == 1024
    assert formats.folder_size(tmp_path / 'sub') == 24


def test_a_control_names_its_goalkeeper_as_the_registry_does():
    assert formats.Control().goalkeeper == 'scenario'
    assert formats.Control.from_dict({'goalkeeper': 'builtin'}).goalkeeper == 'builtin'
