# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The plugin `builtin`, the standards that ship with the arena, registered as any plugin is.

The package declares this module as an entry point of the group `cuju.arena.plugins`.
The registry also loads it by name for a source checkout.
"""

from __future__ import annotations

from typing import Any

from ._plugin_standards import PROVIDES
from ._version import __version__

PLUGIN = {
    'id': 'builtin',
    'version': __version__,
    'requires': None,
    'provides': PROVIDES,
}


class _AnalyticsTable:
    """Uses the table builder supplied by the renderer."""

    def __init__(self, name: str) -> None:
        self.name = name

    def table(self, recording: Any, options: dict[str, Any]) -> Any:
        return options['_standard'](self.name, recording, options)


def register(registry: Any) -> None:
    """Hands the standards to the registry. Each part loads on first use of its module."""
    from ._evaluate import ratings, schedule
    from ._format import attachments
    from ._moments import detectors, search
    from ._store import stats
    from ._tournaments import tournament

    registry.provide('rating_method/1', 'points', ratings.PointsMethod())
    registry.provide('rating_method/1', 'bradley_terry', ratings.BradleyTerryMethod())
    registry.provide('evidence/1', 'default', ratings.DefaultEvidence())
    for name, rule in tournament.STANDARD_SEEDINGS.items():
        registry.provide('seeding/1', name, rule)
    registry.provide('pairing/1', 'least_settled', schedule.LeastSettled())
    for name, stage in tournament.STANDARD_STAGES.items():
        registry.provide('stage_type/1', name, stage)
    for name, tiebreaker in tournament.STANDARD_TIEBREAKERS.items():
        registry.provide('tiebreaker/1', name, tiebreaker)
    registry.provide('moments/1', 'cuju', detectors.Cuju())
    for signal, name in detectors.SIGNALS.items():
        registry.provide('moments/1', name, search.SignalDetector(signal))
    registry.provide('statistics/1', 'match', stats.MatchStatistics())
    registry.provide('statistics/1', 'heat', stats.HeatStatistics())
    registry.provide('statistics/1', 'signals', stats.SignalStatistics())
    for name in PLUGIN['provides']['analytics/1']:
        registry.provide('analytics/1', name, _AnalyticsTable(name))
    for name, producer in attachments.STANDARD.items():
        registry.provide('attachment/1', name, producer)
