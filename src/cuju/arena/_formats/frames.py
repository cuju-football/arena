# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The frames between the match process and a format process, and between a format and its child.

A frame is two lengths, eight bytes in all, then a header of JSON in UTF-8, then the raw bytes of
every array it carries. The header holds the contract number, the kind of the message, the value
as JSON with each array in its place as `{"$array": index}`, and the dtype and shape of each
array. Arrays travel as their own bytes in little-endian order, so a frame carries the values of
GRF's raw observation exactly, and nothing is ever pickled in either direction.

A list of GRF raw observations travels packed, `{"$raw": [first, own, other, rows]}`. Its single
values and its score go in the header, one row per player, and each array key of `TABLE` goes in
the body once, the players stacked, with the shape that the sizes of the two teams give it.

`TABLE` is the fixed table of GRF's raw observation, the type of every key as `grf_observation`
of cuju gives it, Python integers for the single values, a list of two for the score and numpy
arrays of GRF's dtypes elsewhere. A packed list reads back in exactly those types, and
`typed(raw)` holds any other observation to them. A test pins the table to `grf_observation`.

This module uses the standard library and numpy alone, since the child of a format imports it.
"""

from __future__ import annotations

import json
import math
import struct
import sys
from typing import Any, BinaryIO

import numpy as np

# The contract that every frame names in its header.
CONTRACT = 1
# The two lengths in front of a frame, the header's and the body's.
_LENGTHS = struct.Struct('<II')
# The largest header and the largest body of a frame. A step of eleven players is about 14 KB,
# so these leave room for any answer a contract allows and stop a runaway stream.
MAXIMUM_HEADER = 16 * 1024 * 1024
MAXIMUM_BODY = 256 * 1024 * 1024
# The dtypes that may cross, by their names in numpy.
DTYPES = frozenset(
    {
        'bool',
        'uint8',
        'int8',
        'int16',
        'uint16',
        'int32',
        'uint32',
        'int64',
        'uint64',
        'float16',
        'float32',
        'float64',
    }
)
# Each dtype that may cross by its code in little-endian order, which a header names, and back.
_CODES = {np.dtype(name).newbyteorder('<').str: np.dtype(name) for name in DTYPES}
_CODE_OF = {dtype: code for code, dtype in _CODES.items()}
# The keys of an array and of a packed list in their places in the value.
_ARRAY = '$array'
_RAW = '$raw'
_NATIVE = sys.byteorder == 'little'

# GRF: the types of a raw observation, as `cuju.raw.grf_observation` gives them. `int` is a
# Python integer, `score` a list of two integers, and a dtype names a numpy array of that dtype,
# with its rank beside it.
TABLE: dict[str, tuple[str, int]] = {
    'ball': ('float64', 1),
    'ball_direction': ('float64', 1),
    'ball_rotation': ('float64', 1),
    'ball_owned_team': ('int', 0),
    'ball_owned_player': ('int', 0),
    'left_team': ('float64', 2),
    'left_team_direction': ('float64', 2),
    'left_team_tired_factor': ('float64', 1),
    'left_team_yellow_card': ('bool', 1),
    'left_team_active': ('bool', 1),
    'left_team_roles': ('int64', 1),
    'right_team': ('float64', 2),
    'right_team_direction': ('float64', 2),
    'right_team_tired_factor': ('float64', 1),
    'right_team_yellow_card': ('bool', 1),
    'right_team_active': ('bool', 1),
    'right_team_roles': ('int64', 1),
    'score': ('score', 1),
    'steps_left': ('int', 0),
    'game_mode': ('int', 0),
    'active': ('int', 0),
    'designated': ('int', 0),
    'sticky_actions': ('uint8', 1),
}
_RAW_KEYS = frozenset(TABLE)
_RAW_SCALARS = tuple(key for key, (kind, _) in TABLE.items() if kind == 'int')
_RAW_ARRAYS = tuple(key for key, (kind, _) in TABLE.items() if kind not in ('int', 'score'))
_RAW_DTYPES = {key: np.dtype(TABLE[key][0]) for key in _RAW_ARRAYS}
_RAW_PLACES = {key: place for place, key in enumerate(_RAW_ARRAYS)}


class FrameError(ValueError):
    """Raised for a frame that does not read, or a value that no frame can carry."""


def _raw_shape(key: str, own: int, other: int) -> tuple[int, ...]:
    """Returns the shape of an array key of a raw observation, by the sizes of the two teams."""
    if key.startswith('left_'):
        return (own, 2) if TABLE[key][1] == 2 else (own,)
    if key.startswith('right_'):
        return (other, 2) if TABLE[key][1] == 2 else (other,)
    return (10,) if key == 'sticky_actions' else (3,)


def _packable(value: list[Any]) -> tuple[int, int] | None:
    """Returns the team sizes of a list of raw observations that packs, or `None`."""
    if not all(isinstance(item, dict) and item.keys() == _RAW_KEYS for item in value):
        return None
    own, other = len(value[0]['left_team']), len(value[0]['right_team'])
    shapes = {key: _raw_shape(key, own, other) for key in _RAW_ARRAYS}
    for item in value:
        for key in _RAW_ARRAYS:
            array = item[key]
            if (
                type(array) is not np.ndarray
                or array.dtype != _RAW_DTYPES[key]
                or array.shape != shapes[key]
            ):
                return None
        for key in _RAW_SCALARS:
            if type(item[key]) is not int:
                return None
        score = item['score']
        if not (type(score) is list and len(score) == 2 and all(type(g) is int for g in score)):
            return None
    return own, other


class _Writer:
    """The body of a frame as it is built, its parts of bytes and the dtype and shape of each."""

    def __init__(self) -> None:
        self.parts: list[bytes] = []
        self.shapes: list[list[Any]] = []

    def array(self, array: np.ndarray) -> int:
        dtype = array.dtype
        code = _CODE_OF.get(dtype.newbyteorder('=') if not dtype.isnative else dtype)
        if code is None:
            raise FrameError(f'an array of {dtype} cannot cross, only {sorted(DTYPES)}')
        if not (_NATIVE and dtype.isnative) and dtype.byteorder != '|':
            array = array.astype(dtype.newbyteorder('<'))
        self.parts.append(array.tobytes())
        self.shapes.append([code, list(array.shape)])
        return len(self.shapes) - 1

    def block(self, arrays: list[np.ndarray], shape: tuple[int, ...]) -> None:
        dtype = arrays[0].dtype
        if _NATIVE:
            self.parts.append(b''.join([array.tobytes() for array in arrays]))
        else:  # pragma: no cover, a big-endian machine
            self.parts.append(
                b''.join([array.astype('<' + dtype.str[1:]).tobytes() for array in arrays])
            )
        self.shapes.append([_CODE_OF[dtype], [len(arrays), *shape]])

    def tree(self, value: Any) -> Any:
        """Returns a value as JSON with its arrays set aside as bytes."""
        if value is None or isinstance(value, bool | int | float | str):
            return value
        if isinstance(value, np.ndarray):
            return {_ARRAY: self.array(value)}
        if isinstance(value, list) and value and isinstance(value[0], dict):
            sizes = _packable(value)
            if sizes is not None:
                first = len(self.shapes)
                for key in _RAW_ARRAYS:
                    self.block([item[key] for item in value], _raw_shape(key, *sizes))
                rows = [[item[key] for key in _RAW_SCALARS] + item['score'] for item in value]
                return {_RAW: [first, sizes[0], sizes[1], rows]}
        if isinstance(value, np.generic):
            return self.tree(value.item())
        if isinstance(value, dict):
            found = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise FrameError(f'a key of a mapping must be text, found {key!r}')
                if key in (_ARRAY, _RAW):
                    raise FrameError(f'the key {key!r} is the frames own')
                found[key] = self.tree(item)
            return found
        if isinstance(value, list | tuple):
            return [self.tree(item) for item in value]
        raise FrameError(f'a value of type {type(value).__name__} cannot cross')


def _unpack(packed: Any, arrays: list[np.ndarray]) -> list[dict[str, Any]]:
    """Returns the raw observations of a packed list, in GRF's own types."""
    try:
        first, own, other, rows = packed
        blocks = arrays[first : first + len(_RAW_ARRAYS)]
        count = len(rows)
        if len(blocks) != len(_RAW_ARRAYS):
            raise FrameError('a packed list of raw observations lacks its arrays')
        for key, block in zip(_RAW_ARRAYS, blocks, strict=True):
            if block.dtype != _RAW_DTYPES[key] or block.shape != (
                count,
                *_raw_shape(key, own, other),
            ):
                raise FrameError(f'the raw key {key!r} holds an array of another shape')
        found = []
        width = len(_RAW_SCALARS) + 2
        for index, row in enumerate(rows):
            if len(row) != width or not all(type(item) is int for item in row):
                raise FrameError('a packed raw observation holds a value of another type')
            entry: dict[str, Any] = {}
            scalars = dict(zip(_RAW_SCALARS, row, strict=False))
            for key in TABLE:
                if key in scalars:
                    entry[key] = scalars[key]
                elif key == 'score':
                    entry[key] = row[-2:]
                else:
                    entry[key] = blocks[_RAW_PLACES[key]][index]
            found.append(entry)
        return found
    except FrameError:
        raise
    except (TypeError, ValueError) as error:
        raise FrameError(f'a packed list of raw observations does not read, {error}') from None


