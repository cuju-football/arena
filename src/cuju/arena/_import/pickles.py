# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""A reader of GRF dumps that runs no code of the file.

A GRF dump is a run of Python pickles, one for each step, and a pickle may name any function of
any module for the reader to call. This reader names the few classes that a dump holds, GRF's
actions and numpy's arrays, and maps each to a small stand-in of its own, so a pickle that names
anything else stops the read before a call happens. Arrays come back as numpy arrays, actions as
their names, and every other value as plain Python data.

A pickle may also refer to one value many times, so a few bytes could stand for a very large
value once each reference is copied. The reader counts the values it makes and the depth of
their nesting, and stops a file that passes either limit.

cuju's own `cuju.dumps.read_dump` reads dumps under the same rule. This reader keeps the rule
inside the renderer, adds the limits, and returns numpy arrays, which the rest of the import uses.
"""

from __future__ import annotations

import io
import math
import pickle
from collections.abc import Iterator
from typing import Any

import numpy as np

# The values that a file may make for each byte it holds, beside a fixed allowance. A dump holds
# about one value for every eight bytes.
VALUES_PER_BYTE = 4
_VALUES_ALLOWANCE = 1 << 20
# The deepest nesting of containers that a step may hold. A step of GRF nests four deep.
MAX_DEPTH = 32
# The numpy types that a dump's arrays may hold, by their code without the byte order.
_TYPES = {'f8', 'f4', 'f2', 'i8', 'i4', 'i2', 'i1', 'u8', 'u4', 'u2', 'u1', 'b1'}


class RefusedError(Exception):
    """Raised when a file holds what a dump does not, which the reader never calls or keeps.

    Attributes:
        found: A short text of what the file holds, such as the name of a function.
    """

    def __init__(self, found: str) -> None:
        super().__init__(found)
        self.found = found


class TruncatedError(Exception):
    """Raised by `steps` when the file ends inside a step, with the whole steps before it.

    Attributes:
        steps: The steps read before the cut.
    """

    def __init__(self, steps: list[dict[str, Any]]) -> None:
        super().__init__(f'the file ends inside step {len(steps)}')
        self.steps = steps


class _Action:
    """GRF's `CoreAction`, of which the reader keeps the name alone."""

    def __init__(self, *arguments: Any) -> None:
        self.name: Any = None

    def __setstate__(self, state: Any) -> None:
        if isinstance(state, tuple) and len(state) == 2:
            # A pickle may give the attributes and the slots apart.
            state = state[0] if isinstance(state[0], dict) else state[1]
        if isinstance(state, dict):
            self.name = state.get('_name')


class _BackendAction(int):
    """GRF's `e_BackendAction`, a whole number."""


class _Type:
    """A numpy `dtype`, of which the reader keeps the code and the byte order."""

    def __init__(self, code: Any = None, align: Any = False, copy: Any = True) -> None:
        self.code, self.order = code, '|'

    def __setstate__(self, state: Any) -> None:
        if isinstance(state, tuple) and len(state) > 1:
            self.order = state[1]


class _Array:
    """A numpy array, of which the reader keeps the shape, the type and the bytes."""

    def __init__(self, *arguments: Any) -> None:
        self.shape: Any = None
        self.type: Any = None
        self.data: Any = None

    def __setstate__(self, state: Any) -> None:
        if isinstance(state, tuple) and len(state) == 5:
            _, self.shape, self.type, _, self.data = state


class _Scalar:
    """A numpy scalar, a type and its bytes."""

    def __init__(self, numpy_type: Any = None, data: Any = None) -> None:
        self.type, self.data = numpy_type, data


def _reconstruct(cls: Any, shape: Any, code: Any) -> _Array:
    return _Array()


def _frombuffer(buffer: Any, numpy_type: Any, shape: Any, order: Any) -> _Array:
    array = _Array()
    array.shape, array.type, array.data = shape, numpy_type, buffer
    return array


def _encode(text: Any, encoding: Any = 'utf-8') -> bytes:
    # Python 3 writes bytes into a pickle of protocol 2 as text that this function of `_codecs`
    # turns back, so the reader takes it for text and Latin-1 alone.
    if not isinstance(text, str) or encoding not in ('latin1', 'latin-1'):
        raise RefusedError('bytes of another encoding than Latin-1')
    return text.encode('latin-1')


def _reconstructor(cls: Any, base: Any, state: Any) -> Any:
    # The pickles of protocols 0 and 1 build an object through this function of `copyreg`.
    if cls not in (_Action, _Array):
        raise RefusedError('an object of a class that a dump does not hold')
    return cls()


_GLOBALS: dict[tuple[str, str], Any] = {
    ('gfootball.env.football_action_set', 'CoreAction'): _Action,
    ('cuju.gfootball.env.football_action_set', 'CoreAction'): _Action,
    ('_gameplayfootball', 'e_BackendAction'): _BackendAction,
    ('gfootball_engine', 'e_BackendAction'): _BackendAction,
    ('cuju.gfootball_engine._gameplayfootball', 'e_BackendAction'): _BackendAction,
    ('cuju.gfootball_engine', 'e_BackendAction'): _BackendAction,
    ('numpy.core.multiarray', '_reconstruct'): _reconstruct,
    ('numpy._core.multiarray', '_reconstruct'): _reconstruct,
    ('numpy.core.numeric', '_frombuffer'): _frombuffer,
    ('numpy._core.numeric', '_frombuffer'): _frombuffer,
    ('numpy.core.multiarray', 'scalar'): _Scalar,
    ('numpy._core.multiarray', 'scalar'): _Scalar,
    ('numpy', 'ndarray'): _Array,
    ('numpy', 'dtype'): _Type,
    ('copyreg', '_reconstructor'): _reconstructor,
    ('_codecs', 'encode'): _encode,
    ('copy_reg', '_reconstructor'): _reconstructor,
}


