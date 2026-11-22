# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The statistics of each recording, found once beside the index of moments and kept as a file.

The statistics of a recording are `stats/<id>.json` in the renderer's home. The index of moments
finds them when it reads a recording, at its save or import or at the server's next pass, so a
view over many episodes, such as the evaluation dashboard, reads small files and decodes no
frames. A file of another `VERSION` is found again. The file holds these fields.

- `version`, the version of the statistics, and `id`, the recording.
- `frames`, the frames of the recording, and `length`, the steps that the episode had played at
  its last frame.
- `score`, the goals of the left and the right team at the last frame, and `outcome`, how the
  left team stands at that score, `win`, `draw` or `loss`.
- `goals`, `shots` and `shots_on_target`, each a pair for the left and the right team, counted
  from the moments of the recording. A shot counts as on target when the moments say so.
- `passes`, the passes of each team, `completed`, those that a teammate received, and
  `pass_accuracy`, the completed share of the passes that a player of either team received,
  `None` for a team without one.
- `possession`, the share of the frames with a team on the ball that each team held it, and
  `owned`, the frames that each team held it.
- `actions`, the actions of the agents, `None` for a recording without them, else a mapping of
  `set`, the action set that numbers them, `counts`, the times each team's agents gave each
  action, by action number, and `share`, each count over the actions of its team.
- `heat`, the heat bins of the players and the ball, as `heat` describes them.

`heat` and `signals` also serve the index of moments, which keeps them in its entries.
"""

from __future__ import annotations

import json
import math
import os
import struct
import threading
from collections import OrderedDict
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import plugins
from .._analysis import Recording, plain
from . import home as _home

if TYPE_CHECKING:
    import numpy as np

# The version of the statistics files, which a change of their fields raises.
VERSION = 1
# The folder of the home that holds the statistics.
FOLDER = 'stats'
# The bins of a heat map, columns along the length of the pitch and rows across it.
HEAT_COLUMNS, HEAT_ROWS = 12, 8
# GRF's pitch, 55 meters from the centre to each goal line and 36 to each touch line.
HEAT_EXTENT = (-55.0, 55.0, -36.0, 36.0)
# The largest one-step drops and rises of each annotated series that the index keeps.
SIGNAL_CHANGES = 5
# The probability under which a chosen action counts as unlikely, and the most frames of each
# player that the index keeps, the least likely first.
UNLIKELY = 0.1
UNLIKELY_FRAMES = 100
# The share of the probabilities that must be kept for the entropy of a step to be known.
_WHOLE = 0.999
_FRAME_HEAD = 76
_NODE_BYTES = 196
_POSITION_UNIT = 1 / 500
_NONE = 255


def _padded(players: int) -> int:
    return -(-players // 4) * 4


def frame_bytes(players: int) -> int:
    """Returns the bytes of a frame of `players` players, as version 1 stores it."""
    return _FRAME_HEAD + players * _NODE_BYTES + 3 * _padded(players)


def _array(frames: Sequence[bytes], players: int) -> np.ndarray:
    """Returns the whole frames as one array of bytes, a row for each frame."""
    import numpy as np

    size = frame_bytes(players)
    data = b''.join(frame for frame in frames if len(frame) == size)
    return np.frombuffer(data, dtype=np.uint8).reshape(-1, size)


def _bins(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Returns the bin of each position, its row times the columns plus its column."""
    import numpy as np

    left, right, bottom, top = HEAT_EXTENT
    column = np.floor((x - left) / (right - left) * HEAT_COLUMNS).astype(np.int64)
    row = np.floor((y - bottom) / (top - bottom) * HEAT_ROWS).astype(np.int64)
    column = np.clip(column, 0, HEAT_COLUMNS - 1)
    row = np.clip(row, 0, HEAT_ROWS - 1)
    return row * HEAT_COLUMNS + column


