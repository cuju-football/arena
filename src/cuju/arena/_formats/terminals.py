# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The terminals of format processes, kept at `<home>/terminals/<run>/<side>.log`.

A terminal holds what a side prints, the standard output and standard error of its format
process, from Python and from C alike, with the output of the children of the process joined in.
All of it is the submission's. The renderer's own lines, the start, the exit code, a refusal or a
forfeit with its name and the timings, never enter that stream. They go to
`<side>.meta.jsonl`, one JSON object for each line, `{at, t, kind, fields}`, where `at` is the
number of bytes the submission had printed when the renderer wrote the line. A viewer draws them
as rows of their own, so nothing a submission prints can forge one.

A terminal keeps at most 1 MiB, the first 256 KiB and the last 768 KiB, and records the bytes cut
between them. All terminals together keep at most `[terminals] max_mib`, the oldest run out first,
apart from runs that print still and runs pinned. A terminal is pinned for an owner, a tournament,
when its side refused or forfeited, and stays until `seal(owner)`, which hands back the last 16 KiB
of each terminal for the owner's file.

    with terminals.run('check-arena') as run:
        terminal = run.open('check')
        process = Process(folder, env, terminal)  # its standard error goes to the terminal
        ...
        terminal.close()

`<side>.json` holds the state of a terminal, `{total, cut, closed, pinned, started}`.
"""

from __future__ import annotations

import contextlib
import contextvars
import io
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
import weakref
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .._store import home as _home

FOLDER = 'terminals'
HEAD_BYTES = 256 * 1024
TAIL_BYTES = 768 * 1024
LIMIT_BYTES = HEAD_BYTES + TAIL_BYTES
# What a pinned terminal leaves in the file of its tournament.
TAIL_COPY = 16 * 1024
# The longest row a reader answers, longer lines come in pieces.
ROW_CHARS = 4096
# The most rows one answer of `read` holds.
ROW_LIMIT = 2000
_FLUSH_SECONDS = 0.25
_PIPE_POLL_SECONDS = 0.01
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}')
_LIVE: weakref.WeakSet[Terminal] = weakref.WeakSet()
_CURRENT: contextvars.ContextVar[Run | None] = contextvars.ContextVar(
    'cuju_terminals_run', default=None
)
_LOCK = threading.RLock()
# The owner of a pin that a refusal or a forfeit made, which no tournament has claimed.
UNOWNED = 'run'


def valid(name: str) -> bool:
    """Returns whether `name` is safe as the name of a run or a side."""
    return bool(_NAME.fullmatch(name)) and '..' not in name


def check_run(team: str, checkpoint: str | None, format: str) -> str:
    """Returns the name of the run that holds a team's check under a format."""
    parts = [
        re.sub(r'[^A-Za-z0-9._-]+', '-', str(part)).strip('-.')
        for part in (team, checkpoint, format)
    ]
    return ('check-' + '-'.join(part for part in parts if part))[:96].rstrip('-.')


def root() -> Path:
    """Returns the folder of the terminals, made when missing."""
    return _home.folder(FOLDER)


def _settings(config: Any = None) -> dict[str, Any]:
    from .._config import layers as _layers

    return _layers.table('terminals', config=config)


