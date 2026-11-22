# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The safety ceilings of the renderer, constants in code, and the guard of a side's process tree.

The ceilings protect the machine. A format may tighten them and never loosen them, and no
setting changes them. They are, design v6 11:

| Ceiling | Value |
|---|---|
| Memory of a side's process tree | 65,536 MiB, or the format's own `memory_mib` where lower |
| Processes of a side's tree | 8, beside the launcher of a virtual environment on Windows |
| Threads of a side's tree | the format's `threads` plus 10 for each process of the tree |
| A step | 300 s, and an option of `bounds = "step"` at most that |
| A match | the format's `match_minutes`, at most 360 |
| A pre-check | the format's `check_minutes`, at most 120 |

`check_ranges(spec)` refuses a format whose options allow more time than its `match_minutes`
holds, when the format is added. `guard(process, threads, on_ceiling)` gives a side what it plays
under, its environment, its CPU set, its share of the GPU and the watch on its tree.

A match process that a ceiling ends writes its last words, one line on standard error that names
the side and the ceiling, and exits with `EXIT_CEILING`. `read_last_words` reads them back for
the process that started it, which records the forfeit against that side.

This module uses the standard library alone, since a match process imports it early.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from collections.abc import Callable, Iterable
from typing import Any

# The memory of a side's process tree, in MiB.
MEMORY_MIB = 65536
# The processes of a side's tree, the format process and every process it starts.
PROCESSES = 8
# The threads of each process of a tree beyond those the format declares, for the interpreter,
# the pipe's reader and those the system and libraries keep, as strict/1 has always allowed.
THREAD_ALLOWANCE = 10
# The seconds between two looks at a tree.
POLL_SECONDS = 0.25
# The seconds of one step, and the ceilings of `match_minutes` and `check_minutes`.
STEP_SECONDS = 300
MATCH_MINUTES = 360
CHECK_MINUTES = 120
# GRF: a full match of the shipped scenarios lasts 3000 steps, the longest of any scenario.
LONGEST_STEPS = 3000
# The exit code of a match process that a ceiling ended, and of a format process that a
# submission ended by starting a process.
EXIT_CEILING = 80
EXIT_PROCESS = 81
# The variable that marks a process started to play matches, which a ceiling may end.
MATCH_PROCESS = 'CUJU_ARENA_MATCH_PROCESS'
# The start of the line of last words.
LAST_WORDS = 'cuju-arena: ceiling '
# The kinds of ceiling that the watch on a tree records.
KINDS = ('ceiling.memory', 'ceiling.threads', 'ceiling.process')


@dataclasses.dataclass(frozen=True)
class Limits:
    """The ceilings of one side's process tree.

    Attributes:
        memory_mib: The memory of the whole tree.
        processes: The processes of the tree.
        threads: The threads the format declares, before the allowance.
    """

    memory_mib: int
    processes: int = PROCESSES
    threads: int = 1

    @property
    def memory_bytes(self) -> int:
        """The memory of the whole tree, in bytes."""
        return int(self.memory_mib) * 1024 * 1024

    def thread_limit(self, processes: int) -> int:
        """Returns the threads a tree of `processes` processes may hold."""
        return int(self.threads) + THREAD_ALLOWANCE * max(1, int(processes))


def limits(process: Any, threads: int | None = None) -> Limits:
    """Returns the ceilings of a side's tree under a format's `[process]` table.

    Args:
        process: The format's `contract.Process`.
        threads: The threads the side plays with, by default the format's own.
    """
    memory = getattr(process, 'memory_mib', None) or MEMORY_MIB
    return Limits(
        memory_mib=min(int(memory), MEMORY_MIB),
        processes=PROCESSES,
        threads=int(threads or getattr(process, 'threads', 1) or 1),
    )


def worst_seconds(spec: Any, steps: int = LONGEST_STEPS) -> float:
    """Returns the most time a side may take over a match within its format's own options.

    Every option of `bounds = "step"` but the one of the smallest maximum counts once, as a
    first call or a reset does, and the smallest counts at every other step. Every option of
    `bounds = "match"` counts once with its maximum.
    """
    step = sorted(
        float(option.range[1])
        for option in spec.options.values()
        if option.bounds == 'step' and option.range is not None
    )
    match = sum(
        float(option.range[1])
        for option in spec.options.values()
        if option.bounds == 'match' and option.range is not None
    )
    if not step:
        return match
    once = sum(step[1:])
    return once + step[0] * max(0, int(steps) - (len(step) - 1)) + match


def check_ranges(spec: Any, steps: int = LONGEST_STEPS) -> None:
    """Refuses a format whose options allow more time than the renderer's ceilings honor.

    An option of `bounds = "step"` allows at most 300 s and needs a range, an option of
    `bounds = "match"` needs a range, and `worst_seconds` over the longest scenario must fit in
    the format's `match_minutes`, so the format's own forfeit always comes before the ceiling.

    Raises:
        ContractError: If a range cannot be honored.
    """
    from .contract import ContractError

    for option in spec.options.values():
        if option.bounds is None:
            continue
        if option.range is None:
            raise ContractError(f'[options.{option.key}] has bounds, so it needs a range')
        if option.bounds == 'step' and option.range[1] > STEP_SECONDS:
            raise ContractError(
                f'[options.{option.key}] allows {option.range[1]:g} s a step, '
                f'the ceiling is {STEP_SECONDS} s'
            )
    minutes = float(spec.process.match_minutes)
    worst = worst_seconds(spec, steps)
    if worst > minutes * 60.0:
        raise ContractError(
            f'the options allow {worst / 60.0:.1f} minutes over {steps} steps, '
            f'past match_minutes = {minutes:g}'
        )


