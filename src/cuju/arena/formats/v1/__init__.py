# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Contract 1 of submission formats, the library a format imports as `formats`.

    from cuju.arena.formats import v1 as formats

A format is a folder whose `format.toml` says `contract = 1` and names its script. The script
holds two functions:

- `accept(folder, team)`, the pre-check, which returns `formats.accept(...)` or
  `formats.refuse(...)`, a `Verdict`;
- `start(folder, match)`, which returns the side's player for one match, an object with `act(step)`
  and, where it needs them, `reset()`, `end(result)` and `close()`.

The renderer plays the engine in lockstep. It sends every engine step to each side's player,
applies the answers together and steps once, so two formats may meet in one match. The format
decides what counts as a forfeit, by raising `formats.Forfeit` or returning one from `end`, and
the renderer records it against the format's team. The renderer keeps four stops of its own for
the machine, a step left unanswered for 300 seconds, a side's time past the format's
`match_minutes`, a load past 15 minutes and a process that ends, each a `ceiling.` forfeit of
the side.

The helpers make the engine look exactly as gfootball does. `Observer` gives gfootball's
representations, `kaggle_agent` and `kaggle_observation` play a Kaggle submission as Kaggle and
cuju-render always have, and `as_actions` checks an answer against gfootball's action sets.
`Child` runs a submission in a process of its own, timed and stopped from outside.

`BEHAVIOUR` is raised whenever a helper's output changes for the same input. It joins the
identity of every format, so ratings never mix what two behaviours played. The guide is
`docs/formats.md`.
"""

from __future__ import annotations

from ._actions import ACTION_COUNTS, as_actions
from ._children import Child, ChildCrash, ChildTimeout
from ._folders import (
    KaggleStruct,
    folder_size,
    from_folder,
    imports_of,
    kaggle_agent,
    kaggle_observation,
    load_module,
    read_manifest,
    require,
    seed_globals,
)
from ._objects import ROLES, Control, Match, Result, Step, Team
from ._observer import REPRESENTATIONS, Observer
from ._testing import CheckMatch, test_match
from ._verdicts import (
    CEILINGS,
    KINDS,
    LEVELS,
    Forfeit,
    Invalid,
    ManifestError,
    Missing,
    Problem,
    Verdict,
    accept,
    problem,
    refuse,
)

# The contract this library serves.
CONTRACT = 1
# The behaviour of the helpers, raised whenever one of them gives another output for an input.
BEHAVIOUR = 1

__all__ = [
    'ACTION_COUNTS',
    'BEHAVIOUR',
    'CEILINGS',
    'CONTRACT',
    'KINDS',
    'LEVELS',
    'REPRESENTATIONS',
    'ROLES',
    'CheckMatch',
    'Child',
    'ChildCrash',
    'ChildTimeout',
    'Control',
    'Forfeit',
    'Invalid',
    'KaggleStruct',
    'ManifestError',
    'Match',
    'Missing',
    'Observer',
    'Problem',
    'Result',
    'Step',
    'Team',
    'Verdict',
    'accept',
    'as_actions',
    'folder_size',
    'from_folder',
    'imports_of',
    'kaggle_agent',
    'kaggle_observation',
    'load_module',
    'problem',
    'read_manifest',
    'refuse',
    'require',
    'seed_globals',
    'test_match',
]
