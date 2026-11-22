# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The safety ceilings of format processes: the tree, CPU sets, GPUs, threads and the audit hook."""

import dataclasses
import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
import textwrap
import time
import types
from pathlib import Path

import pytest

from cuju.arena._formats import audit as _audit
from cuju.arena._formats import ceilings as _ceilings
from cuju.arena._formats import contract as _contract
from cuju.arena._formats import cpusets as _cpusets
from cuju.arena._formats import gpu as _gpu
from cuju.arena._formats import host as _host
from cuju.arena._formats import launch as _launch
from cuju.arena._formats import pool as _pool
from cuju.arena._formats import registry as _registry
from cuju.arena._formats import threads as _threads
from cuju.arena._formats import tree as _tree
from cuju.arena._playing import play as _play
from cuju.arena._text.messages import MessageError
from cuju.arena.formats import v1 as formats
from support.common import _toy_team

SUPPORT = Path(__file__).parents[1] / 'support' / 'formats'
SAFETY = SUPPORT / 'safety'
PROBE = SUPPORT / 'probe'
AGENT = 'def agent(obs):\n    return [5 if obs["step"] % 3 else 13]\n'
LINUX = sys.platform.startswith('linux')
WINDOWS = os.name == 'nt'

# The formats of design v6, whose files the built-in and example formats ship: Kaggle allows
# 1.1 s a step and a 600 s bank in 90 minutes, League 1.1 s a call and a first call of 300 s in 60.
KAGGLE = """
contract = 1
name = "Kaggle"
script = "format.py"
[process]
host = "match"
gpu = "allowed"
match_minutes = 90
[options.step_seconds]
bounds = "step"
type = "float"
default = 0.5
range = [0.05, 1.1]
[options.overage_seconds]
bounds = "match"
type = "float"
default = 60
range = [0, 600]
"""
LEAGUE = """
contract = 1
name = "League 5 vs 5"
script = "format.py"
[process]
threads = 1
match_minutes = 60
check_minutes = 90
[options.first_call_seconds]
bounds = "step"
type = "float"
default = 10
range = [0.1, 300]
[options.call_seconds]
bounds = "step"
type = "float"
default = 1
range = [0.01, 1.1]
"""


def _spec(text, builtin=True):
    return _contract.parse(textwrap.dedent(text), builtin)


# The time a format allows, against its match_minutes.


@pytest.mark.parametrize('text', [KAGGLE, LEAGUE])
def test_the_kaggle_and_league_files_fit_their_match_minutes(text):
    _ceilings.check_ranges(_spec(text))


def test_every_shipped_and_test_format_fits_its_ranges():
    folders = [SAFETY, PROBE]
    builtin = _registry.builtin_folder()
    if builtin.is_dir():
        folders += [found for found in builtin.iterdir() if (found / _contract.FILE).is_file()]
    for folder in folders:
        _ceilings.check_ranges(_contract.read(folder, builtin=True))


def test_the_worst_time_counts_each_extra_step_option_once():
    """League may take its first call of 300 s once and 1.1 s at every other step."""
    assert _ceilings.worst_seconds(_spec(LEAGUE)) == pytest.approx(300 + 2999 * 1.1)
    assert _ceilings.worst_seconds(_spec(KAGGLE)) == pytest.approx(3000 * 1.1 + 600)
    assert not _ceilings.fits(_spec(KAGGLE), 5000)


def test_a_format_whose_ranges_cannot_be_honored_is_refused_when_it_is_added(arena_home, tmp_path):
    folder = tmp_path / 'greedy'
    shutil.copytree(SAFETY, folder)
    toml = folder / 'format.toml'
    toml.write_text(
        toml.read_text(encoding='utf-8')
        + '\n[options.step_seconds]\nbounds = "step"\ntype = "float"\ndefault = 1\n'
        'range = [0.1, 2]\n',
        encoding='utf-8',
    )
    with pytest.raises(MessageError) as caught:
        _registry.add(folder)
    assert caught.value.message_id == 'server.formats.contract'
    assert 'past match_minutes = 60' in caught.value.values['reason']
    with pytest.raises(_contract.ContractError, match='needs a range'):
        _ceilings.check_ranges(
            _spec(
                'contract = 1\nname = "N"\nscript = "f.py"\n'
                '[options.bank]\nbounds = "match"\ntype = "float"\ndefault = 1\n'
            )
        )


