# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The two hosts that play one side of a match through a format.

- `ProcessHost` runs the format in a process of its own for the side, started by
  `launch.Process`, with frames on its pipes. Each step the match sends every such side its step
  first and reads the answers after, so the sides think at the same time and the engine still
  steps in lockstep.
- `MatchHost` runs a built-in format that declares `[process] host = "match"` in the match process
  itself, as cuju-render played teams before formats. A watchdog thread times each call.

Both keep the renderer's four stops for the machine, each a forfeit of the side whose answer is
missing, never a match without a result:

| Kind | When |
|---|---|
| `ceiling.answer` | no answer to a step within 300 s |
| `ceiling.time` | the side's own round trips over a match past the format's `match_minutes` |
| `ceiling.load` | `start` past 15 minutes |
| `ceiling.crash` | the side's format process, or its submission in the match process, ends |

`ceiling(kind, message)` records any other ceiling against the side, such as those of the watch
on its process tree, and ends its process.

A failure is blamed by where it arose (`blame`). The submission's own error is the team's forfeit,
`load` in `start` and `crash` later, and an error of the format's own code is a `FormatFailure`,
which gives no result and marks the format failing, never a forfeit of the team.
"""

from __future__ import annotations

import _thread
import importlib
import os
import signal
import sys
import threading
import time
from typing import Any, BinaryIO

from ..formats import v1 as _v1
from ..formats.v1 import _child, _context
from . import blame as _blame
from . import ceilings as _ceilings
from . import launch as _launch
from . import terminals as _terminals

# The renderer's own stops, design v6 2.
ANSWER_SECONDS = 300.0
LOAD_SECONDS = 15 * 60.0
# The seconds a call in the match process has, after the watchdog interrupted it, to give way
# before the match process ends itself.
_GRACE = 5.0
# The seconds the host waits for the news of a system that refused a side memory or a
# process, once the side's process ended or answered an error.
_SETTLE = 0.5
versions = _child.versions


class SideForfeit(Exception):  # noqa: N818, it is a forfeit, not a failure of the renderer
    """A forfeit of the side, the format's own or one of the renderer's ceilings.

    Attributes:
        kind: One of the contract's five kinds or a `ceiling.` kind.
        message: What happened, one line.
        detail: A finer name, or `None`.
    """

    def __init__(self, kind: str, message: str, detail: str | None = None) -> None:
        self.kind = kind
        self.message = message
        self.detail = detail
        super().__init__(f'{kind}: {message}')

    def as_dict(self) -> dict[str, Any]:
        """Returns the forfeit as JSON."""
        return {'kind': self.kind, 'message': self.message, 'detail': self.detail}


class FormatFailure(RuntimeError):  # noqa: N818, the contract names it so
    """A failure of the format's own code, which gives no result and blames no team."""


def check_answer(answer: Any, step: _v1.Step) -> list[int] | None:
    """Returns a player's answer as actions, checked by the renderer's own rule.

    An answer to a step whose answer is not used may be `None`. Every other answer holds one
    whole number for each entry of `step.raw`, inside the action set, where a negative number
    counts from the end as GRF reads it.

    Raises:
        FormatFailure: If the answer does not fit, which `as_actions` should have caught.
    """
    if answer is None:
        if step.used:
            raise FormatFailure('the format answered nothing to a step whose answer is used')
        return None
    if hasattr(answer, 'tolist') and not isinstance(answer, list | tuple):
        answer = answer.tolist()
    size = _v1.ACTION_COUNTS[step.action_set]
    if (
        not isinstance(answer, list | tuple)
        or len(answer) != len(step.raw)
        or not all(
            isinstance(value, int) and not isinstance(value, bool) and -size <= value < size
            for value in answer
        )
    ):
        raise FormatFailure(
            f'the format answered {answer!r}, expected {len(step.raw)} actions of {size}'
        )
    return [int(value) for value in answer]


