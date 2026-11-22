# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""What the recorder and a live source read of a batch: the batch itself, its scenario name, its
action set and the provenance that a recording of either keeps."""

from __future__ import annotations

from typing import Any


def batch_of(target: Any) -> Any:
    """Returns the batch that `target` is or holds, or `None` when it holds none.

    A batch is what takes step observers. An environment of `cuju.env.create_environment` and of
    cuju's gym ids keeps its batch as `batch`, and a wrapper around one reaches it as
    `unwrapped.batch`.
    """

    def held(holder: Any, name: str) -> Any:
        # A wrapper may raise anything for a name that it lacks.
        try:
            return getattr(holder, name, None)
        except Exception:
            return None

    for found in (target, held(target, 'batch'), held(held(target, 'unwrapped'), 'batch')):
        if found is not None and callable(held(found, 'add_observer')):
            return found
    return None


# GRF: the action sets, by the number of their actions.
_ACTION_SETS = {19: 'default', 20: 'v2', 33: 'full'}


def action_set(batch: Any) -> str | None:
    """Returns the name of the action set of a batch, `default`, `v2` or `full`, or `None` for a
    batch that tells no number of actions that names one."""
    return _ACTION_SETS.get(getattr(batch, 'action_count', None))


def origin(source: str, environment: int, seed: int | None, actions: str | None) -> dict[str, Any]:
    """Returns the provenance of a recording of a recorder or of a live stream.

    Args:
        source: `recorder` or `live`.
        environment: The index of the environment in its batch.
        seed: The engine seed of the episode, or `None` while no state has told it.
        actions: The name of the action set of the batch, which names the actions of the
            annotations, or `None`.
    """
    found: dict[str, Any] = {'source': source, 'environment': environment}
    if seed is not None:
        found['engine_seed'] = seed
    if actions is not None:
        found['action_set'] = actions
    return found


def scenario_name(batch: Any) -> str:
    """Returns the scenarios a batch was created with, as `scenario_name` names them, or `match`."""
    return getattr(batch, 'scenario_name', None) or 'match'
