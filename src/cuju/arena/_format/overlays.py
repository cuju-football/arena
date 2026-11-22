# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Spatial overlays, the grids and vectors of a policy that a recording keeps in `GRID`.

An overlay belongs to a frame and, optionally, to one player of the header. A grid holds a value
for each cell of a rectangle of the pitch, such as a value map, and a vector holds a value for
each player, as the targets of a pass, or for each action, as a whole distribution. The section
holds an index, a JSON object that lists one record for each overlay, and after it one block of
bytes with the numbers of every overlay, which the compression of the file packs together.

A record in memory holds `f`, the frame, `p`, the index of the player in the header or no entry
for the whole match, `name`, `kind`, one of `KINDS`, `values`, the numbers, and as it needs them
`side`, 0 or 1 for the side whose own view the numbers lie in, `extent`, the rectangle of a grid
as x and y of its first corner and of its last, in GRF's raw units, and `type`, how the file
stores the numbers. See Overlays in `docs/recording-format.md` for the layout.
"""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from cuju.errors import ArgumentError

if TYPE_CHECKING:
    import numpy as np


TAG = b'GRID'
# The kinds of an overlay, a grid of cells, a value for each player and a value for each action.
KINDS = ('grid', 'targets', 'actions')
# How the section stores numbers, as bytes with a scale and an offset or as 16-bit floats.
TYPES = ('uint8', 'float16')
# The limits of the overlays of a recording. A step and player take at most this many grids and
# this many vectors, a grid at most GRF's minimap of 72 rows of 96 cells, and the section at most
# 32 MiB before the compression of the file.
MAX_GRIDS = 4
MAX_VECTORS = 4
MAX_ROWS = 72
MAX_COLUMNS = 96
MAX_VECTOR = 64
MAX_BYTES = 32 << 20
NAME_LENGTH = 40
_MAX_FRAME = 50_000
_MAX_PLAYER = 32
_LENGTH = struct.Struct('<I')


def _whole(value: Any, limit: int) -> bool:
    """Returns whether a value is a whole number from 0 to below `limit`, which `True` is not."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < limit


def _shape_fits(kind: Any, shape: Any) -> bool:
    """Returns whether a shape is one that an overlay of `kind` may take."""
    if not isinstance(shape, list | tuple) or not all(_whole(size, 1 << 16) for size in shape):
        return False
    if kind == 'grid':
        return len(shape) == 2 and 1 <= shape[0] <= MAX_ROWS and 1 <= shape[1] <= MAX_COLUMNS
    return kind in KINDS and len(shape) == 1 and 1 <= shape[0] <= MAX_VECTOR


def _extent(value: Any) -> list[float] | None:
    """Returns the rectangle of a grid as four finite numbers, or `None` for a malformed one."""
    if hasattr(value, 'tolist'):
        value = value.tolist()
    if not isinstance(value, list | tuple) or len(value) != 4:
        return None
    if not all(
        isinstance(item, int | float) and not isinstance(item, bool) and math.isfinite(item)
        for item in value
    ):
        return None
    return [float(item) for item in value]


def _count(counts: dict[tuple[int, int, bool], int], record: dict[str, Any]) -> bool:
    """Counts an overlay against the limits of its step and player, and returns whether it fits."""
    key = (record['f'], record.get('p', -1), record['kind'] == 'grid')
    counts[key] = counts.get(key, 0) + 1
    return counts[key] <= (MAX_GRIDS if key[2] else MAX_VECTORS)


def quantize(values: Any) -> tuple[np.ndarray, float, float]:
    """Returns a grid as bytes, each value rounded to one of 256 steps between its least and its
    greatest value, with the scale and the offset that give the values again.

    A grid of one value takes the step 0 and the scale 0.

    Args:
        values: The finite numbers of the grid.
    """
    import numpy as np

    values = np.asarray(values, np.float64)
    low, high = float(values.min()), float(values.max())
    scale = (high - low) / 255
    steps = np.rint((values - low) / scale) if scale > 0 else np.zeros(values.shape)
    return np.clip(steps, 0, 255).astype(np.uint8), scale, low


