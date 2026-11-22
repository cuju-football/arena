# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The four example formats of the guide, Arena 5 vs 5, Policy, Squad and Bot, through contract 1.

Each example is a folder under docs/formats. The tests add each format, check its samples,
check its refusals with broken samples, and
play short matches under them, so the guide's examples never drift from the contract.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from cuju.arena._formats import ceilings as _ceilings
from cuju.arena._formats import checks as _checks
from cuju.arena._formats import contract as _contract
from cuju.arena._formats import launch as _launch
from cuju.arena._formats import registry as _registry
from cuju.arena._formats import tree as _tree
from cuju.arena._playing import play as _play
from cuju.arena._registry import teams as _teams
from cuju.arena._version import __version__
from cuju.arena.formats import v1 as formats
from support.common import _control

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / 'docs' / 'formats'
SAMPLES = EXAMPLES / 'samples'
NAMES = ('arena', 'policy', 'squad', 'bot')
GPU = os.environ.get('CUJU_EXAMPLES_GPU') == '1'


def _copied(source, folder):
    """Copies an example format, which is a folder a person adds."""
    shutil.copytree(source, folder, ignore=shutil.ignore_patterns('__pycache__'))
    return folder


@pytest.fixture
def formats_added(arena_home, tmp_path):
    """The four examples, added to the home as a person adds them, by their identifier."""
    return {
        name: _registry.add(_copied(EXAMPLES / name, tmp_path / 'added' / name)) for name in NAMES
    }


def _run(script, folder):
    """Runs a short script of the samples that writes untrained weights, in a folder."""
    subprocess.run([sys.executable, '-I', script], cwd=folder, check=True, timeout=300)


@pytest.fixture(scope='session')
def weights(tmp_path_factory):
    """The Policy and Squad samples with their untrained weights written, once for the session."""
    root = tmp_path_factory.mktemp('samples')
    for name, script in (('policy', 'make_model.py'), ('squad', 'make_policies.py')):
        shutil.copytree(SAMPLES / name, root / name)
        _run(script, root / name)
    return root


def _copy(source, tmp_path, name):
    folder = tmp_path / name
    shutil.copytree(source, folder, ignore=shutil.ignore_patterns('__pycache__'))
    return folder


def _check(format, folder, size='5v5', control=None, full=False, **options):
    """Runs the pre-check of a format on a folder as the renderer does and returns its verdict."""
    values = _contract.options(format.spec, options)
    scenario = {'5v5': '5_vs_5', '11v11': '11_vs_11_kaggle'}[size]
    team = formats.Team(
        'checked', size, None, scenario, control or formats.Control(), None, values, full
    )
    return _checks.check(format, values, folder, team)


def _registered(folder, name, size='5v5', control=None):
    """Registers a sample as a team.

    The registry still asks every team for a main.py at its top, so the folder gets a placeholder
    that no example format reads.
    """
    (folder / 'main.py').write_text('# The sample plays through its format.\n', encoding='utf-8')
    return _teams.add_folder(folder, name, size, control)['id']


def _actions(team, format_choice, scenario='5_vs_5', steps=40, seed=3, right='builtin'):
    replay = {}
    _play.play(
        team,
        right,
        scenario,
        seed=seed,
        steps=steps,
        save=False,
        replay=replay,
        submission_format=[format_choice, None],
    )
    return replay['actions']


def _rule(team, format_choice, steps=40, right='builtin'):
    with pytest.raises(_play.ForfeitError) as caught:
        _actions(team, format_choice, steps=steps, right=right)
    return caught.value.rule


# The contract and the ranges


@pytest.mark.parametrize('name', NAMES)
def test_every_example_reads_under_contract_1_and_asks_for_the_release(name):
    spec = _contract.parse((EXAMPLES / name / 'format.toml').read_text(encoding='utf-8'))
    assert spec.contract == 1 and spec.script == 'format.py'
    assert spec.requires == '>=0.1.0'
    assert _contract.satisfies(__version__, spec.requires)
    # A person's format plays in a process of its own. The arena starts one for each match and the
    # others keep one for a run.
    assert spec.process.host == 'format'
    assert spec.process.scope == ('match' if name == 'arena' else 'run')
    _ceilings.check_ranges(spec)