def heat(header: dict[str, Any], frames: Sequence[bytes]) -> dict[str, Any]:
    """Returns the heat bins of a recording, how many frames each player and the ball spent in
    each bin of the pitch.

    The pitch splits into `HEAT_COLUMNS` columns along its length and `HEAT_ROWS` rows across it,
    over `HEAT_EXTENT`, the left, right, lower and upper edge in meters of the recording's own
    frame. Bin `row * HEAT_COLUMNS + column` counts from the left goal line and the lower touch
    line, and a position beyond an edge counts in the bin at that edge, so the counts of each
    player add up to the frames. A player's position is the root joint of its pose.

    Returns:
        `columns`, `rows` and `extent`, then `players`, the counts of each player of the header in
        its order, and `ball`, the counts of the ball, each a list of `columns * rows` counts.
    """
    import numpy as np

    players = len(header.get('players') or [])
    raw = _array(frames, players)
    cells = HEAT_COLUMNS * HEAT_ROWS
    found: dict[str, Any] = {
        'columns': HEAT_COLUMNS,
        'rows': HEAT_ROWS,
        'extent': list(HEAT_EXTENT),
        'players': [[0] * cells for _ in range(players)],
        'ball': [0] * cells,
    }
    if not len(raw):
        return found
    nodes = raw[:, _FRAME_HEAD : _FRAME_HEAD + players * _NODE_BYTES].copy().view('<i2')
    roots = nodes.reshape(len(raw), players, _NODE_BYTES // 2)[:, :, :2] * _POSITION_UNIT
    for k in range(players):
        counted = np.bincount(_bins(roots[:, k, 0], roots[:, k, 1]), minlength=cells)
        found['players'][k] = counted.tolist()
    ball = raw[:, 32:44].copy().view('<f4').astype(np.float64)
    found['ball'] = np.bincount(_bins(ball[:, 0], ball[:, 1]), minlength=cells).tolist()
    return found


def _round(value: float) -> float:
    return float(f'{value:.6g}')


def signals(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Returns the signals of the annotations of a recording, which a search of policy signals
    reads without decoding a file.

    A series is one named number of one player, or of the whole match, over the frames that
    annotate it in their order. The index keeps extremes and candidates alone, so the thresholds
    of a search apply when it runs and a change of them needs no new index.

    Args:
        records: The records of the annotations, as `_recording.annotations` returns them.

    Returns:
        `series`, by the key `<player>:<name>`, the player as its index in the header or `-` for
        the whole match, each with its `count` of frames, `min`, `max` and `mean`, and `drops` and
        `rises`, the `SIGNAL_CHANGES` largest falls and climbs from one annotated frame to the
        next, each `[frame, change]` at the later frame, the largest first. Then `unlikely`, by
        player, the frames where the chosen action's probability fell under `UNLIKELY`, each
        `[frame, probability]` with the least likely first and at most `UNLIKELY_FRAMES` of them,
        where a chosen action outside the kept probabilities counts at the most it can be. Then
        `entropy`, by player, for the frames that keep every probability, the `count` of such
        frames, `mean`, and `min` and `max`, each `[frame, entropy]` in nats.
    """
    series: dict[str, list[tuple[int, float]]] = {}
    unlikely: dict[str, list[tuple[float, int]]] = {}
    entropy: dict[str, list[tuple[int, float]]] = {}
    for record in sorted(records, key=lambda item: (item.get('f', 0), item.get('p', -1))):
        frame = record.get('f')
        if not isinstance(frame, int):
            continue
        player = record.get('p')
        who = str(player) if isinstance(player, int) else '-'
        for name, value in (record.get('v') or {}).items():
            if isinstance(value, int | float) and math.isfinite(value):
                series.setdefault(f'{who}:{name}', []).append((frame, float(value)))
        likely = record.get('k')
        if not isinstance(likely, list) or not likely:
            continue
        pairs = [
            (action, float(probability))
            for action, probability in (item for item in likely if len(item) == 2)
            if isinstance(probability, int | float)
        ]
        total = sum(probability for _, probability in pairs)
        chosen = record.get('a')
        if isinstance(chosen, int) and pairs:
            held = next((probability for action, probability in pairs if action == chosen), None)
            if held is None:
                # The chosen action lies outside the kept probabilities, so it had at most the
                # least of them and at most what the kept ones leave.
                held = min(min(probability for _, probability in pairs), max(0.0, 1.0 - total))
            if held < UNLIKELY:
                unlikely.setdefault(who, []).append((held, frame))
        if total >= _WHOLE:
            value = -sum(p * math.log(p) for _, p in pairs if p > 0)
            entropy.setdefault(who, []).append((frame, value))
    found: dict[str, Any] = {'series': {}, 'unlikely': {}, 'entropy': {}}
    for key, values in series.items():
        numbers = [value for _, value in values]
        steps = [
            (values[index][0], values[index][1] - values[index - 1][1])
            for index in range(1, len(values))
        ]
        drops = sorted((step for step in steps if step[1] < 0), key=lambda step: step[1])
        rises = sorted((step for step in steps if step[1] > 0), key=lambda step: -step[1])
        found['series'][key] = {
            'count': len(numbers),
            'min': _round(min(numbers)),
            'max': _round(max(numbers)),
            'mean': _round(sum(numbers) / len(numbers)),
            'drops': [[frame, _round(change)] for frame, change in drops[:SIGNAL_CHANGES]],
            'rises': [[frame, _round(change)] for frame, change in rises[:SIGNAL_CHANGES]],
        }
    for who, frames in unlikely.items():
        frames.sort()
        found['unlikely'][who] = [
            [frame, _round(probability)] for probability, frame in frames[:UNLIKELY_FRAMES]
        ]
    for who, values in entropy.items():
        low = min(values, key=lambda item: (item[1], item[0]))
        high = max(values, key=lambda item: (item[1], -item[0]))
        found['entropy'][who] = {
            'count': len(values),
            'mean': _round(sum(value for _, value in values) / len(values)),
            'min': [low[0], _round(low[1])],
            'max': [high[0], _round(high[1])],
        }
    return found


def _pair() -> list[int]:
    return [0, 0]


def _share(part: float, whole: float) -> float | None:
    return round(part / whole, 4) if whole else None


def _match(
    header: dict[str, Any],
    frames: Sequence[bytes],
    moments: list[dict[str, Any]],
    actions: np.ndarray | None = None,
) -> dict[str, Any]:
    """Returns the statistics of a recording, the fields that the module describes.

    Args:
        header: The header of the recording.
        frames: Its frames, as version 1 stores them.
        moments: Its moments, as the index of moments finds them.
        actions: Its actions, as `_recording.actions` returns them, or `None`.
    """
    players = len(header.get('players') or [])
    raw = _array(frames, players)
    found: dict[str, Any] = {
        'frames': len(raw),
        'length': None,
        'score': None,
        'outcome': None,
    }
    if len(raw):
        last = raw[-1].tobytes()
        step, left, right = struct.unpack_from('<i', last)[0], *struct.unpack_from('<2i', last, 8)
        found['length'] = step
        found['score'] = [left, right]
        found['outcome'] = 'win' if left > right else 'loss' if left < right else 'draw'
    goals, shots, on_target = _pair(), _pair(), _pair()
    passes, completed, received = _pair(), _pair(), _pair()
    for moment in moments:
        side = moment.get('team')
        if side not in (0, 1):
            continue
        data = moment.get('data') or {}
        kind = moment.get('kind')
        if kind == 'goal':
            goals[side] += 1
        elif kind == 'shot':
            shots[side] += 1
            on_target[side] += data.get('onTarget') is True
        elif kind == 'pass':
            passes[side] += 1
            outcome = data.get('outcome')
            completed[side] += outcome == 'complete'
            received[side] += outcome in ('complete', 'intercepted')
    owned = _pair()
    if len(raw):
        teams = raw[:, 16:20].copy().view('<i4').reshape(-1)
        owned = [int((teams == 0).sum()), int((teams == 1).sum())]
    found.update(
        goals=goals,
        shots=shots,
        shots_on_target=on_target,
        passes=passes,
        completed=completed,
        pass_accuracy=[_share(completed[side], received[side]) for side in (0, 1)],
        possession=[_share(owned[side], sum(owned)) for side in (0, 1)],
        owned=owned,
        actions=_actions(header, actions),
    )
    return found


def _labelled(values: dict[str, Any], units: dict[str, str]) -> dict[str, Any]:
    return {
        key: {'label': key.replace('_', ' ').capitalize(), 'unit': units[key], 'value': value}
        for key, value in values.items()
    }


class MatchStatistics:
    """Counts the play of each side."""

    def compute(self, recording: Recording) -> dict[str, Any]:
        values = _match(recording.header, recording.frames, recording.moments, recording.actions)
        return _labelled(
            values,
            {
                'frames': 'frames',
                'length': 'steps',
                'score': 'goals',
                'outcome': '',
                'goals': 'goals',
                'shots': 'shots',
                'shots_on_target': 'shots',
                'passes': 'passes',
                'completed': 'passes',
                'pass_accuracy': 'fraction',
                'possession': 'fraction',
                'owned': 'frames',
                'actions': '',
            },
        )


class HeatStatistics:
    """Counts the frames in each zone of the pitch."""

    def compute(self, recording: Recording) -> dict[str, Any]:
        return _labelled(
            heat(recording.header, recording.frames),
            dict(columns='bins', rows='bins', extent='meters', players='frames', ball='frames'),
        )


class SignalStatistics:
    """Summarizes the policy annotations."""

    def compute(self, recording: Recording) -> dict[str, Any]:
        return _labelled(
            signals(recording.annotations), dict(series='', unlikely='', entropy='nats')
        )


def _checked(answer: Any) -> dict[str, Any]:
    if not isinstance(answer, dict):
        raise ValueError('expected a mapping of named statistics')
    for key, row in answer.items():
        if (
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(row, dict)
            or set(row) != {'label', 'unit', 'value'}
            or not isinstance(row['label'], str)
            or not row['label'].strip()
            or not isinstance(row['unit'], str)
            or not plain(row['value'])
        ):
            raise ValueError('expected a label, unit and finite JSON value for each statistic')
    return deepcopy(answer)


def _bare(name: str, recording: Recording) -> dict[str, Any]:
    values = plugins.call('statistics', name, 'compute', recording)
    return {key: item['value'] for key, item in values.items()}


def signal_summary(recording: Recording) -> dict[str, Any]:
    """Returns the signal summary in its index format."""
    return _bare('signals', recording)


def compute(
    header: dict[str, Any],
    frames: Sequence[bytes],
    moments: list[dict[str, Any]],
    actions: np.ndarray | None = None,
    identifier: str = '',
) -> dict[str, Any]:
    """Returns the match statistics in their original library format."""
    recording = Recording(header, tuple(frames), actions=actions, moments=moments)
    found = {'version': VERSION, 'id': identifier, **_bare('match', recording)}
    found['heat'] = _bare('heat', recording)
    return found


def _actions(header: dict[str, Any], actions: np.ndarray | None) -> dict[str, Any] | None:
    """Returns the counts and shares of the actions of each team's agents, `None` without any."""
    agents = header.get('agents')
    if actions is None or not isinstance(agents, list) or len(agents) != 2:
        return None
    import numpy as np

    counts: list[dict[str, int]] = []
    share: list[dict[str, float]] = []
    columns = [range(0, agents[0]), range(agents[0], agents[0] + agents[1])]
    for side in (0, 1):
        values = actions[:, list(columns[side])].reshape(-1) if columns[side] else np.zeros(0)
        values = values[values != _NONE]
        numbers, times = np.unique(values, return_counts=True) if len(values) else ([], [])
        counted = {
            str(int(number)): int(count) for number, count in zip(numbers, times, strict=True)
        }
        total = sum(counted.values())
        counts.append(counted)
        share.append({key: round(value / total, 4) for key, value in counted.items()})
    return {'set': header.get('action_set') or 'default', 'counts': counts, 'share': share}


def folder() -> Path:
    """Returns the folder of the statistics, made when missing."""
    return _home.folder(FOLDER)


def write(identifier: str, statistics: dict[str, Any], home: Path | None = None) -> None:
    """Writes the statistics of a recording through a temporary file."""
    target = (home / FOLDER if home is not None else folder()) / f'{identifier}.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.part')
    temporary.write_text(json.dumps(statistics, separators=(',', ':')), encoding='utf-8')
    os.replace(temporary, target)


def read(identifier: str, home: Path | None = None) -> dict[str, Any] | None:
    """Returns the statistics of a recording as its file holds them, `None` for none or another
    version.

    `of` finds statistics that are missing, and this reads only what is kept.
    """
    if not isinstance(identifier, str) or not _home.valid_identifier(identifier):
        return None
    path = (home / FOLDER if home is not None else folder()) / f'{identifier}.json'
    try:
        found = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(found, dict) or found.get('version') != VERSION:
        return None
    return found


_VALUES: OrderedDict[tuple[str, tuple[Path, tuple[int, ...]] | None, str, Any], dict[str, Any]] = (
    OrderedDict()
)
_VALUES_LOCK = threading.Lock()


def of(identifier: str, *, name: str | None = None) -> dict[str, Any]:
    """Returns the statistics of a recording of the library, found first when none are kept.

    The statistics hold the goals, shots, passes, possession, the share of each action and the
    heat bins of the match, as the folder `stats` keeps them.

    Args:
        identifier: The identifier of the recording, such as `sample-5v5`.
        name: A statistics provider, which gives labelled values.

    Raises:
        ArgumentError: If the library holds no such recording outside the Trash.
    """
    from .._moments import search as _search

    if name is None:
        return _search.statistics(identifier)

    from .._format import attachments
    from .._text.messages import MessageError
    from . import library

    path = library.path_of(identifier)
    if path is None:
        raise MessageError('server.library.unknown', identifier=repr(identifier))
    plugins.get('statistics', name)
    signature = tuple(attachments.section_signature(path))
    key = (
        attachments.digest(path),
        (path, signature) if signature else None,
        name,
        plugins.signature(),
    )
    with _VALUES_LOCK:
        if key in _VALUES:
            _VALUES.move_to_end(key)
            return deepcopy(_VALUES[key])
    recording = _search.analysis_recording(path)
    recording.moments.extend(_search.standard_moments(recording))
    found = plugins.call('statistics', name, 'compute', recording, check=_checked)
    with _VALUES_LOCK:
        found = _VALUES.setdefault(key, found)
        _VALUES.move_to_end(key)
        if len(_VALUES) > 64:
            _VALUES.popitem(last=False)
    return deepcopy(found)


def many(identifiers: list[str]) -> dict[str, dict[str, Any] | None]:
    """Returns the kept statistics of several recordings by identifier, `None` for one without.

    This reads the files alone, so a dashboard over many episodes costs a small file each.
    """
    return {identifier: read(identifier) for identifier in identifiers}