class Terminal:
    """The terminal of one side of one run.

    A process that is given the terminal as its standard error writes to it through a pipe, which
    a thread of this object reads. Call `release` once the process has started, since the pipe's
    write end then belongs to the process and its children alone, and `close` when the side ends.
    A terminal of a side that plays in the match process has no pipe, and `capture` routes what
    that side prints from Python.

    Attributes:
        run: The name of the run.
        side: The name of the side.
        path: The log file.
    """

    def __init__(self, run: str, side: str, pipe: bool = True) -> None:
        folder = root() / run
        folder.mkdir(parents=True, exist_ok=True)
        self.run = run
        self.side = side
        self.path = folder / f'{side}.log'
        self._meta = folder / f'{side}.meta.jsonl'
        self._state = folder / f'{side}.json'
        self._lock = threading.RLock()
        self._head = bytearray()
        self._tail = bytearray()
        self._cut = 0
        self._total = 0
        self._dirty = False
        self._timer: threading.Timer | None = None
        self._closed = False
        self._pinned: list[str] = []
        self._started = time.time()
        self._forward: Terminal | None = None
        self._read: int | None = None
        self._write: int | None = None
        self._pump: threading.Thread | None = None
        self._pipe_state = threading.Condition()
        self._drained = False
        self.path.write_bytes(b'')
        self._meta.write_bytes(b'')
        if pipe:
            self._read, self._write = os.pipe()
            if os.name != 'nt':
                os.set_blocking(self._read, False)
            self._pump = threading.Thread(target=self._pump_loop, daemon=True, name='cuju-terminal')
            self._pump.start()
        self._flush()
        _LIVE.add(self)
        enforce(keep=run)

    # The pipe.

    def fileno(self) -> int:
        """Returns the write end of the pipe, so that the terminal serves as `stderr` of `Popen`."""
        if self._write is None:
            raise ValueError('this terminal has no pipe')
        return self._write

    def forward_to(self, other: Terminal) -> None:
        """Hands what the process prints from now on to another terminal, the next match's.

        A process that serves the matches of a run keeps one pipe, which this terminal's thread
        reads. After the handoff that thread writes to `other`, and this terminal, closed or not,
        holds the output of its own match alone.
        """
        self._forward = other

    @staticmethod
    def _read_pipe(descriptor: int) -> bytes | None:
        """Reads available bytes, or returns `None` while the pipe is open and empty."""
        if os.name == 'nt':
            import ctypes
            import msvcrt
            from ctypes import wintypes

            available = wintypes.DWORD()
            ok = ctypes.windll.kernel32.PeekNamedPipe(
                wintypes.HANDLE(msvcrt.get_osfhandle(descriptor)),
                None,
                0,
                None,
                ctypes.byref(available),
                None,
            )
            if not ok:
                raise ctypes.WinError()
            if not available.value:
                return None
        try:
            return os.read(descriptor, 65536)
        except BlockingIOError:
            return None

    def drain(self, timeout: float = 1.0) -> None:
        """Waits until the thread has taken what the process wrote so far, for at most `timeout`."""
        with self._pipe_state:
            self._drained = False
            self._pipe_state.notify_all()
            self._pipe_state.wait_for(lambda: self._drained or self._read is None, timeout)

    def release(self) -> None:
        """Closes the write end that this process holds, once the process has started."""
        with self._lock:
            write, self._write = self._write, None
        if write is not None:
            try:
                os.close(write)
            except OSError:
                pass

    def _pump_loop(self) -> None:
        descriptor = self._read
        try:
            while True:
                # A Windows pipe can block a peek while another thread reads from it.
                with self._pipe_state:
                    data = self._read_pipe(descriptor)
                    if data is None:
                        self._drained = True
                        self._pipe_state.notify_all()
                        self._pipe_state.wait(_PIPE_POLL_SECONDS)
                        continue
                if not data:
                    return
                self.write(data)
        except (OSError, ValueError):
            return
        finally:
            with contextlib.suppress(OSError):
                os.close(descriptor)
            with self._pipe_state:
                self._read = None
                self._pipe_state.notify_all()

    # The stream.

    def write(self, data: bytes) -> None:
        """Appends what the submission printed, within the caps."""
        if not data:
            return
        forward = self._forward
        if forward is not None:
            # A terminal that was handed on passes on what its pipe still brings, through the chain.
            forward.write(data)
            return
        with self._lock:
            if self._closed:
                return
            self._total += len(data)
            room = HEAD_BYTES - len(self._head)
            if room > 0 and not self._tail and not self._cut:
                self._head += data[:room]
                data = data[room:]
            if data:
                self._tail += data
                extra = len(self._tail) - TAIL_BYTES
                if extra > 0:
                    del self._tail[:extra]
                    self._cut += extra
            self._dirty = True
            self._schedule_flush()

    def write_str(self, text: str) -> None:
        """Appends text that the submission printed in the match process."""
        self.write(text.encode('utf-8', 'replace'))

    def _schedule_flush(self) -> None:
        if self._dirty and not self._closed and self._timer is None:
            self._timer = threading.Timer(_FLUSH_SECONDS, self._tick)
            self._timer.daemon = True
            self._timer.start()

    def _tick(self) -> None:
        with self._lock:
            self._timer = None
            if self._dirty and not self._closed:
                self._flush()

    def _flush(self) -> None:
        with self._lock:
            self._dirty = False
            body = bytes(self._head) + bytes(self._tail)
            self._replace(self.path, body)
            state = {
                'total': self._total,
                'cut': self._cut,
                'head': HEAD_BYTES,
                'closed': self._closed,
                'pinned': list(self._pinned),
                'started': self._started,
            }
            self._replace(self._state, json.dumps(state).encode('utf-8'))
            self._schedule_flush()

    def _replace(self, path: Path, body: bytes) -> None:
        temporary = path.with_name(f'{path.name}.{secrets.token_hex(2)}.tmp')
        try:
            temporary.write_bytes(body)
            os.replace(temporary, path)
        except OSError:
            # A reader can hold the file, so a live terminal retries the flush.
            self._dirty = True
            with contextlib.suppress(OSError):
                temporary.unlink()

    def note(self, kind: str, /, **fields: Any) -> None:
        """Writes a line of the renderer, out of band, at the present place of the stream.

        Args:
            kind: One of `start`, `again`, `load`, `time`, `exit`, `refused`, `forfeit`, `failure`.
            fields: JSON values, such as `code`, `name`, `message` and `seconds`.
        """
        with self._lock:
            self._flush()
            line = {'at': self._total, 't': round(time.time(), 3), 'kind': kind, 'fields': fields}
            with self._meta.open('ab') as handle:
                handle.write(json.dumps(line).encode('utf-8') + b'\n')
        if kind in ('refused', 'forfeit', 'failure'):
            self.pin()

    def pin(self, owner: str = 'run') -> None:
        """Keeps the terminal out of the eviction until `seal(owner)`."""
        with self._lock:
            if owner not in self._pinned:
                self._pinned.append(owner)
            self._flush()

    def close(self, wait: bool = True) -> None:
        """Ends the terminal, after the last of its output that has arrived.

        Args:
            wait: Whether to wait for the thread that reads the pipe, which ends with the
                process. A process that goes on to another match leaves it running.
        """
        self.release()
        pump = self._pump
        if wait and pump is not None and pump is not threading.current_thread():
            pump.join(2.0)
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._closed:
                return
            self._closed = True
            self._flush()
        _LIVE.discard(self)
        enforce(keep=self.run)

    @property
    def closed(self) -> bool:
        """Whether the terminal has ended."""
        return self._closed


