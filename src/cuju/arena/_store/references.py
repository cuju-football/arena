# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Resolve references to stored arena objects."""

from collections.abc import Iterable, Sequence
from typing import Any


def matching(reference: str, identifiers: Sequence[str], prefix: str) -> list[str]:
    """Return the exact identifier or the identifiers that start with the reference."""
    if reference in identifiers:
        return [reference]
    head = reference.removeprefix(prefix)
    if len(head) < 4:
        return []
    return list(
        dict.fromkeys(value for value in identifiers if value.removeprefix(prefix).startswith(head))
    )


def kept(reference: str, entries: Iterable[dict[str, Any]], prefix: str) -> str | None:
    """Resolve a reference against a collection ordered newest first."""
    identifiers = [entry['id'] for entry in entries]
    if reference == 'last' and identifiers and reference not in identifiers:
        return identifiers[0]
    return _single(reference, matching(reference, identifiers, prefix))


def team(reference: str, entries: Sequence[dict[str, Any]]) -> str | None:
    """Resolve a team identifier, exact name or unique identifier prefix."""
    identifiers = [entry['id'] for entry in entries]
    if reference in identifiers:
        return reference
    names = [entry['id'] for entry in entries if entry['name'].casefold() == reference.casefold()]
    return _single(reference, names or matching(reference, identifiers, 't-'))


def _single(reference: str, candidates: list[str]) -> str | None:
    if len(candidates) > 1:
        from .._text.messages import MessageError

        error = MessageError(
            'server.reference.ambiguous', reference=repr(reference), count=len(candidates)
        )
        error.hint = f'use one of {", ".join(candidates[:5])}'
        raise error
    return candidates[0] if candidates else None
