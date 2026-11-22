# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The host watches a check match's process tree over the checker's existing pipes."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterable
from typing import Any

from . import ceilings as _ceilings
from . import launch as _launch
from . import tree as _tree

if os.name == 'nt':
    import _winapi


class RemoteTree:
    """The checker asks the host to watch the process it starts for a check match."""

    def __init__(
        self,
        request: Callable[..., Any],
        cpus: Iterable[int] | None,
        release: Callable[[], None],
    ) -> None:
        self._request = request
        self._cpus = None if cpus is None else list(cpus)
        self._release = release
        self._process: subprocess.Popen | None = None
        self.closed = False

    def popen_options(self) -> dict[str, Any]:
        return _tree.popen_options()

    def attach(self, process: subprocess.Popen) -> None:
        self._process = process
        try:
            self._request(
                'attach', pid=process.pid, handle=getattr(process, '_handle', None), cpus=self._cpus
            )
        except BaseException:
            with contextlib.suppress(OSError):
                process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(_launch.REAP_SECONDS)
            for stream in (process.stdin, process.stdout):
                stream.close()
            raise

    def verdict(self, wait: float = 0.0) -> tuple[str, str, str | None] | None:
        found = self._request('verdict', pid=self._process.pid, wait=wait)
        if found is not None:
            return tuple(found)
        return _tree.audit_verdict(self._process)

    def kill(self) -> None:
        if not self.closed:
            with contextlib.suppress(EOFError, OSError):
                self._request('kill', pid=self._process.pid)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            with contextlib.suppress(EOFError, OSError):
                self._request('close', pid=self._process.pid)
        finally:
            self._release()


class _Process:
    """The handle the host uses while the checker owns the child's pipes and reaps it."""

    def __init__(self, checker: subprocess.Popen, pid: int, handle: int | None) -> None:
        if type(pid) is not int or pid <= 0:
            raise ValueError('a check match needs a process identifier')
        self.pid = pid
        self._handle = None
        if os.name == 'nt':
            import ctypes

            current = _winapi.GetCurrentProcess()
            self._handle = subprocess.Handle(
                _winapi.DuplicateHandle(
                    int(checker._handle), handle, current, 0, False, _winapi.DUPLICATE_SAME_ACCESS
                )
            )
            if ctypes.windll.kernel32.GetProcessId(ctypes.c_void_p(self._handle)) != pid:
                raise ValueError('the check match handle does not belong to its process')
        else:
            pids = (
                _tree._children(checker.pid)
                if _tree.sys.platform.startswith('linux')
                else _tree.ps_tree(checker.pid, _tree._ps())[0][1:]
            )
            if pid not in pids or os.getpgid(pid) != pid:
                raise ValueError('the check match needs its own process in the checker tree')
            self._started = _tree._start_time(pid)

    def poll(self) -> int | None:
        if os.name == 'nt':
            if _winapi.WaitForSingleObject(self._handle, 0) == _winapi.WAIT_OBJECT_0:
                return _winapi.GetExitCodeProcess(self._handle)
        else:
            with contextlib.suppress(ProcessLookupError):
                os.kill(self.pid, 0)
                if self._started == _tree._start_time(self.pid):
                    return None
            return 0
        return None

    def kill(self) -> None:
        if os.name == 'nt':
            _winapi.TerminateProcess(self._handle, 1)
        elif self.poll() is None:
            os.kill(self.pid, signal.SIGKILL)


class CheckTrees:
    """The host owns the watches for the checker's matches."""

    def __init__(self, checker: _launch.Process, limits: _ceilings.Limits) -> None:
        self.checker = checker
        self.limits = limits
        self.trees: dict[int, _tree.Tree] = {}

    def receive(self, timeout: float) -> dict[str, Any]:
        """Answers tree requests while waiting for the checker to finish its call."""
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise _launch.Timeout(f'no answer within {timeout:g} s')
            frame = self.checker.receive(left)
            if frame['kind'] != 'tree':
                return frame
            try:
                value = self._handle(frame.get('operation'), frame['value'], deadline)
            except (KeyError, OSError, TypeError, ValueError) as error:
                self.checker.send('tree.error', str(error))
            else:
                self.checker.send('tree.answer', value)

    def _handle(self, operation: str, value: dict[str, Any], deadline: float) -> Any:
        pid = value['pid']
        if operation == 'attach':
            if pid in self.trees:
                raise ValueError('the host already watches this check match process')
            process = _Process(self.checker.process, pid, value['handle'])
            tree = _tree.Tree(self.limits, cpus=value['cpus'])
            try:
                tree.attach(process, parent=self.checker.pid)
            except BaseException:
                tree.close()
                raise
            self.trees[pid] = tree
        elif operation == 'kill':
            self.trees[pid].kill()
        elif operation == 'close':
            self.trees.pop(pid).close()
        elif operation == 'verdict':
            return self.trees[pid].verdict(min(float(value['wait']), deadline - time.monotonic()))
        else:
            raise ValueError('the checker requested an unknown tree operation')
        return None

    def close(self) -> None:
        for tree in self.trees.values():
            tree.close()
        self.trees.clear()
