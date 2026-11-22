# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Match workers, which keep the processes of a format of `scope = "run"` across a run's matches.

A rating run, an evaluation and a tournament lease a worker for each slot that plays a match of a
format of `[process] scope = "run"`. The tests hold which matches go to a worker, that a worker
keeps a side's process across its matches with a terminal for each match, that a forfeit retires
the side's process while the worker plays on, that a ceiling's last words reach the context as a
forfeit, and that a cancel, a crash or the end of the run leaves no process behind.
"""

import contextlib
import os
import shutil
import threading
import time
import types
from pathlib import Path

import pytest

from cuju.arena._evaluate import evaluate as _evaluate
from cuju.arena._evaluate import runner as _runner
from cuju.arena._formats import ceilings as _ceilings
from cuju.arena._formats import registry as _registry
from cuju.arena._formats import terminals as _terminals
from cuju.arena._formats import worker as _worker
from cuju.arena._store import home as _home
from support.common import _toy_team

PROBE = Path(__file__).parents[1] / 'support' / 'formats' / 'probe'
HELLO = """\
def agent(obs):
    if obs["step"] == 0:
        print("hello from a match", flush=True)
    return [5 if obs["step"] % 3 else 13]
"""


@pytest.fixture
def probe(arena_home):
    """The Probe format, a person's format of `scope = "match"`."""
    return _registry.add(PROBE)


@pytest.fixture
def runprobe(arena_home, tmp_path):
    """The Probe format with `[process] scope = "run"`."""
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
    try:
        return 'zombie' not in Path(f'/proc/{pid}/status').read_text(encoding='utf-8')
    except OSError:
        return not Path('/proc').is_dir()


