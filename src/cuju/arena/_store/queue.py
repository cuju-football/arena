# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The saved state of a queue of matches played on demand, so a phase survives a restart.

An evaluation run that plays its matches on demand, and a benchmark, keep the state of their
queue here after every change, and pick up where they stopped when the server starts again. The
scheduler that fills the queue belongs to its owner, and this module keeps its state alone. A
rating run keeps its queue in its own document instead.

An owner is `run:<id>`, whose state is the field `queue` of the run's manifest, which the runs
manifest writes through a temporary file.

A state is a mapping, which these fields describe where it holds them.

- `mode`, `batched` or `on_demand`.
- `ratings`, the rating of each team key so far.
- `played`, the matches played, `playing`, those under way, and `next`, those that come next,
  each a list of mappings that the scheduler fills, such as a pairing of keys with its seed.
- `count`, the matches played, and `most`, the most that the phase plays, or `None`.
- `settled`, whether the settling rule holds.

Any other field of plain JSON is kept as it is, and a field that does not fit its type is left
out, so a damaged file still resumes with what it holds. `saved` holds the local time of the last
change.
"""

from __future__ import annotations

import math
import time
from typing import Any

from .._text.messages import MessageError
from . import home as _home
from . import runs as _runs

# The kinds of owner, each before a colon and the identifier.
OWNERS = ('run',)
_LISTS = ('played', 'playing', 'next')


def _plain(value: Any) -> bool:
    if value is None or isinstance(value, str | bool):
        return True
    if isinstance(value, int | float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(map(_plain, value))
    if isinstance(value, dict):
        return all(isinstance(key, str) and _plain(item) for key, item in value.items())
    return False


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _clean(state: Any) -> dict[str, Any]:
    """Returns a state with each field that does not fit left out."""
    if not isinstance(state, dict):
        return {}
    clean: dict[str, Any] = {}
    for key, value in state.items():
        if not isinstance(key, str) or not _plain(value):
            continue
        if key == 'mode' and value not in ('batched', 'on_demand'):
            continue
        if key == 'ratings' and not (
            isinstance(value, dict)
            and all(
                isinstance(item, int | float) and not isinstance(item, bool)
                for item in value.values()
            )
        ):
            continue
        if key in _LISTS and not (
            isinstance(value, list) and all(isinstance(item, dict) for item in value)
        ):
            continue
        if key == 'count' and not _whole(value):
            continue
        if key == 'most' and not (value is None or _whole(value)):
            continue
        if key == 'settled' and not isinstance(value, bool):
            continue
        clean[key] = value
    return clean


def _owner(owner: Any) -> tuple[str, str]:
    """Returns the kind and identifier of an owner, or raises `ArgumentError`."""
    kind, _, identifier = owner.partition(':') if isinstance(owner, str) else ('', '', '')
    if kind not in OWNERS or not _home.valid_identifier(identifier):
        raise MessageError('server.queue.owner', owner=repr(owner))
    return kind, identifier


def queue_state(owner: str, resume: bool = False) -> dict[str, Any] | None:
    """Returns the saved state of a queue, or `None` for an owner without one.

    Args:
        owner: `run:<id>`.
        resume: Whether the state serves a restart, in which the matches that were playing at the
            stop play again, so they move from `playing` to the front of `next`.

    Raises:
        ArgumentError: If the owner is no run.
    """
    _, identifier = _owner(owner)
    document = _runs.get(identifier)
    state = document.get('queue') if document is not None else None
    if not isinstance(state, dict):
        return None
    state = _clean(state)
    if resume and state.get('playing'):
        state['next'] = [*state['playing'], *state.get('next', [])]
        state['playing'] = []
    return state


def save_queue(owner: str, state: dict[str, Any]) -> dict[str, Any]:
    """Saves the state of a queue through a temporary file and returns it as saved.

    Raises:
        ArgumentError: If the owner is no run, the run is unknown, or the state is no mapping.
    """
    _, identifier = _owner(owner)
    if not isinstance(state, dict):
        raise MessageError('server.queue.state', state=repr(state))
    clean = {**_clean(state), 'saved': time.strftime('%Y-%m-%dT%H:%M:%S')}
    _runs.update(identifier, queue=clean)
    return clean


def clear_queue(owner: str) -> bool:
    """Deletes the saved state of a queue, and returns whether there was one.

    Raises:
        ArgumentError: If the owner is no run.
    """
    _, identifier = _owner(owner)
    document = _runs.get(identifier)
    if document is None or document.get('queue') is None:
        return False
    _runs.update(identifier, queue=None)
    return True