def test_the_ceilings_of_a_tree():
    spec = _contract.read(SAFETY)
    found = _ceilings.limits(spec.process)
    assert (found.memory_mib, found.processes, found.threads) == (1024, 8, 1)
    assert found.thread_limit(1) == 11 and found.thread_limit(3) == 31
    assert (
        _ceilings.limits(_spec('contract = 1\nname = "N"\nscript = "f.py"\n').process).memory_mib
        == 65536
    )


# CPU sets.


def test_cpu_sets_stay_disjoint_until_the_machine_is_full():
    sets = _cpusets.Sets(list(range(8)))
    taken = [sets.acquire(2) for _ in range(3)]
    cpus = [cpu for found in taken for cpu in found.cpus]
    assert len(cpus) == len(set(cpus)) == 6 and 0 not in cpus
    assert not any(found.shared for found in taken)
    crowded = sets.acquire(2)
    assert crowded.shared and len(crowded.cpus) == 2
    for found in [*taken, crowded]:
        sets.release(found)
    assert sets.held() == {}
    assert sets.acquire(1).cpus == (1,)


def test_cpu_sets_spread_over_the_physical_cores_first(tmp_path):
    for cpu in range(4):
        topology = tmp_path / f'cpu{cpu}' / 'topology'
        topology.mkdir(parents=True)
        (topology / 'physical_package_id').write_text('0', encoding='ascii')
        (topology / 'core_id').write_text(str(cpu // 2), encoding='ascii')
    assert _cpusets.order(range(4), tmp_path) == [0, 2, 1, 3]


def test_the_matches_at_once_that_the_cpu_sets_fit():
    assert _cpusets.matches_at_once(1, cpus=12) == 5
    assert _cpusets.matches_at_once([2, 1], cpus=12) == 3
    assert _cpusets.matches_at_once(8, cpus=4) == 1
    from cuju.arena._evaluate import schedule as _schedule

    assert _schedule.fit(8, None) == 8
    assert _schedule.fit(8, 64) == 1


# GPUs.


def test_a_format_that_allows_no_gpu_sees_none():
    hidden = _gpu.environment(False)
    assert hidden['CUDA_VISIBLE_DEVICES'] == '-1' and hidden['JAX_PLATFORMS'] == 'cpu'
    assert _gpu.environment(True, None) == {}


def test_one_gpu_is_shared_equally_and_several_give_each_side_its_own():
    one = _gpu.Shares(['0'])
    first, second = one.acquire(), one.acquire()
    assert first.fraction == second.fraction == _gpu.SHARE
    found = _gpu.environment(True, first)
    assert found['XLA_PYTHON_CLIENT_MEM_FRACTION'] == '0.45'
    assert found['XLA_PYTHON_CLIENT_PREALLOCATE'] == 'false'
    assert found['TF_FORCE_GPU_ALLOW_GROWTH'] == 'true'
    assert found[_gpu.FRACTION_VARIABLE] == '0.4500'
    two = _gpu.Shares(['0', '1'])
    left, right = two.acquire(), two.acquire()
    assert {left.device, right.device} == {'0', '1'} and left.fraction == right.fraction == 1.0
    assert _gpu.FRACTION_VARIABLE not in _gpu.environment(True, left)
    assert _gpu.Shares([]).acquire() is None
    assert _gpu.devices({'CUDA_VISIBLE_DEVICES': '1,3'}) == ['1', '3']
    assert _gpu.devices({'CUDA_VISIBLE_DEVICES': '-1'}) == []


# Threads.


def test_the_thread_variables_name_every_library():
    found = _threads.variables(3)
    assert all(found[name] == '3' for name in _threads.VARIABLES)
    assert 'intra_op_parallelism_threads=3' in found['XLA_FLAGS']
    assert '--xla_cpu_multi_thread_eigen=false' in found['XLA_FLAGS']


def test_the_post_import_hook_limits_torch_once_it_imports(tmp_path):
    """The stand-in records the two thread counts and the share requested when CUDA starts."""
    fake = tmp_path / 'torch'
    (fake / 'cuda').mkdir(parents=True)
    (fake / '__init__.py').write_text(
        'SEEN = []\n'
        'def set_num_threads(n): SEEN.append(("threads", n))\n'
        'def set_num_interop_threads(n): SEEN.append(("interop", n))\n'
        'from . import cuda\n',
        encoding='utf-8',
    )
    (fake / 'cuda' / '__init__.py').write_text(
        'QUEUED = []\n'
        'def _lazy_call(f): QUEUED.append(f)\n'
        'def set_per_process_memory_fraction(f): import torch; torch.SEEN.append(("share", f))\n',
        encoding='utf-8',
    )
    script = (
        'import sys\n'
        'sys.path[:0] = sys.argv[1:]\n'
        'from cuju.arena._formats import threads\n'
        'threads.install(2, 0.45)\n'
        'import torch\n'
        'torch.cuda.QUEUED[0]()\n'
        'print(torch.SEEN, type(torch.__loader__).__name__)\n'
    )
    source = str(Path(_launch.package_folder()).parents[1])
    found = subprocess.run(
        [sys.executable, '-I', '-c', script, str(tmp_path), source],
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=False,
    )
    assert found.returncode == 0, found.stderr
    assert "[('threads', 2), ('interop', 2), ('share', 0.45)]" in found.stdout
    assert '_Loader' not in found.stdout


# The audit hook.


def _guarded(body, tmp_path):
    script = tmp_path / 'guarded.py'
    script.write_text(
        'import sys\n'
        'sys.path.insert(0, sys.argv[1])\n'
        'from cuju.arena._formats import audit\n'
        'audit.install()\n' + textwrap.dedent(body),
        encoding='utf-8',
    )
    source = str(Path(_launch.package_folder()).parents[1])
    return subprocess.run(
        [sys.executable, '-I', str(script), source],
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=False,
    )


def test_the_audit_hook_refuses_a_new_process(tmp_path):
    found = _guarded(
        'import subprocess\nsubprocess.run([sys.executable, "-c", "pass"])\nprint("ran")\n',
        tmp_path,
    )
    assert found.returncode == _ceilings.EXIT_PROCESS == _audit.EXIT_PROCESS
    assert 'ran' not in found.stdout
    assert 'the submission started a process, subprocess.Popen' in found.stderr


def test_the_audit_hook_lets_the_librarys_own_starts_and_ctypes_helpers_through(tmp_path):
    found = _guarded(
        'import subprocess, ctypes.util\n'
        'from cuju.arena._formats import audit\n'
        'with audit.permitted():\n'
        '    subprocess.run([sys.executable, "-c", "pass"], check=True)\n'
        'ctypes.util.find_library("c")\n'
        'print("ran")\n',
        tmp_path,
    )
    assert found.returncode == 0, found.stderr
    assert 'ran' in found.stdout


# The tree, over a fake look.


def _fake_tree(samples, limits=None):
    seen = []
    looks = iter(samples)
    tree = _tree.Tree(
        limits or _ceilings.Limits(memory_mib=100, processes=3, threads=1),
        lambda *found: seen.append(found),
        poll=0.01,
        sampler=lambda pid: next(looks),
    )
    return tree, seen


@pytest.mark.parametrize(
    ('sample', 'kind'),
    [
        (_tree.Sample([1, 2, 3, 4], 8, 10), 'ceiling.process'),
        (_tree.Sample([1], 5, 101 * 1024 * 1024), 'ceiling.memory'),
        (_tree.Sample([1, 2], 22, 10), 'ceiling.threads'),
    ],
)
def test_the_watch_records_each_ceiling_once(sample, kind):
    """As on macOS and every polled system, a look past a ceiling records it once and ends all."""
    ok = _tree.Sample([1], 3, 10)
    tree, seen = _fake_tree([ok, ok, sample, sample])
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        tree.attach(process)
        deadline = time.monotonic() + 10
        while not seen and time.monotonic() < deadline:
            time.sleep(0.01)
        assert [found[0] for found in seen] == [kind]
        assert tree.verdict()[0] == kind
        process.wait(10)
        assert tree.peak_processes >= 1 and tree.figures()['watched']
    finally:
        tree.close()
        if process.poll() is None:
            process.kill()


def test_a_ps_table_reads_as_a_tree():
    table = textwrap.dedent(
        """\
        100     1   100  1000 S
        101   100   100  2000 S
        102   101   102  3000 R
        103     1   100   500 S
        104   100   100   100 Z
        200     1   200  9000 S
        """
    )
    pids, memory = _tree.ps_tree(100, table)
    assert sorted(pids) == [100, 101, 102, 103] and memory == 6500 * 1024
    assert _tree.ps_tree(999, table) == ([], 0)


def test_a_side_cgroup_takes_its_limits_where_cgroups_are_delegated(tmp_path):
    root = tmp_path / 'cgroup'
    own = root / 'user' / 'renderer'
    own.mkdir(parents=True)
    (own / 'cgroup.subtree_control').write_text('cpuset memory pids', encoding='ascii')
    mine = tmp_path / 'self'
    mine.write_text('0::/user/renderer\n', encoding='ascii')
    limits = _ceilings.Limits(memory_mib=512, processes=8, threads=2)
    found = _tree._Cgroup.make(limits, (3, 4), root, mine)
    assert found is not None
    assert (found.folder / 'memory.max').read_text(encoding='ascii') == str(512 * 1024 * 1024)
    assert (found.folder / 'pids.max').read_text(encoding='ascii') == str(2 + 10 * 8)
    assert (found.folder / 'cpuset.cpus').read_text(encoding='ascii') == '3,4'
    (own / 'cgroup.subtree_control').write_text('cpu', encoding='ascii')
    assert _tree._Cgroup.make(limits, None, root, mine) is None


# The tree, on this system.


FORKS = """
import subprocess, sys, time
children = []
for attempt in range(6):
    if attempt == 2:
        sys.stdin.readline()
    try:
        children.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']))
    except OSError as error:
        print('refused', error, flush=True)
        break
time.sleep(60)
"""


def _alive(pid):
    if WINDOWS:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL

        process_query_limited_information = 0x1000
        error_invalid_parameter = 87
        still_active = 259
        handle = kernel.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == error_invalid_parameter:
                return False
            raise ctypes.WinError(error)
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return code.value == still_active
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    status = Path(f'/proc/{pid}/status')
    return not status.is_file() or 'zombie' not in status.read_text(encoding='utf-8')


def test_a_tree_that_forks_past_its_ceiling_is_stopped_and_ended():
    """Linux and macOS watch the count and end the whole tree, Windows refuses the process."""
    seen = []
    tree = _tree.Tree(
        _ceilings.Limits(memory_mib=4096, processes=3, threads=1),
        lambda *found: seen.append(found),
        poll=0.05,
    )
    executable = getattr(sys, '_base_executable', sys.executable) if WINDOWS else sys.executable
    process = subprocess.Popen(
        [executable, '-c', FORKS],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        **tree.popen_options(),
    )
    tree.attach(process)
    try:
        deadline = time.monotonic() + 20
        children = set()
        while len(children) < 2 and time.monotonic() < deadline:
            children = set(tree._known) - {process.pid}
            time.sleep(0.05)
        assert len(children) >= 2
        assert all(_alive(pid) for pid in children)
        process.stdin.write('\n')
        process.stdin.flush()

        deadline = time.monotonic() + 20
        while tree.verdict() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert tree.verdict()[0] == 'ceiling.process'
        tree.kill()
        process.wait(10)
        known = set(tree._known) - {process.pid}
        time.sleep(0.5)
        assert not any(_alive(pid) for pid in known)
        if WINDOWS:
            assert 'processes' in tree.enforced
        elif LINUX:
            assert 'processes' in tree.watched
    finally:
        tree.close()
        if process.poll() is None:
            process.kill()


# The ceilings of a side's host, without a match.


def _side_host(tmp_path, mode, memory=None):
    folder = tmp_path / 'format'
    shutil.copytree(SAFETY, folder)
    if memory is not None:
        toml = folder / 'format.toml'
        text = toml.read_text(encoding='utf-8')
        toml.write_text(text.replace('memory_mib = 1024', f'memory_mib = {memory}'), 'utf-8')
    spec = _contract.read(folder)
    found = types.SimpleNamespace(
        spec=spec, folder=folder, builtin=False, module=None, id='safe', label='safe'
    )
    submission = tmp_path / 'submission'
    submission.mkdir()
    (submission / 'main.py').write_text(AGENT, encoding='utf-8')
    cache = tmp_path / 'cache'
    cache.mkdir()
    match = formats.Match(
        scenario='5_vs_5',
        steps=10,
        seed=1,
        policy_seed=1,
        side='right',
        players=1,
        indices=(6,),
        roles={},
        assignment='dynamic',
        team_action_set='default',
        kind='match',
        options={'mode': mode, 'after': -1},
        gpu=False,
        threads=1,
        folder=submission,
        cache=cache,
    )
    return _host.ProcessHost(found, match)


@pytest.mark.parametrize(
    ('mode', 'memory', 'kind'),
    [
        ('spawn', None, 'ceiling.process'),
        ('children', None, 'ceiling.process'),
        ('threads', None, 'ceiling.threads'),
        ('memory', 512, 'ceiling.memory'),
    ],
)
def test_each_ceiling_stops_a_side_as_it_loads(tmp_path, mode, memory, kind):
    """The host turns each ceiling of the side's tree into its forfeit, on every system."""
    host = _side_host(tmp_path, mode, memory)
    try:
        with pytest.raises(_host.SideForfeit) as caught:
            host.start()
        assert caught.value.kind == kind
        if mode == 'spawn':
            assert (
                caught.value.message == 'the submission started a process, which the arena refuses'
            )
        assert not host.alive()
        # A refused start may end the process before the watch's first look, so the figures
        # name what held the tree rather than a peak.
        figures = host.tree.figures()
        assert figures['enforced'] or figures['watched']
    finally:
        host.close()
    assert _cpusets.SETS.held() == {}


def test_a_side_reports_its_minimal_environment_and_its_cpus(tmp_path, monkeypatch):
    monkeypatch.setenv('SAFETY_SECRET', 'kept')
    monkeypatch.setenv('SAFETY_OTHER', 'left out')
    host = _side_host(tmp_path, 'report')
    host.match = dataclasses.replace(host.match, options={'mode': 'report', 'after': 0})
    try:
        host.start()
        found = json.loads((tmp_path / 'cache' / 'report-right.json').read_text('utf-8'))
        assert found['environment']['SAFETY_SECRET'] == 'kept'
        assert found['environment']['SAFETY_OTHER'] is None
        assert found['child']['OMP_NUM_THREADS'] == '1'
        held = host.tree.cpus
        assert held and _cpusets.parse(found['environment']['CUJU_ARENA_CPUS']) == held
        if WINDOWS:
            assert {'memory', 'processes', 'cpus'} <= set(host.tree.enforced)
        elif LINUX:
            assert set(found['cpus']) == set(held) and found['child']['cpus'] == found['cpus']
    finally:
        host.close()


@pytest.mark.skipif(not WINDOWS, reason='exercises the Windows platform fallback')
def test_a_windows_worker_uses_the_hosts_platform_information(tmp_path):
    bootstrap = _launch.BOOTSTRAP.replace(
        'import os, sys, types\n',
        'import json, os, platform, sys, types\n'
        'def unavailable(*args):\n'
        '    raise OSError("WMI unavailable")\n'
        'platform._wmi_query = unavailable\n',
    ).replace(
        'from cuju.arena.formats.v1 import _child\n_child.main(sys.stdin.buffer, answers)',
        'print(json.dumps(list(platform.uname())), file=sys.stderr)',
    )
    found = subprocess.run(
        [sys.executable, '-I', '-X', 'utf8', '-c', bootstrap],
        cwd=tmp_path,
        env=_launch.environment(),
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=False,
        timeout=30,
    )
    assert found.returncode == 0, found.stderr
    assert json.loads(found.stderr) == list(platform.uname())


# The ceilings in a match, each against the side that passed it.


@pytest.fixture
def safety(arena_home):
    return _registry.add(SAFETY)


def _limited(tmp_path, memory=None):
    folder = tmp_path / 'limited'
    shutil.copytree(SAFETY, folder)
    if memory is not None:
        toml = folder / 'format.toml'
        toml.write_text(
            toml.read_text(encoding='utf-8').replace('memory_mib = 1024', f'memory_mib = {memory}'),
            encoding='utf-8',
        )
    return _registry.add(folder)


def _rule(left, right, formats, steps=30):
    with pytest.raises(_play.ForfeitError) as caught:
        _play.play(
            left, right, '5_vs_5', seed=2, steps=steps, save=False, submission_format=formats
        )
    return caught.value.rule


def _mode(which, mode, after=2):
    return {'id': which, 'options': {'mode': mode, 'after': after}}


@pytest.mark.parametrize(
    ('mode', 'kind'),
    [('spawn', 'ceiling.process'), ('children', 'ceiling.process'), ('threads', 'ceiling.threads')],
)
def test_each_ceiling_forfeits_the_side_that_passed_it(arena_home, tmp_path, safety, mode, kind):
    team = _toy_team(tmp_path, 'greedy', AGENT, '5v5')['id']
    rule = _rule(team, 'builtin', [_mode('safety', mode), None])
    assert (rule['kind'], rule['side']) == (kind, 'home')


def test_a_ceiling_of_the_right_side_forfeits_the_right_side(arena_home, tmp_path, safety):
    left = _toy_team(tmp_path, 'calm', AGENT, '5v5')['id']
    right = _toy_team(tmp_path, 'greedy', AGENT, '5v5')['id']
    rule = _rule(left, right, [_mode('safety', 'play'), _mode('safety', 'spawn')])
    assert (rule['kind'], rule['side']) == ('ceiling.process', 'away')


def test_a_tree_past_its_memory_forfeits_as_ceiling_memory(arena_home, tmp_path):
    _limited(tmp_path, memory=512)
    team = _toy_team(tmp_path, 'hungry', AGENT, '5v5')['id']
    rule = _rule(team, 'builtin', [_mode('limited', 'memory'), None])
    assert (rule['kind'], rule['side']) == ('ceiling.memory', 'home')


@pytest.mark.skipif(not LINUX, reason='ctypes runs its helper process on Linux')
def test_a_helper_of_ctypes_plays_on(arena_home, tmp_path, safety):
    team = _toy_team(tmp_path, 'helped', AGENT, '5v5')['id']
    _play.play(
        team,
        'builtin',
        '5_vs_5',
        seed=2,
        steps=10,
        save=False,
        submission_format=[_mode('safety', 'helper'), None],
    )


def test_two_sides_play_on_disjoint_cpus_with_the_minimal_environment(
    arena_home, tmp_path, safety, monkeypatch
):
    """The thread counts reach the format process and its child, and only the variables of
    `[process] env` pass beside the minimal ones."""
    monkeypatch.setenv('SAFETY_SECRET', 'kept')
    monkeypatch.setenv('SAFETY_OTHER', 'left out')
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    left = _toy_team(tmp_path, 'west', AGENT, '5v5')['id']
    right = _toy_team(tmp_path, 'east', AGENT, '5v5')['id']
    _play.play(
        left,
        right,
        '5_vs_5',
        seed=2,
        steps=5,
        save=False,
        submission_format=[_mode('safety', 'report'), _mode('safety', 'report')],
    )
    reports = [
        json.loads(path.read_text(encoding='utf-8'))
        for path in (arena_home / 'format-cache').rglob('report-*.json')
    ]
    assert len(reports) == 2
    for found in reports:
        environment = found['environment']
        assert (
            environment['OMP_NUM_THREADS'] == '1' and environment['TF_NUM_INTEROP_THREADS'] == '1'
        )
        assert environment['CUDA_VISIBLE_DEVICES'] == '-1'
        assert environment['SAFETY_SECRET'] == 'kept'
        assert environment['SAFETY_OTHER'] is None and environment['PYTHONPATH'] is None
        child = found['child']
        assert child['OMP_NUM_THREADS'] == '1' and child['SAFETY_SECRET'] == 'kept'
        assert child['CUJU_ARENA_CPUS'] == environment['CUJU_ARENA_CPUS']
    if LINUX:
        first, second = (set(found['cpus']) for found in reports)
        assert first and second and not first & second
        assert all(found['child']['cpus'] == found['cpus'] for found in reports)
    assert _cpusets.SETS.held() == {}


def test_a_ceiling_retires_the_process_of_a_run(arena_home, tmp_path):
    folder = tmp_path / 'runsafety'
    shutil.copytree(SAFETY, folder)
    toml = folder / 'format.toml'
    toml.write_text(
        toml.read_text(encoding='utf-8').replace('[process]\n', '[process]\nscope = "run"\n'),
        encoding='utf-8',
    )
    _registry.add(folder)
    team = _toy_team(tmp_path, 'steady', AGENT, '5v5')['id']
    # The same options in both matches, so the second takes the process the first kept, and
    # only the second plays long enough to break the ceiling.
    chosen = [_mode('runsafety', 'spawn', after=40), None]
    with _pool.Pool() as run:
        _play.play(team, 'builtin', '5_vs_5', seed=2, steps=5, save=False, submission_format=chosen)
        assert len(run) == 1
        kept = run.pids()
        with pytest.raises(_play.ForfeitError) as caught:
            _play.play(
                team, 'builtin', '5_vs_5', seed=2, steps=60, save=False, submission_format=chosen
            )
        assert caught.value.rule['kind'] == 'ceiling.process'
        assert len(run) == 0
        _play.play(team, 'builtin', '5_vs_5', seed=2, steps=5, save=False, submission_format=chosen)
        assert len(run) == 1 and run.pids() != kept
    assert _cpusets.SETS.held() == {}


# The match process.


@pytest.fixture
def inprobe(tmp_path, monkeypatch):
    root = tmp_path / 'builtin'
    # The shipped built-ins stay beside the probe, since a side that chooses nothing plays Kaggle.
    for shipped in _registry.BUILTIN.iterdir():
        if shipped.is_dir() and not shipped.name.startswith('__'):
            shutil.copytree(
                shipped, root / shipped.name, ignore=shutil.ignore_patterns('__pycache__')
            )
    shutil.copytree(PROBE, root / 'inprobe')
    toml = root / 'inprobe' / 'format.toml'
    toml.write_text(
        toml.read_text(encoding='utf-8').replace('[process]\n', '[process]\nhost = "match"\n'),
        encoding='utf-8',
    )
    monkeypatch.setattr(_registry, 'BUILTIN', root)
    return _registry.get('inprobe')


def test_os_exit_in_the_match_process_is_the_sides_forfeit(arena_home, tmp_path, inprobe):
    team = _toy_team(tmp_path, 'quitter', AGENT, '5v5')['id']
    chosen = [{'id': 'inprobe', 'options': {'mode': 'exit', 'after': 3}}, None]
    rule = _rule(team, 'builtin', chosen, steps=20)
    assert (rule['kind'], rule['side'], rule['step']) == ('ceiling.crash', 'home', 3)
    assert os._exit is _host._dispatch


def test_os_exit_in_any_call_of_the_match_process_is_the_callers_forfeit(tmp_path):
    """The submission's own `os._exit`, even caught, forfeits the side and leaves the process."""
    host = _side_host(tmp_path, 'play')
    spec = _contract.read(PROBE, builtin=True)
    format = types.SimpleNamespace(
        spec=spec, folder=PROBE, builtin=True, module=None, id='p', label='p'
    )
    inside = _host.MatchHost(format, host.match)

    # CPython's `os._exit` is the `_exit` of `posix`, or of `nt` on Windows, by either name.
    native = importlib.import_module(os.name)

    def caught(exit):
        def call():
            try:
                exit(4)
            except BaseException:
                return [0]

        return call

    for function in (
        lambda: os._exit(3),
        lambda: native._exit(3),
        caught(os._exit),
        caught(native._exit),
    ):
        with pytest.raises(_host.SideForfeit) as found:
            inside.call(function, [], 5.0, False)
        assert (found.value.kind, found.value.detail) == ('ceiling.crash', 'exit')
    assert inside.call(lambda: [1], [], 5.0, False) == [1]
    assert os._exit is native._exit is _host._dispatch


def test_a_child_of_the_match_process_plays_under_the_sides_ceilings(tmp_path):
    """A `Child` of a built-in format, as timed Kaggle starts, takes a tree and a CPU set."""
    host = _side_host(tmp_path, 'play')
    spec = _contract.read(PROBE, builtin=True)
    format = types.SimpleNamespace(
        spec=spec, folder=PROBE, builtin=True, module=None, id='p', label='p'
    )
    match = dataclasses.replace(host.match, options={'mode': 'child', 'after': 1000})
    inside = _host.MatchHost(format, match)
    try:
        inside.start()
        assert inside.context.guard is inside.guard
        (guard,) = inside.guard.trees
        assert guard.cpus is not None and guard.tree.process.poll() is None
        assert guard.environment['OMP_NUM_THREADS'] == '1'
        guard.tree.breaks('ceiling.memory', 'the tree used 9000 MiB', '9000')
        with pytest.raises(_host.SideForfeit) as found:
            inside.call(lambda: [0], [], 5.0, False)
        assert found.value.kind == 'ceiling.memory'
    finally:
        inside.close()
    assert guard.tree.closed and _cpusets.SETS.held() == {}


def test_a_host_without_a_guard_plays_as_before_the_ceilings(tmp_path):
    """No tree, no CPU set, no guard for a child, and a refused process is a plain crash."""
    host = _side_host(tmp_path / 'process', 'spawn')
    plain = _host.ProcessHost(host.format, host.match, guarded=False)
    try:
        with pytest.raises(_host.SideForfeit) as found:
            plain.start()
        assert found.value.kind == 'ceiling.crash'
        assert plain.guard is None and plain.tree is None
    finally:
        plain.close()
    spec = _contract.read(PROBE, builtin=True)
    format = types.SimpleNamespace(
        spec=spec, folder=PROBE, builtin=True, module=None, id='p', label='p'
    )
    match = dataclasses.replace(host.match, options={'mode': 'child', 'after': 1000})
    inside = _host.MatchHost(format, match, guarded=False)
    try:
        inside.start()
        assert inside.guard is None and inside.context.guard is None
        assert inside._player.child._process.tree is None
        assert inside.call(lambda: [2], [], 5.0, False) == [2]
    finally:
        inside.close()
    assert _cpusets.SETS.held() == {}


CONCURRENT = """
import threading
import time


def accept(folder, team):
    raise NotImplementedError


def start(folder, match):
    return Player()


class Player:
    def __init__(self):
        self.answer = None
        self.thread = None

    def send(self, step):
        def think():
            time.sleep(0.4)
            self.answer = [step.number % 19]

        self.thread = threading.Thread(target=think)
        self.thread.start()

    def receive(self):
        self.thread.join()
        return self.answer

    def act(self, step):
        raise AssertionError('a player with send and receive is never asked to act')
"""


def test_a_player_with_send_and_receive_thinks_beside_the_other_side(tmp_path):
    """Two sides in the match process both think between `send` and `receive`, as `strict/1`."""
    folder = tmp_path / 'concurrent'
    folder.mkdir()
    (folder / 'format.toml').write_text(
        'contract = 1\nname = "Concurrent"\nscript = "format.py"\n[process]\nhost = "match"\n',
        encoding='utf-8',
    )
    (folder / 'format.py').write_text(CONCURRENT, encoding='utf-8')
    spec = _contract.read(folder, builtin=True)
    format = types.SimpleNamespace(
        spec=spec, folder=folder, builtin=True, module=None, id='c', label='c'
    )
    match = _side_host(tmp_path / 'side', 'play').match
    sides = [_host.MatchHost(format, match), _host.MatchHost(format, match)]
    step = formats.Step(
        number=7,
        used=True,
        previous_applied=True,
        raw=[{}],
        indices=(6,),
        action_set='default',
        renderer_seconds=None,
        score=(0, 0),
    )
    try:
        for side in sides:
            side.start()
            assert side.concurrent
        began = time.perf_counter()
        for side in sides:
            side.send(step)
        answers = [side.receive() for side in sides]
        seconds = time.perf_counter() - began
        assert answers == [[7], [7]]
        assert seconds < 0.75, seconds
        assert all(side.last >= 0.4 for side in sides)
    finally:
        for side in sides:
            side.close()


WATCHDOG = """
import sys
sys.path.insert(0, sys.argv[1])
from cuju.arena._formats import host
watchdog = host._Watchdog(
    {'side': 'right', 'kind': 'ceiling.answer', 'message': 'no answer', 'step': 7, 'score': [1, 0]}
)
watchdog._end_process()
"""


def test_the_last_words_of_a_stalled_match_process_name_the_side(tmp_path):
    source = str(Path(_launch.package_folder()).parents[1])
    found = subprocess.run(
        [sys.executable, '-c', WATCHDOG, source],
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=False,
        env=_ceilings.match_environment(),
    )
    assert found.returncode == _ceilings.EXIT_CEILING
    words = _ceilings.read_last_words(found.stderr.splitlines())
    assert (words['side'], words['kind'], words['step'], words['score']) == (
        'right',
        'ceiling.answer',
        7,
        [1, 0],
    )
    rule = _ceilings.rule(words, ['West', 'East'])
    assert (rule['side'], rule['step'], rule['at']) == ('away', 7, [1, 0])
    assert rule['reason'] == 'East forfeits, no answer'
