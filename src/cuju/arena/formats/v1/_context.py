# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""What the renderer tells the library of the format whose code runs now.

A host sets it around every call into a format, so `Child` finds the format's own scripts and
`test_match` the team and the format it checks. In a format process it holds one format for the
life of the process, and in the match process each side's call sets its own.
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import Any


@dataclasses.dataclass
class Context:
    """The format whose code runs now.

    Attributes:
        format_folder: The format's folder, where `Child` finds its scripts.
        submission: The submission's folder.
        environment: The environment a `Child` starts with, the format process's own.
        imports: The folders that import cuju and the renderer in a child, which the renderer
            names since `-I` leaves every variable of Python out.
        team: The team of a pre-check, for `test_match`, or `None` outside one.
        play_check: What plays a check match for `test_match`, set by the renderer in a
            pre-check, or `None`.
        guard: The side's `SideGuard` of `_formats.host`, whose `start` starts a `Child` of the
            match process under the side's ceilings, or `None` where the child already plays
            inside its side's process tree.
    """

    format_folder: Path | None = None
    submission: Path | None = None
    environment: dict[str, str] | None = None
    imports: list[str] = dataclasses.field(default_factory=list)
    team: Any = None
    play_check: Callable[..., Any] | None = None
    guard: Any = None


CURRENT = Context()
_ACTIVE: ContextVar[Context | None] = ContextVar('format_context', default=None)


@contextlib.contextmanager
def using(context: Context) -> Iterator[Context]:
    """Makes a context the current one for a block, then puts the previous one back."""
    token = _ACTIVE.set(context)
    try:
        yield context
    finally:
        _ACTIVE.reset(token)


def current() -> Context:
    """Returns the context of the format whose code runs now."""
    active = _ACTIVE.get()
    return CURRENT if active is None else active
