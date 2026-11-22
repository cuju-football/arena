# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Checks one team under a submission format, the pre-flight of a tournament and its command.

`check` runs the format's pre-check of a team of the registry and reports the verdict in the shape
that a tournament keeps of its pre-flight, whether the team passed, the rule it broke, its
warnings and the digest of its checkpoint. The tournament runs it in a process of its own, as
`python -m cuju.arena._tournaments.format_check`, which reports one line of JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .._formats import checks as _checks
from .._formats import host as _host
from .._formats import registry as _registry
from .._formats import side as _side
from .._registry import checkpoints as _checkpoints
from .._registry import teams as _teams
from .._text import messages as _messages

# The format a context that chooses none plays under, Kaggle open.
KAGGLE_OPEN = {'id': 'kaggle/1', 'preset': 'open'}


def _label(text: str) -> dict[str, Any]:
    """Returns a verdict's message as a label without a translation, which shows as text."""
    return {'id': None, 'values': None, 'text': text}


def check(team: str, scenario: str | None, choice: Any = None) -> dict[str, Any]:
    """Runs a format's pre-check of a team and returns the report.

    Args:
        team: The team of the registry, or an entrant `team@checkpoint`.
        scenario: The scenario of the context, or `None` for the one of the team's size.
        choice: The format, an identifier or `{id, preset, options}`, Kaggle open by default.

    Raises:
        ArgumentError: If the team or the format is unknown or broken, or an option does not fit.
        FormatFailure: If the format's own code fails.
    """
    from .._formats import verdicts as _verdicts

    chosen = _side.choose(choice or KAGGLE_OPEN)
    found = _checkpoints.resolve(team)
    entry = _teams.entry(found['team'])
    size = entry['format']
    name = _teams.shown_name(entry)
    default = _checks.SIZE_SCENARIOS.get(size, '11_vs_11_kaggle')
    # A kept verdict of the same checkpoint, format, options and scenario answers at once, and
    # one never kept, a refusal of the machine or of time, is checked again here. This process
    # is already the check's own, so the check runs in it.
    outcome = _verdicts.ensure(
        _verdicts.subject_of_team(team), chosen, scenario, full=True, isolate=False
    )
    if outcome.failed is not None:
        raise _host.FormatFailure(outcome.failed)
    verdict = outcome.verdict
    errors = [item for item in verdict.problems if item.level == 'error']
    rule = None
    if errors:
        first = errors[0]
        rule = {
            'side': 'home',
            'kind': first.kind,
            'reason': first.message,
            'reason_id': None,
            'reason_values': None,
            'detail': first.detail,
            'step': 0,
            'at': [0, 0],
        }
    return {
        'team': team,
        **_messages.named('name', name),
        'scenario': scenario or default,
        'ok': verdict.accepted,
        'rule': rule,
        'timings': {},
        'warnings': [_label(item.message) for item in verdict.problems if item.level != 'error'],
        'problems': [item.as_dict() for item in verdict.problems],
        'deterministic': verdict.deterministic,
        'environment': verdict.environment,
        'submission_format': chosen.format.label,
        'options': chosen.values,
        'digest': found['checkpoint'],
        'terminal': outcome.terminal,
        'stored': outcome.stored,
    }


def main(arguments: list[str] | None = None) -> None:
    """Checks the team that the command line names and reports one line of JSON."""
    parser = argparse.ArgumentParser(prog='python -m cuju.arena._tournaments.format_check')
    parser.add_argument('--team', required=True)
    parser.add_argument('--scenario', default=None)
    parser.add_argument('--format', type=json.loads, default=None)
    options = parser.parse_args(arguments)
    # The report keeps a copy of standard output, and everything else written there goes to
    # standard error, as a match's reports do.
    sys.stdout.flush()
    reports = os.fdopen(os.dup(1), 'w', encoding='utf-8')
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    try:
        report = check(options.team, options.scenario, options.format)
    except _host.FormatFailure as error:
        chosen = options.format or KAGGLE_OPEN
        identifier = chosen if isinstance(chosen, str) else chosen.get('id', '')
        with_format = _registry.get(identifier)
        _registry.mark_failing(with_format.id, str(error))
        report = {
            'error': f'the format {with_format.label} failed, {error}',
            'error_id': 'server.formats.failed',
            'error_values': {'format': with_format.label, 'reason': str(error)},
        }
    except Exception as error:
        report = {'error': _messages.described(error), **_messages.id_fields(error)}
    reports.write(json.dumps(report) + '\n')
    reports.flush()


if __name__ == '__main__':
    # The check runs from the module as the package imports it, as a match does.
    from cuju.arena._tournaments import format_check as _module

    _module.main()
