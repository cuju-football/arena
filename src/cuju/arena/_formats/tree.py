# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The watch on a side's process tree, its ceilings and its end, on Linux, Windows and macOS.

A side's format process starts under a `Tree`, which holds the process and everything it starts
to the side's `ceilings.Limits` and ends all of it at the end. Each system enforces what it can,
and the watch looks at the rest every `ceilings.POLL_SECONDS`:

| System | Enforced by the system | Watched |
|---|---|---|
| Linux, cgroups v2 delegated | memory, tasks and the CPU set, by a cgroup | threads, processes |
| Linux | the CPU set, by affinity | memory, threads and processes, summed from `/proc` |
| Windows | memory of the job, processes and the CPU set, by a job object | threads |
| macOS | nothing | memory, threads and processes, from `ps` |

- **Linux.** The format process starts in a session of its own and makes itself a child
  subreaper (`audit.install`), so an orphan of its tree stays in it. With cgroups v2 delegated to
  the renderer, the side gets a cgroup with `memory.max`, `pids.max` and `cpuset.cpus`.
- **Windows.** The process starts suspended, joins a job object with
  `JOB_OBJECT_LIMIT_ACTIVE_PROCESS` at 8, plus the launcher of a virtual environment, a job-wide
  memory limit, the affinity and kill on close, and only then runs. The job reports a process or
  memory refused at once, through a completion port.
- **macOS.** The process starts in a session of its own, and the watch reads `ps`. A process that
  leaves the session and the tree escapes the end, which is weaker, and the guide says so.

A broken ceiling calls `on_ceiling(kind, message, detail)`, the host's `ceiling`, once, which
records the forfeit against the side and ends its process. `kill()` ends the whole tree.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, ClassVar

from . import ceilings as _ceilings

_MIB = 1024 * 1024


@dataclasses.dataclass
class Sample:
    """One look at a tree.

    Attributes:
        pids: The processes of the tree that still run, the format process first.
        threads: Their threads.
        memory: Their resident memory, or committed memory on Windows, in bytes.
    """

    pids: list[int]
    threads: int | None
    memory: int | None

    @property
    def processes(self) -> int:
        return len(self.pids)


def popen_options() -> dict[str, Any]:
    """Returns how the host starts a process it holds in a tree."""
    if os.name == 'nt':
        # CREATE_SUSPENDED leaves the process paused until it joins its job.
        return {'creationflags': 0x00000004}
    return {'start_new_session': True}


def audit_verdict(process: Any) -> tuple[str, str, str | None] | None:
    """Returns the forfeit for a process the spawn guard stopped."""
    if process is not None and process.poll() == _ceilings.EXIT_PROCESS:
        return (
            'ceiling.process',
            'the submission started a process, which the arena refuses',
            'audit',
        )
    return None