def _finite_number(value: Any) -> bool:
    """Returns whether a value is a finite number, which `True` is not."""
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def prepare(record: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    """Checks one overlay and returns its entry of the index without its place, and its bytes.

    A grid of `type` `uint8` whose `values` are bytes already, with its `scale` and `offset`, as
    `quantize` returns them, keeps those bytes as they are, so a caller may hold a grid in a
    quarter of the room of its numbers until the recording is written.

    Args:
        record: The overlay, as the module describes a record in memory.

    Raises:
        ArgumentError: If the record is malformed, holds a number that is not finite, or passes
            a limit of one overlay.
    """
    import numpy as np

    frame, player, name, kind = (record.get(key) for key in ('f', 'p', 'name', 'kind'))
    if not _whole(frame, _MAX_FRAME) or not (player is None or _whole(player, _MAX_PLAYER)):
        raise ArgumentError(f'expected an overlay of a frame and a player, found {record!r}')
    if not isinstance(name, str) or not 0 < len(name) <= NAME_LENGTH:
        raise ArgumentError(f'expected a name of 1 to {NAME_LENGTH} characters, found {name!r}')
    if kind not in KINDS:
        raise ArgumentError(f'expected the kind of an overlay from {KINDS}, found {kind!r}')
    given = record.get('values')
    stored = isinstance(given, np.ndarray) and given.dtype == np.uint8 and 'scale' in record
    values = given if stored else np.asarray(given, np.float64)
    if not _shape_fits(kind, values.shape):
        raise ArgumentError(
            f'expected a grid of at most {MAX_ROWS} rows of {MAX_COLUMNS} cells or a vector '
            f'of at most {MAX_VECTOR} values, found the shape {values.shape} for {name!r}'
        )
    if not stored and not np.isfinite(values).all():
        raise ArgumentError(f'expected finite numbers in the overlay {name!r}')
    kept: dict[str, Any] = {'f': frame}
    if player is not None:
        kept['p'] = player
    kept.update(name=name, kind=kind, shape=list(values.shape))
    if record.get('side') is not None:
        if record['side'] not in (0, 1) or isinstance(record['side'], bool):
            raise ArgumentError(f'expected the side 0 or 1, found {record["side"]!r}')
        kept['side'] = record['side']
    if record.get('extent') is not None:
        extent = _extent(record['extent'])
        if extent is None or kind != 'grid':
            raise ArgumentError(
                f'expected the extent of a grid as four finite numbers, found {record["extent"]!r}'
            )
        kept['extent'] = extent
    kind_of = record.get('type') or ('uint8' if kind == 'grid' else 'float16')
    if kind_of not in TYPES or (stored and kind_of != 'uint8'):
        raise ArgumentError(f'expected the type of an overlay from {TYPES}, found {kind_of!r}')
    kept['type'] = kind_of
    if stored:
        scale, offset = record['scale'], record['offset']
        if not (_finite_number(scale) and _finite_number(offset) and scale >= 0):
            raise ArgumentError(f'expected the scale and offset of {name!r} as finite numbers')
        kept.update(scale=float(scale), offset=float(offset))
        return kept, values.tobytes()
    if kind_of == 'uint8':
        steps, scale, low = quantize(values)
        kept.update(scale=scale, offset=low)
        return kept, steps.tobytes()
    with np.errstate(over='ignore'):
        halves = values.astype('<f2')
    if not np.isfinite(halves).all():
        raise ArgumentError(f'expected numbers that 16-bit floats hold in {name!r}')
    return kept, halves.tobytes()


def entry_bytes(entry: dict[str, Any], data: bytes) -> int:
    """Returns the most room that one overlay takes in a section, its entry of the index with its
    place, its bytes and the byte that may align them, as `prepare` returns them."""
    return len(json.dumps({**entry, 'at': MAX_BYTES}, separators=(',', ':'))) + len(data) + 2


def encode(records: Iterable[dict[str, Any]]) -> bytes:
    """Returns the payload of an overlays section, its records in order of frame and player.

    A record without a `type` stores a grid as bytes, each value rounded to one of 256 steps
    between the least and the greatest value of the grid, and a vector as 16-bit floats.

    Args:
        records: The overlays, each as the module describes a record in memory.

    Raises:
        ArgumentError: If a record is malformed, holds a number that is not finite, repeats the
            frame, player and name of another, or the overlays pass a limit of the section.
    """
    index, blob, size = [], [], 0
    counts: dict[tuple[int, int, bool], int] = {}
    seen = set()

    def place(record: dict[str, Any]) -> tuple[int, int, str]:
        frame, player = record.get('f'), record.get('p')
        return (
            frame if isinstance(frame, int) else -1,
            player if isinstance(player, int) else -1,
            str(record.get('name')),
        )

    for record in sorted(records, key=place):
        kept, data = prepare(record)
        frame, player, name = kept['f'], kept.get('p'), kept['name']
        if (frame, player, name) in seen:
            raise ArgumentError(f'expected one overlay {name!r} of a step and player, found two')
        seen.add((frame, player, name))
        if not _count(counts, kept):
            raise ArgumentError(
                f'expected at most {MAX_GRIDS} grids and {MAX_VECTORS} vectors of a step and '
                f'player, found more at frame {frame}'
            )
        # A 16-bit float starts at an even byte, so a reader views the numbers in place.
        if kept['type'] == 'float16' and size % 2:
            blob.append(b'\0')
            size += 1
        kept['at'] = size
        index.append(kept)
        blob.append(data)
        size += len(data)
    text = json.dumps({'records': index}, separators=(',', ':')).encode()
    text += b' ' * (-(_LENGTH.size + len(text)) % 4)
    payload = _LENGTH.pack(len(text)) + text + b''.join(blob)
    if len(payload) > MAX_BYTES:
        raise ArgumentError(
            f'expected overlays of at most {MAX_BYTES >> 20} MiB in a recording, found '
            f'{len(payload) >> 20} MiB'
        )
    return payload


def decode(payload: bytes) -> list[dict[str, Any]]:
    """Returns the overlays of a section, leaving out any that is malformed or past a limit.

    Each record comes as the module describes it in memory, its `values` as 32-bit floats in the
    shape of the overlay, with the `type` that the file stored them in.
    """
    if len(payload) < _LENGTH.size or len(payload) > MAX_BYTES:
        return []
    (length,) = _LENGTH.unpack_from(payload)
    start = _LENGTH.size + length
    if start > len(payload):
        return []
    try:
        records = json.loads(payload[_LENGTH.size : start])['records']
    except (ValueError, KeyError, TypeError, RecursionError):
        return []
    if not isinstance(records, list):
        return []
    blob = memoryview(payload)[start:]
    found = []
    counts: dict[tuple[int, int, bool], int] = {}
    for record in records:
        overlay = _read(record, blob)
        if overlay is not None and _count(counts, overlay):
            found.append(overlay)
    return found


def _read(record: Any, blob: memoryview) -> dict[str, Any] | None:
    """Returns one overlay of the index with its numbers, or `None` for a malformed record."""
    import numpy as np

    if not isinstance(record, dict):
        return None
    frame, player, name, kind = (record.get(key) for key in ('f', 'p', 'name', 'kind'))
    shape, kind_of, at = record.get('shape'), record.get('type'), record.get('at')
    if not (
        _whole(frame, _MAX_FRAME)
        and (player is None or _whole(player, _MAX_PLAYER))
        and isinstance(name, str)
        and 0 < len(name) <= NAME_LENGTH
        and _shape_fits(kind, shape)
        and kind_of in TYPES
        and _whole(at, MAX_BYTES)
    ):
        return None
    cells = math.prod(shape)
    width = 1 if kind_of == 'uint8' else 2
    if at + cells * width > len(blob) or (width == 2 and at % 2):
        return None
    overlay: dict[str, Any] = {'f': frame}
    if player is not None:
        overlay['p'] = player
    overlay.update(name=name, kind=kind, type=kind_of)
    if record.get('side') is not None:
        if record['side'] not in (0, 1) or isinstance(record['side'], bool):
            return None
        overlay['side'] = record['side']
    if record.get('extent') is not None:
        extent = _extent(record['extent'])
        if extent is None:
            return None
        overlay['extent'] = extent
    if kind_of == 'uint8':
        scale, offset = record.get('scale'), record.get('offset')
        if not all(
            isinstance(number, int | float)
            and not isinstance(number, bool)
            and math.isfinite(number)
            for number in (scale, offset)
        ):
            return None
        stored = np.frombuffer(blob, np.uint8, cells, at)
        values = stored.astype(np.float64) * scale + offset
    else:
        values = np.frombuffer(blob, '<f2', cells, at).astype(np.float64)
    if not np.isfinite(values).all():
        return None
    overlay['values'] = values.astype(np.float32).reshape(shape)
    return overlay
