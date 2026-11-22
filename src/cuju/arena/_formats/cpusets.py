# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The CPU sets of the sides, disjoint and sized by each format's `[process] threads`.

Each side whose format plays in a process of its own takes a set of logical CPUs for its whole
process tree, by affinity on Linux and through its job object on Windows. The first CPU stays
with the renderer and the engine. On Linux the sets spread over the physical cores first, so two
sides of one thread each sit on cores of their own before any two share the halves of one core.
A side of `host = "match"` is never pinned, as cuju-render played before formats.

The sets stay disjoint while the sides at once fit the machine, which `matches_at_once` gives and
the scheduler of a run keeps to. A side that finds too few free CPUs takes those least in use,
and its set then overlaps another's, which `CpuSet.shared` says. macOS offers no affinity, so a
side there runs on every CPU.

This module uses the standard library alone, since a format process imports `pin_self`.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import threading
from collections.abc import Iterable
from pathlib import Path

# The variable that tells a format process its CPU set.
VARIABLE = 'CUJU_ARENA_CPUS'
# The CPUs kept for the renderer and the engine, first in the order.
RESERVED = 1
# The sides of one match.
SIDES = 2


@dataclasses.dataclass(frozen=True)
class CpuSet:
    """The CPUs of one side.

    Attributes:
        cpus: The logical CPUs, in order.
        shared: Whether another side held one of them when it was taken.
    """

    cpus: tuple[int, ...]
    shared: bool = False

    @property
    def mask(self) -> int:
        """The CPUs as a bit mask, as a job object of Windows takes them."""
        found = 0
        for cpu in self.cpus:
            found |= 1 << cpu
        return found


def text(cpus: Iterable[int]) -> str:
    """Returns CPUs as the variable carries them, numbers joined by commas."""
    return ','.join(str(int(cpu)) for cpu in cpus)


def parse(value: str | None) -> tuple[int, ...]:
    """Returns the CPUs of the variable's text, or none for a text that does not read."""
    try:
        return tuple(int(part) for part in (value or '').split(',') if part.strip())
    except ValueError:
        return ()


def available() -> list[int]:
    """Returns the logical CPUs this process may run on."""
    if hasattr(os, 'sched_getaffinity'):
        return sorted(os.sched_getaffinity(0))
    return list(range(os.cpu_count() or 1))


def order(cpus: Iterable[int], root: Path | str = '/sys/devices/system/cpu') -> list[int]:
    """Returns CPUs in the order sets take them, one of each physical core first on Linux.

    Args:
        cpus: The logical CPUs.
        root: The folder of the CPUs' topology, for the tests.
    """
    cpus = sorted(int(cpu) for cpu in cpus)
    folder = Path(root)
    if not sys.platform.startswith('linux') and str(root) == '/sys/devices/system/cpu':
        return cpus
    cores: dict[tuple[str, str], list[int]] = {}
    for cpu in cpus:
        topology = folder / f'cpu{cpu}' / 'topology'
        try:
            package = (topology / 'physical_package_id').read_text(encoding='ascii').strip()
            core = (topology / 'core_id').read_text(encoding='ascii').strip()
        except OSError:
            return cpus
        cores.setdefault((package, core), []).append(cpu)
    groups = sorted(cores.values(), key=lambda found: found[0])
    found: list[int] = []
    depth = max((len(group) for group in groups), default=0)
    for level in range(depth):
        found += [group[level] for group in groups if level < len(group)]
    return found


def matches_at_once(threads: int | Iterable[int], cpus: int | None = None) -> int:
    """Returns the matches that may play at once with disjoint sets, at least one.

    Args:
        threads: The threads of each side, one number for both sides, or one for each.
        cpus: The logical CPUs, by default those of this process.
    """
    count = len(available()) if cpus is None else int(cpus)
    sides = [int(threads)] * SIDES if isinstance(threads, int) else [int(t) for t in threads]
    need = max(1, sum(max(1, side) for side in sides))
    return max(1, (count - RESERVED) // need)


class Sets:
    """The CPU sets of the sides that play now, taken and given back.

    Args:
        cpus: The logical CPUs to share out, by default those of this process.
    """

    def __init__(self, cpus: Iterable[int] | None = None) -> None:
        self._lock = threading.Lock()
        self._cpus = list(cpus) if cpus is not None else None
        self._use: dict[int, int] = {}

    def cpus(self) -> list[int]:
        """Returns the CPUs that sets take, in their order, the reserved ones left out."""
        if self._cpus is None:
            self._cpus = order(available())
        found = self._cpus[RESERVED:] if len(self._cpus) > RESERVED else list(self._cpus)
        return found

    def acquire(self, threads: int) -> CpuSet:
        """Takes a set of `threads` CPUs, free ones where there are enough."""
        with self._lock:
            pool = self.cpus()
            size = max(1, min(int(threads), len(pool)))
            free = [cpu for cpu in pool if not self._use.get(cpu)]
            if len(free) >= size:
                chosen, shared = free[:size], False
            else:
                ranked = sorted(pool, key=lambda cpu: (self._use.get(cpu, 0), pool.index(cpu)))
                chosen, shared = ranked[:size], True
            for cpu in chosen:
                self._use[cpu] = self._use.get(cpu, 0) + 1
            return CpuSet(tuple(sorted(chosen)), shared)

    def release(self, found: CpuSet) -> None:
        """Gives a set back."""
        with self._lock:
            for cpu in found.cpus:
                left = self._use.get(cpu, 0) - 1
                if left > 0:
                    self._use[cpu] = left
                else:
                    self._use.pop(cpu, None)

    def held(self) -> dict[int, int]:
        """Returns the sides that hold each CPU now."""
        with self._lock:
            return dict(self._use)


# The sets of this process, which every host takes from.
SETS = Sets()


def pin_self() -> tuple[int, ...]:
    """Pins this process to the CPU set its variable names, on Linux, and returns the set."""
    found = parse(os.environ.get(VARIABLE))
    if found and hasattr(os, 'sched_setaffinity'):
        try:
            os.sched_setaffinity(0, found)
        except OSError:
            return ()
    return found