def fits(spec: Any, steps: int) -> bool:
    """Returns whether a format's ranges fit a match of `steps` steps, such as a set length."""
    return worst_seconds(spec, steps) <= float(spec.process.match_minutes) * 60.0


class Guard:
    """What one side plays under, its environment, CPU set, GPU share and the watch on its tree.

    Attributes:
        limits: The `Limits` of the tree.
        environment: The variables the side's process takes beyond the minimal environment.
        cpus: The CPU set of the side, or `None` where nothing is pinned.
        share: The `gpu.Share` of the side, or `None`.
        tree: The `tree.Tree`, which the side's process starts under.
    """

    def __init__(
        self,
        process: Any,
        threads: int | None,
        on_ceiling: Callable[[str, str, str | None], None],
        pin: bool = True,
        monitor: Callable[[Limits, Iterable[int] | None, Callable[[], None]], Any] | None = None,
    ) -> None:
        from . import cpusets as _cpusets
        from . import gpu as _gpu
        from . import threads as _threads
        from . import tree as _tree

        self.limits = limits(process, threads)
        self.cpus = _cpusets.SETS.acquire(self.limits.threads) if pin else None
        allowed = getattr(process, 'gpu', 'never') == 'allowed'
        self.share = _gpu.SHARES.acquire() if allowed else None
        self.environment = {
            **_threads.variables(self.limits.threads),
            **_gpu.environment(allowed, self.share),
        }
        if self.cpus is not None:
            self.environment[_cpusets.VARIABLE] = _cpusets.text(self.cpus.cpus)
        cpus = None if self.cpus is None else self.cpus.cpus
        self.tree = (
            _tree.Tree(self.limits, on_ceiling, cpus=cpus, on_close=self.release)
            if monitor is None
            else monitor(self.limits, cpus, self.release)
        )

    def release(self) -> None:
        """Gives back the side's CPU set and GPU share, once its tree has ended."""
        from . import cpusets as _cpusets
        from . import gpu as _gpu

        cpus, self.cpus = self.cpus, None
        share, self.share = self.share, None
        if cpus is not None:
            _cpusets.SETS.release(cpus)
        if share is not None:
            _gpu.SHARES.release(share)


def guard(
    process: Any,
    threads: int | None,
    on_ceiling: Callable[[str, str, str | None], None],
    pin: bool = True,
    monitor: Callable[[Limits, Iterable[int] | None, Callable[[], None]], Any] | None = None,
) -> Guard:
    """Returns the `Guard` of a side whose format plays in a process of its own.

    Args:
        process: The format's `contract.Process`.
        threads: The threads the side plays with, by default the format's own.
        on_ceiling: What records a ceiling against the side, `host.ceiling`.
        pin: Whether the side takes a CPU set of its own.
    """
    return Guard(process, threads, on_ceiling, pin, monitor)


def last_words(
    side: str,
    kind: str,
    message: str,
    step: int | None = None,
    score: Iterable[int] | None = None,
) -> str:
    """Returns the line a match process writes before a ceiling ends it.

    Args:
        side: `left` or `right`.
        kind: The `ceiling.` kind.
        message: What happened, one line.
        step: The step the side was asked, or `None` before its first.
        score: The score at that step from the left side's view, or `None`.
    """
    found = {
        'side': side,
        'kind': kind,
        'message': ' '.join(str(message).split()),
        'step': None if step is None else int(step),
        'score': None if score is None else [int(goals) for goals in score],
    }
    return LAST_WORDS + json.dumps(found, sort_keys=True) + '\n'


def say_last_words(
    side: str,
    kind: str,
    message: str,
    step: int | None = None,
    score: Iterable[int] | None = None,
) -> None:
    """Writes the last words of a match process to its standard error, whatever Python holds."""
    line = last_words(side, kind, message, step, score).encode('utf-8', 'replace')
    try:
        sys.stderr.flush()
    except (OSError, ValueError, AttributeError):
        pass
    try:
        os.write(2, line)
    except OSError:
        pass


def read_last_words(lines: Iterable[str]) -> dict[str, Any] | None:
    """Returns the last words among the lines of a process's standard error, or `None`.

    The last such line counts. A line that does not read is passed over.
    """
    found = None
    for line in lines:
        at = line.find(LAST_WORDS)
        if at < 0:
            continue
        try:
            value = json.loads(line[at + len(LAST_WORDS) :])
        except ValueError:
            continue
        if (
            isinstance(value, dict)
            and value.get('side') in ('left', 'right')
            and isinstance(value.get('kind'), str)
            and value['kind'].startswith('ceiling.')
        ):
            found = value
    return found


def rule(words: dict[str, Any], names: list[Any]) -> dict[str, Any]:
    """Returns last words as the rule of a forfeit, as a tournament's result keeps it.

    Args:
        words: The last words, as `read_last_words` gives them.
        names: The name of the left and of the right side, text or a label.
    """
    from .._text import messages as _messages

    index = 0 if words['side'] == 'left' else 1
    reason = _messages.label('server.formats.forfeit', player=names[index], reason=words['message'])
    return {
        'side': ('home', 'away')[index],
        'kind': words['kind'],
        'reason': reason['text'],
        'reason_id': reason['id'],
        'reason_values': reason['values'],
        'detail': None,
        'step': words.get('step') or 0,
        'at': words.get('score') or [0, 0],
    }


def match_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """Returns the environment of a match process, which a ceiling may end with its last words."""
    found = dict(os.environ if base is None else base)
    found[MATCH_PROCESS] = '1'
    return found


def in_match_process() -> bool:
    """Returns whether this process was started to play matches."""
    return bool(os.environ.get(MATCH_PROCESS))
