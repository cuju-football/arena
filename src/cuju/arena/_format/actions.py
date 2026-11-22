# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The actions of the agents, which a recording keeps in the section `ACTS` beside its frames.

The section holds one byte for each agent and frame, the action that the agent gave on the step
that led to the frame, in the numbering of the action set that the header names. The agents count
as the batch counts them, those of the left team first, and the header gives their numbers on
each side in `agents`. Row 0 holds no action in a recording that starts at its kick off, since a
reset plays none, and one more row follows the last frame, for the step that ended an episode
whose frame an automatic reset replaced.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cuju.errors import StateError

if TYPE_CHECKING:
    import numpy as np

TAG = b'ACTS'
# The byte of a row that holds no action, or one that the recorder did not see.
NONE = 255


def encode(rows: Any) -> bytes:
    """Returns the section of actions `[frames + 1, agents]`, a lane of bytes for each agent."""
    import numpy as np

    return np.ascontiguousarray(np.asarray(rows, np.uint8).T).tobytes()


def decode(payload: bytes, count: int, agents: int) -> np.ndarray:
    """Returns the actions of a section as `[frames + 1, agents]` `uint8`.

    Args:
        payload: The section.
        count: The number of frames of the recording.
        agents: The number of agents, those of both sides together.

    Raises:
        StateError: If the section holds another number of bytes than its frames and agents give.
    """
    import numpy as np

    if len(payload) != agents * (count + 1):
        raise StateError(
            f'expected the actions of {agents} agents over {count} frames, found '
            f'{len(payload)} bytes'
        )
    lanes = np.frombuffer(payload, np.uint8).reshape(agents, count + 1)
    return np.ascontiguousarray(lanes.T)
