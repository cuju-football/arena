# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The pool of a run, which keeps the format processes of `[process] scope = "run"` alive.

A run is a run of consecutive matches, such as a rating run, an evaluation or a round of a
tournament. Under a format of `scope = "run"` a side's process lives across the matches of the
run, so the format's module, and what it loads at module level such as a model, loads once. The
format's `start`, `reset`, `end` and `close` still come once for every match.

    with pool.Pool() as run:
        for match in matches:
            play(..., pool=run)

A host is kept under its format's identity, the checkpoint and the effective control, and only
between matches, so two sides of one team in one match get a process each. A side that met a
forfeit, a ceiling or a failure, or whose process ended, is never kept, and the next match starts
a fresh process. At the end of the run, on an exception, a stop by Ctrl-C or the server's end,
every process of the pool is closed, and the read-only copy of its submission removed. A process
whose parent ends reads the end of its pipe and exits by itself, so nothing outlives the run.

Under `scope = "match"`, the default, a side's process serves one match alone, as without a pool.
`current()` gives the pool of the block that `Pool` opens, for a caller that cannot hand it down.
"""

from __future__ import annotations

import atexit
import contextvars
import dataclasses
import hashlib
import json
import threading
import weakref
from pathlib import Path
from typing import Any

from . import identity as _identity

_CURRENT: contextvars.ContextVar[Pool | None] = contextvars.ContextVar(
    'cuju_format_pool', default=None
)
_OPEN: weakref.WeakSet[Pool] = weakref.WeakSet()


@dataclasses.dataclass
class Lease:
    """A host of the pool and the read-only copy of the submission it plays from.

    Attributes:
        key: The key it is kept under.
        host: The `host.ProcessHost`.
        copy: The read-only copy, which leaves with the host, or `None`.
        matches: The matches it has played.
    """

    key: str
    host: Any
    copy: Path | None
    matches: int = 0


def key_of(choice: Any, checkpoint: str | None, control: Any) -> str:
    """Returns the key of a host, the format's identity, the checkpoint and the control."""
    text = json.dumps(
        {
            'identity': _identity.key(choice.identity()),
            'checkpoint': checkpoint,
            'control': control,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]


class Pool:
    """The format processes of one run, kept between its matches and closed at its end."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._idle: dict[str, list[Lease]] = {}
        self._leases: list[Lease] = []
        self._closed = False
        self._token: contextvars.Token[Pool | None] | None = None
        _OPEN.add(self)

    def __enter__(self) -> Pool:
        self._token = _CURRENT.set(self)
        return self

    def __exit__(self, *exception: Any) -> None:
        if self._token is not None:
            _CURRENT.reset(self._token)
            self._token = None
        self.close()

    @property
    def closed(self) -> bool:
        """Whether the run has ended."""
        return self._closed

    def take(self, key: str) -> Lease | None:
        """Returns a kept host of a key whose process still runs, or `None` for a fresh one."""
        with self._lock:
            idle = self._idle.get(key) or []
            while idle:
                lease = idle.pop()
                if lease.host.alive():
                    return lease
                self._retire(lease)
            return None

    def adopt(self, lease: Lease) -> None:
        """Counts a fresh host as the pool's, so the end of the run closes it whatever happens."""
        with self._lock:
            if self._closed:
                raise RuntimeError('the run of this pool has ended')
            self._leases.append(lease)

    def give(self, lease: Lease) -> None:
        """Keeps a host after a match that it finished cleanly, for the next match of the run."""
        with self._lock:
            lease.matches += 1
            if self._closed or not lease.host.alive():
                self._retire(lease)
                return
            self._idle.setdefault(lease.key, []).append(lease)

    def retire(self, lease: Lease) -> None:
        """Closes a host that met a forfeit, a ceiling or a failure, and removes its copy."""
        with self._lock:
            self._retire(lease)

    def _retire(self, lease: Lease) -> None:
        from . import side as _side

        try:
            lease.host.close()
        finally:
            if lease.copy is not None:
                _side.thaw(lease.copy)
                lease.copy = None
            if lease in self._leases:
                self._leases.remove(lease)
            for idle in self._idle.values():
                if lease in idle:
                    idle.remove(lease)

    def pids(self) -> list[int]:
        """Returns the identifiers of the processes the pool holds, in use or kept."""
        with self._lock:
            return [lease.host.pid for lease in self._leases if lease.host.pid is not None]

    def __len__(self) -> int:
        with self._lock:
            return len(self._leases)

    def close(self) -> None:
        """Closes every process of the pool, kept or in use, and removes their copies."""
        with self._lock:
            self._closed = True
            for lease in list(self._leases):
                self._retire(lease)
            self._idle.clear()


def current() -> Pool | None:
    """Returns the pool of the block that a `Pool` opened, or `None` outside one."""
    found = _CURRENT.get()
    return None if found is None or found.closed else found


@atexit.register
def _close_every_pool() -> None:
    # The renderer's end closes what a run that never ended still holds.
    for pool in list(_OPEN):
        pool.close()