def test_the_arena_declares_the_strict_defaults_and_a_mean_that_is_always_reachable():
    spec = _contract.parse((EXAMPLES / 'arena' / 'format.toml').read_text(encoding='utf-8'))
    values = _contract.options(spec, {})
    assert (
        values['load_seconds'],
        values['first_call_seconds'],
        values['call_seconds'],
        values['mean_ms'],
        values['reset_seconds'],
        spec.process.threads,
        spec.process.match_minutes,
    ) == (30, 10, 1, 20, 5, 1, 60)
    assert spec.options['call_seconds'].range[1] == 1.1
    assert values['seed_globals'] is False and spec.options['check_matches'].reaches == 'check'
    # The call limits at their largest still leave the match inside the ceiling of match_minutes.
    assert _ceilings.worst_seconds(spec) <= spec.process.match_minutes * 60
    assert 'check_matches' not in _contract.playing(spec, values)


def test_the_examples_join_the_menu_beside_the_built_in_formats(formats_added):
    found = [format.id for format in _registry.formats()]
    assert found == ['kaggle', 'strict', *sorted(NAMES)]
    assert formats_added['arena'].spec.name == 'Arena 5 vs 5'
    assert all(not format.builtin for format in formats_added.values())


# Arena 5 vs 5


def test_the_arena_accepts_its_sample(formats_added, tmp_path):
    folder = _copy(SAMPLES / 'arena', tmp_path, 'sample')
    verdict = _check(formats_added['arena'], folder)
    assert verdict.accepted, verdict
    assert (verdict.players, verdict.name, verdict.deterministic) == (1, 'sample-scripted', True)
    assert verdict.problems == ()


def test_the_arena_plays_the_full_check_against_the_random_side_and_the_built_in_ai(
    formats_added, tmp_path
):
    folder = _copy(SAMPLES / 'arena', tmp_path, 'sample')
    verdict = _check(formats_added['arena'], folder, full=True)
    assert verdict.accepted, verdict
    notes = {found.detail: found for found in verdict.problems}
    assert {'match.random', 'match.builtin', 'mean'} <= set(notes)
    assert notes['mean'].level == 'note' and 'ms after the first call' in notes['mean'].message
    assert ' to ' in notes['match.random'].message and ' steps' in notes['match.random'].message


@pytest.mark.skipif(sys.platform != 'linux', reason='exercises the macOS sampler on Linux')
def test_a_full_check_samples_its_match_tree_outside_the_spawn_guard(
    formats_added, tmp_path, monkeypatch, capfd
):
    folder = _copy(SAMPLES / 'arena', tmp_path, 'sample')
    monkeypatch.setattr(_tree, 'sys', SimpleNamespace(platform='darwin', executable=sys.executable))
    bootstrap = _launch.BOOTSTRAP.replace(
        'from cuju.arena.formats.v1 import _child',
        'from cuju.arena._formats import tree\n'
        'tree.sys = types.SimpleNamespace(platform="darwin", executable=sys.executable)\n'
        'from cuju.arena.formats.v1 import _child',
    )
    monkeypatch.setattr(_launch, 'BOOTSTRAP', bootstrap)
    nested_pids = set()
    match_sampled = Event()
    attach = _tree.Tree.attach
    sample = _tree._ps_sample

    def watch(tree, process, parent=None):
        if parent is not None:
            nested_pids.add(process.pid)
        attach(tree, process, parent=parent)
        if parent is not None:
            # The checker waits for the host to answer before starting the match.
            assert match_sampled.wait(10), 'the host did not sample the nested match tree'

    def record(pid, table):
        found = sample(pid, table)
        if pid in nested_pids and pid in found.pids:
            match_sampled.set()
        return found

    monkeypatch.setattr(_tree.Tree, 'attach', watch)
    monkeypatch.setattr(_tree, '_ps_sample', record)
    with _checks.capped(30):
        verdict = _check(formats_added['arena'], folder, full=True)

    output = capfd.readouterr().err
    assert 'the submission started a process' not in output, output
    assert verdict.accepted, verdict
    assert {'match.random', 'match.builtin'} <= {note.detail for note in verdict.problems}
    assert match_sampled.is_set()


