# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Answers turned into the actions of a step, checked against gfootball's action sets."""

from __future__ import annotations

from typing import Any

from ._verdicts import Invalid

# GRF: the actions of each action set, `default`, `v2`, which adds the built-in AI's action, and
# `full`.
ACTION_COUNTS = {'default': 19, 'v2': 20, 'full': 33}


def _one(value: Any, size: int, negative: bool) -> int:
    if hasattr(value, 'ndim') and getattr(value, 'ndim', 1) == 0 and hasattr(value, 'item'):
        value = value.item()
    # Kaggle: the `Action` enum of the football helpers holds the index as its value.
    if not isinstance(value, int) and isinstance(getattr(value, 'value', None), int):
        value = value.value
    if hasattr(value, 'item') and not isinstance(value, int | float | bool):
        value = value.item()
    if isinstance(value, bool) or not isinstance(value, int):
        raise Invalid(f'expected a whole number for an action, found {value!r}')
    if negative and -size <= value < 0:
        value += size
    if not 0 <= value < size:
        low = -size if negative else 0
        raise Invalid(f'expected an action from {low} to {size - 1}, found {value}')
    return int(value)


def as_actions(
    value: Any, count: int, action_set: str = 'default', negative: bool = False
) -> list[int]:
    """Returns an answer as a list of `count` actions of an action set, or raises `Invalid`.

    It takes a whole number, a list or tuple, a numpy array, a tensor, or an enum whose value is
    the action, such as `Action` of Kaggle's football helpers. Booleans are refused, since GRF
    would read `True` as action 1.

    Args:
        value: The answer.
        count: The players it answers for, `len(step.raw)`.
        action_set: `default`, `v2` or `full`, the set of the step, `step.action_set`.
        negative: Whether `-1` counts from the end of the set, as Kaggle's agents may answer.

    Raises:
        Invalid: If the answer holds another number of actions, or one that is no whole number or
            lies outside the set.
    """
    if action_set not in ACTION_COUNTS:
        raise ValueError(f'an action set is one of {", ".join(ACTION_COUNTS)}, found {action_set}')
    size = ACTION_COUNTS[action_set]
    if hasattr(value, 'detach'):
        value = value.detach()
    if hasattr(value, 'tolist') and not isinstance(value, list | tuple):
        value = value.tolist()
    if not isinstance(value, list | tuple):
        value = [value]
    flat = []
    for item in value:
        if isinstance(item, list | tuple) and len(item) == 1:
            item = item[0]
        flat.append(item)
    if len(flat) != count:
        raise Invalid(f'expected {count} actions, one for each player, found {len(flat)}')
    return [_one(item, size, negative) for item in flat]
