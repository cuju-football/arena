# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The two hosts of submission formats, lockstep with mixed formats, the stops and blame."""

import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

import cuju
from cuju.arena._format import recording as _recording
from cuju.arena._formats import ceilings as _ceilings
from cuju.arena._formats import checks as _checks
from cuju.arena._formats import host as _host
from cuju.arena._formats import launch as _launch
from cuju.arena._formats import pool as _pool
from cuju.arena._formats import registry as _registry
from cuju.arena._playing import play as _play
from cuju.arena._playing.errors import PolicyError
from cuju.arena.formats import v1 as formats
from support.common import _toy_team

PROBE = Path(__file__).parents[1] / 'support' / 'formats' / 'probe'
AGENT = 'def agent(obs):\n    return [5 if obs["step"] % 3 else 13]\n'


@pytest.fixture
def probe(arena_home):
    """The Probe format, added to the home as a person's format, which plays in a process."""
    return _registry.add(PROBE)


@pytest.fixture
def inprobe(tmp_path, monkeypatch):
    """The Probe format as a built-in one, which plays in the match process."""
    root = tmp_path / 'builtin'
    # The shipped built-ins stay beside it, since a side that chooses no format plays Kaggle open.
    shutil.copytree(_registry.BUILTIN, root, ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(PROBE, root / 'inprobe')
    toml = root / 'inprobe' / 'format.toml'
    toml.write_text(
        toml.read_text(encoding='utf-8').replace('[process]\n', '[process]\nhost = "match"\n'),
        encoding='utf-8',
    )
    monkeypatch.setattr(_registry, 'BUILTIN', root)
    return _registry.get('inprobe')


def _actions(left, right, formats=None, steps=40, seed=3):
    replay = {}
    _play.play(
        left,
        right,
        '5_vs_5',
        seed=seed,
        steps=steps,
        save=False,
        replay=replay,
        submission_format=formats,
    )
    return replay['actions']


def _forfeit(left, mode, which='probe', after=3, **options):
    with pytest.raises(_play.ForfeitError) as caught:
        _actions(
            left,
            'builtin',
            [{'id': which, 'options': {'mode': mode, 'after': after}}, None],
            **options,
        )
    return caught.value.rule


def _blocking_match_forfeit(team, mode):
    """Reads the forfeit of a match process whose blocked call never returns."""
    script = """
import sys
from pathlib import Path
from cuju.arena._formats import host, registry
from cuju.arena._playing import play

registry.BUILTIN = Path(sys.argv[1])
host.LOAD_SECONDS = host.ANSWER_SECONDS = 1.5
formats = [{'id': 'inprobe', 'options': {'mode': sys.argv[3], 'after': 3}}, None]
play.play(sys.argv[2], 'builtin', '5_vs_5', seed=3, steps=40, save=False, submission_format=formats)
"""
    found = subprocess.run(
        [sys.executable, '-I', '-c', script, str(_registry.BUILTIN), team, mode],
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=30,
        check=False,
        env=_ceilings.match_environment(),
    )
    assert found.returncode == _ceilings.EXIT_CEILING, found.stderr
    words = _ceilings.read_last_words(found.stderr.splitlines())
    assert words is not None, found.stderr
    return _ceilings.rule(words, [team, 'builtin'])


@pytest.mark.parametrize('which', ['probe', 'inprobe'])
def test_a_side_under_a_format_plays_exactly_as_the_team_plays_without_one(
    arena_home, tmp_path, probe, inprobe, which
):
    """A format that plays a Kaggle main.py gives the same actions as cuju-render before formats."""
    team = _toy_team(tmp_path, 'plain', AGENT, '5v5')['id']
    assert _actions(team, 'builtin', [which, None]) == _actions(team, 'builtin')


def test_mixed_formats_meet_in_one_match_in_lockstep(arena_home, tmp_path, probe, inprobe):
    """A side in a format process meets a side in the match process and a side without a format."""
    left = _toy_team(tmp_path, 'left', AGENT, '5v5')['id']
    right = _toy_team(tmp_path, 'right', AGENT.replace('13', '12'), '5v5')['id']
    plain = _actions(left, right)
    assert _actions(left, right, ['probe', 'inprobe']) == plain
    assert _actions(left, right, ['inprobe', None]) == plain
    assert _actions(left, right, 'probe') == plain


def test_a_child_of_a_format_answers_in_the_submissions_folder(arena_home, tmp_path, probe):
    """A format's `Child` passes every answer through a process of its own, unchanged."""
    team = _toy_team(tmp_path, 'child', AGENT, '5v5')['id']
    chosen = [{'id': 'probe', 'options': {'mode': 'child'}}, None]
    assert _actions(team, 'builtin', chosen, steps=15) == _actions(team, 'builtin', steps=15)


def test_a_recording_and_its_ratings_row_keep_the_format(arena_home, tmp_path, probe):
    """A requested match under a format names it in its provenance and its row of evidence."""
    from cuju.arena._store import ratings as _store

    team = _toy_team(tmp_path, 'kept', AGENT, '5v5')['id']
    path = _play.play(team, 'builtin', '5_vs_5', seed=5, length=60, submission_format='probe')
    provenance = _recording.read_header(path)['provenance']
    kept = provenance['submission_format']
    assert kept[0]['id'] == 'probe' and kept[1]['id'] == 'probe'
    assert kept[0]['identity']['format'] == 'probe'
    assert kept[0]['identity']['options'] == {'mode': 'play', 'after': 3}
    row = _store.results()[-1]
    side = row['home'] if row['home']['key'] == team else row['away']
    assert side['submission_format']['format'] == 'probe'
    assert len(side['format']) == 12
    assert 'numpy' in side['versions']
    assert set(row['versions']) == {'cuju', 'cuju-arena', 'numpy'}
    assert _store.model_of(side).endswith('~' + side['format'])


@pytest.mark.parametrize('which', ['probe', 'inprobe'])
def test_the_format_decides_its_forfeits(arena_home, tmp_path, probe, inprobe, which):
    """A format's forfeit ends the match at its step, against its team, with its forfeit score."""
    team = _toy_team(tmp_path, 'judged', AGENT, '5v5')['id']
    rule = _forfeit(team, 'forfeit', which)
    assert (rule['side'], rule['kind'], rule['detail'], rule['step']) == (
        'home',
        'rule',
        'probe',
        3,
    )
    assert rule['forfeit_score'] == [0, 3]
    assert rule['submission_format'] == _registry.get(which).label
    assert 'the probe forfeits at step 3' in rule['reason']
    assert _forfeit(team, 'invalid', which)['kind'] == 'invalid'
    ended = _forfeit(team, 'end_forfeit', which, after=10**5)
    assert (ended['kind'], ended['detail']) == ('rule', 'mean')


@pytest.mark.parametrize('which', ['probe', 'inprobe'])
def test_an_error_of_the_submission_is_the_teams_crash(arena_home, tmp_path, probe, inprobe, which):
    """Blame by the innermost owned frame, the submission's code forfeits its team."""
    source = 'def agent(obs):\n    if obs["step"] == 2:\n        raise ValueError("boom")\n'
    source += '    return [5]\n'
    team = _toy_team(tmp_path, 'crashing', source, '5v5')['id']
    with pytest.raises(_play.ForfeitError) as caught:
        _actions(team, 'builtin', [which, None])
    assert (caught.value.rule['kind'], caught.value.rule['side']) == ('crash', 'home')
    assert 'ValueError: boom' in caught.value.rule['reason']


@pytest.mark.parametrize('which', ['probe', 'inprobe'])
def test_an_error_of_the_formats_code_blames_no_team(arena_home, tmp_path, probe, inprobe, which):
    """The format's own failure gives no result, and marks the format failing."""
    team = _toy_team(tmp_path, 'blameless', AGENT, '5v5')['id']
    options = [{'id': which, 'options': {'mode': 'format_error'}}, None]
    with pytest.raises(PolicyError) as caught:
        _play.play(
            team,
            'builtin',
            '5_vs_5',
            seed=1,
            steps=20,
            save=False,
            submission_format=options,
            policy_errors=True,
        )
    assert caught.value.message_id == 'server.formats.failed'
    assert 'the probe broke on purpose' in _registry.failing(which)['message']


@pytest.mark.parametrize(
    ('which', 'mode'), [('probe', 'stall'), ('inprobe', 'spin'), ('inprobe', 'stall')]
)
def test_a_side_without_an_answer_forfeits_at_the_answer_ceiling(
    arena_home, tmp_path, probe, inprobe, monkeypatch, which, mode
):
    """No stall voids a loss, the side whose answer is missing forfeits as `ceiling.answer`."""
    monkeypatch.setattr(_host, 'ANSWER_SECONDS', 1.5)
    team = _toy_team(tmp_path, 'stalling', AGENT, '5v5')['id']
    if os.name == 'nt' and which == 'inprobe' and mode == 'stall':
        rule = _blocking_match_forfeit(team, mode)
    else:
        rule = _forfeit(team, mode, which)
    assert (rule['kind'], rule['side'], rule['step']) == ('ceiling.answer', 'home', 3)


def test_a_format_process_that_ends_forfeits_as_a_crash(arena_home, tmp_path, probe):
    team = _toy_team(tmp_path, 'exiting', AGENT, '5v5')['id']
    rule = _forfeit(team, 'exit')
    assert (rule['kind'], rule['side']) == ('ceiling.crash', 'home')


@pytest.mark.skipif(os.name != 'nt', reason='Windows uses a virtual environment launcher')
def test_a_windows_format_handle_belongs_to_the_virtual_environments_interpreter(
    tmp_path, monkeypatch
):
    virtual = tmp_path / 'venv'
    venv.create(virtual, with_pip=False)
    executable = virtual / 'Scripts' / 'python.exe'
    monkeypatch.setattr(sys, 'executable', str(executable))
    monkeypatch.setattr(
        _launch,
        'BOOTSTRAP',
        'import json, os, sys\n'
        'from pathlib import Path\n'
        'interpreter = [os.getpid(), sys.executable, sys.prefix]\n'
        'Path("interpreter.json").write_text(json.dumps(interpreter))',
    )
    process = _launch.Process(tmp_path, _launch.environment(), subprocess.DEVNULL)
    try:
        assert process.process.wait(10) == 0
        pid, child_executable, prefix = json.loads(
            (tmp_path / 'interpreter.json').read_text(encoding='utf-8')
        )
        assert pid == process.pid
        assert Path(child_executable) == executable and Path(prefix) == virtual
    finally:
        process.close()


def test_a_formats_answer_reader_ends_with_its_child(tmp_path, monkeypatch):
    """The answer pipe ends with its writer while a grandchild still runs."""
    inheritable_dup = """\
import os
duplicate = os.dup

def inheritable_dup(fd):
    result = duplicate(fd)
    os.set_inheritable(result, True)
    return result

os.dup = inheritable_dup
"""
    grandchild = """\
from pathlib import Path
import time

Path('running').touch()
deadline = time.monotonic() + 30
while not Path('release').exists() and time.monotonic() < deadline:
    time.sleep(0.01)
Path('stopped').touch()
"""
    bootstrap = _launch.BOOTSTRAP.split('import cuju\n', 1)[0]
    # Exercise an inheritable duplicate of standard output on every system.
    monkeypatch.setattr(
        _launch,
        'BOOTSTRAP',
        inheritable_dup
        + bootstrap
        + f'import subprocess\nsubprocess.Popen([sys.executable, "-c", {grandchild!r}], '
        'close_fds=False)\n'
        'from pathlib import Path\n'
        'import time\n'
        'while not Path("running").exists():\n'
        '    time.sleep(0.01)\n',
    )
    process = _launch.Process(tmp_path, _launch.environment(), subprocess.DEVNULL)
    try:
        assert process.process.wait(10) == 0
        with pytest.raises(_launch.Ended):
            process.receive(1)
        process._reader.join(1)
        assert not process._reader.is_alive()
        assert not (tmp_path / 'stopped').exists()
    finally:
        (tmp_path / 'release').touch()
        if process.alive():
            process.process.kill()
            process.process.wait(10)
        process._reader.join(10)
        if not process._reader.is_alive():
            process.close()


@pytest.mark.parametrize('which', ['probe', 'inprobe'])
def test_a_load_past_its_ceiling_forfeits(arena_home, tmp_path, probe, inprobe, monkeypatch, which):
    monkeypatch.setattr(_host, 'LOAD_SECONDS', 1.5)
    team = _toy_team(tmp_path, 'loading', AGENT, '5v5')['id']
    if os.name == 'nt' and which == 'inprobe':
        rule = _blocking_match_forfeit(team, 'load_stall')
    else:
        rule = _forfeit(team, 'load_stall', which)
    assert (rule['kind'], rule['side'], rule['step']) == ('ceiling.load', 'home', 0)


def test_a_side_past_the_formats_match_minutes_forfeits(arena_home, tmp_path):
    folder = tmp_path / 'brief'
    shutil.copytree(PROBE, folder)
    toml = folder / 'format.toml'
    text = toml.read_text(encoding='utf-8').replace('match_minutes = 60', 'match_minutes = 0.0002')
    toml.write_text(text, encoding='utf-8')
    _registry.add(folder)
    team = _toy_team(tmp_path, 'slow', AGENT, '5v5')['id']
    rule = _forfeit(team, 'play', 'brief', steps=200)
    assert (rule['kind'], rule['side']) == ('ceiling.time', 'home')


def test_the_pre_check_and_its_check_matches(arena_home, tmp_path, probe, inprobe):
    """`accept` runs in a checker, and `test_match` plays through the same format."""
    team = _toy_team(tmp_path, 'checked', AGENT, '5v5')
    folder = arena_home / 'teams' / team['id']
    for which in (probe, inprobe):
        values = {'mode': 'play', 'after': 3, 'check_steps': 12}
        checked = formats.Team('checked', '5v5', None, '5_vs_5', formats.Control(), None, values)
        verdict = _checks.check(which, values, folder, checked)
        assert verdict.accepted, verdict
        assert [found.message for found in verdict.problems] == [
            'probe read main.py',
            '12 steps, same True',
        ]
    empty = tmp_path / 'empty'
    empty.mkdir()
    verdict = _checks.check(probe, {}, empty, checked)
    assert not verdict.accepted and verdict.problems[0].kind == 'load'


def test_a_check_match_runs_its_single_environment_on_the_calling_thread(
    arena_home, tmp_path, inprobe, monkeypatch
):
    team = _toy_team(tmp_path, 'checked', AGENT, '5v5')
    folder = arena_home / 'teams' / team['id']
    values = {'mode': 'play', 'after': 3, 'check_steps': 0}
    checked = formats.Team('checked', '5v5', None, '5_vs_5', formats.Control(), None, values)
    start = cuju.BatchEnvironment
    batches = []

    def batch(*args, **kwargs):
        found = start(*args, **kwargs)
        batches.append((kwargs['environment_count'], kwargs.get('threads'), found.threads))
        return found

    monkeypatch.setattr(cuju, 'BatchEnvironment', batch)
    match = _checks.play_side(inprobe, folder, checked, 'builtin', 'left', 1, 0)
    assert match['steps'] == 1
    assert batches == [(1, 1, 1)]


def test_a_checker_can_play_two_check_matches_at_once(arena_home, tmp_path):
    folder = tmp_path / 'parallel'
    shutil.copytree(PROBE, folder)
    script = folder / 'format.py'
    with script.open('a', encoding='utf-8') as stream:
        stream.write(
            """

def accept(folder, team):
    from concurrent.futures import ThreadPoolExecutor

    def play(side):
        return formats.test_match(side=side, steps=20)

    with ThreadPoolExecutor(max_workers=2) as pool:
        matches = list(pool.map(play, ('left', 'right')))
    assert [(match.steps, match.forfeit) for match in matches] == [(20, None)] * 2
    return formats.accept(players=1)
"""
        )
    format = _registry.add(folder)
    team = _toy_team(tmp_path, 'checked', AGENT, '5v5')
    submission = arena_home / 'teams' / team['id']
    values = {'mode': 'play', 'after': 3, 'check_steps': 0}
    checked = formats.Team('checked', '5v5', None, '5_vs_5', formats.Control(), None, values)
    verdict = _checks.check(format, values, submission, checked)
    assert verdict.accepted, verdict


@pytest.fixture
def runprobe(arena_home, tmp_path):
    """The Probe format with `[process] scope = "run"`, a person's format."""
    folder = tmp_path / 'runprobe'
    shutil.copytree(PROBE, folder)
    toml = folder / 'format.toml'
    text = toml.read_text(encoding='utf-8').replace('[process]\n', '[process]\nscope = "run"\n')
    toml.write_text(text, encoding='utf-8')
    return _registry.add(folder)


def _running(pid):
    """Returns whether a process of that identifier still runs, as the system sees it."""
    if os.name == 'nt':
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    status = Path(f'/proc/{pid}/status')
    try:
        return 'zombie' not in status.read_text(encoding='utf-8')
    except OSError:
        # A system without /proc has answered the signal, and a process gone has no status.
        return not Path('/proc').is_dir()


def _run_match(team, mode='play', which='runprobe', pool=None, **options):
    replay = {}
    chosen = [{'id': which, 'options': {'mode': mode}}, None]
    _play.play(
        team,
        'builtin',
        '5_vs_5',
        seed=3,
        steps=12,
        save=False,
        replay=replay,
        submission_format=chosen,
        **options,
    )
    return replay['actions']


def test_one_process_serves_every_match_of_a_run(arena_home, tmp_path, runprobe, probe):
    """Under `scope = "run"` a side's process plays every match of the run, reset between them."""
    team = _toy_team(tmp_path, 'steady', AGENT, '5v5')['id']
    fresh = _run_match(team, which='probe')
    with _pool.Pool() as run:
        pids = []
        for _ in range(4):
            assert _run_match(team) == fresh
            pids.append(run.pids())
        assert len(run) == 1 and all(found == pids[0] for found in pids)
        held = pids[0][0]
        assert _running(held)
    assert len(run) == 0 and not _running(held)


def test_a_crash_or_a_forfeit_retires_the_process_of_a_run(arena_home, tmp_path, runprobe):
    """The process that crashed in the second match of a run gives way to a fresh one."""
    team = _toy_team(tmp_path, 'unsteady', AGENT, '5v5')['id']
    with _pool.Pool() as run:
        _run_match(team, 'second')
        first = run.pids()
        with pytest.raises(_play.ForfeitError) as caught:
            _run_match(team, 'second')
        assert caught.value.rule['kind'] == 'ceiling.crash'
        assert run.pids() == [] and not _running(first[0])
        _run_match(team, 'second')
        second = run.pids()
        assert len(second) == 1 and second != first
        # The fresh process meets its own second match in the fourth of the run.
        with pytest.raises(_play.ForfeitError):
            _run_match(team, 'second')
        assert run.pids() == []
    with _pool.Pool() as run:
        _run_match(team)
        kept = run.pids()
        with pytest.raises(_play.ForfeitError):
            _run_match(team, 'forfeit')
        assert run.pids() == kept
    assert not _running(kept[0])


def test_the_pool_is_empty_after_a_run_that_an_exception_ended(arena_home, tmp_path, runprobe):
    team = _toy_team(tmp_path, 'stopped', AGENT, '5v5')['id']

    def stop(share):
        raise KeyboardInterrupt

    with pytest.raises(RuntimeError), _pool.Pool() as run:
        _run_match(team)
        held = run.pids()[0]
        raise RuntimeError('the run stops')
    assert len(run) == 0 and not _running(held) and _pool.current() is None
    with pytest.raises(KeyboardInterrupt), _pool.Pool() as run:
        replay = {}
        _play.play(
            team,
            'builtin',
            '5_vs_5',
            seed=3,
            length=60,
            replay=replay,
            progress=stop,
            submission_format=[{'id': 'runprobe'}, None],
        )
    assert len(run) == 0


def test_a_format_of_scope_match_keeps_no_process_in_a_pool(arena_home, tmp_path, probe):
    team = _toy_team(tmp_path, 'brief', AGENT, '5v5')['id']
    with _pool.Pool() as run:
        _run_match(team, which='probe')
        assert len(run) == 0


def test_mixed_kaggle_match_measures_the_other_submissions_tree(
    arena_home, tmp_path, probe, monkeypatch
):
    import os

    from cuju.arena._formats import tree

    left = _toy_team(tmp_path, 'local', AGENT, '5v5')['id']
    right = _toy_team(tmp_path, 'guarded', AGENT.replace('13', '12'), '5v5')['id']
    baseline = _actions(left, right)
    measured = []
    sample = tree.Tree.sample

    def watched(self):
        if self.process is not None:
            measured.append((os.getpid(), self.process.pid))
        return sample(self)

    monkeypatch.setattr(tree.Tree, 'sample', watched)
    assert _actions(left, right, ['kaggle', 'probe']) == baseline
    assert measured and all(owner == os.getpid() and owner != pid for owner, pid in measured)