def _arena_broken(tmp_path, change):
    folder = _copy(SAMPLES / 'arena', tmp_path, 'broken')
    change(folder)
    return folder


@pytest.mark.parametrize(
    ('change', 'kind', 'detail'),
    [
        (lambda folder: (folder / 'agent.py').unlink(), 'load', 'layout'),
        (lambda folder: (folder / 'manifest.yml').unlink(), 'load', 'manifest'),
        (
            lambda folder: (folder / 'manifest.yml').write_text(
                'team: a\ncontrolled_players: 9\n', encoding='utf-8'
            ),
            'load',
            'manifest.players',
        ),
        (
            lambda folder: (folder / 'manifest.yml').write_text(
                'controlled_players: 1\n', encoding='utf-8'
            ),
            'load',
            'manifest.team',
        ),
        (
            lambda folder: (folder / 'manifest.yml').write_text(
                'team: a\ncontrolled_players: 1\ndeterministic: maybe\n',
                encoding='utf-8',
            ),
            'load',
            'manifest.deterministic',
        ),
        (
            lambda folder: (folder / 'agent.py').write_text(
                'class Other:\n    pass\n', encoding='utf-8'
            ),
            'load',
            'agent',
        ),
        (
            lambda folder: (folder / 'agent.py').write_text(
                'raise RuntimeError("no")\n', encoding='utf-8'
            ),
            'crash',
            None,
        ),
    ],
)
def test_a_broken_arena_sample_is_refused_with_the_right_problem(
    formats_added, tmp_path, change, kind, detail
):
    verdict = _check(formats_added['arena'], _arena_broken(tmp_path, change))
    assert not verdict.accepted
    refusal = next(found for found in verdict.problems if found.level == 'error')
    assert refusal.kind == kind
    if detail is not None:
        assert refusal.detail == detail


def test_the_arena_refuses_another_scenario_and_a_folder_past_its_limit(formats_added, tmp_path):
    arena = formats_added['arena']
    folder = _copy(SAMPLES / 'arena', tmp_path, 'sample')
    verdict = _check(arena, folder, size='11v11')
    assert (verdict.problems[0].kind, verdict.problems[0].detail) == ('rule', 'scenario')
    (folder / 'weights.bin').write_bytes(bytes(2 * 2**20))
    verdict = _check(arena, folder, folder_mib=1)
    assert (verdict.problems[0].kind, verdict.problems[0].detail) == ('rule', 'size')
    assert '2.0 MiB, the limit is 1 MiB' in verdict.problems[0].message


def test_a_submission_that_imports_gfootball_needs_the_alias_and_the_refusal_says_so(
    formats_added, tmp_path
):
    folder = _copy(SAMPLES / 'arena', tmp_path, 'importing')
    agent = folder / 'agent.py'
    agent.write_text('import gfootball.env\n' + agent.read_text(encoding='utf-8'), encoding='utf-8')
    verdict = _check(formats_added['arena'], folder)
    if importlib.util.find_spec('gfootball') is None:
        assert not verdict.accepted and verdict.environment
        assert 'install cuju[gfootball]' in verdict.problems[0].message
    else:
        assert verdict.accepted


def test_the_arena_plays_its_sample_and_seeds_the_generators_on_request(
    formats_added, tmp_path, arena_home
):
    team = _registered(_copy(SAMPLES / 'arena', tmp_path, 'sample'), 'sample')
    plain = _actions(team, 'arena', steps=30)
    assert len(plain) == 30 and plain == _actions(team, 'arena', steps=30)
    seeded = {'id': 'arena', 'options': {'seed_globals': True}}
    assert _actions(team, seeded, steps=30) == _actions(team, seeded, steps=30)