def _gone(pids, seconds=20.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not any(_running(pid) for pid in pids):
            return True
        time.sleep(0.05)
    return not any(_running(pid) for pid in pids)


def _workers():
    return _worker.Workers(
        _runner.worker_command(), _home.process_environment(), _home.process_folder()
    )


def _spec(team, terminal, mode='play'):
    return {
        'left': team,
        'right': 'builtin',
        'scenario': '5_vs_5',
        'seed': 3,
        'index': 0,
        'submission_format': [{'id': 'runprobe', 'options': {'mode': mode}}, None],
        'kind': 'rating',
        'terminal': terminal,
    }


def _result(reports):
    return next(report['result'] for report in reports if isinstance(report.get('result'), dict))


def test_only_a_format_of_scope_run_in_its_own_process_goes_to_a_worker(probe, runprobe):
    assert not _worker.wanted(None)
    assert not _worker.wanted([None, None])
    assert not _worker.wanted({'id': 'kaggle/1', 'preset': 'open'})
    assert not _worker.wanted('kaggle/1')
    assert not _worker.wanted([{'id': 'probe'}, None])
    assert _worker.wanted([None, {'id': 'runprobe'}])
    assert _worker.wanted('runprobe')
    # A choice that does not read is left to the match, which fails with its own message.
    assert not _worker.wanted([{'id': 'missing'}, None])


def test_a_worker_keeps_the_side_s_process_across_its_matches(arena_home, tmp_path, runprobe):
    """Two matches of one slot play in one worker and one format process, each match with a
    terminal of its own, and the end of the run closes both processes."""
    team = _toy_team(tmp_path, 'steady', HELLO, '5v5')['id']
    workers = _workers()
    seen = []
    for number in range(2):
        with workers.lease() as found:
            heard = []
            reports, code = found.play(_spec(team, f'wrun-m{number}'), heard.append)
            seen.append((found.process.pid, list(found.pids)))
        assert code is None and heard == reports
        assert _result(reports)['forfeit'] is None and _result(reports)['length'] > 0
        assert any('progress' in report for report in reports)
    (worker, first), (again, second) = seen
    assert worker == again and len(first) == 1 and first == second
    assert len(workers) == 1
    # Each match's terminal holds its own match alone.
    for number in range(2):
        name = f'wrun-m{number}'
        assert _terminals.sides(name) == ['left']
        rows = _terminals.read(name, 'left')['rows']
        assert [row['text'] for row in rows if row['kind'] == 'out'] == ['hello from a match']
    workers.close()
    assert len(workers) == 0 and _gone([worker, *first])


def test_a_forfeit_retires_its_process_while_the_worker_plays_on(arena_home, tmp_path, runprobe):
    team = _toy_team(tmp_path, 'unsteady', HELLO, '5v5')['id']
    workers = _workers()
    with workers.lease() as found:
        found.play(_spec(team, 'wforfeit-m0'))
        kept = list(found.pids)
        reports, code = found.play(_spec(team, 'wforfeit-m1', 'forfeit'))
        assert code is None and _result(reports)['forfeit']['side'] == 'home'
        # The side that forfeited left the pool, and the clean side of the first match stays.
        assert found.pids == kept and found.alive()
        reports, code = found.play(_spec(team, 'wforfeit-m2'))
        assert code is None and _result(reports)['forfeit'] is None and found.pids == kept
    workers.close()
    assert _gone(kept)


def test_a_killed_worker_leaves_no_process_behind(arena_home, tmp_path, runprobe):
    """A cancel or a crash of the run kills the workers, and their format processes go too."""
    team = _toy_team(tmp_path, 'stopped', HELLO, '5v5')['id']
    workers = _workers()
    with workers.lease() as found:
        found.play(_spec(team, 'wkill-m0'))
        held = [found.process.pid, *found.pids]
    workers.kill()
    assert _gone(held)
    with pytest.raises(RuntimeError):
        with workers.lease():
            pass


def test_a_worker_ends_by_itself_when_its_parent_goes(arena_home, tmp_path, runprobe):
    """A worker whose input ends closes its pool and exits, as when the run's process dies."""
    team = _toy_team(tmp_path, 'orphan', HELLO, '5v5')['id']
    found = _worker.Worker(
        _runner.worker_command(), _home.process_environment(), _home.process_folder()
    )
    found.play(_spec(team, 'worphan-m0'))
    held = list(found.pids)
    found.process.stdin.close()
    assert found.process.wait(30) == 0
    assert _gone(held)


def test_the_last_words_of_a_ceiling_in_a_worker_are_the_forfeit_of_its_side(
    arena_home, tmp_path, monkeypatch
):
    """A worker that a ceiling ended reports nothing more, and its last words name the side."""
    team = _toy_team(tmp_path, 'greedy', HELLO, '5v5')
    words = '{"side": "left", "kind": "ceiling.memory", "message": "used too much", "step": 7}'

    class Ended:
        def __init__(self):
            self.process = object()
            self.stderr = [f'{_ceilings.LAST_WORDS}{words}\n']

        def play(self, request, on_report=None, seconds=None):
            return [{'progress': 0.1}], _ceilings.EXIT_CEILING

    class Held:
        def __init__(self):
            self.lent = 0

        def lease(self):
            @contextlib.contextmanager
            def lent():
                self.lent += 1
                yield Ended()

            return lent()

    held = Held()
    owner = types.SimpleNamespace(lock=threading.Lock(), processes=set(), workers=held)
    monkeypatch.setattr(_worker, 'wanted', lambda choices: True)
    spec = _spec(team['id'], 'wceiling-m0')
    played = _evaluate._Play._child(owner, spec)
    assert held.lent == 1 and owner.processes == set()
    assert played.forfeit['side'] == 'home' and played.forfeit['kind'] == 'ceiling.memory'
    assert played.forfeit['step'] == 7 and played.forfeit['submission_format'] == 'runprobe'
    assert 'greedy' in played.forfeit['reason']
    assert played.lengths == [7] and played.actions.shape == (0, 1, 1)


def test_a_run_holds_its_workers_while_it_plays_and_ends_them_whatever_ends_it(monkeypatch):
    made = []

    class Fake:
        def __init__(self, *arguments):
            self.calls = []
            made.append(self)

        def kill(self):
            self.calls.append('kill')

        def close(self):
            self.calls.append('close')

    monkeypatch.setattr(_worker, 'Workers', Fake)
    owner = types.SimpleNamespace(workers=None)
    with _evaluate.match_workers(owner) as workers:
        assert owner.workers is workers
    assert owner.workers is None and made[0].calls == ['close']
    with pytest.raises(KeyboardInterrupt), _evaluate.match_workers(owner):
        raise KeyboardInterrupt
    assert owner.workers is None and made[1].calls == ['kill', 'close']


def test_a_match_worker_of_the_tournaments_plays_one_match_after_another(
    arena_home, tmp_path, runprobe
):
    """`_playing.play --worker` reads the arguments of one match on each line, as a tournament
    hands them over, and keeps the side's process between the matches."""
    team = _toy_team(tmp_path, 'cupper', HELLO, '5v5')['id']
    command = [*_home.python_command(), '-m', 'cuju.arena._playing.play', '--worker']
    workers = _worker.Workers(command, _home.process_environment(), _home.process_folder())
    choice = '[{"id": "runprobe"}, null]'
    pids = []
    with workers.lease() as found:
        for number in range(2):
            arguments = ['--left', team, '--right', 'builtin', '--scenario', '5_vs_5']
            arguments += ['--seed', str(number), '--length', '60', '--submission-format', choice]
            arguments += ['--terminal', f'wcup-m{number}']
            reports, code = found.play(arguments)
            assert code is None
            assert any(isinstance(report.get('match'), str) for report in reports), reports
            pids.append(list(found.pids))
        # A request that names no match is reported, and the worker plays on.
        reports, code = found.play({'left': team})
        assert code is None and reports[0]['error'].startswith('the worker read no match')
    assert len(pids[0]) == 1 and pids[0] == pids[1]
    workers.close()
    assert _gone(pids[0])