class Run:
    """A run, the folder that holds the terminals of the sides of a pre-check or a match.

    Attributes:
        name: The name of the run, which is the identifier of its recording once `adopt` has run.
        owned: Whether the run was made for one match, which `adopt` may rename.
    """

    def __init__(self, name: str, owned: bool = False) -> None:
        if not valid(name):
            raise ValueError(f'{name!r} is no name of a run')
        self.name = name
        self.owned = owned
        self.terminals: list[Terminal] = []
        (root() / name).mkdir(parents=True, exist_ok=True)

    @classmethod
    def fresh(cls, prefix: str = 'match') -> Run:
        """Returns a run under a new name that sorts by time, as `match-20261118-101500-3fa9`."""
        return cls(_home.new_identifier(prefix), owned=True)

    def open(self, side: str, pipe: bool = True) -> Terminal:
        """Opens the terminal of a side, under a name that no earlier terminal of the run holds.

        Args:
            side: The name of the side, such as `left`, `right` or `check`.
            pipe: Whether the side runs in a process of its own, which writes to the terminal.
        """
        folder = root() / self.name
        name, count = side, 1
        while (folder / f'{name}.log').exists():
            count += 1
            name = f'{side}-{count}'
        terminal = Terminal(self.name, name, pipe)
        self.terminals.append(terminal)
        return terminal

    def adopt(self, name: str) -> bool:
        """Renames the run, so that it carries the identifier of its recording.

        Returns:
            Whether it was renamed. A run that a caller named, one whose terminals still print and
            one whose new name is taken keep their own.
        """
        if (
            not self.owned
            or not valid(name)
            or name == self.name
            or any(not t.closed for t in self.terminals)
        ):
            return False
        source, target = root() / self.name, root() / name
        if target.exists() or not source.is_dir():
            return False
        try:
            source.rename(target)
        except OSError:
            return False
        for terminal in self.terminals:
            terminal.run = name
            terminal.path = target / terminal.path.name
            terminal._meta = target / terminal._meta.name
            terminal._state = target / terminal._state.name
        self.name = name
        return True