def _sleepy(tmp_path, seconds):
    folder = _copy(SAMPLES / 'arena', tmp_path, 'sleepy')
    agent = folder / 'agent.py'
    source = agent.read_text(encoding='utf-8').replace(
        'def act(self, observations):\n',
        f'def act(self, observations):\n        time.sleep({seconds})\n',
    )
    agent.write_text('import time\n' + source, encoding='utf-8')
    return _registered(folder, 'sleepy')


def test_the_arenas_mean_forfeit_fires_on_a_sample_that_sleeps(formats_added, tmp_path):
    """Each call takes 50 ms, inside every call limit and past the mean of 20 ms."""
    team = _sleepy(tmp_path, 0.05)
    rule = _rule(team, 'arena', steps=30)
    assert (rule['kind'], rule['side']) == ('rule', 'home')
    assert rule['detail'] == 'mean' and rule['step'] == 30


def test_the_arenas_call_limit_forfeits_a_slow_call_and_its_options_reach_play(
    formats_added, tmp_path
):
    team = _sleepy(tmp_path, 0.4)
    options = {'id': 'arena', 'options': {'call_seconds': 0.1, 'first_call_seconds': 5}}
    rule = _rule(team, options, steps=10)
    assert (rule['kind'], rule['side']) == ('timeout', 'home') and rule['step'] == 1


# Policy


def test_policy_accepts_its_sample_and_plays_it(formats_added, weights, tmp_path):
    folder = _copy(weights / 'policy', tmp_path, 'sample')
    verdict = _check(formats_added['policy'], folder)
    assert verdict.accepted, verdict
    assert (verdict.players, verdict.deterministic) == (1, True)
    assert verdict.problems[0].detail == 'smoke'
    team = _registered(folder, 'policy-sample')
    first = _actions(team, 'policy', steps=40)
    assert len(first) == 40 and first == _actions(team, 'policy', steps=40)


def test_policy_samples_its_actions_from_the_matchs_seed(formats_added, weights, tmp_path):
    folder = _copy(weights / 'policy', tmp_path, 'sampling')
    (folder / 'policy.toml').write_text(
        (folder / 'policy.toml')
        .read_text(encoding='utf-8')
        .replace('sample = false', 'sample = true'),
        encoding='utf-8',
    )
    verdict = _check(formats_added['policy'], folder)
    assert verdict.accepted and verdict.deterministic is False
    team = _registered(folder, 'policy-sampling')
    assert _actions(team, 'policy', steps=40, seed=5) == _actions(team, 'policy', steps=40, seed=5)


@pytest.mark.parametrize(
    ('change', 'detail'),
    [
        (lambda folder: (folder / 'policy.toml').unlink(), 'policy.toml'),
        (lambda folder: (folder / 'model.pt').unlink(), 'policy.toml'),
        (
            lambda folder: (folder / 'policy.toml').write_text(
                'model = "model.pt"\nrepresentation = "pixels"\n',
                encoding='utf-8',
            ),
            'policy.toml',
        ),
    ],
)
def test_a_broken_policy_sample_is_refused_as_a_load_problem(
    formats_added, weights, tmp_path, change, detail
):
    folder = _copy(weights / 'policy', tmp_path, 'broken')
    change(folder)
    verdict = _check(formats_added['policy'], folder)
    assert not verdict.accepted
    assert (verdict.problems[0].kind, verdict.problems[0].detail) == ('load', detail)


def test_policy_plays_the_active_player_alone(formats_added, weights, tmp_path):
    folder = _copy(weights / 'policy', tmp_path, 'sample')
    verdict = _check(formats_added['policy'], folder, control=formats.Control(players=2))
    assert (verdict.problems[0].kind, verdict.problems[0].detail) == ('rule', 'players')


def test_policy_on_cuda_is_refused_where_no_gpu_is_visible(arena_home, weights, tmp_path):
    """A copy of the format that hides every GPU stands in for a machine without one."""
    blind = _copied(EXAMPLES / 'policy', tmp_path / 'blind')
    toml = blind / 'format.toml'
    toml.write_text(
        toml.read_text(encoding='utf-8').replace('gpu = "allowed"', 'gpu = "never"'),
        encoding='utf-8',
    )
    format = _registry.add(blind)
    folder = _copy(weights / 'policy', tmp_path, 'gpu')
    shutil.copy(SAMPLES / 'policy' / 'policy-gpu.toml', folder / 'policy.toml')
    verdict = _check(format, folder)
    assert not verdict.accepted and verdict.environment
    assert (verdict.problems[0].kind, verdict.problems[0].detail) == ('load', 'gpu')


