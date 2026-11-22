# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The rows of the ratings from before formats, each team side given the format it played under.

Every match before formats was played as one of two built-in formats plays now:

- a match of a strict tournament played as `strict/1`, with the tournament's stored
  limits as its options, so its team sides gain that identity and a model of their own;
- every other match played as Kaggle open, `kaggle/1` under its preset `open`, whose models keep
  their identifiers.

`rows(rows, tournaments)` adds the format to each team side of the rows that lack one, and returns
the rows with the number it changed. Nothing is refitted, since a table is made from the rows on
every read. The built-in AI, the random entrant and named policies play under no format.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import identity as _identity

# What strict/1 names itself in its identity, as its `format.toml` states it.
STRICT = {'format': 'strict/1', 'name': 'Strict', 'version': '1'}


def strict_identity(limits: dict[str, Any]) -> dict[str, Any]:
    """Returns the identity of `strict/1` under a tournament's stored limits."""
    from ..formats import v1 as _v1

    return {
        **STRICT,
        'digest': None,
        'contract': 1,
        'behaviour': _v1.BEHAVIOUR,
        'options': dict(limits),
        'per_entrant': {},
        'revision': None,
    }


def _limits(folder: Path, identifier: str, held: dict[str, Any]) -> Any:
    if identifier not in held:
        held[identifier] = None
        try:
            document = json.loads((folder / f'{identifier}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        config = document.get('config') if isinstance(document, dict) else None
        strict = config.get('strict') if isinstance(config, dict) else None
        if isinstance(strict, dict):
            held[identifier] = strict
    return held[identifier]


def is_team(key: str) -> bool:
    """Returns whether a key of a row names a team of the registry, which played under a format."""
    return isinstance(key, str) and ':' not in key and key != 'builtin'


def rows(
    found: list[dict[str, Any]], tournaments: Path, explicit: bool = True
) -> tuple[list[dict[str, Any]], int]:
    """Gives each team side of the rows without a format the one it played under.

    Args:
        found: The rows, changed in place.
        tournaments: The folder of the tournament files, whose `config.strict` tells a strict one.
        explicit: Whether a side of Kaggle open names it too. The store leaves it out, since a team
            side without a format reads as Kaggle open, and its model keeps its identifier.

    Returns:
        The rows and the number of sides changed.
    """
    held: dict[str, Any] = {}
    changed = 0
    for row in found:
        limits = None
        if row.get('source') == 'tournament' and isinstance(row.get('ref'), str):
            limits = _limits(tournaments, row['ref'].split('/')[0], held)
        for name in ('home', 'away'):
            side = row[name]
            if 'submission_format' in side or not is_team(side.get('key')):
                continue
            if limits is None:
                if not explicit:
                    continue
                side['submission_format'] = dict(_identity.DEFAULT)
            else:
                identity = strict_identity(limits)
                side['submission_format'] = identity
                side['format'] = _identity.model_suffix(identity, None)
            changed += 1
    return found, changed