class Tree:
    """A side's process tree, held to its ceilings and ended at the end.

    Args:
        limits: The side's `ceilings.Limits`.
        on_ceiling: What records a broken ceiling, called once with the kind, a message and a
            detail, or `None`.
        cpus: The side's CPU set, or `None`.
        on_close: What runs once the tree has ended, such as giving its CPU set back.
        poll: The seconds between two looks.
        sampler: What looks at the tree, a function of the format process's identifier that
            returns a `Sample`, by default the one of this system.

    Attributes:
        enforced: The ceilings the system itself holds the tree to.
        watched: The ceilings the watch checks.
        broken: The ceiling the tree broke, `(kind, message, detail)`, or `None`.
        peak_memory: The most memory seen, in bytes.
        peak_threads: The most threads seen.
        peak_processes: The most processes seen.
    """

    def __init__(
        self,
        limits: _ceilings.Limits,
        on_ceiling: Callable[[str, str, str | None], None] | None = None,
        cpus: Iterable[int] | None = None,
        on_close: Callable[[], None] | None = None,
        poll: float = _ceilings.POLL_SECONDS,
        sampler: Callable[[int], Sample] | None = None,
    ) -> None:
        self.limits = limits
        self.on_ceiling = on_ceiling
        self.cpus = tuple(cpus) if cpus is not None else None
        self.poll = poll
        self.enforced: list[str] = []
        self.watched: list[str] = []
        self.broken: tuple[str, str, str | None] | None = None
        self.peak_memory = 0
        self.peak_threads = 0
        self.peak_processes = 0
        self.process: Any = None
        self._sampler = sampler
        self._on_close = on_close
        self._known: dict[int, Any] = {}
        self._job: _Job | None = None
        self._cgroup: _Cgroup | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._closed = False
        self._watch: threading.Thread | None = None

    # The start.

    def popen_options(self) -> dict[str, Any]:
        """Returns what `subprocess.Popen` takes to start the format process under the tree."""
        return {} if os.name == 'nt' and self._sampler is not None else popen_options()

    def attach(self, process: Any, parent: int | None = None) -> None:
        """Puts a started format process under the tree and starts the watch."""
        self.process = process
        if self._sampler is None:
            if os.name == 'nt':
                self._attach_windows(process)
            else:
                self._attach_posix(process, parent)
        else:
            self.watched = ['memory', 'threads', 'processes']
        self._watch = threading.Thread(target=self._run, daemon=True, name='cuju-format-tree')
        self._watch.start()

    def _attach_windows(self, process: Any) -> None:
        try:
            self._job = _Job(
                self.limits.memory_bytes,
                self.limits.processes,
                None if not self.cpus else _mask(self.cpus),
            )
            self._job.assign(int(process._handle))
            self.enforced = ['memory', 'processes'] + (['cpus'] if self.cpus else [])
            self.watched = ['threads']
        except OSError:
            if self._job is not None:
                self._job.close()
            self._job = None
            self.watched = ['threads']
        finally:
            _resume(int(process._handle))

    def _attach_posix(self, process: Any, parent: int | None) -> None:
        if sys.platform.startswith('linux'):
            if self.cpus and hasattr(os, 'sched_setaffinity'):
                with contextlib.suppress(OSError):
                    os.sched_setaffinity(process.pid, self.cpus)
            cgroup = Path('/proc/self/cgroup' if parent is None else f'/proc/{parent}/cgroup')
            self._cgroup = _Cgroup.make(self.limits, self.cpus, cgroup=cgroup)
            if self._cgroup is not None and self._cgroup.add(process.pid):
                self.enforced = ['memory', 'tasks'] + (['cpus'] if self.cpus else [])
                self.watched = ['threads', 'processes']
            else:
                if self._cgroup is not None:
                    self._cgroup.remove()
                self._cgroup = None
                self.enforced = ['cpus'] if self.cpus else []
                self.watched = ['memory', 'threads', 'processes']
        else:
            self.watched = ['memory', 'threads', 'processes']

    # The watch.

    def sample(self) -> Sample | None:
        """Looks at the tree once, or returns `None` once its format process has ended."""
        process = self.process
        if process is None or process.poll() is not None:
            return None
        if self._sampler is not None:
            found = self._sampler(process.pid)
        elif os.name == 'nt':
            found = _windows_sample(process.pid, self._job)
        elif sys.platform.startswith('linux'):
            found = _linux_sample(process.pid)
            if self._cgroup is not None:
                found.memory = self._cgroup.memory() or found.memory
        else:
            found = _ps_sample(process.pid, _ps())
        with self._lock:
            for pid in found.pids:
                if pid not in self._known:
                    self._known[pid] = _start_time(pid)
        return found

    def check(self, found: Sample) -> tuple[str, str, str | None] | None:
        """Returns the ceiling a look at the tree breaks, or `None`, and keeps the peaks."""
        limits = self.limits
        if found.memory is not None:
            self.peak_memory = max(self.peak_memory, found.memory)
        if found.threads is not None:
            self.peak_threads = max(self.peak_threads, found.threads)
        self.peak_processes = max(self.peak_processes, found.processes)
        own = found.processes
        if own > limits.processes:
            return (
                'ceiling.process',
                f'the tree held {own} processes, the ceiling is {limits.processes}',
                str(own),
            )
        if found.memory is not None and found.memory > limits.memory_bytes:
            return (
                'ceiling.memory',
                f'the tree used {found.memory / _MIB:.0f} MiB, '
                f'the ceiling is {limits.memory_mib} MiB',
                f'{found.memory / _MIB:.0f}',
            )
        allowed = limits.thread_limit(max(1, own))
        if found.threads is not None and found.threads > allowed:
            return (
                'ceiling.threads',
                f'the tree held {found.threads} threads, the ceiling is {allowed}',
                str(found.threads),
            )
        return None

    def _run(self) -> None:
        while not self._stop.wait(self.poll):
            found = self._job.message() if self._job is not None else None
            if found is None:
                try:
                    sample = self.sample()
                except (OSError, ValueError, subprocess.SubprocessError):
                    continue
                if sample is None:
                    return
                found = self.check(sample)
            if found is not None:
                self.breaks(*found)
                return

    def breaks(self, kind: str, message: str, detail: str | None = None) -> None:
        """Records a broken ceiling once, tells the host and ends the tree."""
        with self._lock:
            if self.broken is not None:
                return
            self.broken = (kind, message, detail)
        if self.on_ceiling is not None:
            try:
                self.on_ceiling(kind, message, detail)
            finally:
                self.kill()
        else:
            self.kill()

    def verdict(self, wait: float = 0.0) -> tuple[str, str, str | None] | None:
        """Returns the ceiling the tree broke, from the watch, the job or the process's end.

        Args:
            wait: The seconds to wait for the news of a system that refused memory or a process,
                which comes a moment after the refusal, where the system enforces a limit.
        """
        found = self._verdict()
        if found is not None or wait <= 0 or (self._job is None and self._cgroup is None):
            return found
        deadline = time.monotonic() + wait
        while found is None and time.monotonic() < deadline:
            time.sleep(0.02)
            found = self._verdict()
        return found

    def _verdict(self) -> tuple[str, str, str | None] | None:
        if self.broken is not None:
            return self.broken
        if self._job is not None:
            found = self._job.message()
            if found is not None:
                self.broken = found
                return found
        found = audit_verdict(self.process)
        if found is not None:
            self.broken = found
        return self.broken

    # The end.

    @property
    def closed(self) -> bool:
        """Whether the tree has ended and given back what its side held."""
        return self._closed

    def kill(self) -> None:
        """Ends every process of the tree, the format process and all it started."""
        process = self.process
        if process is None:
            return
        if self._job is not None:
            self._job.terminate()
        if self._cgroup is not None:
            self._cgroup.kill()
        if os.name != 'nt':
            with self._lock:
                known = dict(self._known)
            if process.poll() is None:
                with contextlib.suppress(OSError):
                    known.update({pid: _start_time(pid) for pid in _descendants(process.pid)})
            # The session of the format process holds whatever it started and did not move.
            with contextlib.suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
            for pid, started in known.items():
                if pid != process.pid and started is not None and _start_time(pid) == started:
                    with contextlib.suppress(OSError):
                        os.kill(pid, signal.SIGKILL)
        if process.poll() is None:
            with contextlib.suppress(OSError):
                process.kill()

    def close(self) -> None:
        """Stops the watch, ends what is left of the tree and gives back what the side held."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._stop.set()
        self.kill()
        if self._job is not None:
            self.peak_memory = max(self.peak_memory, self._job.peak())
            self._job.close()
            self._job = None
        if self._cgroup is not None:
            self._cgroup.remove()
            self._cgroup = None
        if self._on_close is not None:
            self._on_close()

    def figures(self) -> dict[str, Any]:
        """Returns what the tree enforced, watched and peaked at, for a report."""
        return {
            'enforced': list(self.enforced),
            'watched': list(self.watched),
            'peak_memory_mib': round(self.peak_memory / _MIB, 1),
            'peak_threads': self.peak_threads,
            'peak_processes': self.peak_processes,
            'cpus': None if self.cpus is None else list(self.cpus),
        }


def _mask(cpus: Iterable[int]) -> int:
    found = 0
    for cpu in cpus:
        found |= 1 << int(cpu)
    return found


# Linux.


def _start_time(pid: int) -> str | None:
    """Returns when a process started, from `/proc`, so a later process of its number differs."""
    if not sys.platform.startswith('linux'):
        return 'here'
    try:
        text = Path(f'/proc/{pid}/stat').read_text(encoding='ascii', errors='replace')
    except OSError:
        return None
    # The name of the command may hold spaces, so the fields count from its closing bracket.
    return text[text.rfind(')') + 2 :].split()[19]


def _children(pid: int) -> list[int]:
    found: list[int] = []
    for task in os.listdir(f'/proc/{pid}/task'):
        with contextlib.suppress(OSError):
            found += [
                int(part)
                for part in Path(f'/proc/{pid}/task/{task}/children')
                .read_text(encoding='ascii')
                .split()
            ]
    return found


def _descendants(pid: int) -> list[int]:
    """Returns the processes under a process on Linux, from `/proc`, nearest first."""
    if not sys.platform.startswith('linux'):
        return []
    found, queue = [], [pid]
    while queue:
        at = queue.pop(0)
        with contextlib.suppress(OSError):
            for child in _children(at):
                if child not in found:
                    found.append(child)
                    queue.append(child)
    return found


def _status(pid: int) -> dict[str, list[str]]:
    found = {}
    with open(f'/proc/{pid}/status', encoding='ascii', errors='replace') as stream:
        for line in stream:
            name, _, value = line.partition(':')
            found[name] = value.split()
    return found


def _linux_sample(pid: int) -> Sample:
    """Sums the resident memory and the threads of a tree on Linux, from `/proc`."""
    pids, threads, memory = [], 0, 0
    for found in [pid, *_descendants(pid)]:
        try:
            status = _status(found)
        except OSError:
            continue
        # A zombie has ended and holds nothing but its number.
        if status.get('State', ['?'])[0] in ('Z', 'X'):
            continue
        pids.append(found)
        threads += int(status.get('Threads', ['0'])[0])
        memory += int(status.get('VmRSS', ['0'])[0]) * 1024
    return Sample(pids, threads, memory)


class _Cgroup:
    """The cgroup of one side under a cgroup v2 hierarchy delegated to the renderer."""

    _COUNT = 0

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    @staticmethod
    def own(root: Path = Path('/sys/fs/cgroup'), cgroup: Path = Path('/proc/self/cgroup')) -> Path:
        """Returns the folder of this process's cgroup."""
        for line in cgroup.read_text(encoding='ascii').splitlines():
            if line.startswith('0::'):
                return root / line[3:].lstrip('/')
        raise OSError('no cgroup v2')

    @classmethod
    def make(
        cls,
        limits: _ceilings.Limits,
        cpus: Iterable[int] | None,
        root: Path = Path('/sys/fs/cgroup'),
        cgroup: Path = Path('/proc/self/cgroup'),
    ) -> _Cgroup | None:
        """Returns a new cgroup of a side, or `None` where cgroups v2 are not delegated."""
        try:
            own = cls.own(root, cgroup)
            controls = (own / 'cgroup.subtree_control').read_text(encoding='ascii').split()
            if not {'memory', 'pids'} <= set(controls) or not os.access(own, os.W_OK):
                return None
            cls._COUNT += 1
            folder = own / f'cuju-side-{os.getpid()}-{cls._COUNT}'
            folder.mkdir()
        except OSError:
            return None
        found = cls(folder)
        try:
            (folder / 'memory.max').write_text(str(limits.memory_bytes), encoding='ascii')
            if (folder / 'memory.swap.max').exists():
                (folder / 'memory.swap.max').write_text('0', encoding='ascii')
            # The controller of pids counts every task, threads among them, so it holds the
            # tree to the most threads its processes may have.
            tasks = limits.thread_limit(limits.processes)
            (folder / 'pids.max').write_text(str(tasks), encoding='ascii')
            if cpus and 'cpuset' in controls:
                (folder / 'cpuset.cpus').write_text(
                    ','.join(str(cpu) for cpu in cpus), encoding='ascii'
                )
        except OSError:
            found.remove()
            return None
        return found

    def add(self, pid: int) -> bool:
        try:
            (self.folder / 'cgroup.procs').write_text(str(pid), encoding='ascii')
            return True
        except OSError:
            return False

    def memory(self) -> int | None:
        with contextlib.suppress(OSError, ValueError):
            return int((self.folder / 'memory.current').read_text(encoding='ascii'))
        return None

    def kill(self) -> None:
        with contextlib.suppress(OSError):
            (self.folder / 'cgroup.kill').write_text('1', encoding='ascii')

    def remove(self) -> None:
        for _ in range(20):
            try:
                self.folder.rmdir()
                return
            except FileNotFoundError:
                return
            except OSError:
                time.sleep(0.05)