@pytest.mark.skipif(not GPU, reason='a GPU run holds the heavy lock, set CUJU_EXAMPLES_GPU=1')
def test_policy_on_cuda_accepts_and_reports_itself_as_not_deterministic(
    formats_added, weights, tmp_path
):
    torch = pytest.importorskip('torch')
    if not torch.cuda.is_available():
        pytest.skip('no GPU is visible')
    folder = _copy(weights / 'policy', tmp_path, 'gpu')
    shutil.copy(SAMPLES / 'policy' / 'policy-gpu.toml', folder / 'policy.toml')
    verdict = _check(formats_added['policy'], folder)
    assert verdict.accepted, verdict
    assert verdict.deterministic is False


def test_policy_reads_an_onnx_file_through_onnxruntime(formats_added, weights, tmp_path):
    pytest.importorskip('onnxruntime')
    torch = pytest.importorskip('torch')
    pytest.importorskip('onnx')
    folder = _copy(weights / 'policy', tmp_path, 'onnx')
    network = torch.jit.load(str(folder / 'model.pt'))
    torch.onnx.export(network, (torch.zeros(1, 460),), str(folder / 'model.onnx'), dynamo=False)
    text = (folder / 'policy.toml').read_text(encoding='utf-8').replace('model.pt', 'model.onnx')
    (folder / 'policy.toml').write_text(text, encoding='utf-8')
    assert _check(formats_added['policy'], folder).accepted


# Squad

SQUAD = _control(3, 'fixed', ['CB', 'CM', 'CF'], goalkeeper='builtin')


def test_squad_accepts_its_sample_and_plays_each_role_with_its_own_policy(
    formats_added, weights, tmp_path
):
    folder = _copy(weights / 'squad', tmp_path, 'sample')
    control = formats.Control.from_dict(SQUAD)
    # The check match plays the roles the control selects and no other, so the sample, which holds
    # a policy for exactly CB, CM and CF, needs no fallback.
    verdict = _check(formats_added['squad'], folder, size='11v11', control=control)
    assert verdict.accepted, verdict
    assert verdict.problems[0].message == '20 steps, 3 policies'
    team = _registered(folder, 'squad-sample', '11v11', SQUAD)
    actions = _actions(team, 'squad', '11_vs_11_kaggle', steps=30)
    # The engine takes 19, the built-in AI's action, for every player the team does not control,
    # so the three chosen players are the only entries that hold an action of the team's own.
    own = [[action for action in step if action != 19] for step in actions]
    assert len(actions) == 30 and max(map(len, own)) <= 3 and any(own)
    assert actions == _actions(team, 'squad', '11_vs_11_kaggle', steps=30)


@pytest.mark.parametrize(
    ('control', 'detail'),
    [
        (formats.Control(players=3), 'control'),
        (formats.Control(players=2, assignment='fixed', selection=(1, 2)), 'control'),
        (formats.Control(players=2, assignment='fixed', selection=('CB', 'LB')), 'roles'),
    ],
)
def test_squad_refuses_a_control_that_names_no_policy_for_each_role(
    formats_added, weights, tmp_path, control, detail
):
    folder = _copy(weights / 'squad', tmp_path, 'sample')
    verdict = _check(formats_added['squad'], folder, size='11v11', control=control)
    assert not verdict.accepted
    assert verdict.problems[0].detail == detail


def test_squad_refuses_a_missing_policy_file(formats_added, weights, tmp_path):
    folder = _copy(weights / 'squad', tmp_path, 'broken')
    (folder / 'policies' / 'forward.pt').unlink()
    verdict = _check(
        formats_added['squad'], folder, size='11v11', control=formats.Control.from_dict(SQUAD)
    )
    assert (verdict.problems[0].kind, verdict.problems[0].detail) == ('load', 'squad.toml')
    assert 'forward.pt, is missing' in verdict.problems[0].message