def current() -> Run | None:
    """Returns the run that the block of `run` opened, or `None`."""
    return _CURRENT.get()


@contextlib.contextmanager
def run(name: str) -> Iterator[Run]:
    """Makes the run `name` the present one, so that the hosts started inside it write there."""
    found = Run(name)
    token = _CURRENT.set(found)
    try:
        yield found
    finally:
        _CURRENT.reset(token)


# What a side prints in the match process.


class _Router(io.TextIOBase):
    """Stands for `sys.stdout` or `sys.stderr` and sends what a capturing thread prints to its
    terminal, and everything else to the stream it replaced."""

    def __init__(self, target: Any) -> None:
        self.target = target

    def write(self, text: str) -> int:
        found = getattr(_ACTIVE, 'terminal', None)
        if found is not None:
            found.write_str(text)
            return len(text)
        return self.target.write(text)

    def flush(self) -> None:
        self.target.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.target, name)


_ACTIVE = threading.local()
_ROUTED = {'count': 0, 'out': None, 'err': None}


@contextlib.contextmanager
def capture(terminal: Terminal | None) -> Iterator[None]:
    """Sends what the calling thread prints from Python to `terminal` while the block runs."""
    if terminal is None:
        yield
        return
    with _LOCK:
        if _ROUTED['count'] == 0:
            _ROUTED['out'], _ROUTED['err'] = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = _Router(sys.stdout), _Router(sys.stderr)
        _ROUTED['count'] += 1
    before = getattr(_ACTIVE, 'terminal', None)
    _ACTIVE.terminal = terminal
    try:
        yield
    finally:
        _ACTIVE.terminal = before
        with _LOCK:
            _ROUTED['count'] -= 1
            if _ROUTED['count'] == 0:
                for name, key in (('stdout', 'out'), ('stderr', 'err')):
                    if isinstance(getattr(sys, name), _Router):
                        setattr(sys, name, _ROUTED[key])


# Reading, listing and eviction.


def _json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def sides(run_name: str) -> list[str]:
    """Returns the names of the sides of a run, in the order they began."""
    folder = root() / run_name
    if not valid(run_name) or not folder.is_dir():
        return []
    found = [
        (_json(path.with_suffix('.json'), {}).get('started', 0.0), path.stem)
        for path in folder.glob('*.log')
    ]
    return [name for _, name in sorted(found)]


def summary(run_name: str, side: str) -> dict[str, Any]:
    """Returns the state of a terminal, `{side, total, cut, closed, live, pinned}`."""
    state = _json(root() / run_name / f'{side}.json', {})
    return {
        'side': side,
        'total': int(state.get('total', 0)),
        'cut': int(state.get('cut', 0)),
        'closed': bool(state.get('closed')),
        'live': _live(run_name, side),
        'pinned': list(state.get('pinned') or []),
    }


def _live(run_name: str, side: str) -> bool:
    return any(t.run == run_name and t.side == side and not t.closed for t in list(_LIVE))


