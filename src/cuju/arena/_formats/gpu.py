# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The GPUs a side sees, none unless its format allows them, and its share of them.

A format of `[process] gpu = "never"` sees no GPU at all: CUDA, ROCm and JAX are each told so by
their own variables. A format of `gpu = "allowed"` gets a GPU of its own where the machine has
several, chosen through `CUDA_VISIBLE_DEVICES`, and on one GPU an equal share of its memory,
`SHARE` of it for each of the two sides of a match:

- JAX, `XLA_PYTHON_CLIENT_PREALLOCATE=false` and `XLA_PYTHON_CLIENT_MEM_FRACTION`;
- TensorFlow, `TF_FORCE_GPU_ALLOW_GROWTH=true`;
- torch, `torch.cuda.set_per_process_memory_fraction`, which the post-import hook of `threads`
  applies once torch starts CUDA.

Other frameworks are not bounded. The GPUs are counted from `CUDA_VISIBLE_DEVICES` of the
renderer where it names them, otherwise from `nvidia-smi -L`, once.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import threading

# The share of a GPU's memory each side takes, half of it less room for the drivers' contexts
# and the engine.
SHARE = 0.45
# The sides that share one GPU.
SIDES_PER_GPU = 2
# The variable that tells a format process its share, which the post-import hook reads.
FRACTION_VARIABLE = 'CUJU_ARENA_GPU_FRACTION'
# The variables that hide every GPU from a process.
HIDDEN = {
    'CUDA_VISIBLE_DEVICES': '-1',
    'HIP_VISIBLE_DEVICES': '-1',
    'JAX_PLATFORMS': 'cpu',
}
# The variables of a GPU that a format process takes from the renderer.
NAMES = (
    'CUDA_VISIBLE_DEVICES',
    'HIP_VISIBLE_DEVICES',
    'JAX_PLATFORMS',
    'XLA_PYTHON_CLIENT_PREALLOCATE',
    'XLA_PYTHON_CLIENT_MEM_FRACTION',
    'TF_FORCE_GPU_ALLOW_GROWTH',
    FRACTION_VARIABLE,
)


@dataclasses.dataclass(frozen=True)
class Share:
    """The GPU of one side and the share of its memory.

    Attributes:
        device: The device as `CUDA_VISIBLE_DEVICES` names it.
        fraction: The share of its memory, from 0 to 1.
        shared: Whether more sides than `SIDES_PER_GPU` hold the device.
    """

    device: str
    fraction: float
    shared: bool = False


_FOUND: list[str] | None = None
_LOCK = threading.Lock()


def devices(environ: dict[str, str] | None = None, probe: bool = True) -> list[str]:
    """Returns the GPUs the renderer sees, as `CUDA_VISIBLE_DEVICES` names them.

    Args:
        environ: The renderer's environment, by default this process's own.
        probe: Whether to ask `nvidia-smi` where the environment names none.
    """
    global _FOUND
    environ = os.environ if environ is None else environ
    named = environ.get('CUDA_VISIBLE_DEVICES')
    if named is not None:
        found = [part.strip() for part in named.split(',') if part.strip()]
        # CUDA reads the list up to its first entry that names no device.
        if any(part.startswith('-') for part in found):
            found = found[: next(i for i, part in enumerate(found) if part.startswith('-'))]
        return found
    if not probe:
        return []
    with _LOCK:
        if _FOUND is None:
            _FOUND = _probe()
        return list(_FOUND)


def _probe() -> list[str]:
    program = shutil.which('nvidia-smi')
    if program is None:
        return []
    try:
        lines = subprocess.run(
            [program, '-L'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=20,
            check=False,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []
    return [str(index) for index, line in enumerate(ln for ln in lines if ln.startswith('GPU '))]


class Shares:
    """The GPU shares of the sides that play now, taken and given back.

    Args:
        found: The GPUs to share out, by default those `devices` gives.
    """

    def __init__(self, found: list[str] | None = None) -> None:
        self._lock = threading.Lock()
        self._devices = found
        self._use: dict[str, int] = {}

    def acquire(self) -> Share | None:
        """Takes a share on the GPU least in use, or `None` where there is no GPU."""
        with self._lock:
            if self._devices is None:
                self._devices = devices()
            if not self._devices:
                return None
            device = min(self._devices, key=lambda d: (self._use.get(d, 0), self._devices.index(d)))
            held = self._use.get(device, 0)
            self._use[device] = held + 1
            alone = len(self._devices) > 1
            return Share(device, 1.0 if alone and held == 0 else SHARE, held >= SIDES_PER_GPU)

    def release(self, share: Share) -> None:
        """Gives a share back."""
        with self._lock:
            left = self._use.get(share.device, 0) - 1
            if left > 0:
                self._use[share.device] = left
            else:
                self._use.pop(share.device, None)


# The shares of this process, which every host takes from.
SHARES = Shares()


def environment(allowed: bool, share: Share | None = None) -> dict[str, str]:
    """Returns the variables of a side's GPUs.

    Args:
        allowed: Whether the format allows a GPU.
        share: The side's `Share`, or `None` to leave the renderer's GPUs as they are.
    """
    if not allowed:
        return dict(HIDDEN)
    if share is None:
        return {}
    found = {
        'CUDA_VISIBLE_DEVICES': share.device,
        'XLA_PYTHON_CLIENT_PREALLOCATE': 'false',
        'TF_FORCE_GPU_ALLOW_GROWTH': 'true',
    }
    if share.fraction < 1.0:
        found['XLA_PYTHON_CLIENT_MEM_FRACTION'] = f'{share.fraction:.2f}'
        found[FRACTION_VARIABLE] = f'{share.fraction:.4f}'
    return found