# Bot


def test_bot_accepts_its_sample_and_replays_from_the_seed(formats_added, tmp_path):
    folder = _copy(SAMPLES / 'bot', tmp_path, 'sample')
    verdict = _check(formats_added['bot'], folder)
    assert verdict.accepted, verdict
    team = _registered(folder, 'bot-sample')
    first = _actions(team, 'bot', steps=60, seed=7)
    assert len(first) == 60 and first == _actions(team, 'bot', steps=60, seed=7)
    assert first != _actions(team, 'bot', steps=60, seed=8)


@pytest.mark.parametrize(
    ('source', 'detail'),
    [(None, 'entry'), ('def other(observation):\n    return "idle"\n', 'entry')],
)
def test_a_broken_bot_sample_is_refused_at_its_entry(formats_added, tmp_path, source, detail):
    folder = _copy(SAMPLES / 'bot', tmp_path, 'broken')
    if source is None:
        (folder / 'bot.py').unlink()
    else:
        (folder / 'bot.py').write_text(source, encoding='utf-8')
    verdict = _check(formats_added['bot'], folder)
    assert (verdict.problems[0].kind, verdict.problems[0].detail) == ('load', detail)


def test_a_bot_that_names_no_action_forfeits_as_invalid(formats_added, tmp_path):
    folder = _copy(SAMPLES / 'bot', tmp_path, 'dizzy')
    (folder / 'bot.py').write_text(
        'def decide(observation):\n    return "dance"\n', encoding='utf-8'
    )
    verdict = _check(formats_added['bot'], folder)
    assert not verdict.accepted and verdict.problems[0].kind == 'invalid'
    rule = _rule(_registered(folder, 'dizzy'), 'bot', steps=10)
    assert (rule['kind'], rule['side']) == ('invalid', 'home')


def test_the_samples_hold_no_binary_file_and_the_scripts_write_the_weights(weights):
    kept = sorted(
        p.suffix for p in SAMPLES.rglob('*') if p.is_file() and '__pycache__' not in p.parts
    )
    assert not {'.pt', '.onnx', '.bin', '.pkl'} & set(kept)
    assert (weights / 'policy' / 'model.pt').is_file()
    assert sorted(p.name for p in (weights / 'squad' / 'policies').iterdir()) == [
        'defender.pt',
        'forward.pt',
        'midfielder.pt',
    ]


# The guide


def _guide_blocks(heading):
    """Returns the first toml block and the first python block after a heading of the guide."""
    text = (ROOT / 'docs' / 'formats.md').read_text(encoding='utf-8')
    after = text[text.index(heading) :]
    blocks = {}
    for kind in ('toml', 'python'):
        start = after.index(f'```{kind}\n') + len(kind) + 4
        blocks[kind] = after[start : after.index('```', start)]
    return blocks


def test_the_smallest_format_of_the_guide_is_added_accepts_and_plays(arena_home, tmp_path):
    """The twenty lines of the guide are a format, so the guide can never show broken code."""
    blocks = _guide_blocks('### The smallest format')
    folder = tmp_path / 'plain'
    folder.mkdir()
    (folder / 'format.toml').write_text(blocks['toml'] + 'requires = ">=0.1"\n', encoding='utf-8')
    (folder / 'format.py').write_text(blocks['python'], encoding='utf-8')
    format = _registry.add(folder)
    sample = tmp_path / 'main'
    sample.mkdir()
    (sample / 'main.py').write_text('def agent(obs):\n    return [5]\n', encoding='utf-8')
    assert _check(format, sample).accepted
    empty = tmp_path / 'empty'
    empty.mkdir()
    assert not _check(format, empty).accepted
    team = _teams.add_folder(sample, 'plain-sample', '5v5')['id']
    actions = _actions(team, 'plain', steps=20)
    assert len(actions) == 20 and all(list(step) == [5] for step in actions)
