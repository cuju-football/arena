# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""`Observer`, gfootball's observation of a side, built from the raw observations of each step."""

from __future__ import annotations

import collections
from typing import Any

# gfootball: the representations that `create_environment` takes and that a raw step can make.
REPRESENTATIONS = ('raw', 'simple115', 'simple115v2', 'extracted')
# gfootball: the frames that `stacked=True` keeps.
STACK = 4


def _raw_of(step_or_raw: Any) -> list[dict[str, Any]]:
    raw = getattr(step_or_raw, 'raw', step_or_raw)
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list | tuple):
        raise TypeError(f'expected a step or a list of raw observations, found {raw!r}')
    return list(raw)


class Observer:
    """Gives exactly what `cuju.gfootball.env.create_environment` gives a side, step by step.

    It runs cuju's own copies of GRF's wrappers over each step's raw observations, so the arrays
    equal gfootball's for the same arguments: `raw`, `simple115`, `simple115v2` or `extracted`,
    with `channel_dimensions` for the minimap, and with `stacked` the last four observations
    joined along the last axis, the first one repeated after a reset, as gfootball's `FrameStack`
    does. `pixels` cannot be made, since cuju draws no frames.

    Call `observe` or `per_player` once for every step, whether or not its answer is used, since
    gfootball stacks every step. A second call with the same step reads the stack again without
    adding to it.
    """

    def __init__(
        self,
        representation: str = 'raw',
        stacked: bool = False,
        channel_dimensions: tuple[int, int] = (96, 72),
    ) -> None:
        """Chooses the representation.

        Args:
            representation: As gfootball names it.
            stacked: Whether to stack four steps, which `raw` cannot.
            channel_dimensions: The width and height of the minimap of `extracted`.

        Raises:
            ValueError: For a representation outside `REPRESENTATIONS`, or `raw` stacked.
        """
        if representation not in REPRESENTATIONS:
            raise ValueError(
                f'a representation is one of {", ".join(REPRESENTATIONS)}, found {representation!r}'
            )
        if stacked and representation == 'raw':
            raise ValueError('gfootball stacks no raw observations')
        self.representation = representation
        self.stacked = bool(stacked)
        self.channel_dimensions = tuple(channel_dimensions)
        self._frames: collections.deque[Any] = collections.deque(maxlen=STACK)
        self._last: Any = None
        self._current: Any = None

    def reset(self) -> None:
        """Clears the stack, as a reset of gfootball's environment does."""
        self._frames.clear()
        self._last = None
        self._current = None

    def _convert(self, raw: list[dict[str, Any]]) -> Any:
        if self.representation == 'raw':
            return raw
        if self.representation in ('simple115', 'simple115v2'):
            from cuju.gfootball.env import wrappers

            return wrappers.Simple115StateWrapper.convert_observation(
                raw, self.representation == 'simple115v2'
            )
        from cuju.gfootball.env import observation_preprocessing

        return observation_preprocessing.generate_smm(
            raw, channel_dimensions=self.channel_dimensions
        )

    def _push(self, step_or_raw: Any) -> Any:
        raw = _raw_of(step_or_raw)
        if self._last is not None and self._last is getattr(step_or_raw, 'raw', step_or_raw):
            return self._current
        self._last = getattr(step_or_raw, 'raw', step_or_raw)
        converted = self._convert(raw)
        if not self.stacked:
            self._current = converted
            return converted
        import numpy as np

        if self._frames and np.shape(self._frames[-1]) != np.shape(converted):
            # A side whose players change, as after a red card, starts its stack again.
            self._frames.clear()
        if not self._frames:
            self._frames.extend([converted] * STACK)
        else:
            self._frames.append(converted)
        self._current = np.concatenate(list(self._frames), axis=-1)
        return self._current

    def per_player(self, step_or_raw: Any) -> list[Any]:
        """Returns one observation for each controlled player, in the order of `step.raw`.

        It is `observe` without gfootball's squeeze of a single player, so a format that plays
        any number of players reads them alike.
        """
        found = self._push(step_or_raw)
        if self.representation == 'raw':
            return list(found)
        return [found[index] for index in range(len(found))]

    def observe(self, step_or_raw: Any) -> Any:
        """Returns gfootball's observation of the side, for one controlled player its own alone."""
        found = self._push(step_or_raw)
        if self.representation != 'raw' and len(found) == 1:
            return found[0]
        return found