class Host:
    """What both hosts share, the side's format, its match, its time and its stops.

    Attributes:
        format: The `registry.Format` the side plays under.
        match: The side's `v1.Match`.
        seconds: The side's own round trips over the match so far.
        last: The round trip of the last step, or `None` before the first.
        revision: What a remote player reported as its model after `start`, or `None`.
        load_seconds: The seconds `start` took.
        pending: A ceiling recorded from outside, raised at the next call, or `None`.
        guard: The `SideGuard` that holds the side's processes to the ceilings, or `None` for
            a host that plays as before them.
    """

    def __init__(
        self,
        format: Any,
        match: _v1.Match,
        terminal: BinaryIO | None = None,
        guarded: bool = True,
        threads: int | None = None,
    ) -> None:
        self.format = format
        self.match = match
        self.terminal = terminal
        self.seconds = 0.0
        self.last: float | None = None
        self.revision: str | None = None
        self.load_seconds: float | None = None
        self.pending: SideForfeit | None = None
        self._limit = float(format.spec.process.match_minutes) * 60.0
        self.guard: SideGuard | None = (
            SideGuard(format.spec.process, threads, self.ceiling) if guarded else None
        )

    def ceiling(self, kind: str, message: str, detail: str | None = None) -> None:
        """Records a ceiling against the side, which its next call raises, and ends its work."""
        if not kind.startswith('ceiling.'):
            raise ValueError(f'a ceiling kind starts with ceiling., found {kind!r}')
        self.pending = SideForfeit(kind, message, detail)
        self._stop()

    def note(self, kind: str, /, **fields: Any) -> None:
        """Writes a line of the renderer to the side's terminal, if it has one."""
        if self.terminal is not None and hasattr(self.terminal, 'note'):
            self.terminal.note(kind, **fields)

    def _stop(self) -> None:
        """Ends what the side runs, where a host can end it."""

    def _raise_pending(self, wait: float = 0.0) -> None:
        # A ceiling of the side's trees, which the watch or the system reported, wins over what
        # the side's process said or did as it broke it.
        if self.pending is None and self.guard is not None:
            self.pending = self.guard.pending(wait)
        if self.pending is not None:
            raise self.pending

    def _settle(self) -> None:
        # The process ended or answered an error, which a system that refused it memory or a
        # process reports a moment later, so the side's ceiling waits that long to be seen.
        self._raise_pending(_SETTLE)

    def _count(self, seconds: float) -> None:
        self.last = seconds
        self.seconds += seconds
        if self.seconds > self._limit:
            minutes = self._limit / 60.0
            raise SideForfeit(
                'ceiling.time',
                f'the answers took {self.seconds / 60.0:.1f} minutes, past {minutes:g}',
            )

    def start(self) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError

    def send(self, step: _v1.Step) -> None:
        raise NotImplementedError

    def receive(self) -> list[int] | None:
        raise NotImplementedError

    def end(self, result: _v1.Result) -> SideForfeit | None:
        raise NotImplementedError

    def versions(self) -> dict[str, str]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def _forfeit_of(value: dict[str, Any], starting: bool) -> SideForfeit | FormatFailure:
    """Returns what an error frame of a format process stands for."""
    kind = value.get('kind')
    message = value.get('message', '')
    if kind == 'forfeit':
        found = value['forfeit']
        return SideForfeit(found['kind'], found.get('message', ''), found.get('detail'))
    if kind == 'invalid':
        return SideForfeit('invalid', message)
    if kind == 'missing':
        return SideForfeit('load', message, 'environment')
    if kind == 'crash':
        return SideForfeit('load' if starting else 'crash', message)
    return FormatFailure(message or 'the format failed')


