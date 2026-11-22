# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks that teams of cuju-render 0.1.0 read, save, export and import with nothing lost.

The teams of `tests/support/old-teams` were written by the code of the tag v0.1.0, and a few are
the same team file with values broken by hand. `cases.json` there says what each one reads as,
the registry's identity and the look that the look store takes over once into `cuju-look`.
"""

import json
import shutil
from pathlib import Path

from cuju.arena._registry import teams as _teams
from cuju.arena._store import home as _home
from cuju.arena._store import runs as _runs

OLD = Path(__file__).parents[1] / 'support' / 'old-teams'


def _install(case):
    """Copies a case's folder into the registry and returns the team's identifier."""
    identifier = _teams.BUILTIN if case.get('builtin') else f't-{case["folder"]}'
    shutil.copytree(OLD / case['folder'], _home.folder('teams') / identifier)
    return identifier


def test_a_team_of_old_takes_its_ground_once_and_keeps_it(arena_home):
    """A team of 0.1.0 has no ground until asked, then keeps the one of its files as they were."""
    identifier = _install({'folder': 'plain'})
    assert _teams.entry(identifier)['ground'] is None
    first = _runs.team_checkpoint(identifier)['digest']
    assert _teams.first_checkpoint(identifier) == first
    document = json.loads(
        (_home.folder('teams') / identifier / 'cuju-team' / 'team.json').read_text('utf-8')
    )
    assert document['ground'] == {'checkpoint': first}
    (_home.folder('teams') / identifier / 'main.py').write_text(
        'def agent(obs):\n    return [1]\n', encoding='utf-8'
    )
    assert _teams.first_checkpoint(identifier) == first
    assert _teams.first_checkpoint(_teams.BUILTIN) is None
