# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The start of a format process or a format's child, and the frames to and from it.

Every such process starts as `python -I -X utf8` with `subprocess`, never with `multiprocessing`
and never by fork, so CUDA that the parent initialised cannot break it, the same on Linux,
Windows and macOS. `-I` leaves out every variable of Python, the user's site packages and the
working folder, so the process names the folders that import cuju and the renderer in a
variable of its own, and a short bootstrap puts them on its path. The bootstrap then moves its
standard output aside as the channel of its answers and points the descriptor at standard error,
so whatever the submission prints, from Python or from C, goes to the terminal of the side and
cannot forge an answer. The answer channel belongs to that process alone and closes when it exits.

The bootstrap gives the package `cuju.arena` as a bare package, so the process loads the
library of formats and nothing else of the renderer, which keeps its start short.

The environment of such a process is a minimal one: the variables a system and its libraries
need to run, those the format names in `[process] env`, the renderer's own, the thread counts
of `threads` and the GPUs of `gpu`. Before the format loads, the bootstrap guards the process
through `audit.install`, and the renderer holds it under the `tree.Tree` it starts with.

This module uses the standard library, numpy and the frames alone, since a child imports it.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO

from . import audit as _audit
from . import gpu as _gpu
from . import threads as _threads

# The variables that carry the folders of the imports and the folder of the renderer's package.
IMPORT_VARIABLE = 'CUJU_ARENA_IMPORT'
PACKAGE_VARIABLE = 'CUJU_ARENA_PACKAGE'
WINDOWS_UNAME_VARIABLE = 'CUJU_ARENA_WINDOWS_UNAME'
# The variables of the thread counts, which `[process] threads` sets.
THREAD_VARIABLES = _threads.VARIABLES
# The variable that names the variables a format asked for in `[process] env`, which its
# children keep as well.
NAMES_VARIABLE = 'CUJU_ARENA_ENV_NAMES'
# The variables of the renderer that a format process keeps, those a system, its interpreter and
# its libraries of numbers and GPUs need to run, beside every `LC_` variable of the locale.
MINIMAL = frozenset(
    {
        'PATH',
        'PATHEXT',
        'SYSTEMROOT',
        'SYSTEMDRIVE',
        'WINDIR',
        'COMSPEC',
        'TEMP',
        'TMP',
        'TMPDIR',
        'HOME',
        'USERPROFILE',
        'HOMEDRIVE',
        'HOMEPATH',
        'APPDATA',
        'LOCALAPPDATA',
        'PROGRAMDATA',
        'PROGRAMFILES',
        'USER',
        'USERNAME',
        'LOGNAME',
        'LANG',
        'LANGUAGE',
        'TZ',
        'NUMBER_OF_PROCESSORS',
        'PROCESSOR_ARCHITECTURE',
        'PROCESSOR_IDENTIFIER',
        'OS',
        'LD_LIBRARY_PATH',
        'CUDA_HOME',
        'CUDA_PATH',
        'CONDA_PREFIX',
        'VIRTUAL_ENV',
        'XLA_FLAGS',
        *_threads.VARIABLES,
        *_gpu.NAMES,
    }
)

BOOTSTRAP = """\
import os, sys, types
sys.path[:0] = [p for p in os.environ.get('CUJU_ARENA_IMPORT', '').split(os.pathsep) if p]
answer_fd = os.dup(1)
os.set_inheritable(answer_fd, False)
answers = os.fdopen(answer_fd, 'wb', buffering=0)
os.dup2(2, 1)
if os.name == 'nt' and 'CUJU_ARENA_WINDOWS_UNAME' in os.environ:
    import json, platform
    values = json.loads(os.environ['CUJU_ARENA_WINDOWS_UNAME'])
    info = platform.uname_result(*values[:-1])
    info.__dict__['processor'] = values[-1]
    platform._uname_cache = info
import cuju
if 'cuju.arena' not in sys.modules:
    arena = types.ModuleType('cuju.arena')
    arena.__path__ = [os.environ['CUJU_ARENA_PACKAGE']]
    arena.__package__ = 'cuju.arena'
    sys.modules['cuju.arena'] = arena
    cuju.arena = arena
from cuju.arena._formats import audit
audit.install()
from cuju.arena.formats.v1 import _child
_child.main(sys.stdin.buffer, answers)
"""


class Ended(EOFError):  # noqa: N818, it reads as what happened
    """Raised when the process ended, or its stream broke, before it answered."""


class Timeout(TimeoutError):  # noqa: N818, it reads as what happened
    """Raised when the process did not answer in time."""


def package_folder() -> Path:
    """Returns the folder of the package `cuju.arena` that this module belongs to."""
    return Path(__file__).resolve().parents[1]


def imports() -> list[str]:
    """Returns the folders that import cuju and the renderer, the engine's first.

    They are the folders that hold the package `cuju` in this process, which an editable or a
    split install keeps apart, and the folder that holds this renderer's `cuju`.
    """
    found: list[str] = []
    cuju = sys.modules.get('cuju')
    for portion in list(getattr(cuju, '__path__', None) or []):
        folder = str(Path(portion).resolve().parent)
        if folder not in found:
            found.append(folder)
    own = str(package_folder().parents[1])
    if own not in found:
        found.append(own)
    return found


def _kept(name: str, names: set[str]) -> bool:
    upper = name.upper()
    return (
        upper in MINIMAL
        or upper.startswith('LC_')
        or upper.startswith('CUDA_PATH_V')
        or name.startswith('CUJU_ARENA_')
        or name in names
    )


