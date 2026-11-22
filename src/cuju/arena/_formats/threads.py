# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The thread counts of a side's process, by variable and by a hook that runs once torch imports.

`variables(threads)` gives every variable that the usual libraries of numbers read, design v6 11,
set to the format's `[process] threads`:

- `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `NUMEXPR_NUM_THREADS`,
  `NUMEXPR_MAX_THREADS`, `VECLIB_MAXIMUM_THREADS`, `BLIS_NUM_THREADS`;
- `TF_NUM_INTRAOP_THREADS` and `TF_NUM_INTEROP_THREADS`;
- `XLA_FLAGS` with `--xla_cpu_multi_thread_eigen=false` and `intra_op_parallelism_threads`.

torch reads none of them for its inter-op pool, so `install()` puts a hook on the imports of a
format process. Once torch has imported, the hook sets `torch.set_num_threads` and
`torch.set_num_interop_threads` to the same count, and where the side has a share of a GPU it asks
torch to apply `torch.cuda.set_per_process_memory_fraction` when CUDA starts. onnxruntime has no
variable, so a format sets its session's threads itself.

This module uses the standard library alone, since a format process imports it at its start.
"""

from __future__ import annotations

import contextlib
import importlib.abc
import os
import sys
from collections.abc import Callable
from typing import Any

VARIABLES = (
    'OMP_NUM_THREADS',
    'MKL_NUM_THREADS',
    'OPENBLAS_NUM_THREADS',
    'NUMEXPR_NUM_THREADS',
    'NUMEXPR_MAX_THREADS',
    'VECLIB_MAXIMUM_THREADS',
    'BLIS_NUM_THREADS',
    'TF_NUM_INTRAOP_THREADS',
    'TF_NUM_INTEROP_THREADS',
)
# The variable that tells a format process its threads, which the hook reads.
THREADS_VARIABLE = 'CUJU_ARENA_THREADS'


def variables(threads: int, flags: str | None = None) -> dict[str, str]:
    """Returns the variables of `threads` threads.

    Args:
        threads: The threads.
        flags: The `XLA_FLAGS` the process would have otherwise, which the new flags follow.
    """
    found = dict.fromkeys(VARIABLES, str(int(threads)))
    found[THREADS_VARIABLE] = str(int(threads))
    extra = f'--xla_cpu_multi_thread_eigen=false intra_op_parallelism_threads={int(threads)}'
    found['XLA_FLAGS'] = f'{flags} {extra}'.strip() if flags else extra
    return found


def limit_torch(torch: Any, threads: int | None, fraction: float | None) -> None:
    """Gives torch its threads and, once CUDA starts, its share of the GPU."""
    if threads:
        with contextlib.suppress(Exception):
            torch.set_num_threads(int(threads))
        with contextlib.suppress(Exception):
            torch.set_num_interop_threads(int(threads))
    if fraction:
        cuda = getattr(torch, 'cuda', None)

        def share() -> None:
            with contextlib.suppress(Exception):
                cuda.set_per_process_memory_fraction(float(fraction))

        lazy = getattr(cuda, '_lazy_call', None)
        if lazy is not None:
            with contextlib.suppress(Exception):
                lazy(share)
        elif cuda is not None and getattr(cuda, 'is_initialized', lambda: False)():
            share()


class _Loader(importlib.abc.Loader):
    """A loader that runs the module's own loader, then the hook once the module has run."""

    def __init__(self, loader: Any, after: Callable[[Any], None]) -> None:
        self._loader = loader
        self._after = after

    def create_module(self, spec: Any) -> Any:
        return self._loader.create_module(spec)

    def exec_module(self, module: Any) -> None:
        # The module keeps its own loader, as though nothing stood between.
        module.__loader__ = self._loader
        if getattr(module, '__spec__', None) is not None:
            module.__spec__.loader = self._loader
        self._loader.exec_module(module)
        with contextlib.suppress(Exception):
            self._after(module)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._loader, name)


class _After(importlib.abc.MetaPathFinder):
    """Finds a module of `hooks` by the finders after it, and runs its hook once it has run."""

    def __init__(self, hooks: dict[str, Callable[[Any], None]]) -> None:
        self.hooks = hooks

    def find_spec(self, name: str, path: Any = None, target: Any = None) -> Any:
        after = self.hooks.get(name)
        if after is None:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, 'find_spec'):
                continue
            spec = finder.find_spec(name, path, target)
            if spec is not None:
                break
        else:
            return None
        if spec.loader is None:
            return spec
        self.hooks.pop(name, None)
        spec.loader = _Loader(spec.loader, after)
        return spec


def install(threads: int | None = None, fraction: float | None = None) -> None:
    """Puts the hook that limits torch on this process's imports, from its variables by default.

    Args:
        threads: The threads, by default `THREADS_VARIABLE`.
        fraction: The share of the GPU, by default that `gpu.FRACTION_VARIABLE` names.
    """
    if threads is None:
        with contextlib.suppress(ValueError):
            threads = int(os.environ.get(THREADS_VARIABLE) or 0) or None
    if fraction is None:
        with contextlib.suppress(ValueError):
            fraction = float(os.environ.get('CUJU_ARENA_GPU_FRACTION') or 0) or None
    if not threads and not fraction:
        return
    if 'torch' in sys.modules:
        limit_torch(sys.modules['torch'], threads, fraction)
        return
    sys.meta_path.insert(0, _After({'torch': lambda torch: limit_torch(torch, threads, fraction)}))
