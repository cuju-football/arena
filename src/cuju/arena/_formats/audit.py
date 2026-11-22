# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The guard of a format process and of a format's child, from their first line of Python.

`install()` runs in the bootstrap of every such process, before the format or the submission
loads. It pins the process to its CPU set, makes it a child subreaper on Linux so that its
descendants stay in its tree, puts the post-import hook of `threads` on its imports, and turns
on an audit hook that refuses every new process.

The audit hook lets two kinds of start through: the library's own `Child`, which `permitted()`
marks around its start, and the short helpers of Python's `ctypes.util`, which runs
`ldconfig -p` on Linux to find a library, as torch does when it imports. Any other start writes
one line to the side's terminal and ends the process with `ceilings.EXIT_PROCESS`, which the
host records as the side's `ceiling.process` forfeit. The hook stops accidents. Python offers no
way to stop code that means harm, which the guide says plainly.

This module uses the standard library alone.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
from collections.abc import Iterator
from typing import Any

# The audit events of Python that start another process.
EVENTS = frozenset(
    {
        'subprocess.Popen',
        'os.system',
        'os.posix_spawn',
        'os.fork',
        'os.forkpty',
        'os.exec',
        'os.spawn',
        'os.startfile',
        'pty.spawn',
        '_winapi.CreateProcess',
    }
)
# The exit code of a process that the hook ends, `ceilings.EXIT_PROCESS`.
EXIT_PROCESS = 81
# The module whose helper processes the hook lets through.
_HELPERS = ('ctypes', 'util.py')

_LOCAL = threading.local()
_INSTALLED = False


@contextlib.contextmanager
def permitted() -> Iterator[None]:
    """Lets the starts of a block through the hook, for the library's own `Child`."""
    held = getattr(_LOCAL, 'permitted', 0)
    _LOCAL.permitted = held + 1
    try:
        yield
    finally:
        _LOCAL.permitted = held


def _helper() -> bool:
    """Returns whether the start comes from Python's `ctypes.util`."""
    frame = sys._getframe(2)
    while frame is not None:
        name = os.path.normcase(frame.f_code.co_filename)
        parts = name.replace('\\', '/').split('/')
        if parts[-2:] == list(_HELPERS):
            return True
        frame = frame.f_back
    return False


def _hook(event: str, arguments: tuple[Any, ...]) -> None:
    if event not in EVENTS:
        return
    if getattr(_LOCAL, 'permitted', 0) or _helper():
        return
    with contextlib.suppress(Exception):
        os.write(2, f'cuju-arena: the submission started a process, {event}\n'.encode())
    os._exit(EXIT_PROCESS)


def subreaper() -> bool:
    """Makes this process a child subreaper on Linux, so orphans of its tree stay in it."""
    if not sys.platform.startswith('linux'):
        return False
    try:
        import ctypes

        # Linux: PR_SET_CHILD_SUBREAPER is 36.
        return ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False


def install() -> None:
    """Guards this process, a format process or a child, before any other code of it runs."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    from . import cpusets as _cpusets
    from . import threads as _threads

    _cpusets.pin_self()
    subreaper()
    _threads.install()
    sys.addaudithook(_hook)