class _Unpickler(pickle.Unpickler):
    """Reads one pickle, resolving the names of `_GLOBALS` alone."""

    def find_class(self, module: str, name: str) -> Any:
        found = _GLOBALS.get((module, name))
        if found is None:
            raise RefusedError(f'{module}.{name}'[:200])
        return found

    def persistent_load(self, pid: Any) -> Any:
        raise RefusedError('a persistent reference')


class _Budget:
    """Counts the values made of one file."""

    def __init__(self, size: int) -> None:
        self.left = VALUES_PER_BYTE * size + _VALUES_ALLOWANCE

    def spend(self, count: int) -> None:
        self.left -= count
        if self.left < 0:
            raise RefusedError(
                'more values than its size can hold, as a pickle that repeats itself'
            )


def _numpy_type(value: Any) -> np.dtype:
    """Returns the numpy type of a `_Type`, or raises `RefusedError`."""
    if not isinstance(value, _Type) or not isinstance(value.code, str):
        raise RefusedError('an array without a numpy type')
    code = value.code.lstrip('<|=')
    if code not in _TYPES or value.order == '>':
        raise RefusedError(f'an array of the type {value.code!r}'[:200])
    found = np.dtype(code)
    return found.newbyteorder('<') if found.itemsize > 1 else found


def _array(value: _Array, budget: _Budget) -> np.ndarray:
    """Returns the numpy array of an `_Array`, or raises `RefusedError`."""
    numpy_type = _numpy_type(value.type)
    shape = value.shape
    if not isinstance(shape, tuple) or not all(
        isinstance(size, int) and not isinstance(size, bool) and size >= 0 for size in shape
    ):
        raise RefusedError('an array without a shape')
    count = math.prod(shape)
    budget.spend(count)
    data = value.data
    if isinstance(data, str):
        # Python 2 wrote the bytes of an array as text of Latin-1.
        data = data.encode('latin-1')
    if not isinstance(data, (bytes, bytearray)) or len(data) != count * numpy_type.itemsize:
        raise RefusedError('an array whose bytes do not fit its shape')
    return np.frombuffer(bytes(data), numpy_type).reshape(shape).copy()


def _converted(value: Any, budget: _Budget, depth: int = 0) -> Any:
    """Returns a loaded value as plain data, numpy arrays and action names.

    Raises:
        RefusedError: If the value holds what a dump does not, or passes a limit.
    """
    if depth > MAX_DEPTH:
        raise RefusedError(f'values nested more than {MAX_DEPTH} deep')
    budget.spend(1)
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, _BackendAction):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (bytes, bytearray)):
        budget.spend(len(value) // 8)
        return bytes(value)
    if isinstance(value, _Array):
        return _array(value, budget)
    if isinstance(value, _Scalar):
        numpy_type = _numpy_type(value.type)
        data = value.data.encode('latin-1') if isinstance(value.data, str) else value.data
        if not isinstance(data, bytes) or len(data) != numpy_type.itemsize:
            raise RefusedError('a numpy value whose bytes do not fit its type')
        return np.frombuffer(data, numpy_type)[0].item()
    if isinstance(value, _Action):
        if not isinstance(value.name, str):
            raise RefusedError('an action without a name')
        return value.name
    if isinstance(value, dict):
        found = {}
        for key, item in value.items():
            if not isinstance(key, (str, int)) or isinstance(key, bool):
                raise RefusedError('a mapping whose keys are not names')
            found[key] = _converted(item, budget, depth + 1)
        return found
    if isinstance(value, list):
        return [_converted(item, budget, depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(_converted(item, budget, depth + 1) for item in value)
    raise RefusedError(f'a value of the type {type(value).__name__}'[:200])


def _loads(stream: Any, budget: _Budget) -> Any:
    """Reads one pickle of a stream and returns it converted."""
    try:
        loaded = _Unpickler(stream).load()
    except RefusedError:
        raise
    except EOFError:
        raise
    except RecursionError:
        raise RefusedError(f'values nested more than {MAX_DEPTH} deep') from None
    except pickle.UnpicklingError as error:
        if 'truncated' in str(error):
            raise EOFError(str(error)) from None
        raise RefusedError(f'data that is no pickle, {error}'[:200]) from None
    except (TypeError, ValueError, AttributeError, KeyError, IndexError, MemoryError) as error:
        raise RefusedError(f'a pickle that does not build, {type(error).__name__}'[:200]) from None
    try:
        return _converted(loaded, budget)
    except RecursionError:
        raise RefusedError(f'values nested more than {MAX_DEPTH} deep') from None


def iterate(data: bytes) -> Iterator[dict[str, Any]]:
    """Yields the steps of a dump's bytes, each a mapping.

    Raises:
        RefusedError: If a pickle names anything but GRF's actions and numpy's arrays, makes more
            values than its size allows, or a step is no mapping.
        TruncatedError: If the bytes end inside a step, with the steps before it.
    """
    stream = io.BytesIO(data)
    budget = _Budget(len(data))
    read: list[dict[str, Any]] = []
    while stream.tell() < len(data):
        try:
            step = _loads(stream, budget)
        except EOFError:
            raise TruncatedError(read) from None
        if not isinstance(step, dict):
            raise RefusedError(f'a step of the type {type(step).__name__}'[:200])
        read.append(step)
        yield step


def steps(data: bytes) -> list[dict[str, Any]]:
    """Returns the steps of a dump's bytes, as `iterate` reads them."""
    return list(iterate(data))