def read(run_name: str, side: str, after: int = 0, meta: int = 0) -> dict[str, Any] | None:
    """Returns the rows of a terminal from a place on, for a viewer.

    Args:
        run_name: The run.
        side: The side.
        after: The number of bytes of the stream already read, the `after` of the last answer.
        meta: The number of renderer lines already read, the `meta` of the last answer.

    Returns:
        `None` for a terminal that does not exist, else `{run, side, rows, after, meta, total,
        cut, closed, live, pinned, more}`. A row is `{kind: 'out', text, cont}` for a line of the
        submission, `cont` true for the next piece of a line that was split, or `{kind:
        'renderer', event, fields, t}` for a line of the renderer, `event` one of the kinds of
        `Terminal.note` or `cut`. Only whole lines come while the terminal prints, and the last
        line comes once it ends.
    """
    if not (valid(run_name) and valid(side)):
        return None
    folder = root() / run_name
    log = folder / f'{side}.log'
    if not log.is_file():
        return None
    state = _json(folder / f'{side}.json', {})
    total, cut = int(state.get('total', 0)), int(state.get('cut', 0))
    closed = bool(state.get('closed'))
    try:
        body = log.read_bytes()
    except OSError:
        return None
    # The bytes after the head follow the cut, so the place `a` of the stream is `a` in the head
    # and `a - cut` in the body past it.
    held = HEAD_BYTES if cut else len(body)
    # The head of a cut terminal ends where the cut begins, so its last line is whole as far as
    # it goes.
    segments = [(0, body[:held], closed or bool(cut))]
    if cut:
        segments.append((HEAD_BYTES + cut, body[HEAD_BYTES:], closed))
    lines: list[tuple[int, bytes, bool]] = []
    cut_row = bool(cut) and after <= HEAD_BYTES
    end = max(after, 0)
    for start, data, whole in segments:
        if start + len(data) <= after:
            continue
        data = data[max(after - start, 0) :]
        start = max(start, after)
        position = start
        pieces = data.split(b'\n')
        for number, piece in enumerate(pieces):
            last = number == len(pieces) - 1
            if last and not piece:
                break
            if last and not whole:
                break
            lines.append((position, piece.removesuffix(b'\r'), last))
            position += len(piece) + (0 if last else 1)
        end = position
    notes = []
    try:
        for found in (folder / f'{side}.meta.jsonl').read_text(encoding='utf-8').splitlines():
            try:
                notes.append(json.loads(found))
            except ValueError:
                continue
    except OSError:
        pass
    if closed:
        end = max(end, total)
    rows: list[dict[str, Any]] = []
    more = False
    queue = [(n, found) for n, found in enumerate(notes) if n >= meta and found['at'] <= end]
    delivered = meta
    taken = 0
    inserted_cut = not cut_row
    for place, data, _ in lines:
        while queue and queue[0][1]['at'] <= place:
            number, found = queue.pop(0)
            rows.append(_renderer(found))
            delivered = number + 1
        if not inserted_cut and place >= HEAD_BYTES + cut:
            rows.append({'kind': 'renderer', 'event': 'cut', 'fields': {'bytes': cut}, 't': 0})
            inserted_cut = True
        text = data.decode('utf-8', 'replace')
        for piece in range(0, max(len(text), 1), ROW_CHARS):
            rows.append({'kind': 'out', 'text': text[piece : piece + ROW_CHARS], 'cont': piece > 0})
        taken += 1
        if taken >= ROW_LIMIT:
            more = True
            end = place + len(data) + 1
            break
    if not more:
        if not inserted_cut:
            rows.append({'kind': 'renderer', 'event': 'cut', 'fields': {'bytes': cut}, 't': 0})
        for number, found in queue:
            rows.append(_renderer(found))
            delivered = number + 1
    return {
        'run': run_name,
        'side': side,
        'rows': rows,
        'after': end,
        'meta': delivered,
        'total': total,
        'cut': cut,
        'closed': closed,
        'live': _live(run_name, side),
        'pinned': list(state.get('pinned') or []),
        'more': more,
    }


def _renderer(found: dict[str, Any]) -> dict[str, Any]:
    return {
        'kind': 'renderer',
        'event': str(found.get('kind', '')),
        'fields': found.get('fields') or {},
        't': found.get('t', 0),
    }


def runs() -> list[dict[str, Any]]:
    """Returns the runs, the newest first, each as `{run, sides, bytes, pinned, live}`."""
    found = []
    for folder in root().iterdir():
        if not folder.is_dir() or not valid(folder.name):
            continue
        names = sides(folder.name)
        held = [
            owner for n in names for owner in _json(folder / f'{n}.json', {}).get('pinned') or []
        ]
        pinned = bool(held)
        found.append(
            (
                folder.stat().st_mtime,
                {
                    'run': folder.name,
                    'sides': names,
                    'bytes': _size(folder),
                    'pinned': pinned,
                    'owned': any(owner != UNOWNED for owner in held),
                    'age': max(time.time() - _touched(folder), 0.0),
                    'live': any(_live(folder.name, n) for n in names),
                },
            )
        )
    return [entry for _, entry in sorted(found, key=lambda item: item[0], reverse=True)]


def _touched(folder: Path) -> float:
    """Returns when a file of the run changed last."""
    found = folder.stat().st_mtime
    for entry in folder.iterdir():
        with contextlib.suppress(OSError):
            found = max(found, entry.stat().st_mtime)
    return found


