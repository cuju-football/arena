# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""`Child`, a submission in a process of its own that a format times and stops from outside."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from cuju.arena._formats import launch as _launch

from . import _context
from ._verdicts import Forfeit, Invalid, Missing


class ChildTimeout(TimeoutError):  # noqa: N818, the contract names it so
    """Raised by `Child` for a load or a call that outlasted its time. The child is killed."""


class ChildCrash(RuntimeError):  # noqa: N818, the contract names it so
    """Raised by `Child` when the submission raised, or the child ended, during a load or call."""


class Child:
    """A process of its own that builds an object of one of the format's files and answers calls.

    The child starts as `python -I -X utf8` with `subprocess`, never by fork, with the format
    process's own environment and the thread count given. It puts the submission's folder first
    on `sys.path`, works from it, imports the format's `script` by its path under a synthetic name,
    and builds `factory(folder, *arguments)`. Each `call` then runs one method of that object, with
    the submission's folder as the working folder, and returns its value and the seconds it took
    as the format measures them, from its side of the pipe.

    Arguments and answers cross as frames, the raw observations of a step as typed arrays, other
    values as JSON, and an answer of at most 1 MiB. Nothing is ever pickled. What the child prints
    joins the terminal of the side.

    Attributes:
        load_seconds: The seconds the child took to build its object.
    """

    def __init__(
        self,
        folder: Path | str,
        script: str,
        factory: str,
        *arguments: Any,
        threads: int | None = None,
        load_timeout: float | None = None,
        forfeits: bool = False,
    ) -> None:
        """Starts the child and waits for its object.

        Args:
            folder: The submission's folder.
            script: A file of the format's folder, such as `inner.py`.
            factory: The name of what the file defines that builds the object, called with the
                folder and `arguments`.
            arguments: Values for the factory, as JSON or arrays.
            threads: The threads the child may use, or `None` for the format process's own.
            load_timeout: The seconds the load may take, or `None` without a limit.
            forfeits: Whether a failure to load raises `Forfeit`, `timeout` past the time and
                `crash` for an error of the submission, and the default of `call`.

        Raises:
            Forfeit: As above, with `forfeits`.
            ChildTimeout, ChildCrash: Without it.
            Missing: If the child lacks a module that the format requires.
        """
        context = _context.current()
        if context.format_folder is None:
            raise RuntimeError('a Child starts only from the code of a format')
        self.folder = Path(folder)
        path = (context.format_folder / script).resolve()
        if not path.is_relative_to(context.format_folder.resolve()) or not path.is_file():
            raise FileNotFoundError(f'the format holds no {script}')
        self.forfeits = forfeits
        base = context.environment if context.environment is not None else dict(os.environ)
        if context.guard is not None:
            # A child of the match process starts under its side's guard, in a tree of its own.
            self._process = context.guard.start(self.folder, threads=threads, base=base)
        else:
            self._process = _launch.Process(self.folder, _launch.environment(threads, base=base))
        self.load_seconds: float | None = None
        builtin = context.format_folder.resolve().is_relative_to(_launch.package_folder())
        try:
            self._process.send(
                'hello',
                {
                    'role': 'child',
                    'folder': str(self.folder),
                    'format_folder': None if builtin else str(context.format_folder),
                    'script': str(path),
                    'factory': factory,
                    'arguments': list(arguments),
                },
            )
            answer = self._process.receive(load_timeout)
        except _launch.Timeout:
            self._process.kill()
            self._raise_timeout(f'the submission did not load within {load_timeout:g} s', forfeits)
        except _launch.Ended as error:
            self._process.kill()
            self._raise_crash(f'the child ended while it loaded, {error}', forfeits)
        if answer['kind'] == 'error':
            self._process.kill()
            self._raise(answer['value'], forfeits)
        self.load_seconds = time.perf_counter() - self._process.started

    @staticmethod
    def _raise_timeout(message: str, forfeits: bool) -> None:
        if forfeits:
            raise Forfeit('timeout', message)
        raise ChildTimeout(message)

    @staticmethod
    def _raise_crash(message: str, forfeits: bool) -> None:
        if forfeits:
            raise Forfeit('crash', message)
        raise ChildCrash(message)

    def _raise(self, value: dict[str, Any], forfeits: bool) -> None:
        kind = value.get('kind')
        if kind == 'forfeit':
            found = value['forfeit']
            raise Forfeit(found['kind'], found.get('message', ''), found.get('detail'))
        if kind == 'missing':
            raise Missing(value.get('modules') or [], None)
        if kind == 'invalid':
            if forfeits:
                raise Forfeit('invalid', value.get('message', ''))
            raise Invalid(value.get('message', ''))
        if kind == 'crash':
            self._raise_crash(value.get('message', ''), forfeits)
        raise RuntimeError(f'the format failed in its child, {value.get("message", "")}')

    def call(
        self,
        method: str,
        *arguments: Any,
        timeout: float | None = None,
        forfeits: bool | None = None,
    ) -> tuple[Any, float]:
        """Calls a method of the child's object and returns its value and its seconds.

        Args:
            method: The method's name.
            arguments: Its arguments, raw observations, JSON values or arrays.
            timeout: The seconds the call may take, or `None` without a limit.
            forfeits: Whether a failure raises the standard `Forfeit`, `timeout` for a call past
                its time, which also kills the child, `crash` for an error of the submission or
                a child that ended, and `invalid` for an answer `as_actions` refused. `None`
                takes the child's own default.

        Raises:
            Forfeit: As above, and always for a `Forfeit` the child raised itself.
            ChildTimeout, ChildCrash, Invalid: Without `forfeits`.
        """
        forfeits = self.forfeits if forfeits is None else forfeits
        began = time.perf_counter()
        try:
            self._process.send('call', {'method': method, 'arguments': list(arguments)})
            answer = self._process.receive(timeout)
        except _launch.Timeout:
            self._process.kill()
            self._raise_timeout(f'{method} took longer than {timeout:g} s', forfeits)
        except _launch.Ended as error:
            self._process.kill()
            self._raise_crash(f'the child ended during {method}, {error}', forfeits)
        seconds = time.perf_counter() - began
        if answer['kind'] == 'error':
            self._raise(answer['value'], forfeits)
        return answer.get('value'), seconds

    @property
    def pid(self) -> int:
        """The child's process identifier."""
        return self._process.pid

    def close(self) -> None:
        """Ends the child, and kills it if it does not end within 5 seconds."""
        self._process.close()

    def __enter__(self) -> Child:
        return self

    def __exit__(self, *exception: Any) -> None:
        self.close()
