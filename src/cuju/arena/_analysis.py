# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The recording data shared by analysis plugins."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np


@dataclass(frozen=True)
class Recording:
    """The header, frames and attachments of a recording."""

    header: dict[str, Any]
    frames: tuple[bytes, ...]
    sections: dict[str, bytes] = field(default_factory=dict)
    annotations: list[dict[str, Any]] = field(default_factory=list)
    actions: np.ndarray | None = None
    moments: list[dict[str, Any]] = field(default_factory=list)


def plain(value: Any) -> bool:
    """Checks that a value consists of JSON data with finite numbers."""
    if value is None or isinstance(value, str | bool | int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(map(plain, value))
    if isinstance(value, dict):
        return all(isinstance(key, str) and plain(item) for key, item in value.items())
    return False
