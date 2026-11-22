# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Verdicts of the pre-check, their problems, forfeits and the errors that the helpers raise."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable
from typing import Any

# The five kinds of forfeit that a format names, and the renderer's own, which only it records.
KINDS = ('load', 'timeout', 'crash', 'invalid', 'rule')
CEILINGS = (
    'ceiling.answer',
    'ceiling.time',
    'ceiling.load',
    'ceiling.crash',
    'ceiling.memory',
    'ceiling.threads',
    'ceiling.process',
)
# The levels of a problem. An error refuses the team, a warning is shown, a note reports a figure.
LEVELS = ('error', 'warning', 'note')
# A problem's name and detail, letters, digits, dots and underscores, and the longest message.
_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_.]{0,39}')
MESSAGE_LENGTH = 300


def _name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError(
            f'{what} is letters, digits, dots and underscores, at most 40, found {value!r}'
        )
    return value


def _message(value: Any) -> str:
    text = ' '.join(str(value).split())
    return text if len(text) <= MESSAGE_LENGTH else text[: MESSAGE_LENGTH - 3] + '...'


@dataclasses.dataclass(frozen=True)
class Problem:
    """One finding of a pre-check.

    Attributes:
        kind: Its name, such as `load`, `rule` or `size`, letters, digits, dots and underscores.
        message: What it says, plain text of at most 300 characters.
        level: `error`, which refuses the team, `warning`, shown beside it, or `note`, a figure.
        detail: A finer name, such as `manifest.players`, or `None`.
        environment: Whether the problem belongs to the machine rather than the team, such as a
            missing package or no GPU. A verdict with one is never kept.
    """

    kind: str
    message: str
    level: str = 'error'
    detail: str | None = None
    environment: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Returns the problem as JSON."""
        return dataclasses.asdict(self)


def problem(
    kind: str,
    message: Any,
    level: str = 'error',
    detail: str | None = None,
    environment: bool = False,
) -> Problem:
    """Returns one problem of a pre-check, see `Problem`.

    Raises:
        ValueError: If the kind, the level or the detail does not fit.
    """
    if level not in LEVELS:
        raise ValueError(f'a level is one of {", ".join(LEVELS)}, found {level!r}')
    return Problem(
        _name(kind, 'a kind'),
        _message(message),
        level,
        None if detail is None else _name(detail, 'a detail'),
        bool(environment),
    )


@dataclasses.dataclass(frozen=True)
class Verdict:
    """What a format's pre-check says of a team.

    Attributes:
        players: The players that the submission states it controls, which then set the
            controlled players under this format, or `None` for the team's own control.
        name: The name the submission states, or `None`.
        deterministic: Whether the submission plays the same actions from the same seed, `True`,
            `False` or `None` for unknown.
        problems: Every problem found, in order.
    """

    players: int | None = None
    name: str | None = None
    deterministic: bool | None = None
    problems: tuple[Problem, ...] = ()

    @property
    def accepted(self) -> bool:
        """Whether no problem has the level `error`."""
        return not any(found.level == 'error' for found in self.problems)

    @property
    def environment(self) -> bool:
        """Whether a problem belongs to the machine, so the verdict is never kept."""
        return any(found.environment for found in self.problems)

    def as_dict(self) -> dict[str, Any]:
        """Returns the verdict as JSON, with `accepted` and `environment` beside its fields."""
        return {
            'accepted': self.accepted,
            'environment': self.environment,
            'players': self.players,
            'name': self.name,
            'deterministic': self.deterministic,
            'problems': [found.as_dict() for found in self.problems],
        }

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> Verdict:
        """Returns the verdict that `as_dict` gave."""
        return cls(
            document.get('players'),
            document.get('name'),
            document.get('deterministic'),
            tuple(
                problem(
                    item['kind'],
                    item['message'],
                    item.get('level', 'error'),
                    item.get('detail'),
                    item.get('environment', False),
                )
                for item in document.get('problems') or ()
            ),
        )


def accept(
    players: int | None = None,
    name: str | None = None,
    deterministic: bool | None = None,
    problems: Iterable[Problem] = (),
) -> Verdict:
    """Returns a verdict that accepts the team, unless one of its problems is an error.

    Args:
        players: The players the submission states it controls, from 1 to 11, or `None`.
        name: The name the submission states, at most 64 characters, or `None`.
        deterministic: `True`, `False` or `None` for unknown.
        problems: Warnings and notes, and any error, which refuses the team.

    Raises:
        ValueError: If a value does not fit.
    """
    if players is not None and (
        not isinstance(players, int) or isinstance(players, bool) or not 1 <= players <= 11
    ):
        raise ValueError(f'players is a whole number from 1 to 11, found {players!r}')
    if name is not None:
        name = ' '.join(str(name).split())[:64] or None
    if deterministic not in (True, False, None):
        raise ValueError(f'deterministic is true, false or None, found {deterministic!r}')
    found = tuple(problems)
    if not all(isinstance(item, Problem) for item in found):
        raise ValueError('every problem comes from formats.problem')
    return Verdict(players, name, deterministic, found)


def refuse(
    kind: str, message: Any, detail: str | None = None, environment: bool = False
) -> Verdict:
    """Returns a verdict that refuses the team, with one problem of the level `error`.

    Args:
        kind: The problem's name, one of the five kinds of forfeit or a name of the format's own.
        message: What it says, plain text.
        detail: A finer name, or `None`.
        environment: Whether the refusal belongs to the machine, such as a missing package or no
            GPU, so the renderer never keeps it and checks again at a context's start.
    """
    return Verdict(problems=(problem(kind, message, 'error', detail, environment),))


class Forfeit(Exception):  # noqa: N818, the contract names it so
    """The forfeit of the team, raised from `start`, `reset` or `act`, or returned by `end`.

    A forfeit is always the team's. The renderer ends the match at that step and records the
    score at that moment, the format's forfeit score, and the kind, the message and the detail.

    Attributes:
        kind: One of `KINDS`, `load`, `timeout`, `crash`, `invalid` or `rule`.
        message: What happened, plain text of at most 300 characters.
        detail: A finer name of the format's own, such as `overage` or `mean`, or `None`.
    """

    def __init__(self, kind: str, message: Any = '', detail: str | None = None) -> None:
        if kind not in KINDS:
            raise ValueError(f'a forfeit is one of {", ".join(KINDS)}, found {kind!r}')
        self.kind = kind
        self.message = _message(message) or kind
        self.detail = None if detail is None else _name(detail, 'a detail')
        super().__init__(self.message)

    def as_dict(self) -> dict[str, Any]:
        """Returns the forfeit as JSON."""
        return {'kind': self.kind, 'message': self.message, 'detail': self.detail}


class Invalid(ValueError):  # noqa: N818, the contract names it so
    """Raised by `as_actions` for an answer that is no list of actions of the match's set."""


class Missing(ImportError):  # noqa: N818, the contract names it so
    """Raised by `require` for a module that cannot be imported, an environment refusal.

    Attributes:
        modules: The modules that were missing.
        hint: What installs them, or `None`.
    """

    def __init__(self, modules: list[str], hint: str | None = None) -> None:
        self.modules = list(modules)
        self.hint = hint
        text = f'needs {", ".join(self.modules)}'
        super().__init__(f'{text}, {hint}' if hint else text)


class ManifestError(ValueError):
    """Raised by `read_manifest` for a manifest that is missing or does not read."""