class SideGuard:
    """What holds a side's processes to the renderer's safety ceilings, which a host owns.

    A host with a guard starts each process of its side through `start`, which gives the
    process a tree of its own under the side's `ceilings.Limits`, its CPU set and its share of
    the GPU. `pending` reads the ceiling any of those trees broke, `settle` waits a moment for
    the news of a system that refused memory or a process, and `last_words` names the side's
    call for a match process that a stall ends. A host without a guard plays as before the
    ceilings, with no tree, no CPU set and the plain line of a stall.

    Args:
        process: The format's `contract.Process`.
        threads: The threads of the side, by default the format's own.
        on_ceiling: What records a ceiling against the side, `Host.ceiling`, or `None` for a
            pre-check, which reads `pending` itself.
    """

    def __init__(
        self,
        process: Any,
        threads: int | None = None,
        on_ceiling: Any = None,
    ) -> None:
        self.process = process
        self.threads = threads or process.threads
        self.on_ceiling = on_ceiling
        self.trees: list[Any] = []
        self.monitor = None

    def start(
        self,
        folder: Any,
        terminal: Any = None,
        threads: int | None = None,
        extra: dict[str, str] | None = None,
        base: dict[str, str] | None = None,
        names: Any = (),
    ) -> _launch.Process:
        """Starts a process of the side in a tree of its own, and returns it.

        Args:
            folder: Its working folder, the submission's.
            terminal: Where its standard error goes, or `None`.
            threads: Its threads, by default the side's.
            extra: Variables to add after the guard's own.
            base: The environment to start from, as `launch.environment` reads it.
            names: The variables of `[process] env` that it takes from `base`.
        """
        # A tree that ended stays only for what its end may still tell.
        self.trees = [kept for kept in self.trees if not kept.tree.closed]
        threads = threads or self.threads
        found = _ceilings.guard(self.process, threads, self.on_ceiling, monitor=self.monitor)
        environment = _launch.environment(
            threads, {**found.environment, **(extra or {})}, base=base, names=names
        )
        try:
            started = _launch.Process(folder, environment, terminal, tree=found.tree)
        except BaseException:
            found.release()
            raise
        self.trees.append(found)
        return started

    def pending(self, wait: float = 0.0) -> SideForfeit | None:
        """Returns the ceiling a tree of the side broke, or `None`.

        Args:
            wait: The seconds to wait for the news of a system that enforces a limit.
        """
        for found in self.trees:
            broken = found.tree.verdict(wait)
            if broken is not None:
                return SideForfeit(*broken)
        return None

    def settle(self) -> SideForfeit | None:
        """Returns the ceiling a tree broke, waiting the moment a system takes to report it."""
        return self.pending(_SETTLE)

    @staticmethod
    def last_words(
        match: _v1.Match, step: _v1.Step | None, starting: bool, timeout: float
    ) -> dict[str, Any]:
        """Returns what the last words of the match process say of a side's call."""
        score = None
        if step is not None:
            # A side sees its own team as the left, so the right side's score turns round.
            score = list(step.score) if match.side == 'left' else list(step.score)[::-1]
        return {
            'side': match.side,
            'kind': 'ceiling.load' if starting else 'ceiling.answer',
            'message': f'no answer within {timeout:g} s',
            'step': 0 if step is None else step.number,
            'score': score,
        }

    def close(self) -> None:
        """Ends every tree of the side and gives back what they held."""
        for found in self.trees:
            found.tree.close()
        self.trees = []


