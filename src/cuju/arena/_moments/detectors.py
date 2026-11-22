# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Recording detectors and checks for the moments they return."""

from __future__ import annotations

import re
import struct
from copy import deepcopy
from functools import partial
from typing import Any

from .. import plugins
from .._analysis import Recording, plain
from . import events

SIGNALS = {'value-drop': 'value_drop', 'surprise': 'surprise', 'entropy': 'entropy'}
STANDARDS = ('cuju', *SIGNALS.values())
_KIND = re.compile(r'[a-z][a-z0-9_]*')
_QUALIFIED = re.compile(r'[a-z0-9-]+\.[a-z][a-z0-9_]*')


class Cuju:
    """Finds the moments of play."""

    def detect(self, recording: Recording) -> list[dict[str, Any]]:
        return events.moments(recording.header, recording.frames, timing=True)


def custom_kind(kind: str) -> bool:
    return _QUALIFIED.fullmatch(kind) is not None


def _checked(answer: Any, recording: Recording, name: str) -> list[dict[str, Any]]:
    if not isinstance(answer, list):
        raise ValueError('expected a list of moments')
    found = []
    rate = recording.header.get('steps_per_second') or 10
    lineup = recording.header.get('players') or []
    for index, moment in enumerate(answer):
        if not isinstance(moment, dict) or set(moment) - {'kind', 'step', 'team', 'player', 'data'}:
            raise ValueError(
                'expected a moment with a kind and step, and optional team, player and data'
            )
        kind, step = moment.get('kind'), moment.get('step')
        team, player = moment.get('team'), moment.get('player')
        data = moment.get('data', {})
        if not isinstance(kind, str) or not _KIND.fullmatch(kind):
            raise ValueError('expected a moment kind in lowercase letters, digits and underscores')
        if type(step) is not int or not 0 <= step < len(recording.frames):
            raise ValueError('expected the position of a frame in the recording')
        if player is not None:
            if type(player) is not int or not 0 <= player < len(lineup):
                raise ValueError('expected a player in the recording lineup')
            if team is None:
                team = lineup[player].get('team')
        if team is not None and (type(team) is not int or team not in (0, 1)):
            raise ValueError('expected team 0 or 1')
        if not isinstance(data, dict) or not plain(data):
            raise ValueError('expected moment data with finite JSON values')
        left, right = struct.unpack_from('<2i', recording.frames[step], 8)
        clock = struct.unpack_from('<i', recording.frames[step], 24)[0]
        found.append(
            {
                'id': f'{name}:{step}:{index}',
                'kind': f'{name.partition(".")[0]}.{kind}',
                'step': step,
                't': step / rate,
                'team': team,
                'player': player,
                'data': deepcopy(data),
                'clock': clock,
                'score': [left, right],
            }
        )
    return found


def catalog(
    recording: Recording,
    selected: tuple[str, ...],
    standard: list[dict[str, Any]],
    config: Any = None,
    overrides: Any = (),
) -> list[dict[str, Any]]:
    """Collects the selected moments in frame order."""
    found = []
    for name in selected:
        if name == 'cuju':
            found.extend(standard)
        elif name not in SIGNALS.values():
            found.extend(
                plugins.call(
                    'moments',
                    name,
                    'detect',
                    deepcopy(recording),
                    check=partial(_checked, recording=recording, name=name),
                    config=config,
                    overrides=overrides,
                )
            )
    return sorted(found, key=lambda moment: moment['step'])
