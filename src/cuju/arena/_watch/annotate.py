# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""`annotate`, which attaches what a policy computed for every agent of a batch in one call.

A training loop holds its values, probabilities and maps as tensors indexed by environment and
agent. `annotate` takes them in that shape, finds the recorders and live sources attached to the
batch, copies the rows of their environments to the host once, and hands each its own row. Each
agent goes to the player that it controls in the frame that takes the annotation, which the lanes
of the players give from the state, so the map follows the engine's switches of the controlled
player on both teams and under both reverse flags. With nothing attached the call returns at once
and reads no tensor.

The module also holds what a recorder and a live source share, the settings of the annotations,
the map from agents to players, the records that one row of a call gives, and the store of the
overlays of an episode or a history.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np

from cuju.errors import ArgumentError

from .._format import annotations as _annotations
from .._format import overlays as _overlays
from .._format import players as _players
from . import batches as _batches

# The words of the settings of the annotations.
ALL = 'all'
AGENT = 'agent'
PITCH = 'pitch'


class Limits:
    """The settings of the annotations of one recorder or live source, from `[annotations]`.

    Attributes:
        top: How many of the most likely actions an annotation keeps, `None` for every action.
        max_values: The named values of one player at one step, at most.
        perspective: `agent` when grids and targets come in the view of the agent's own
            observation, or `pitch` when they come as the recording shows the pitch.
        grid_cells: The cells of one grid, at most.
        grid_every: The steps from one kept grid to the next.
    """

    __slots__ = ('grid_cells', 'grid_every', 'max_values', 'perspective', 'top')

    def __init__(self, settings: Mapping[str, Any]) -> None:
        probabilities = settings['probabilities']
        self.top: int | None = None if probabilities == ALL else int(probabilities)
        self.max_values = min(int(settings['max_values']), _annotations.MAX_VALUES)
        self.perspective = settings['perspective']
        self.grid_cells = min(
            int(settings['grid_cells']), _overlays.MAX_ROWS * _overlays.MAX_COLUMNS
        )
        self.grid_every = int(settings['grid_every'])


@functools.lru_cache(maxsize=64)
def _row_type(players: int) -> np.dtype:
    return _players.row_type(players)


def agent_players(lanes: bytes, lineup: list[dict[str, Any]], agents: tuple[int, int]) -> list[int]:
    """Returns the index in the header of the player that each agent controls, -1 for none.

    The agents count as the batch counts them, those of the left team first. The lanes name the
    agent of each player within its team, as GRF's raw observation of that agent names its active
    player, and the header names the team of each player.

    Args:
        lanes: The lanes of the players of one frame, as `StateReader.lanes` returns them.
        lineup: The header entry of each player.
        agents: The numbers of agents of the left and the right team.
    """
    found = [-1] * (agents[0] + agents[1])
    agent = np.frombuffer(lanes, _row_type(len(lineup)), 1)['agent'][0]
    for index in np.flatnonzero(agent).tolist():
        team = lineup[index].get('team', 0)
        column = int(agent[index]) - 1 + (agents[0] if team == 1 else 0)
        if 0 <= column < len(found):
            found[column] = index
    return found