class ProcessHost(Host):
    """A side played by a format in a process of its own."""

    def __init__(
        self,
        format: Any,
        match: _v1.Match,
        terminal: BinaryIO | None = None,
        threads: int | None = None,
        extra: dict[str, str] | None = None,
        guarded: bool = True,
    ) -> None:
        super().__init__(format, match, terminal, guarded, threads)
        self._threads = threads or format.spec.process.threads
        self._extra = extra or {}
        self._pipe_terminal = terminal
        self._process: _launch.Process | None = None
        self._step: _v1.Step | None = None
        self._sent = 0.0

    def _stop(self) -> None:
        if self._process is not None:
            self._process.kill()

    def _call(self, method: str, arguments: list[Any], timeout: float, starting: bool) -> Any:
        self._raise_pending()
        try:
            self._process.send('call', {'method': method, 'arguments': arguments})
            answer = self._process.receive(timeout)
        except _launch.Timeout:
            self._process.kill()
            kind = 'ceiling.load' if starting else 'ceiling.answer'
            raise SideForfeit(kind, f'{method} gave no answer within {timeout:g} s') from None
        except _launch.Ended as error:
            self._settle()
            raise SideForfeit('ceiling.crash', f'the format process ended, {error}') from None
        if answer['kind'] == 'error':
            self._settle()
            raise _forfeit_of(answer['value'], starting)
        return answer.get('value')

    @property
    def pid(self) -> int | None:
        """The identifier of the side's format process, or `None` before it starts."""
        return None if self._process is None else self._process.pid

    def alive(self) -> bool:
        """Returns whether the side's format process runs and may play another match."""
        return self._process is not None and self._process.alive() and self.pending is None

    def start(self) -> None:
        """Starts the side's process, loads the format and calls its `start`."""
        began = time.perf_counter()
        self._launch()
        self._begin(began)

    def again(self, match: _v1.Match, terminal: BinaryIO | None = None) -> None:
        """Calls the format's `start` for another match in the process that played the last one.

        A process of `[process] scope = "run"` serves the consecutive matches of a run, so the
        format's module and what it keeps at module level load once. `start`, `reset`, `end`
        and `close` still come once for every match, and the side's time starts again.
        """
        self.match = match
        self.seconds = 0.0
        self.last = None
        self._limit = float(self.format.spec.process.match_minutes) * 60.0
        if terminal is not None:
            # What the process prints now belongs to the new match's terminal.
            if self.terminal is not None and hasattr(self.terminal, 'forward_to'):
                self.terminal.forward_to(terminal)
            self.terminal = terminal
        self.note('again', format=self.format.label)
        self._begin(time.perf_counter())

    def finish_match(self) -> None:
        """Calls the player's `close` and keeps the process for the next match of the run."""
        self._call('close', [], ANSWER_SECONDS, False)
        # The match's terminal ends with it, and the process goes on to the next one.
        if self.terminal is not None and hasattr(self.terminal, 'close'):
            if hasattr(self._pipe_terminal, 'drain'):
                self._pipe_terminal.drain()
            self.terminal.close(wait=False)

    def _begin(self, began: float) -> None:
        left = max(1.0, LOAD_SECONDS - (time.perf_counter() - began))
        found = self._call('start', [self.match.as_dict()], left, True) or {}
        self.revision = found.get('revision')
        self.load_seconds = time.perf_counter() - began

    @property
    def tree(self) -> Any:
        """The `tree.Tree` of the side's process, or `None` before it starts or without a guard."""
        return None if self._process is None else self._process.tree

    def _launch(self) -> None:
        names = self.format.spec.process.env
        if self.guard is not None:
            self._process = self.guard.start(
                self.match.folder, self.terminal, self._threads, self._extra, names=names
            )
        else:
            environment = _launch.environment(self._threads, self._extra, names=names)
            self._process = _launch.Process(self.match.folder, environment, self.terminal)
        self.note('start', format=self.format.label, pid=self._process.pid)
        builtin = self.format.builtin
        self._process.send(
            'hello',
            {
                'role': 'format',
                'folder': str(self.match.folder),
                'script': str(self.format.folder / self.format.spec.script),
                'format_folder': None if builtin else str(self.format.folder),
                'builtin': builtin,
                'module': self.format.module,
            },
        )
        try:
            answer = self._process.receive(LOAD_SECONDS)
        except _launch.Timeout:
            self._process.kill()
            raise SideForfeit('ceiling.load', 'the format did not load within 15 minutes') from None
        except _launch.Ended as error:
            self._settle()
            raise SideForfeit('ceiling.crash', f'the format process ended, {error}') from None
        if answer['kind'] == 'error':
            self._settle()
            raise _forfeit_of(answer['value'], True)

    def reset(self) -> None:
        self._call('reset', [], ANSWER_SECONDS, False)

    def send(self, step: _v1.Step) -> None:
        """Sends a step, whose answer `receive` reads."""
        self._raise_pending()
        self._step = step
        self._sent = time.perf_counter()
        try:
            self._process.send('call', {'method': 'act', 'arguments': [step.as_dict()]})
        except _launch.Ended as error:
            self._settle()
            raise SideForfeit('ceiling.crash', f'the format process ended, {error}') from None

    def receive(self) -> list[int] | None:
        """Returns the answer to the step sent last, checked.

        Raises:
            SideForfeit: For the format's forfeit or a ceiling.
            FormatFailure: For an error of the format's code, or an answer that does not fit.
        """
        step = self._step
        try:
            answer = self._process.receive(ANSWER_SECONDS)
        except _launch.Timeout:
            self._process.kill()
            raise SideForfeit(
                'ceiling.answer', f'step {step.number} gave no answer within 300 s'
            ) from None
        except _launch.Ended as error:
            self._settle()
            raise SideForfeit('ceiling.crash', f'the format process ended, {error}') from None
        self._count(time.perf_counter() - self._sent)
        if answer['kind'] == 'error':
            self._settle()
            raise _forfeit_of(answer['value'], False)
        return check_answer(answer.get('value'), step)

    def end(self, result: _v1.Result) -> SideForfeit | None:
        found = self._call('end', [result.as_dict()], ANSWER_SECONDS, False)
        if found is None:
            return None
        return SideForfeit(found['kind'], found.get('message', ''), found.get('detail'))

    def versions(self) -> dict[str, str]:
        """Returns the versions of the frameworks the format process imported."""
        try:
            return dict(self._call('versions', [], 30.0, False) or {})
        except (SideForfeit, FormatFailure):
            return {}

    def close(self) -> None:
        if self._process is not None:
            code = self._process.close()
            self._process = None
            self.note('exit', code=code)
        # The tree ends before the terminal, so the last words of its processes still reach it.
        if self.guard is not None:
            self.guard.close()
        if self.terminal is not None and hasattr(self.terminal, 'close'):
            self.terminal.close()


