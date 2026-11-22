# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""`test_match`, a short match of the format's own team inside its pre-check."""

from __future__ import annotations

import dataclasses
from typing import Any

from . import _context
from ._verdicts import Forfeit

# The opponents a check match may meet.
OPPONENTS = ('builtin', 'random')


@dataclasses.dataclass(frozen=True)
class CheckMatch:
    """What a check match found.

    Attributes:
        goals: The goals of the team and of its opponent.
        steps: The steps played.
        forfeit: The team's `Forfeit`, if the match ended in one, else `None`.
        seconds: The round trip of each step the team answered, as the renderer measured it.
        actions: How often the team answered each action, by action.
        same_actions: With `repeat`, whether the same steps played twice from one seed gave the
            same actions, else `None`.
    """

    goals: tuple[int, int]
    steps: int
    forfeit: Forfeit | None
    seconds: tuple[float, ...]
    actions: dict[int, int]
    same_actions: bool | None = None


def test_match(
    opponent: str = 'builtin',
    side: str = 'left',
    steps: int | None = None,
    seed: int = 0,
    repeat: bool = False,
) -> CheckMatch:
    """Plays a check match of the team that the pre-check judges, through this same format.

    It runs inside `accept` alone. The team plays in a fresh format process, with the options of
    the check, on the context's scenario, or the size's own where the context names none.

    Args:
        opponent: `builtin`, the built-in AI, or `random`, an entrant that answers at random.
        side: `left` or `right`, where the team plays.
        steps: The steps to play, or `None` for the whole match.
        seed: The engine's seed.
        repeat: Whether to play the same steps twice from the seed and compare the actions.

    Raises:
        RuntimeError: Outside a pre-check.
        ValueError: For an unknown opponent or side.
    """
    if opponent not in OPPONENTS:
        raise ValueError(f'an opponent is one of {", ".join(OPPONENTS)}, found {opponent!r}')
    if side not in ('left', 'right'):
        raise ValueError(f'a side is left or right, found {side!r}')
    context = _context.current()
    if context.play_check is None or context.team is None:
        raise RuntimeError('test_match plays only inside accept')
    found = context.play_check(opponent=opponent, side=side, steps=steps, seed=int(seed))
    same = None
    if repeat:
        again = context.play_check(opponent=opponent, side=side, steps=steps, seed=int(seed))
        same = found['sequence'] == again['sequence']
    forfeit = found.get('forfeit')
    return CheckMatch(
        goals=tuple(found['goals']),
        steps=int(found['steps']),
        forfeit=None if forfeit is None else _forfeit_of(forfeit),
        seconds=tuple(found['seconds']),
        actions={int(action): int(count) for action, count in found['actions'].items()},
        same_actions=same,
    )


# pytest collects no helper of a format as a test of its own.
test_match.__test__ = False


def _forfeit_of(found: dict[str, Any]) -> Forfeit:
    kind = found.get('kind')
    if isinstance(kind, str) and kind.startswith('ceiling.'):
        # The renderer's own stops read as the timeout or crash they are.
        forfeit = Forfeit('crash' if kind == 'ceiling.crash' else 'timeout', found.get('message'))
        forfeit.kind = kind
        forfeit.detail = found.get('detail')
        return forfeit
    return Forfeit(found['kind'], found.get('message', ''), found.get('detail'))
