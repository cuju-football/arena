# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The Strict format, built in, the strict/1 rules of earlier tournaments behind the contract.

It is a thin layer. Play is the `Player` of the package, one worker process of a team under the
limits of the options, with the worker, the guard, the frozen copy and the rules of earlier
tournaments unchanged. The pre-check plays the pre-flight of earlier tournaments through
`test_match`, ten steps on each side and the same ten again. Each forfeit names the contract's
kind, and the rule of earlier tournaments is its detail, so old and new records read alike.
"""

from cuju.arena.formats import v1 as formats
from cuju.arena.formats.builtin import strict

PREFLIGHT_STEPS = strict.PREFLIGHT_STEPS


def _forfeit(found):
    """Returns the problem a forfeit of the check match stands for."""
    return formats.problem(found.kind, found.message, 'error', found.detail)


def accept(folder, team):
    """Today's pre-flight. The size, the links, 10 steps on each side and the same 10 again."""
    limits = team.options
    name = team.name
    linked = strict.links_in(folder)
    if linked:
        label = strict._label('link', player=name, path=linked[0])
        return formats.refuse('load', label['text'], detail='link')
    size = strict.folder_bytes(folder) / strict.MEBIBYTE
    if size > limits['folder_mb']:
        label = strict._label(
            'folder_size', player=name, size=f'{size:.1f}', limit=f'{limits["folder_mb"]:g}'
        )
        return formats.refuse('load', label['text'], detail='size')
    first = formats.test_match('builtin', 'left', PREFLIGHT_STEPS, 0, repeat=True)
    if first.forfeit is not None:
        return formats.Verdict(problems=(_forfeit(first.forfeit),))
    second = formats.test_match('builtin', 'right', PREFLIGHT_STEPS, 0)
    if second.forfeit is not None:
        return formats.Verdict(problems=(_forfeit(second.forfeit),))
    problems = [
        formats.problem('setting', found['text'], 'warning')
        for found in strict.setting_warnings(folder)
    ]
    deterministic = first.same_actions
    if not deterministic:
        label = strict._label('deterministic', player=name)
        if limits['deterministic']:
            problems.append(formats.problem('rule', label['text'], 'error', 'deterministic'))
        else:
            warning = strict._label('warning_deterministic', player=name)
            problems.append(formats.problem('rule', warning['text'], 'warning', 'deterministic'))
    actions = {}
    for found in (first, second):
        for action, count in found.actions.items():
            actions[str(action)] = actions.get(str(action), 0) + count
    warned = strict.action_warning(actions, name)
    if warned is not None:
        problems.append(formats.problem('action', warned['text'], 'warning'))
    return formats.accept(problems=problems, deterministic=deterministic)


def start(folder, match):
    """Returns the player of a side, its process started and loaded within the limit."""
    return strict.Player(folder, match)