# The process's own end, which the watchdog keeps while a call of a side borrows `os._exit`.
_EXIT = os._exit
_NO_EXIT = object()
# The module of the system that `os` takes `_exit` from, `posix` or `nt`, whose `_exit` is the
# same function on CPython, so a submission reaches it by either name.
_NATIVE = sys.modules.get(os.name)


class _Exited(BaseException):
    """Raised where a submission in the match process called `os._exit`."""

    def __init__(self, code: Any) -> None:
        self.code = code
        super().__init__(code)


class _Exits:
    """Turns a call of `os._exit` by a side in the match process into that side's forfeit.

    A submission that ends the process would end the match with it, and the other side's
    result. While a side's call runs, `os._exit` and `posix._exit`, or `nt._exit` on Windows,
    raise instead, and the call's forfeit follows even where the submission catches what it
    raised.
    """

    def __init__(self) -> None:
        self.code: Any = _NO_EXIT
        self._held: Any = None

    def __enter__(self) -> _Exits:
        if os._exit is not _dispatch:
            os._exit = _dispatch
        if _NATIVE is not None and getattr(_NATIVE, '_exit', None) is not _dispatch:
            _NATIVE._exit = _dispatch
        self._held = getattr(_CALLS, 'exits', None)
        _CALLS.exits = self
        return self

    def __exit__(self, *exception: Any) -> None:
        _CALLS.exits = self._held
        self._held = None


_CALLS = threading.local()


def _dispatch(code: Any = 0) -> None:
    """Stands in for `os._exit` on every thread, and ends the process outside a side's call."""
    exits = getattr(_CALLS, 'exits', None)
    if exits is None:
        _EXIT(code)
    exits.code = code
    raise _Exited(code)