class Row:
    """What one call gives for one environment, before a source turns it into records.

    The arrays of the players share their first axis, one entry for each of `who`.

    Attributes:
        who: For each entry, the header index of the player, or `None` to leave it out, or, while
            `agents` is true, the agent column that a source maps to its player.
        agents: Whether `who` holds agent columns.
        values: Named arrays of one number for each entry.
        match_values: Named numbers of the whole match.
        match_probabilities: The probabilities of the actions for the whole match, or `None`.
        match_action: The chosen action for the whole match, or `None`.
        probabilities: The probability of each action for each entry, or `None`.
        actions: The chosen action of each entry, or `None`.
        targets: Named arrays of one value for each player, for each entry.
        grids: Named grids, one for each entry.
        match_grids: Named grids of the whole match.
        arrows: Named arrays of one value for each action, for each entry.
        text: The text of the whole match, or `None`.
        player_text: The text of the entries, the same for each, or `None`.
        extent: The rectangle of every grid, or `None`.
    """

    __slots__ = (
        'actions',
        'agents',
        'arrows',
        'extent',
        'grids',
        'match_action',
        'match_grids',
        'match_probabilities',
        'match_values',
        'player_text',
        'probabilities',
        'targets',
        'text',
        'values',
        'who',
    )

    def __init__(self, who: list[Any], agents: bool) -> None:
        self.who = who
        self.agents = agents
        self.values: dict[str, np.ndarray] = {}
        self.match_values: dict[str, Any] = {}
        self.match_probabilities: Any = None
        self.match_action: int | None = None
        self.probabilities: np.ndarray | None = None
        self.actions: np.ndarray | None = None
        self.targets: dict[str, np.ndarray] = {}
        self.grids: dict[str, np.ndarray] = {}
        self.match_grids: dict[str, np.ndarray] = {}
        self.arrows: dict[str, np.ndarray] = {}
        self.text: str | None = None
        self.player_text: str | None = None
        self.extent: list[float] | None = None

    def empty(self) -> bool:
        """Returns whether the row holds nothing to annotate."""
        return not (
            self.values
            or self.match_values
            or self.match_probabilities is not None
            or self.match_action is not None
            or self.probabilities is not None
            or self.actions is not None
            or self.targets
            or self.grids
            or self.match_grids
            or self.arrows
            or self.text is not None
            or self.player_text is not None
        )


def _name(name: Any) -> str:
    """Returns a name of a value or an overlay after checking it."""
    if not isinstance(name, str) or not 0 < len(name) <= _annotations.NAME_LENGTH:
        raise ArgumentError(
            f'expected names of 1 to {_annotations.NAME_LENGTH} characters, found {name!r}'
        )
    return name


def _finite(array: np.ndarray, what: str) -> None:
    """Raises `ArgumentError` unless every number of an array is finite."""
    if array.dtype.kind not in 'biuf' or array.dtype == bool:
        raise ArgumentError(f'expected {what} as numbers, found the type {array.dtype}')
    if array.dtype.kind == 'f' and not np.isfinite(array).all():
        raise ArgumentError(f'expected {what} as finite numbers')


def _side(team: int, perspective: str) -> int | None:
    """Returns the side whose own view an overlay of a player of `team` lies in."""
    return team if perspective == AGENT else None


