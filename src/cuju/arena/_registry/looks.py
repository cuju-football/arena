# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The looks that a program which draws the arena's teams and matches lays over them.

The arena keeps the identity of a team and the game of a match, and the look of either belongs to
the program that draws them, such as cuju render. Such a program registers its producers here as
it loads, and the arena passes what they give on unread.

`TEAM` holds producers that supply the colors of tournament entrants from the team look.

`ADDED` holds producers that take the identifier of a team as soon as the registry holds it, a
team added, imported or copied, so the program keeps the look that the team's folder brought or
gives the team a look of its own. A producer that fails logs why and leaves the team as it is.

Without a producer a team keeps its identity alone, and a header keeps no look, which a viewer
derives when it shows the match.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

TEAM: list[Callable[[dict[str, Any]], dict[str, Any]]] = []
ADDED: list[Callable[[str], None]] = []


def team(entry: dict[str, Any]) -> dict[str, Any]:
    """Returns a team with the look of every producer of `TEAM` laid over it, in their order."""
    for produce in TEAM:
        entry = produce(entry)
    return entry


def added(identifier: str) -> None:
    """Hands the identifier of a team that the registry now holds to every producer of `ADDED`."""
    for produce in ADDED:
        produce(identifier)
