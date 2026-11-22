# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Resolve the names and abbreviations accepted by arena commands."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from .._store import references as _stored
from . import _words as words
from . import contract

TEAM = 'a team identifier, prefix of at least four characters or exact name ignoring case'
ENTRANT = 'TEAM, TEAM@CHECKPOINT, builtin, builtin:DIFFICULTY or random'
CHECKPOINT = (
    'After the at sign use a checkpoint identifier, unique prefix, label, step:N or latest. '
    'A prefix needs at least four characters. If a legacy team name matches a checkpoint '
    'reference, use the team identifier or the full team identifier and checkpoint as shown in '
    'the hint. '
    'Use the full TEAM@ID if a label and another selector match different checkpoints.'
)


def kept_argument(
    parser: contract.Parser, metavar: str, name: str = 'target', *, allow_file: bool = False
) -> None:
    """Add a stored reference with a static suggestion for the newest entry."""
    help_text = 'an identifier, unique prefix of at least four characters or last for the newest'
    if allow_file:
        help_text += ', or a recording file'
    words.offer(parser, name, completion=('last',), metavar=metavar, help=help_text)


def _one(reference: str, candidates: Iterable[str]) -> str | None:
    found = list(dict.fromkeys(candidates))
    if len(found) > 1:
        options = ', '.join(found[:5])
        raise contract.UsageError(
            contract._current.get()._parser,
            f'expected one match for {reference!r}, found {len(found)}',
            hint=f'use one of {options}',
        )
    return found[0] if found else None


def identifier(reference: str, identifiers: Sequence[str], prefix: str) -> str | None:
    """Find an exact identifier or its unique abbreviation."""
    return _one(reference, _stored.matching(reference, identifiers, prefix))


def kept(reference: str, entries: Iterable[dict[str, Any]], prefix: str) -> str:
    """Resolve a reference against a collection ordered newest first."""
    identifiers = [entry['id'] for entry in entries]
    if reference == 'last' and identifiers and reference not in identifiers:
        return identifiers[0]
    return identifier(reference, identifiers, prefix) or reference


def team(reference: str, *, checkpoint: bool = True) -> str:
    """Return a canonical team or checkpoint reference for a command."""
    from .._registry import checkpoints, teams

    if reference in ('builtin', 'random', 'random-actions') or reference.startswith('builtin:'):
        return reference
    entries = teams.teams()
    identifiers = [entry['id'] for entry in entries]

    def names(value: str) -> list[str]:
        return [entry['id'] for entry in entries if entry['name'].casefold() == value.casefold()]

    def named(value: str) -> str | None:
        if value in identifiers:
            return value
        return _one(value, names(value)) or identifier(value, identifiers, 't-')

    if not checkpoint or '@' not in reference or reference in identifiers:
        return named(reference) or reference
    direct = names(reference)
    base, _, point = reference.partition('@')
    if not point:
        if direct:
            return _one(reference, direct) or reference
        raise contract.UsageError(
            contract._current.get()._parser,
            f'expected a checkpoint after the at sign, found {reference!r}',
            hint='use TEAM@CHECKPOINT with an identifier, prefix, label, step:N or latest',
        )
    resolved = named(base)
    if resolved is None or (direct and resolved == teams.BUILTIN):
        return _one(reference, direct) or reference
    candidates = _checkpoint_matches(resolved, point)
    if base == resolved and candidates == [point]:
        return reference
    match = _one(reference, [*direct, *(checkpoints.entrant(resolved, c) for c in candidates)])
    if match is None:
        from .._text.messages import MessageError

        error = MessageError(
            'server.checkpoints.unknown', team=repr(resolved), checkpoint=repr(point)
        )
        error.hint = f'run cuju arena checkpoint list {resolved}'
        raise error
    return match


def _checkpoint_matches(resolved: str, point: str) -> list[str]:
    from .._registry import checkpoints

    if len(point) == 16 and all(char in '0123456789abcdef' for char in point):
        if checkpoints.exists(resolved, point):
            return [point]
    found = checkpoints.listing(resolved)
    rows = found['checkpoints']
    ids = [row['id'] for row in rows]
    if point in ids:
        return [point]
    candidates = [row['id'] for row in rows if row['label'] == point]
    candidates.extend(_stored.matching(point, ids, ''))
    if point == 'latest':
        candidates.extend(ids[:1])
    elif point.startswith('step:') and point[5:].isascii() and point[5:].isdigit():
        step = point[5:].lstrip('0') or '0'
        candidates.extend(row['id'] for row in rows if str(row['step']) == step)
    return candidates