def records(
    row: Row,
    lineup: list[dict[str, Any]],
    players: list[int],
    step: int,
    limits: Limits,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns the annotations and the overlays that a row gives for one frame.

    The records carry no frame, which the source gives them. An overlay of a grid holds its bytes
    as `_overlays.quantize` returns them.

    Args:
        row: The row.
        lineup: The header entry of each player.
        players: The header index of the player of each entry of the row, -1 to leave one out.
        step: The step of the frame, which tells whether it keeps a grid.
        limits: The settings of the source.

    Raises:
        ArgumentError: If a part passes a limit or holds a number that is not finite.
    """
    notes: list[dict[str, Any]] = []
    overlays: list[dict[str, Any]] = []
    grids = step % limits.grid_every == 0
    for entry, player in enumerate(players):
        if player < 0:
            continue
        record: dict[str, Any] = {'p': player}
        if row.values:
            record['v'] = {
                name: _annotations.number(values[entry], f'the value {name!r}')
                for name, values in row.values.items()
            }
        if row.player_text is not None:
            record['t'] = row.player_text[: _annotations.TEXT_LENGTH]
        if row.actions is not None:
            action = row.actions[entry]
            if isinstance(action, float | np.floating) and not float(action).is_integer():
                raise ArgumentError(f'expected the action as a whole number, found {action!r}')
            if int(action) < 0:
                raise ArgumentError(f'expected the action as a whole number, found {action!r}')
            record['a'] = int(action)
        if row.probabilities is not None:
            record['k'] = _annotations.likely(row.probabilities[entry].tolist(), limits.top)
        if len(record) > 1:
            notes.append(record)
        team = lineup[player].get('team', 0)
        if grids:
            for name, values in row.grids.items():
                overlays.append(
                    _grid(name, values[entry], player, _side(team, limits.perspective), row, limits)
                )
        for name, values in row.targets.items():
            overlays.append(
                _vector(name, 'targets', values[entry], player, _side(team, limits.perspective))
            )
        for name, values in row.arrows.items():
            overlays.append(_vector(name, 'actions', values[entry], player, team))
    match: dict[str, Any] = {}
    if row.match_values:
        match['v'] = {
            name: _annotations.number(value, f'the value {name!r}')
            for name, value in row.match_values.items()
        }
    if row.text is not None:
        match['t'] = row.text[: _annotations.TEXT_LENGTH]
    if row.match_action is not None:
        match['a'] = row.match_action
    if row.match_probabilities is not None:
        match['k'] = _annotations.likely(row.match_probabilities.tolist(), limits.top)
    if match:
        notes.append(match)
    if grids:
        for name, values in row.match_grids.items():
            overlays.append(_grid(name, values, None, None, row, limits))
    for record in notes:
        if len(record.get('v', ())) > limits.max_values:
            raise ArgumentError(
                f'expected values as a mapping of at most {limits.max_values} names'
            )
    return notes, overlays


def _grid(
    name: str, values: np.ndarray, player: int | None, side: int | None, row: Row, limits: Limits
) -> dict[str, Any]:
    """Returns the overlay of one grid, its numbers as bytes."""
    if values.ndim != 2 or values.size > limits.grid_cells:
        raise ArgumentError(
            f'expected the grid {name!r} of at most {limits.grid_cells} cells in rows and '
            f'columns, found the shape {values.shape}'
        )
    _finite(values, f'the grid {name!r}')
    record: dict[str, Any] = {'f': 0, 'name': name, 'kind': 'grid'}
    if player is not None:
        record['p'] = player
    if side is not None:
        record['side'] = side
    if row.extent is not None:
        record['extent'] = row.extent
    steps, scale, offset = _overlays.quantize(values)
    record.update(values=steps, type='uint8', scale=scale, offset=offset)
    _overlays.prepare(record)
    return record


def _vector(
    name: str, kind: str, values: np.ndarray, player: int, side: int | None
) -> dict[str, Any]:
    """Returns the overlay of one vector, its numbers as 32-bit floats."""
    _finite(values, f'the overlay {name!r}')
    record: dict[str, Any] = {'f': 0, 'p': player, 'name': name, 'kind': kind}
    if side is not None:
        record['side'] = side
    record['values'] = np.asarray(values, np.float32)
    _overlays.prepare(record)
    return record


def plan_notes(
    notes: Mapping[tuple[int, int], dict[str, Any]],
    frame: int,
    found: list[dict[str, Any]],
    max_values: int,
) -> dict[tuple[int, int], dict[str, Any]]:
    """Returns the annotations of `frame` merged with those already there, changing nothing.

    Raises:
        ArgumentError: If a merged annotation would hold more than `max_values` names.
    """
    merged: dict[tuple[int, int], dict[str, Any]] = {}
    for record in found:
        key = (frame, record.get('p', -1))
        earlier = merged.get(key) or notes.get(key)
        scratch = {key: earlier} if earlier is not None else {}
        merged[key] = _annotations.merge(scratch, frame, record, max_values)
    return merged


def _is_grid(record: dict[str, Any]) -> bool:
    return record['kind'] == 'grid'


class Overlays:
    """The overlays of an episode or of a history, by frame, player and name, within the limits.

    A later overlay of the same frame, player and name replaces the earlier one.

    Attributes:
        records: The overlays, each as `_overlays` describes a record in memory.
        size: The room that the overlays take in a section, at most.
    """

    __slots__ = ('_names', 'records', 'size')

    def __init__(self) -> None:
        self.records: dict[tuple[int, int, str], dict[str, Any]] = {}
        self._names: dict[tuple[int, int, bool], set[str]] = {}
        self.size = 0

    def __len__(self) -> int:
        return len(self.records)

    def plan(
        self, frame: int, found: list[dict[str, Any]], budget: int | None
    ) -> Callable[[], None]:
        """Checks the overlays of one frame against the limits, and returns the call that keeps
        them, so a refusal changes nothing.

        Args:
            frame: The frame, or the serial of a live frame.
            found: The overlays, as `records` returns them.
            budget: The room of the section, or `None` for no limit of room.

        Raises:
            ArgumentError: If a step and player would hold more than four grids or four vectors,
                or the overlays would pass the room of a section.
        """
        names: dict[tuple[int, int, bool], set[str]] = {}
        sizes: dict[tuple[int, int, str], int] = {}
        kept: dict[tuple[int, int, str], dict[str, Any]] = {}
        for record in found:
            player = record.get('p', -1)
            group = (frame, player, _is_grid(record))
            if group not in names:
                names[group] = set(self._names.get(group, ()))
            names[group].add(record['name'])
            limit = _overlays.MAX_GRIDS if group[2] else _overlays.MAX_VECTORS
            if len(names[group]) > limit:
                raise ArgumentError(
                    f'expected at most {_overlays.MAX_GRIDS} grids and {_overlays.MAX_VECTORS} '
                    f'vectors of a step and player, found more for {record["name"]!r}'
                )
            key = (frame, player, record['name'])
            kept[key] = {**record, 'f': frame}
            sizes[key] = _overlays.entry_bytes(
                {name: value for name, value in record.items() if name != 'values'},
                record['values'].tobytes(),
            )
        size = self.size + sum(sizes.values())
        size -= sum(self.records[key]['_size'] for key in kept if key in self.records)
        if budget is not None and size > budget:
            raise ArgumentError(
                f'expected overlays of at most {_overlays.MAX_BYTES >> 20} MiB in a recording, '
                'found more'
            )

        def keep() -> None:
            for key, record in kept.items():
                record['_size'] = sizes[key]
                self.records[key] = record
            self._names.update(names)
            self.size = size

        return keep

    def trim(self, first: int) -> None:
        """Drops the overlays of the frames before `first`."""
        if any(key[0] < first for key in self.records):
            self.records = {key: record for key, record in self.records.items() if key[0] >= first}
            self._names = {key: names for key, names in self._names.items() if key[0] >= first}
            self.size = sum(record['_size'] for record in self.records.values())

    def written(self) -> list[dict[str, Any]]:
        """Returns the overlays as `_overlays.encode` takes them."""
        return [
            {name: value for name, value in record.items() if name != '_size'}
            for record in self.records.values()
        ]


# The sources that `annotate` reaches, which the modules of the recorder and the live source add.
SINKS: list[type] = []


def _host(value: Any, what: str, count: int, places: Any) -> np.ndarray:
    """Returns the rows `places` of a tensor or an array of `count` environments, on the host.

    A tensor of PyTorch gives its rows with one copy, taken on its own device.
    """
    shape = getattr(value, 'shape', None)
    if shape is None:
        value = np.asarray(value)
        shape = value.shape
    if len(shape) == 0 or shape[0] != count:
        raise ArgumentError(
            f'expected {what} with one row for each of the {count} environments, found the shape '
            f'{tuple(shape)}'
        )
    if hasattr(value, 'detach') and hasattr(value, 'index_select'):
        import torch

        index = places.get(value.device)
        if index is None:
            index = places[value.device] = torch.as_tensor(places['rows'], device=value.device)
        rows = value.detach().index_select(0, index)
        if rows.dtype in (torch.bfloat16, torch.float16):
            rows = rows.float()
        return rows.cpu().numpy()
    return np.asarray(value)[places['rows']]


def annotate(
    batch: Any,
    *,
    values: Mapping[str, Any] | None = None,
    probabilities: Any = None,
    logits: Any = None,
    actions: Any = None,
    targets: Mapping[str, Any] | None = None,
    grids: Mapping[str, Any] | None = None,
    arrows: Mapping[str, Any] | None = None,
    text: str | Mapping[int, str] | None = None,
    extent: Any = None,
    agent: int | None = None,
    step: int | None = None,
    played: bool = False,
) -> int:
    """Attaches what a policy computed for every agent of a batch to the step its sources hold.

    Every recorder and live source attached to the batch takes the row of its own environment,
    and each agent goes to the player that it controls in the frame that takes the annotation.
    The tensors may lie on any device, and only the rows of the watched environments reach the
    host, once for each tensor. With nothing attached the call returns 0 at once and reads no
    tensor, so it stays in a training loop for free. `E` stands for the environments of the batch
    and `A` for its agents.

    Args:
        batch: The batch, or an environment that holds one as `batch` or `unwrapped.batch`.
        values: Named numbers, each `[E, A]` for the agents or `[E]` for the whole match.
        probabilities: The probability of each action, `[E, A, actions]`. The settings tell how
            many of the most likely actions an annotation keeps, three or every one.
        logits: In place of `probabilities`, the logits of each action, `[E, A, actions]`, which
            become probabilities through the softmax.
        actions: The chosen actions, `[E, A]`.
        targets: Named values over the players, each `[E, A, players]`, the players of the
            agent's own team first and each team in the order of its indices.
        grids: Named grids over the pitch, each `[E, A, rows, columns]` for the agents or
            `[E, rows, columns]` for the whole match, the first row at the top of the pitch.
        arrows: Named values over the actions, each `[E, A, actions]`, such as a whole
            distribution that the page draws as arrows.
        text: A text of the whole match for every environment, or texts by environment.
        extent: The rectangle that every grid covers, x and y of its first corner and of its
            last, in GRF's coordinates of the view of the grid, by default the whole pitch.
        agent: One agent, whose numbers come without the axis of agents, as `values` of `[E]`.
        step: The step of the episode, as the frame counts it, by default the step captured
            last, as for `Recorder.annotate`.
        played: Whether the call describes the step that the batch has just played, as for
            `Recorder.annotate`.

    Returns:
        How many recorders and live sources took the annotation. A live source declines a step
        that it did not read, and a recorder of version 1 keeps no annotations.

    Raises:
        ArgumentError: If a part has the wrong shape, holds a number that is not finite, or
            passes a limit, before any source takes anything.
    """
    observers = getattr(batch, 'observers', None)
    if observers is None:
        found = _batches.batch_of(batch)
        if found is None:
            raise ArgumentError(
                f'expected a BatchEnvironment or an environment that holds one, found '
                f'{type(batch).__name__}'
            )
        batch, observers = found, found.observers
    sinks = [observer for observer in observers if isinstance(observer, tuple(SINKS))]
    if not sinks:
        return 0
    if not isinstance(played, bool):
        raise ArgumentError(f'expected played as True or False, found {played!r}')
    if step is not None and (isinstance(step, bool) or not isinstance(step, int)):
        raise ArgumentError(f'expected the step as a whole number, found {step!r}')
    count, width = int(batch.environment_count), int(batch.agents)
    if agent is not None and (
        isinstance(agent, bool) or not isinstance(agent, int) or not 0 <= agent < width
    ):
        raise ArgumentError(f'expected an agent from 0 to {width - 1}, found {agent!r}')
    if probabilities is not None and logits is not None:
        raise ArgumentError('expected probabilities or logits, found both')
    environments = sorted({sink.environment for sink in sinks})
    places: dict[Any, Any] = {'rows': environments}
    axis = 0 if agent is not None else 1

    def agents(value: Any, what: str, trailing: int) -> np.ndarray:
        rows = _host(value, what, count, places)
        if rows.ndim != 1 + axis + trailing or (axis and rows.shape[1] != width):
            shape = '[E]' if agent is not None else f'[E, {width}]'
            raise ArgumentError(
                f'expected {what} of the shape {shape} and {trailing} more axes, found the '
                f'shape {(count, *rows.shape[1:])}'
            )
        return rows if axis else rows[:, None]

    def named(mapping: Any, what: str) -> dict[str, Any]:
        if mapping is None:
            return {}
        if not isinstance(mapping, Mapping):
            raise ArgumentError(f'expected {what} as a mapping of names, found {mapping!r}')
        return {_name(name): value for name, value in mapping.items()}

    rows = [Row(list(range(width)) if agent is None else [agent], True) for _ in environments]
    for name, value in named(values, 'values').items():
        array = _host(value, f'the value {name!r}', count, places)
        if agent is None and array.ndim == 1:
            _finite(array, f'the value {name!r}')
            for at, row in enumerate(rows):
                row.match_values[name] = array[at].item()
            continue
        array = agents(value, f'the value {name!r}', 0)
        for at, row in enumerate(rows):
            row.values[name] = array[at]
    if probabilities is not None or logits is not None:
        array = agents(probabilities if logits is None else logits, 'the probabilities', 1)
        if array.shape[-1] > _annotations.MAX_ACTIONS:
            raise ArgumentError(f'expected at most {_annotations.MAX_ACTIONS} probabilities')
        _finite(array, 'the probabilities')
        if logits is not None:
            shifted = np.exp(array - array.max(-1, keepdims=True))
            array = shifted / shifted.sum(-1, keepdims=True)
        for at, row in enumerate(rows):
            row.probabilities = array[at]
    if actions is not None:
        array = agents(actions, 'the actions', 0)
        for at, row in enumerate(rows):
            row.actions = array[at]
    for name, value in named(targets, 'targets').items():
        array = agents(value, f'the targets {name!r}', 1)
        for at, row in enumerate(rows):
            row.targets[name] = array[at]
    for name, value in named(arrows, 'arrows').items():
        array = agents(value, f'the arrows {name!r}', 1)
        for at, row in enumerate(rows):
            row.arrows[name] = array[at]
    for name, value in named(grids, 'grids').items():
        array = _host(value, f'the grid {name!r}', count, places)
        if agent is None and array.ndim == 3:
            for at, row in enumerate(rows):
                row.match_grids[name] = array[at]
            continue
        array = agents(value, f'the grid {name!r}', 2)
        for at, row in enumerate(rows):
            row.grids[name] = array[at]
    if len(rows[0].grids) > _overlays.MAX_GRIDS or len(rows[0].match_grids) > _overlays.MAX_GRIDS:
        raise ArgumentError(f'expected at most {_overlays.MAX_GRIDS} grids of a step and player')
    if len(rows[0].targets) + len(rows[0].arrows) > _overlays.MAX_VECTORS:
        raise ArgumentError(
            f'expected at most {_overlays.MAX_VECTORS} vectors of a step and player'
        )
    if text is not None:
        for at, row in enumerate(rows):
            found = text.get(environments[at]) if isinstance(text, Mapping) else text
            if found is not None and not isinstance(found, str):
                raise ArgumentError(f'expected text as a string, found {found!r}')
            row.text = found
    if extent is not None:
        checked = _checked_extent(extent)
        for row in rows:
            row.extent = checked
    by_environment = dict(zip(environments, rows, strict=True))
    if rows[0].empty():
        raise ArgumentError('expected values, text, actions, probabilities or overlays to annotate')
    plans = []
    for sink in sinks:
        keep = sink._plan(by_environment[sink.environment], step, played)
        if keep is not None:
            plans.append(keep)
    for keep in plans:
        keep()
    return len(plans)


def _checked_extent(extent: Any) -> list[float]:
    """Returns the rectangle of a grid as four finite numbers."""
    if hasattr(extent, 'tolist'):
        extent = extent.tolist()
    if (
        not isinstance(extent, list | tuple)
        or len(extent) != 4
        or not all(
            isinstance(item, int | float) and not isinstance(item, bool) and math.isfinite(item)
            for item in extent
        )
    ):
        raise ArgumentError(
            f'expected the extent of a grid as four finite numbers, found {extent!r}'
        )
    return [float(item) for item in extent]


def one(
    lineup: list[dict[str, Any]],
    player: Any,
    *,
    values: Any = None,
    text: Any = None,
    probabilities: Any = None,
    logits: Any = None,
    action: Any = None,
    targets: Any = None,
    grids: Any = None,
    arrows: Any = None,
    extent: Any = None,
    agent: Any = None,
) -> Row:
    """Returns the row of a call of `Recorder.annotate` or `LiveSource.annotate` for one player.

    The player is a header index, or, with `agent`, the agent column. Without either the row
    belongs to the whole match.

    Raises:
        ArgumentError: If a part is invalid.
    """
    if agent is not None:
        if isinstance(agent, bool) or not isinstance(agent, int) or agent < 0:
            raise ArgumentError(f'expected the agent as a whole number, found {agent!r}')
        if player is not None:
            raise ArgumentError('expected a player or an agent, found both')
        row = Row([agent], True)
    elif player is not None:
        row = Row([_annotations.player_index(lineup, player)], False)
    else:
        row = Row([], False)
    single = bool(row.who)
    if probabilities is not None and logits is not None:
        raise ArgumentError('expected probabilities or logits, found both')
    if values is not None:
        if not isinstance(values, Mapping):
            raise ArgumentError('expected values as a mapping of names')
        for name, value in values.items():
            number = _annotations.number(value, f'the value {_name(name)!r}')
            if single:
                row.values[name] = np.array([number])
            else:
                row.match_values[name] = number
    if text is not None:
        if not isinstance(text, str):
            raise ArgumentError(f'expected text as a string, found {text!r}')
        if single:
            row.player_text = text
        else:
            row.text = text
    if probabilities is not None:
        pairs = _Pairs(probabilities)
        if single:
            row.probabilities = pairs
        else:
            row.match_probabilities = pairs
    if logits is not None:
        array = np.asarray(_numbers(logits), np.float64).reshape(-1)
        _finite(array, 'the logits')
        shifted = np.exp(array - array.max())
        if single:
            row.probabilities = (shifted / shifted.sum())[None]
        else:
            row.match_probabilities = shifted / shifted.sum()
    if action is not None:
        if hasattr(action, 'item'):
            action = action.item()
        if isinstance(action, bool) or not isinstance(action, int) or action < 0:
            raise ArgumentError(f'expected the action as a whole number, found {action!r}')
        if single:
            row.actions = np.array([action])
        else:
            row.match_action = action
    if (targets is not None or arrows is not None) and not single:
        raise ArgumentError('expected a player or an agent for targets and arrows')
    for mapping, kind in ((targets, 'targets'), (arrows, 'arrows'), (grids, 'grids')):
        if mapping is None:
            continue
        if not isinstance(mapping, Mapping):
            raise ArgumentError(f'expected {kind} as a mapping of names, found {mapping!r}')
        for name, value in mapping.items():
            array = np.asarray(_numbers(value))
            if kind == 'grids' and not single:
                row.match_grids[_name(name)] = array
            else:
                getattr(row, kind)[_name(name)] = array[None]
    if len(row.grids) > _overlays.MAX_GRIDS or len(row.match_grids) > _overlays.MAX_GRIDS:
        raise ArgumentError(f'expected at most {_overlays.MAX_GRIDS} grids of a step and player')
    if len(row.targets) + len(row.arrows) > _overlays.MAX_VECTORS:
        raise ArgumentError(
            f'expected at most {_overlays.MAX_VECTORS} vectors of a step and player'
        )
    if extent is not None:
        row.extent = _checked_extent(extent)
    if row.empty():
        raise ArgumentError('expected values, text, an action or probabilities to annotate')
    return row


def _numbers(value: Any) -> Any:
    """Returns a tensor of PyTorch as an array of the host, and anything else as it is."""
    if hasattr(value, 'detach') and hasattr(value, 'cpu'):
        value = value.detach().cpu()
        return value.float().numpy() if value.is_floating_point() else value.numpy()
    return value


class _Pairs:
    """The probabilities of one call for one player, a sequence or a mapping, as `likely` takes
    them, which a row holds in place of an array of one entry."""

    __slots__ = ('given',)

    def __init__(self, given: Any) -> None:
        self.given = given
        # The check comes with the call, so a refusal never reaches a source.
        _annotations.likely(_numbers(given), None)

    def __getitem__(self, entry: int) -> _Pairs:
        return self

    def tolist(self) -> Any:
        given = _numbers(self.given)
        return given.tolist() if hasattr(given, 'tolist') else given