class _Watchdog:
    """Times each call of a side in the match process, and interrupts one that stalls."""

    def __init__(self, words: dict[str, Any] | None = None) -> None:
        self.stalled = False
        self._timer: threading.Timer | None = None
        self._main = threading.get_ident()
        # The side and the step of the call, for the last words of the match process.
        self._words = words

    def __enter__(self) -> _Watchdog:
        return self

    def arm(self, seconds: float) -> None:
        self.stalled = False
        if threading.get_ident() != self._main:
            # A call on another thread has no main thread to interrupt, so a stop of the
            # process stands in for it.
            self._timer = threading.Timer(seconds, self._end_process)
        else:
            self._timer = threading.Timer(seconds, self._interrupt)
        self._timer.daemon = True
        self._timer.start()

    def _interrupt(self) -> None:
        self.stalled = True
        # A signal of its own wakes a main thread that sleeps, where the system has them.
        if hasattr(signal, 'pthread_kill'):
            signal.pthread_kill(self._main, signal.SIGINT)
        else:
            _thread.interrupt_main()
        late = threading.Timer(_GRACE, self._end_process)
        late.daemon = True
        late.start()
        self._late = late

    def _end_process(self) -> None:
        # A call that never gives way leaves the match process nothing else to do. The server
        # that started it records the forfeit from the line written here.
        self.stalled = True
        if _ceilings.in_match_process():
            if self._words is not None:
                _ceilings.say_last_words(**self._words)
            else:
                # A host without a guard says only that a side stalled.
                os.write(2, b'cuju-arena: a side stalled past the answer ceiling\n')
            _EXIT(_ceilings.EXIT_CEILING)

    def disarm(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        late = getattr(self, '_late', None)
        if late is not None:
            late.cancel()
            self._late = None

    def __exit__(self, *exception: Any) -> None:
        self.disarm()


class MatchHost(Host):
    """A side played by a built-in format in the match process."""

    def __init__(
        self,
        format: Any,
        match: _v1.Match,
        terminal: BinaryIO | None = None,
        guarded: bool = True,
    ) -> None:
        super().__init__(format, match, terminal, guarded)
        self._player: Any = None
        self._step: _v1.Step | None = None
        self._sent = 0.0
        self.context = _context.Context(
            format_folder=format.folder,
            submission=match.folder,
            environment=dict(os.environ),
            imports=_launch.imports(),
        )
        # A `Child` that a built-in format starts from the match process, as timed Kaggle does,
        # starts through the side's guard, under its ceilings, in a tree of its own.
        self.context.guard = self.guard
        if format.module is not None:
            self.module = importlib.import_module(format.module)
        else:
            self.module = _child.load_format(format.folder / format.spec.script)

    def call(self, function: Any, arguments: list[Any], timeout: float, starting: bool) -> Any:
        try:
            return self._call(function, arguments, timeout, starting)
        except (SideForfeit, FormatFailure):
            # A ceiling of a child's tree wins over what the child's end raised in the format.
            self._settle()
            raise

    def _call(self, function: Any, arguments: list[Any], timeout: float, starting: bool) -> Any:
        self._raise_pending()
        words = None
        if self.guard is not None:
            words = self.guard.last_words(self.match, self._step, starting, timeout)
        watchdog = _Watchdog(words)
        exits = _Exits()
        try:
            with _context.using(self.context), _terminals.capture(self.terminal), watchdog, exits:
                watchdog.arm(timeout)
                found = function(*arguments)
            if exits.code is not _NO_EXIT:
                raise _Exited(exits.code)
            self._raise_pending()
            return found
        except _Exited as error:
            raise SideForfeit(
                'ceiling.crash', f'the submission ended the match process, {error.code!r}', 'exit'
            ) from None
        except KeyboardInterrupt:
            if watchdog.stalled:
                kind = 'ceiling.load' if starting else 'ceiling.answer'
                raise SideForfeit(kind, f'no answer within {timeout:g} s') from None
            raise
        except SystemExit as error:
            raise SideForfeit('ceiling.crash', f'the submission exited, {error.code!r}') from None
        except _v1.Forfeit as error:
            raise SideForfeit(error.kind, error.message, error.detail) from None
        except _v1.Invalid as error:
            raise SideForfeit('invalid', ' '.join(str(error).split())) from None
        except _v1.Missing as error:
            raise SideForfeit('load', str(error), 'environment') from None
        except Exception as error:
            own = None if self.format.builtin else self.format.folder
            if _blame.blame(error, self.match.folder, own) == _blame.TEAM:
                raise SideForfeit('load' if starting else 'crash', _blame.last_line(error)) from (
                    error
                )
            raise FormatFailure(_blame.last_line(error)) from error

    def start(self) -> None:
        began = time.perf_counter()
        self.note('start', format=self.format.label)
        self._player = self.call(
            self.module.start, [self.match.folder, self.match], LOAD_SECONDS, True
        )
        revision = getattr(self._player, 'revision', None)
        self.revision = None if revision is None else str(revision)[:64]
        self.load_seconds = time.perf_counter() - began

    def reset(self) -> None:
        found = getattr(self._player, 'reset', None)
        if found is not None:
            self.call(found, [], ANSWER_SECONDS, False)

    @property
    def concurrent(self) -> bool:
        """Whether the player thinks between `send` and `receive`, as `strict/1`'s players do.

        Such a player has `send(step)` and `receive()` of its own, which start the step's
        thinking and read its answer, so the sides of a match in the match process think at the
        same time, as they do in processes of their own.
        """
        player = self._player
        return callable(getattr(player, 'send', None)) and callable(
            getattr(player, 'receive', None)
        )

    def send(self, step: _v1.Step) -> None:
        self._step = step
        self._sent = time.perf_counter()
        if self.concurrent:
            self.call(self._player.send, [step], ANSWER_SECONDS, False)

    def receive(self) -> list[int] | None:
        step = self._step
        if self.concurrent:
            answer = self.call(self._player.receive, [], ANSWER_SECONDS, False)
            # The side's time runs from its step's sending, while the other sides thought too.
            self._count(time.perf_counter() - self._sent)
            return check_answer(answer, step)
        began = time.perf_counter()
        answer = self.call(self._player.act, [step], ANSWER_SECONDS, False)
        self._count(time.perf_counter() - began)
        return check_answer(answer, step)

    def end(self, result: _v1.Result) -> SideForfeit | None:
        found = getattr(self._player, 'end', None)
        if found is None:
            return None
        forfeit = self.call(found, [result], ANSWER_SECONDS, False)
        if forfeit is None:
            return None
        if not isinstance(forfeit, _v1.Forfeit):
            raise FormatFailure(f'end returned {type(forfeit).__name__}, not a forfeit')
        return SideForfeit(forfeit.kind, forfeit.message, forfeit.detail)

    def versions(self) -> dict[str, str]:
        return _child.versions()

    def close(self) -> None:
        found = getattr(self._player, 'close', None)
        self._player = None
        if found is not None:
            try:
                self.call(found, [], ANSWER_SECONDS, False)
            except (SideForfeit, FormatFailure):
                pass
        # Whatever children the player left, or a ceiling kept it from closing, end with it, before
        # the terminal closes, so their last words still reach it.
        if self.guard is not None:
            self.guard.close()
        if self.terminal is not None and hasattr(self.terminal, 'close'):
            self.terminal.close()


def host_for(format: Any, match: _v1.Match, terminal: BinaryIO | None = None) -> Host:
    """Returns the host of a side, by the format's `[process] host`."""
    if format.spec.process.host == 'match':
        return MatchHost(format, match, terminal)
    return ProcessHost(format, match, terminal)