def _value(tree: Any, arrays: list[np.ndarray]) -> Any:
    """Returns the value of a tree, with each array back in its place."""
    if isinstance(tree, dict):
        if len(tree) == 1:
            if _ARRAY in tree:
                index = tree[_ARRAY]
                if not isinstance(index, int) or not 0 <= index < len(arrays):
                    raise FrameError(f'no array {index!r} in the frame')
                return arrays[index]
            if _RAW in tree:
                return _unpack(tree[_RAW], arrays)
        return {key: _value(item, arrays) for key, item in tree.items()}
    if isinstance(tree, list):
        return [_value(item, arrays) for item in tree]
    return tree


def encode(kind: str, value: Any = None, **fields: Any) -> bytes:
    """Returns one frame of a message, its kind, its value and any other fields of the header.

    Raises:
        FrameError: If the value holds what no frame carries, such as an object or a set.
    """
    writer = _Writer()
    tree = writer.tree(value)
    header = {'contract': CONTRACT, 'kind': kind, **fields, 'value': tree, 'arrays': writer.shapes}
    head = json.dumps(header, separators=(',', ':')).encode('utf-8')
    body = b''.join(writer.parts)
    if len(head) > MAXIMUM_HEADER or len(body) > MAXIMUM_BODY:
        raise FrameError(f'a frame of {len(head) + len(body)} bytes is too large to cross')
    return _LENGTHS.pack(len(head), len(body)) + head + body


