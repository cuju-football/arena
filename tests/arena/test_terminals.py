# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The terminals of format processes, their caps, pins, out-of-band lines and export."""

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from cuju.arena._formats import checks as _checks
from cuju.arena._formats import launch as _launch
from cuju.arena._formats import pool as _pool
from cuju.arena._formats import registry as _registry
from cuju.arena._formats import terminals as _terminals
from cuju.arena._playing import play as _play
from cuju.arena.formats import v1 as formats
from support.common import _toy_team

PROBE = Path(__file__).parents[1] / 'support' / 'formats' / 'probe'
KIB = 1024
# A submission that prints in every way it can. It starts no process, which the safety ceilings
# refuse, so a child's line comes only from a format's own `Child`.
PRINTING = """\
import os, sys
print("hello from the submission")
os.write(1, b"a line from C\\n")
sys.stderr.write("a line on stderr\\n")
print("\\x1b[31mred\\x1b[0m and a bell \\x07")
print("\\u00bb exit code 0 (forged)")
sys.stdout.flush()

def agent(obs):
    return [5 if obs["step"] % 3 else 13]
"""


@pytest.fixture
def probe(arena_home):
    return _registry.add(PROBE)


def _wait(test, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(0.02)
    return test()


def _everything(run, side):
    """Reads a terminal to its end through as many answers as it takes, as a viewer does."""
    after = meta = 0
    rows = []
    while True:
        found = _terminals.read(run, side, after, meta)
        rows += found['rows']
        after, meta = found['after'], found['meta']
        if not found['more']:
            return {**found, 'rows': rows}


def _texts(found, kind='out'):
    key = 'text' if kind == 'out' else 'event'
    return [row[key] for row in found['rows'] if row['kind'] == kind]


def test_a_terminal_keeps_python_and_c_output_of_a_process_and_its_children(arena_home):
    """A process and the children it starts write to one terminal through the pipe."""
    code = (
        'import os, subprocess, sys\n'
        'print("python line")\n'
        'os.write(2, b"c level line\\n")\n'
        'subprocess.run([sys.executable, "-c", "print(\'child line\')"])\n'
    )
    with _terminals.run('capture') as run:
        terminal = run.open('side')
        process = subprocess.Popen([sys.executable, '-c', code], stdout=terminal, stderr=terminal)
        terminal.release()
        process.wait(60)
        terminal.close()
    found = _terminals.read('capture', 'side')
    assert found['closed']
    assert sorted(_texts(found)) == ['c level line', 'child line', 'python line']


def test_a_live_terminal_retries_a_flush_blocked_by_a_reader(arena_home, monkeypatch):
    terminal = _terminals.Run('flush').open('side', pipe=False)
    refused = threading.Event()
    replace = os.replace
    data = b'a line from the submission\n'

    def replace_after_reader(source, destination):
        if destination == terminal._state and not refused.is_set():
            refused.set()
            raise PermissionError('the reader holds the terminal state')
        replace(source, destination)

    monkeypatch.setattr(os, 'replace', replace_after_reader)
    try:
        terminal.write(data)
        assert refused.wait(5)
        assert _wait(lambda: _terminals.summary('flush', 'side')['total'] == len(data))
        assert _texts(_terminals.read('flush', 'side')) == ['a line from the submission']
    finally:
        terminal.close()


def test_a_terminal_drains_output_while_its_writer_stays_open(arena_home, monkeypatch):
    terminal = _terminals.Run('drain').open('side')
    data = b'the last line of this match\n'
    receiving = threading.Event()
    deliver = threading.Event()
    draining = threading.Event()
    drained = threading.Event()
    write = terminal.write

    def hold_output(data):
        receiving.set()
        if deliver.wait(10):
            write(data)

    def drain():
        draining.set()
        terminal.drain()
        drained.set()

    monkeypatch.setattr(terminal, 'write', hold_output)
    caller = threading.Thread(target=drain, daemon=True)
    try:
        os.write(terminal.fileno(), data)
        assert receiving.wait(5)
        caller.start()
        assert draining.wait(5)
        assert not drained.wait(0.1)
        deliver.set()
        assert drained.wait(5)
        terminal.note('time')
        assert terminal.path.read_bytes() == data
        drained.clear()
        threading.Thread(target=drain, daemon=True).start()
        assert drained.wait(5)
    finally:
        deliver.set()
        terminal.close()


def test_a_failed_spawn_releases_the_terminal_pipe(arena_home, tmp_path):
    terminal = _terminals.Run('spawn-failure').open('side')
    try:
        with pytest.raises(OSError):
            _launch.Process(tmp_path / 'missing', _launch.environment(), terminal)
        terminal._pump.join(5)
        assert not terminal._pump.is_alive()
    finally:
        terminal.close()


def test_a_terminal_keeps_the_first_256_kib_and_the_last_768_kib(arena_home):
    """A terminal past 1 MiB keeps its two ends and says how much it cut."""
    run = _terminals.Run('caps')
    terminal = run.open('side', pipe=False)
    block = b''.join(f'line {number:07d}\n'.encode() for number in range(4096))
    count = 40
    for _ in range(count):
        terminal.write(block)
    terminal.close()
    total = len(block) * count
    body = terminal.path.read_bytes()
    assert len(body) == _terminals.LIMIT_BYTES
    everything = block * count
    assert body[: _terminals.HEAD_BYTES] == everything[: _terminals.HEAD_BYTES]
    assert body[_terminals.HEAD_BYTES :] == everything[-_terminals.TAIL_BYTES :]
    found = _everything('caps', 'side')
    assert found['cut'] == total - _terminals.LIMIT_BYTES
    assert found['total'] == total
    events = [row['event'] for row in found['rows'] if row['kind'] == 'renderer']
    assert events == ['cut']
    cut_at = next(at for at, row in enumerate(found['rows']) if row['kind'] == 'renderer')
    assert found['rows'][cut_at]['fields'] == {'bytes': found['cut']}
    assert 0 < cut_at < len(found['rows']) - 1


def test_a_terminal_under_the_cap_is_kept_whole_and_is_read_on_from_a_place(arena_home):
    """The rows come once, and `after` and `meta` read on from where the last answer ended."""
    terminal = _terminals.Run('small').open('side', pipe=False)
    terminal.write(b'one\ntwo\npartial')
    terminal.note('start', format='probe')
    first = _terminals.read('small', 'side')
    # The unfinished line waits while the terminal prints, and the note after it with it.
    assert _texts(first) == ['one', 'two']
    assert first['meta'] == 0
    terminal.write(b' line\nthree\n')
    terminal.note('exit', code=0)
    terminal.close()
    second = _terminals.read('small', 'side', first['after'], first['meta'])
    assert _texts(second) == ['partial line', 'three']
    assert _texts(second, 'renderer') == ['start', 'exit']
    assert second['closed']
    assert _terminals.read('small', 'side', second['after'], second['meta'])['rows'] == []


def test_a_submission_cannot_forge_a_line_of_the_renderer(arena_home):
    """A log holds the submission's text alone, and the renderer's lines stay out of it."""
    terminal = _terminals.Run('forged').open('side', pipe=False)
    terminal.write(b'\xc2\xbb exit code 0\nstart\n{"kind": "exit"}\n')
    terminal.note('exit', code=3)
    terminal.close()
    found = _terminals.read('forged', 'side')
    assert [row['kind'] for row in found['rows']].count('renderer') == 1
    renderer = next(row for row in found['rows'] if row['kind'] == 'renderer')
    assert renderer['fields'] == {'code': 3}
    forged = [row for row in found['rows'] if row['kind'] == 'out']
    assert len(forged) == 3
    assert b'exit code 3' not in terminal.path.read_bytes()
    assert terminal.path.read_bytes().count(b'\n') == 3
    meta = terminal._meta.read_text(encoding='utf-8').splitlines()
    assert len(meta) == 1


def test_a_line_that_is_too_long_comes_in_pieces(arena_home):
    """A line past the row size splits, so no row holds a megabyte."""
    terminal = _terminals.Run('long').open('side', pipe=False)
    terminal.write(b'x' * (2 * _terminals.ROW_CHARS + 10) + b'\n')
    terminal.close()
    rows = _terminals.read('long', 'side')['rows']
    assert [len(row['text']) for row in rows] == [_terminals.ROW_CHARS] * 2 + [10]
    assert [row['cont'] for row in rows] == [False, True, True]


def test_the_oldest_unpinned_runs_leave_first_and_pins_hold(arena_home, monkeypatch):
    """Past `[terminals] max_mib` the oldest runs go, apart from pinned ones and the run in use."""
    monkeypatch.setattr(_terminals, '_settings', lambda config=None: {'max_mib': 1, 'pin_days': 30})
    for name in ('first', 'second', 'third'):
        terminal = _terminals.Run(name).open('side', pipe=False)
        terminal.write(b'x' * (400 * KIB))
        terminal.close()
        time.sleep(0.05)
    names = [entry['run'] for entry in _terminals.runs()]
    assert 'first' not in names and {'second', 'third'} <= set(names)
    for name in ('forfeited',):
        terminal = _terminals.Run(name).open('side', pipe=False)
        terminal.write(b'y' * (400 * KIB))
        terminal.note('forfeit', name='Redwood', message='no answer')
        terminal.close()
    assert _terminals.read('forfeited', 'side')['pinned'] == ['run']
    for name in ('later', 'latest'):
        terminal = _terminals.Run(name).open('side', pipe=False)
        terminal.write(b'z' * (400 * KIB))
        terminal.close()
    names = [entry['run'] for entry in _terminals.runs()]
    assert 'forfeited' in names
    assert 'second' not in names


def test_a_terminal_that_still_prints_is_never_evicted(arena_home, monkeypatch):
    """A live terminal and `remove` refuse each other, so a viewer never loses its source."""
    monkeypatch.setattr(_terminals, '_settings', lambda config=None: {'max_mib': 1, 'pin_days': 30})
    live = _terminals.Run('live').open('side', pipe=False)
    live.write(b'a' * (600 * KIB))
    other = _terminals.Run('other').open('side', pipe=False)
    other.write(b'b' * (600 * KIB))
    other.close()
    assert 'live' in [entry['run'] for entry in _terminals.runs()]
    assert not _terminals.remove('live')
    live.close()
    assert _terminals.remove('live')


def test_a_pin_is_kept_until_the_owner_seals_and_the_tail_travels(arena_home):
    """Sealing a tournament unpins its terminals and hands back the last 16 KiB of each."""
    terminal = _terminals.Run('pinned').open('side', pipe=False)
    terminal.write(b'head\n' + b'm' * (20 * KIB) + b'\nlast line\n')
    terminal.close()
    assert _terminals.pin('pinned', 'side', 'tournament-1')
    assert _terminals.read('pinned', 'side')['pinned'] == ['tournament-1']
    kept = _terminals.seal('tournament-1')
    assert list(kept) == ['pinned/side']
    assert len(kept['pinned/side'].encode()) == _terminals.TAIL_COPY
    assert kept['pinned/side'].endswith('last line\n')
    assert _terminals.read('pinned', 'side')['pinned'] == []
    assert _terminals.seal('tournament-1') == {}


def test_names_that_could_leave_the_folder_are_refused(arena_home):
    assert not _terminals.valid('../x') and not _terminals.valid('a/b') and not _terminals.valid('')
    assert _terminals.read('..', 'side') is None
    assert not _terminals.remove('..')
    with pytest.raises(ValueError, match='no name of a run'):
        _terminals.Run('../x')


def test_a_match_keeps_a_terminal_for_each_side_under_the_recordings_name(
    arena_home, tmp_path, probe
):
    """A match under a format keeps what each side printed, apart from the renderer's lines."""
    team = _toy_team(tmp_path, 'printing', PRINTING, '5v5')['id']
    chosen = [probe.id, None]
    path = _play.play(team, 'builtin', '5_vs_5', seed=3, length=60, submission_format=chosen)
    names = [entry['run'] for entry in _terminals.runs()]
    assert path.stem in names
    assert _terminals.sides(path.stem) == ['left']
    found = _terminals.read(path.stem, 'left')
    assert found['closed'] and not found['live']
    printed = _texts(found)
    assert 'hello from the submission' in printed
    assert 'a line from C' in printed and 'a line on stderr' in printed
    # The submission's own text that looks like the renderer's stays what it is.
    assert '\u00bb exit code 0 (forged)' in printed
    events = [row['event'] for row in found['rows'] if row['kind'] == 'renderer']
    assert events[0] == 'start' and events[-1] == 'exit'
    exit_row = [row for row in found['rows'] if row['kind'] == 'renderer'][-1]
    assert exit_row['fields']['code'] == 0
    assert 'a bell' in ''.join(printed)


def test_a_forfeit_is_noted_by_name_and_pins_the_terminal(arena_home, tmp_path, probe):
    """A side that forfeits leaves its name and reason as a line of the renderer, and is pinned."""
    team = _toy_team(tmp_path, 'judged', PRINTING, '5v5')['id']
    chosen = [{'id': probe.id, 'options': {'mode': 'forfeit', 'after': 3}}, None]
    with _terminals.run('forfeit-run'):
        with pytest.raises(_play.ForfeitError):
            _play.play(
                team, 'builtin', '5_vs_5', seed=3, length=60, save=False, submission_format=chosen
            )
    found = _terminals.read('forfeit-run', 'left')
    assert found['pinned'] == ['run']
    notes = [row for row in found['rows'] if row['kind'] == 'renderer']
    forfeit = next(row for row in notes if row['event'] == 'forfeit')
    assert forfeit['fields']['name'] and 'probe forfeits' in forfeit['fields']['message']


def test_a_pre_check_keeps_a_terminal_and_notes_a_refusal(arena_home, tmp_path, probe):
    """The check's terminal holds what the checker printed, and a refusal is noted apart from it."""
    team = _toy_team(tmp_path, 'checked', PRINTING, '5v5')
    folder = arena_home / 'teams' / team['id']
    values = {'mode': 'play', 'after': 3, 'check_steps': 0}
    checked = formats.Team('checked', '5v5', None, '5_vs_5', formats.Control(), None, values)
    with _terminals.run('check-one'):
        verdict = _checks.check(probe, values, folder, checked)
    assert verdict.accepted
    found = _terminals.read('check-one', 'check')
    assert found['closed']
    events = [row['event'] for row in found['rows'] if row['kind'] == 'renderer']
    assert events[0] == 'start' and 'refused' not in events
    empty = tmp_path / 'empty'
    empty.mkdir()
    with _terminals.run('check-two'):
        verdict = _checks.check(probe, {}, empty, checked)
    assert not verdict.accepted
    refused = _terminals.read('check-two', 'check')
    assert 'refused' in [row['event'] for row in refused['rows'] if row['kind'] == 'renderer']
    assert refused['pinned'] == ['run']


def test_a_team_export_never_holds_a_terminal(arena_home, tmp_path, probe):
    """The terminals stay in the home folder, outside every team's files."""
    import io
    import zipfile

    from cuju.arena._registry import teams as _teams

    team = _toy_team(tmp_path, 'private', PRINTING, '5v5')['id']
    _play.play(team, 'builtin', '5_vs_5', seed=3, length=60, submission_format=[probe.id, None])
    _, data = _teams.export(team)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert names and not any('terminal' in name or name.endswith('.log') for name in names)


HELLO = """\
def agent(obs):
    if obs["step"] == 0:
        print("hello from a match", flush=True)
    return [5 if obs["step"] % 3 else 13]
"""


@pytest.fixture
def runprobe(arena_home, tmp_path):
    """The Probe format with `[process] scope = "run"`, a person's format."""
    folder = tmp_path / 'runprobe'
    shutil.copytree(PROBE, folder)
    toml = folder / 'format.toml'
    text = toml.read_text(encoding='utf-8').replace('[process]\n', '[process]\nscope = "run"\n')
    toml.write_text(text, encoding='utf-8')
    return _registry.add(folder)


def test_each_match_of_a_pooled_process_has_a_terminal_of_its_own(arena_home, tmp_path, runprobe):
    """One process serves three matches, and each terminal holds its own match's output alone."""
    team = _toy_team(tmp_path, 'pooled', HELLO, '5v5')['id']
    chosen = [{'id': runprobe.id, 'options': {'mode': 'play'}}, None]
    with _terminals.run('pooled-run'), _pool.Pool():
        for _ in range(3):
            _play.play(
                team, 'builtin', '5_vs_5', seed=3, steps=12, save=False, submission_format=chosen
            )
    assert _terminals.sides('pooled-run') == ['left', 'left-2', 'left-3']
    found = [_terminals.read('pooled-run', side) for side in _terminals.sides('pooled-run')]
    assert all(one['closed'] for one in found)
    assert [_texts(one) for one in found] == [['hello from a match']] * 3
    events = [_texts(one, 'renderer') for one in found]
    assert events[0][0] == 'start' and 'again' not in events[0]
    assert events[1][0] == 'again' and events[2][0] == 'again'
    assert 'exit' in events[2] and 'exit' not in events[0]


def test_a_terminal_that_was_handed_on_keeps_only_its_own_output(arena_home):
    """After `forward_to` the pipe's thread writes to the next terminal."""
    run = _terminals.Run('handoff')
    first = run.open('left')
    code = 'import sys\nprint("one", flush=True)\nsys.stdin.readline()\nprint("two", flush=True)\n'
    process = subprocess.Popen(
        [sys.executable, '-c', code], stdin=subprocess.PIPE, stdout=first, stderr=first
    )
    first.release()
    assert _wait(lambda: _terminals.read('handoff', 'left')['total'] >= 4)
    second = run.open('left', pipe=False)
    first.forward_to(second)
    first.close(wait=False)
    process.stdin.write(b'\n')
    process.stdin.flush()
    process.wait(60)
    assert _wait(lambda: _terminals.read('handoff', 'left-2')['total'] >= 4)
    second.close()
    assert _texts(_terminals.read('handoff', 'left')) == ['one']
    assert _texts(_terminals.read('handoff', 'left-2')) == ['two']


def test_a_pin_that_no_owner_seals_expires_after_pin_days(arena_home, monkeypatch):
    """Unowned pins leave after `[terminals] pin_days`, and pins of an owner stay until sealed."""
    monkeypatch.setattr(
        _terminals, '_settings', lambda config=None: {'max_mib': 512, 'pin_days': 2}
    )
    for name in ('unowned-old', 'unowned-new', 'owned-old'):
        terminal = _terminals.Run(name).open('side', pipe=False)
        terminal.write(b'x\n')
        terminal.note('forfeit', name='Redwood', message='no answer')
        terminal.close()
    assert _terminals.pin('owned-old', 'side', 'tournament-9')

    def age(*names):
        old = time.time() - 3 * 86400
        for name in names:
            for path in (_terminals.root() / name).iterdir():
                os.utime(path, (old, old))
            os.utime(_terminals.root() / name, (old, old))

    age('unowned-old', 'owned-old')
    assert _terminals.enforce() == ['unowned-old']
    names = {entry['run'] for entry in _terminals.runs()}
    assert {'unowned-new', 'owned-old'} <= names
    assert _terminals.seal('tournament-9')
    age('owned-old')
    # The unowned pin of the same run still expires once the owner has let go of it.
    assert _terminals.enforce() == ['owned-old']