# macOS, and any other system with ps.


def _ps() -> str:
    return subprocess.run(
        ['ps', '-A', '-o', 'pid=,ppid=,pgid=,rss=,stat='],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
    ).stdout


def ps_tree(pid: int, table: str) -> tuple[list[int], int]:
    """Returns the processes of a tree and their resident memory in bytes, from a `ps` table.

    The table holds one line for each process, its identifier, its parent's, its process
    group's, its resident memory in KiB and its state. The tree is the process, its descendants
    and every process of its group, without zombies.
    """
    rows = {}
    for line in table.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            rows[int(parts[0])] = (int(parts[1]), int(parts[2]), int(parts[3]), parts[4])
        except ValueError:
            continue
    if pid not in rows:
        return [], 0
    members = [pid]
    changed = True
    while changed:
        changed = False
        for found, (parent, group, _, _) in rows.items():
            if found not in members and (parent in members or group == pid):
                members.append(found)
                changed = True
    members = [found for found in members if not rows[found][3].startswith('Z')]
    return members, sum(rows[found][2] for found in members) * 1024


def _ps_threads(pids: list[int]) -> int | None:
    if not pids:
        return 0
    lines = subprocess.run(
        ['ps', '-M', '-p', ','.join(map(str, pids))],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
    ).stdout.splitlines()
    return max(0, len(lines) - 1) or None