def decode(head: bytes, body: bytearray | bytes) -> dict[str, Any]:
    """Returns the header of a frame with its value under `value`, arrays in their places.

    The arrays are views of `body`, so a body read into a `bytearray` gives arrays that may be
    written, as GRF's own are.

    Raises:
        FrameError: If the frame does not read, or names another contract.
    """
    try:
        header = json.loads(head)
    except (UnicodeDecodeError, ValueError) as error:
        raise FrameError(f'a frame header does not read, {error}') from None
    if not isinstance(header, dict) or not isinstance(header.get('kind'), str):
        raise FrameError('a frame header names no kind')
    if header.get('contract') != CONTRACT:
        raise FrameError(f'a frame of contract {header.get("contract")!r}, expected {CONTRACT}')
    arrays = []
    offset = 0
    view = memoryview(body)
    for entry in header.pop('arrays', None) or ():
        try:
            code, shape = entry
            dtype = _CODES[code]
            count = math.prod(shape)
            size = count * dtype.itemsize
        except (KeyError, TypeError, ValueError) as error:
            raise FrameError(f'an array of the frame does not read, {error!r}') from None
        if count < 0 or offset + size > len(body):
            raise FrameError('a frame holds fewer bytes than its arrays')
        if _NATIVE:
            array = np.frombuffer(view[offset : offset + size], dtype=dtype, count=count)
        else:  # pragma: no cover, a big-endian machine
            little = np.frombuffer(view[offset : offset + size], dtype=code, count=count)
            array = little.astype(dtype)
        arrays.append(array.reshape(shape))
        offset += size
    if offset != len(body):
        raise FrameError('a frame holds more bytes than its arrays')
    header['value'] = _value(header.get('value'), arrays)
    return header


def _read_exactly(stream: BinaryIO, size: int) -> bytearray:
    buffer = bytearray(size)
    view = memoryview(buffer)
    done = 0
    while done < size:
        count = stream.readinto(view[done:])
        if not count:
            raise EOFError('the stream ended inside a frame')
        done += count
    return buffer


def read(stream: BinaryIO) -> dict[str, Any]:
    """Reads one frame from a binary stream and returns its header with its value.

    Raises:
        EOFError: If the stream ends before or inside a frame.
        FrameError: If the frame does not read.
    """
    lengths = stream.read(_LENGTHS.size)
    if not lengths:
        raise EOFError('the stream ended')
    if len(lengths) < _LENGTHS.size:
        lengths = bytes(lengths) + bytes(_read_exactly(stream, _LENGTHS.size - len(lengths)))
    head_size, body_size = _LENGTHS.unpack(lengths)
    if head_size > MAXIMUM_HEADER or body_size > MAXIMUM_BODY:
        raise FrameError(f'a frame of {head_size + body_size} bytes is too large to read')
    head = _read_exactly(stream, head_size)
    body = _read_exactly(stream, body_size)
    return decode(bytes(head), body)


def write(stream: BinaryIO, kind: str, value: Any = None, **fields: Any) -> None:
    """Writes one frame to a binary stream and flushes it."""
    stream.write(encode(kind, value, **fields))
    stream.flush()


def typed(raw: Any) -> dict[str, Any]:
    """Returns a decoded raw observation held to `TABLE`, in the types GRF gives it.

    A key outside the table passes as it came, since a frame of a later engine may add one.

    Raises:
        FrameError: If a key of the table holds a value of another type or rank.
    """
    if not isinstance(raw, dict):
        raise FrameError(f'a raw observation is a mapping, found {type(raw).__name__}')
    for key, (kind, rank) in TABLE.items():
        if key not in raw:
            raise FrameError(f'a raw observation lacks {key!r}')
        value = raw[key]
        if kind == 'int':
            good = type(value) is int
        elif kind == 'score':
            good = (
                type(value) is list
                and len(value) == 2
                and all(type(goals) is int for goals in value)
            )
        else:
            good = (
                type(value) is np.ndarray and value.dtype == _RAW_DTYPES[key] and value.ndim == rank
            )
        if not good:
            raise FrameError(f'the raw key {key!r} holds {value!r}, expected {kind}')
    return raw