def environment(
    threads: int | None = None,
    extra: dict[str, str] | None = None,
    base: dict[str, str] | None = None,
    names: tuple[str, ...] | list[str] = (),
) -> dict[str, str]:
    """Returns the minimal environment of a format process or a format's child.

    It keeps the variables of `MINIMAL`, every `LC_` variable, the renderer's own and those
    `names` lists, and no variable of Python. A child starts from its format process's own and
    keeps what that kept, the variables of `[process] env` among them.

    Args:
        threads: The threads it may use, which every thread variable takes, or `None` to leave
            them as they are.
        extra: Variables to add, such as those of its CPU set and its GPUs.
        base: The environment to start from, by default this process's own.
        names: The variables of `[process] env` that the process takes from `base`.
    """
    source = os.environ if base is None else base
    kept = {*names, *(source.get(NAMES_VARIABLE) or '').split(',')} - {''}
    found = {
        name: value
        for name, value in source.items()
        if not name.startswith('PYTHON') and _kept(name, kept)
    }
    if kept:
        found[NAMES_VARIABLE] = ','.join(sorted(kept))
    found[IMPORT_VARIABLE] = os.pathsep.join(imports())
    found[PACKAGE_VARIABLE] = str(package_folder())
    if os.name == 'nt':
        # Windows platform discovery can start a helper or keep WMI threads alive.
        import platform

        found[WINDOWS_UNAME_VARIABLE] = json.dumps(list(platform.uname()))
    if threads is not None:
        found.update(_threads.variables(threads))
    found.update(extra or {})
    return found


CLOSE_SECONDS = 5.0
REAP_SECONDS = 10.0


class Process:
    """A format process or a format's child, with frames on its pipes.

    A thread reads its answers as they come, so a wait for an answer can end at a deadline while
    the process still runs, and the process is killed then.

    Attributes:
        process: The `subprocess.Popen`.
        started: When it was started, on the clock of `time.perf_counter`.
    """

    def __init__(
        self,
        cwd: Path,
        env: dict[str, str],
        stderr: BinaryIO | int | None = None,
        tree: Any = None,
    ) -> None:
        """Starts the process.

        Args:
            cwd: Its working folder, the submission's.
            env: Its environment, as `environment` gives it.
            stderr: Where its standard error goes, the side's terminal, `None` for this process's
                own or `subprocess.DEVNULL`. A terminal is released once the process has started.
            tree: The `tree.Tree` the process starts under, which holds it to the side's
                ceilings and ends all it started, or `None`.
        """
        self.started = time.perf_counter()
        self.tree = tree
        executable = sys.executable
        if os.name == 'nt':
            base = getattr(sys, '_base_executable', executable)
            if os.path.normcase(base) != os.path.normcase(executable):
                # Keep the virtual environment while starting its interpreter directly.
                env = {**env, '__PYVENV_LAUNCHER__': executable}
                executable = base
        release = getattr(stderr, 'release', None)
        try:
            # The library's own start, which the audit hook of a format process lets through.
            with _audit.permitted():
                self.process = subprocess.Popen(
                    [executable, '-I', '-X', 'utf8', '-c', BOOTSTRAP],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stderr,
                    close_fds=True,
                    cwd=str(cwd),
                    env=env,
                    **({} if tree is None else tree.popen_options()),
                )
        finally:
            # The terminal gives up its write end as soon as the spawn finishes.
            if release is not None:
                release()
        if tree is not None:
            tree.attach(self.process)
        self._answers: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True, name='cuju-format-pipe')
        self._reader.start()

    def _read(self) -> None:
        from . import frames as _frames

        stream = self.process.stdout
        while True:
            try:
                self._answers.put(('frame', _frames.read(stream)))
            except EOFError as error:
                # The answers end as the process ends, so whoever reads the end knows its code.
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.process.wait(2)
                self._answers.put(('end', str(error)))
                return
            except (OSError, ValueError) as error:
                self._answers.put(('end', f'the answers did not read, {error}'))
                return

    @property
    def pid(self) -> int:
        """The process's identifier."""
        return self.process.pid

    def alive(self) -> bool:
        """Returns whether the process still runs."""
        return self.process.poll() is None

    def send(self, kind: str, value: Any = None, **fields: Any) -> None:
        """Sends one frame.

        Raises:
            Ended: If the process ended and its pipe is closed.
        """
        from . import frames as _frames

        try:
            _frames.write(self.process.stdin, kind, value, **fields)
        except (BrokenPipeError, OSError, ValueError) as error:
            raise Ended(f'the process ended, {error}') from None

    def receive(self, timeout: float | None = None) -> dict[str, Any]:
        """Returns the next frame, waiting at most `timeout` seconds or without a limit.

        Raises:
            Timeout: If no frame came in time. The process still runs.
            Ended: If the process ended first.
        """
        try:
            what, value = self._answers.get(timeout=timeout)
        except queue.Empty:
            raise Timeout(f'no answer within {timeout:g} s') from None
        if what == 'end':
            # Keep the end for every later wait as well.
            self._answers.put((what, value))
            raise Ended(value)
        return value

    def kill(self) -> None:
        """Kills the process, if it still runs, and all its tree holds, and waits for it."""
        if self.tree is not None:
            self.tree.kill()
        if self.process.poll() is None:
            self.process.kill()
        try:
            self.process.wait(REAP_SECONDS)
        except subprocess.TimeoutExpired:  # pragma: no cover, a process the system holds
            pass
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except OSError:
                pass

    def close(self, timeout: float = CLOSE_SECONDS) -> int | None:
        """Asks the process to end, kills it after `timeout` seconds, and returns its exit code."""
        if self.process.poll() is None:
            try:
                self.send('close')
                self.process.stdin.close()
            except (Ended, OSError):
                pass
            try:
                self.process.wait(timeout)
            except subprocess.TimeoutExpired:
                pass
        self.kill()
        if self.tree is not None:
            self.tree.close()
        return self.process.returncode