def _size(folder: Path) -> int:
    total = 0
    for entry in folder.iterdir():
        try:
            total += entry.stat().st_size
        except OSError:
            pass
    return total


def remove(run_name: str) -> bool:
    """Deletes a run with its terminals, which leave with the run that owns them."""
    folder = root() / run_name
    if not valid(run_name) or not folder.is_dir():
        return False
    if any(_live(run_name, side) for side in sides(run_name)):
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return True


def enforce(keep: str | None = None, config: Any = None) -> list[str]:
    """Removes the oldest runs until the terminals hold at most `[terminals] max_mib`.

    A run that prints still, a run pinned for an owner, a run pinned without an owner for fewer than
    `[terminals] pin_days` days and `keep` stay.

    Returns:
        The names of the runs removed.
    """
    settings = _settings(config)
    limit = float(settings['max_mib']) * 1024 * 1024
    entries = runs()
    removed = []
    # A pin that no owner seals expires, since nothing is left to release it.
    expiry = float(settings['pin_days']) * 86400.0
    for entry in list(entries):
        if entry['pinned'] and not entry['owned'] and not entry['live'] and entry['age'] > expiry:
            if entry['run'] != keep and remove(entry['run']):
                removed.append(entry['run'])
                entries.remove(entry)
    total = sum(entry['bytes'] for entry in entries)
    for entry in reversed(entries):
        if total <= limit:
            break
        if entry['pinned'] or entry['live'] or entry['run'] == keep:
            continue
        if remove(entry['run']):
            total -= entry['bytes']
            removed.append(entry['run'])
    return removed


def pin(run_name: str, side: str, owner: str) -> bool:
    """Pins a stored terminal for an owner, such as a tournament, until `seal(owner)`."""
    state = root() / run_name / f'{side}.json'
    if not (valid(run_name) and valid(side)) or not state.is_file():
        return False
    found = _json(state, {})
    held = list(found.get('pinned') or [])
    if owner not in held:
        held.append(owner)
    found['pinned'] = held
    state.write_text(json.dumps(found), encoding='utf-8')
    return True


def tail(run_name: str, side: str, size: int = TAIL_COPY) -> str:
    """Returns the last `size` bytes of a terminal as text, for the file of a tournament."""
    log = root() / run_name / f'{side}.log'
    if not (valid(run_name) and valid(side)) or not log.is_file():
        return ''
    body = log.read_bytes()
    return body[-size:].decode('utf-8', 'replace')


def seal(owner: str) -> dict[str, str]:
    """Unpins every terminal pinned for `owner` and returns the last 16 KiB of each.

    Returns:
        A dictionary of `run/side` to the tail of that terminal, which the owner writes into its
        own file before its runs may leave.
    """
    found: dict[str, str] = {}
    for entry in runs():
        for side in entry['sides']:
            state = root() / entry['run'] / f'{side}.json'
            document = _json(state, {})
            held = list(document.get('pinned') or [])
            if owner in held:
                found[f'{entry["run"]}/{side}'] = tail(entry['run'], side)
                held.remove(owner)
                document['pinned'] = held
                state.write_text(json.dumps(document), encoding='utf-8')
    return found


def export(run_name: str) -> bytes | None:
    """Returns the terminals of a run as a zip archive, a `.log` and a `.meta.jsonl` for each side.

    Returns:
        The archive, or `None` for a run that has no terminal.
    """
    names = sides(run_name)
    if not names:
        return None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for side in names:
            folder = root() / run_name
            for suffix in ('.log', '.meta.jsonl', '.json'):
                path = folder / f'{side}{suffix}'
                if path.is_file():
                    archive.writestr(f'{side}{suffix}', path.read_bytes())
    return buffer.getvalue()


def export_into(folder: Path, run_name: str) -> list[Path]:
    """Writes the terminals of a run into `folder/terminals`, for an export that asks for them."""
    names = sides(run_name)
    written: list[Path] = []
    if not names:
        return written
    target = Path(folder) / 'terminals'
    target.mkdir(parents=True, exist_ok=True)
    for side in names:
        for suffix in ('.log', '.meta.jsonl', '.json'):
            path = root() / run_name / f'{side}{suffix}'
            if path.is_file():
                shutil.copyfile(path, target / path.name)
                written.append(target / path.name)
    return written
