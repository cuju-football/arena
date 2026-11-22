# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The library of submission formats, versioned by the contract that a format folder follows.

A submission format is a folder of scripts under one contract of cuju-render. Its `format.toml`
names the contract, and its script holds `accept(folder, team)`, the pre-check that accepts or
refuses a team, and `start(folder, match)`, which returns the player of one side for a match. The
renderer plays the engine in lockstep and hands each side's player one step at a time, and the
format decides what its submissions look like, how they are called, what they may take and what
counts as a forfeit.

A format imports the library of its contract by its major number, as

    from cuju.arena.formats import v1 as formats

so a later contract never changes what an earlier format reads. The renderer keeps the current
major and the one before it. `CONTRACTS` names the majors this renderer serves and `CURRENT` the
newest. The guide is `docs/formats.md`.
"""

from __future__ import annotations

from . import v1

# The majors of the contract that this renderer serves, oldest first, and the newest of them.
CONTRACTS = (1,)
CURRENT = 1

__all__ = ['CONTRACTS', 'CURRENT', 'v1']
