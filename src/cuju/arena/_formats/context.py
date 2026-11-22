# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The format a context chose, read the same way by every context that plays matches.

A context, a tournament, a rating run, an evaluation, the academy benchmark, a requested match or
a game, chooses one submission format as `submission_format`, the identifier of a format alone
or a mapping `{id, preset?, options?, per_entrant?}`. `per_entrant` maps the text that names an
entrant in the context, such as `team:rex` or `rex@3fa9c1d200a1b2c3`, to the options set for that
entrant alone. A context that chooses nothing plays Kaggle open, as every context played before
formats, and `check` then gives `None`.

`for_entrant` returns the choice of one side, its per-entrant options folded in, which
`play.chosen_formats` reads. `check_length` holds a context of a set length to the time its
format's ranges allow, and `ensure` runs the format's pre-check of each team at the start of a
context, from the kept verdicts where there is one.
"""

from __future__ import annotations

from typing import Any

from .._text.messages import MessageError

# Kaggle open, the choice of a context that chose none.
OPEN = {'id': 'kaggle/1', 'preset': 'open'}
# GRF: the steps of the longest shipped scenario. A context longer than this checks its format's
# ranges against its own length, since `registry.add` checked them against this one.
LONGEST_STEPS = 3000


def _plain(value: Any) -> dict[str, Any] | None:
    """Returns a choice as a mapping, `None` for none, or raises for one that does not read."""
    if value is None or value == '':
        return None
    if isinstance(value, str):
        return {'id': value}
    if (
        not isinstance(value, dict)
        or not isinstance(value.get('id'), str)
        or not value['id']
        or set(value) - {'id', 'preset', 'options', 'per_entrant'}
    ):
        raise MessageError('server.formats.choice', choice=repr(value))
    found: dict[str, Any] = {'id': value['id']}
    if value.get('preset') is not None:
        if not isinstance(value['preset'], str):
            raise MessageError('server.formats.choice', choice=repr(value))
        found['preset'] = value['preset']
    for name in ('options', 'per_entrant'):
        held = value.get(name)
        if held in (None, {}):
            continue
        if not isinstance(held, dict):
            raise MessageError('server.formats.choice', choice=repr(value))
        found[name] = dict(held)
    for entrant, options in (found.get('per_entrant') or {}).items():
        if not isinstance(entrant, str) or not isinstance(options, dict):
            raise MessageError('server.formats.choice', choice=repr(value))
    return found


def is_open(value: Any) -> bool:
    """Returns whether a choice is Kaggle open with nothing of its own, as no choice plays."""
    found = _plain(value)
    if found is None:
        return True
    return (
        found['id'] in ('kaggle/1', 'kaggle')
        and found.get('preset') == 'open'
        and not found.get('options')
        and not found.get('per_entrant')
    )


def for_entrant(value: Any, entrant: str | None) -> dict[str, Any] | None:
    """Returns the choice of one entrant of a context, `None` where the context chose none.

    The entrant's own options of `per_entrant` become the side's `per_entrant`, which
    `side.choose` lays over the context's options.
    """
    found = _plain(value)
    if found is None:
        return None
    own = (found.pop('per_entrant', None) or {}).get(entrant or '')
    if own:
        found['per_entrant'] = dict(own)
    return found


def check(value: Any, entrants: list[str] | None = None) -> dict[str, Any] | None:
    """Returns a context's choice, checked against the registry, or `None` for Kaggle open.

    Each entrant of `per_entrant` must be one of `entrants` when they are given, and every set of
    options must fit the format, for the context's options and for each entrant's own.

    Raises:
        ArgumentError: If the format is unknown or broken, an option does not fit, or an entrant
            of `per_entrant` is not in the context.
    """
    from . import side as _side

    found = _plain(value)
    if found is None or is_open(found):
        return None
    for entrant in found.get('per_entrant') or {}:
        if entrants is not None and entrant not in entrants:
            raise MessageError('server.formats.entrant', entrant=repr(entrant))
        _side.choose(for_entrant(found, entrant))
    _side.choose(for_entrant(found, None))
    return found


def label(value: Any) -> str:
    """Returns the label of the format a context chose, `kaggle/1` for none."""
    from . import side as _side

    found = _plain(value)
    return _side.choose(for_entrant(found or OPEN, None)).format.label


def check_length(value: Any, steps: int | None) -> None:
    """Refuses a context longer than the longest scenario whose format's ranges cannot fit it.

    Raises:
        ArgumentError: If the format's options allow more time over `steps` than its
            `match_minutes`, so a side could pass the ceiling before its format's own forfeit.
    """
    from . import ceilings as _ceilings
    from . import side as _side

    if steps is None or int(steps) <= LONGEST_STEPS:
        return
    found = _side.choose(for_entrant(_plain(value) or OPEN, None))
    if not _ceilings.fits(found.format.spec, int(steps)):
        raise MessageError('server.formats.length', format=found.format.label, steps=int(steps))


def ensure(value: Any, entrants: list[str], scenario: str | None) -> dict[str, dict[str, Any]]:
    """Runs the pre-check of the chosen format for each team of a context, at its start.

    A context that chose nothing checks nobody, as before formats. A kept verdict answers at once,
    and one never kept, a refusal of the machine or of time, is checked again.

    Args:
        value: The context's choice.
        entrants: The text of each team among the entrants, `team:ID`, `ID` or `ID@checkpoint`.
            Other entrants, the built-in AI and the random entrant, need no check.
        scenario: The context's scenario.

    Returns:
        For each entrant that the format refused or failed to judge, `{problems, failed}`, the
        problems of error level as `{kind, message, detail}` and the format's own failure.
    """
    from . import side as _side
    from . import verdicts as _verdicts

    found = check(value)
    refused: dict[str, dict[str, Any]] = {}
    if found is None:
        return refused
    for entrant in entrants:
        text = entrant[len('team:') :] if entrant.startswith('team:') else entrant
        choice = _side.choose(for_entrant(found, entrant))
        outcome = _verdicts.ensure(_verdicts.subject_of_team(text), choice, scenario)
        if outcome.failed is not None:
            refused[entrant] = {'problems': [], 'failed': outcome.failed}
        elif outcome.verdict is not None and not outcome.verdict.accepted:
            refused[entrant] = {
                'problems': [
                    {'kind': problem.kind, 'message': problem.message, 'detail': problem.detail}
                    for problem in outcome.verdict.problems
                    if problem.level == 'error'
                ],
                'failed': None,
            }
    return refused


def threads(value: Any) -> int | None:
    """Returns the threads of a side whose chosen format plays in a process of its own, or `None`.

    A format of the match process, Kaggle open and `strict/1` among them, takes no CPU set, so
    the matches at once stay as the context sets them.
    """
    from . import side as _side

    found = check(value)
    if found is None:
        return None
    process = _side.choose(for_entrant(found, None)).format.spec.process
    if process.host == 'match':
        return None
    return max(1, int(process.threads or 1))


def from_settings(table: dict[str, Any]) -> dict[str, Any] | None:
    """Returns the choice that a table of settings names with `format`, `format_preset` and
    `format_options`, or `None` for `open`, Kaggle open."""
    name = table.get('format')
    if name in (None, '', 'open'):
        return None
    found: dict[str, Any] = {'id': str(name)}
    if table.get('format_preset') not in (None, '', 'none'):
        found['preset'] = str(table['format_preset'])
    if table.get('format_options'):
        found['options'] = dict(table['format_options'])
    return found