def _ps_sample(pid: int, table: str) -> Sample:
    pids, memory = ps_tree(pid, table)
    return Sample(pids, _ps_threads(pids), memory)


# Windows.


def _resume(handle: int) -> None:
    """Runs a process that started suspended, by its handle."""
    if os.name != 'nt':
        return
    import ctypes

    ctypes.windll.ntdll.NtResumeProcess(ctypes.c_void_p(handle))


def _windows_threads(pids: set[int]) -> int | None:
    """Returns the threads of processes on Windows, from a snapshot of the system's threads."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = (
            ('size', wintypes.DWORD),
            ('usage', wintypes.DWORD),
            ('thread', wintypes.DWORD),
            ('owner', wintypes.DWORD),
            ('base', wintypes.LONG),
            ('delta', wintypes.LONG),
            ('flags', wintypes.DWORD),
        )

    kernel = ctypes.windll.kernel32
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return None
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(Entry)
        count = 0
        found = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.owner in pids:
                count += 1
            found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        return count
    finally:
        kernel.CloseHandle(snapshot)


def _windows_sample(pid: int, job: _Job | None) -> Sample:
    if job is None:
        return Sample([pid], _windows_threads({pid}), None)
    pids = job.pids() or [pid]
    return Sample(pids, _windows_threads(set(pids)), job.memory())


class _Job:
    """A job object of Windows that holds one side's tree, with a completion port for its news."""

    # The limits of a job: the processes at once, the affinity, the end of every process with the
    # job and the memory of the whole job.
    _ACTIVE_PROCESS = 0x8
    _AFFINITY = 0x10
    _KILL_ON_CLOSE = 0x2000
    _JOB_MEMORY = 0x200
    # The news of a job's port: a process refused by the limit, and memory refused.
    _MESSAGES: ClassVar[dict[int, str]] = {3: 'process', 9: 'memory', 10: 'memory'}

    def __init__(self, memory: int, processes: int, affinity: int | None) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes, self._wintypes = ctypes, wintypes
        self.memory_limit = memory
        self.processes = processes

        class Basic(ctypes.Structure):
            _fields_ = (
                ('per_process_time', ctypes.c_int64),
                ('per_job_time', ctypes.c_int64),
                ('flags', wintypes.DWORD),
                ('minimum_working_set', ctypes.c_size_t),
                ('maximum_working_set', ctypes.c_size_t),
                ('active_processes', wintypes.DWORD),
                ('affinity', ctypes.c_size_t),
                ('priority', wintypes.DWORD),
                ('scheduling', wintypes.DWORD),
            )

        class Counters(ctypes.Structure):
            _fields_ = tuple((name, ctypes.c_uint64) for name in ('a', 'b', 'c', 'd', 'e', 'f'))

        class Extended(ctypes.Structure):
            _fields_ = (
                ('basic', Basic),
                ('io', Counters),
                ('process_memory', ctypes.c_size_t),
                ('job_memory', ctypes.c_size_t),
                ('peak_process_memory', ctypes.c_size_t),
                ('peak_job_memory', ctypes.c_size_t),
            )

        class Port(ctypes.Structure):
            _fields_ = (('key', ctypes.c_void_p), ('port', wintypes.HANDLE))

        self._extended = Extended
        kernel = ctypes.windll.kernel32
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.CreateIoCompletionPort.restype = wintypes.HANDLE
        kernel.GetQueuedCompletionStatus.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.DWORD,
        )
        self._kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        self.port = None
        if not self.handle:
            raise OSError('cannot make a job object')
        info = Extended()
        info.basic.flags = self._ACTIVE_PROCESS | self._KILL_ON_CLOSE | self._JOB_MEMORY
        info.basic.active_processes = processes
        info.job_memory = memory
        if affinity:
            info.basic.flags |= self._AFFINITY
            info.basic.affinity = affinity
        if not kernel.SetInformationJobObject(
            wintypes.HANDLE(self.handle), 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            self.close()
            raise OSError('cannot limit a job object')
        self.port = kernel.CreateIoCompletionPort(wintypes.HANDLE(-1), None, 0, 1)
        if self.port:
            link = Port(None, wintypes.HANDLE(self.port))
            if not kernel.SetInformationJobObject(
                wintypes.HANDLE(self.handle), 7, ctypes.byref(link), ctypes.sizeof(link)
            ):
                kernel.CloseHandle(wintypes.HANDLE(self.port))
                self.port = None

    def assign(self, handle: int) -> None:
        wintypes = self._wintypes
        if not self._kernel.AssignProcessToJobObject(
            wintypes.HANDLE(self.handle), wintypes.HANDLE(handle)
        ):
            raise OSError('cannot put the process in a job object')

    def message(self) -> tuple[str, str, str | None] | None:
        """Returns the ceiling the job's port reports, or `None`, without waiting."""
        if not self.port:
            return None
        ctypes, wintypes = self._ctypes, self._wintypes
        while True:
            code = wintypes.DWORD()
            key = ctypes.c_size_t()
            overlapped = ctypes.c_void_p()
            if not self._kernel.GetQueuedCompletionStatus(
                wintypes.HANDLE(self.port),
                ctypes.byref(code),
                ctypes.byref(key),
                ctypes.byref(overlapped),
                0,
            ):
                return None
            kind = self._MESSAGES.get(int(code.value))
            if kind == 'process':
                return (
                    'ceiling.process',
                    f'the tree started more than {self.processes} processes',
                    'job',
                )
            if kind == 'memory':
                limit = self.memory_limit / _MIB
                return ('ceiling.memory', f'the tree passed {limit:.0f} MiB', 'job')

    def _query(self) -> Any:
        ctypes = self._ctypes
        info = self._extended()
        self._kernel.QueryInformationJobObject(
            ctypes.c_void_p(self.handle), 9, ctypes.byref(info), ctypes.sizeof(info), None
        )
        return info

    def peak(self) -> int:
        """Returns the most memory the job committed, in bytes."""
        if not self.handle:
            return 0
        return int(self._query().peak_job_memory)

    def memory(self) -> int | None:
        """Returns the memory the job has committed now, in bytes, read as its peak."""
        return self.peak() if self.handle else None

    def pids(self) -> list[int]:
        """Returns the processes of the job."""
        ctypes = self._ctypes

        class Listed(ctypes.Structure):
            _fields_ = (
                ('assigned', ctypes.c_uint32),
                ('listed', ctypes.c_uint32),
                ('ids', ctypes.c_size_t * 64),
            )

        info = Listed()
        self._kernel.QueryInformationJobObject(
            ctypes.c_void_p(self.handle), 3, ctypes.byref(info), ctypes.sizeof(info), None
        )
        return [int(info.ids[index]) for index in range(min(info.listed, 64))]

    def terminate(self) -> None:
        if self.handle:
            self._kernel.TerminateJobObject(self._wintypes.HANDLE(self.handle), 1)

    def close(self) -> None:
        if self.handle:
            self._kernel.CloseHandle(self._wintypes.HANDLE(self.handle))
            self.handle = None
        if self.port:
            self._kernel.CloseHandle(self._wintypes.HANDLE(self.port))
            self.port = None
