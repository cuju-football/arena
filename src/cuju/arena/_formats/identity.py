# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The identity of a format as it played, the verdict cache key, and the model key of ratings.

A format's identity is what decides how a side played under it:

- its identifier and name;
- for a built-in format its version, for a person's format the SHA-256 of its folder;
- the contract and the library's behaviour, `v1.BEHAVIOUR`;
- every option that reaches play, with its value, and the per-entrant options;
- the `revision` a remote player reports.

`DEFAULT` names Kaggle open, the built-in Kaggle format under its preset `open`, which is how a
side plays where no context chose a format, and how every match before formats was played.
A ratings model under it keeps its earlier key, the team and its checkpoint, so nothing is
refitted. A model under any other identity, or under another control than the team's own,
gains a suffix of its own after a `~`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..formats import v1 as _v1
from . import contract as _contract

# Kaggle open, the identity of a side that no context gave a format.
DEFAULT = {'format': 'kaggle/1', 'preset': 'open'}
# The separator of a model's format suffix.
SEPARATOR = '~'


def _plain(value: Any) -> Any:
    """Returns a value with every whole float as an integer, so 30 and 30.0 name one option."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def _digest(value: Any, length: int = 12) -> str:
    text = json.dumps(_plain(value), sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:length]


def of(
    format: Any,
    values: dict[str, Any],
    per_entrant: dict[str, Any] | None = None,
    revision: str | None = None,
    preset: str | None = None,
) -> dict[str, Any]:
    """Returns the identity of a format with resolved options, as a ratings row records it.

    Args:
        format: The `registry.Format`.
        values: Every option of the format, resolved.
        per_entrant: The options set for one entrant, or `None`.
        revision: What a remote player reported after `start`, or `None`.
        preset: The preset the context chose, which a row names for reading, or `None`.
    """
    from . import registry as _registry

    spec = format.spec
    playing = _contract.playing(spec, values)
    found = {
        'format': format.label,
        'name': spec.name,
        'version': spec.version if format.builtin else None,
        'digest': None if format.builtin else _registry.digest(format.folder)[:12],
        'contract': spec.contract,
        'behaviour': _v1.BEHAVIOUR,
        'options': playing,
        'per_entrant': dict(per_entrant or {}),
        'revision': revision,
    }
    if preset is not None and spec.presets.get(preset) is not None:
        preset_values = _contract.options(spec, preset=preset)
        if _contract.playing(spec, preset_values) == playing:
            found['preset'] = preset
    return found


def is_default(identity: dict[str, Any] | None) -> bool:
    """Returns whether an identity is Kaggle open, the play of a side that chose no format."""
    if identity is None:
        return True
    return (
        identity.get('format') == DEFAULT['format']
        and identity.get('preset') == DEFAULT['preset']
        and not identity.get('per_entrant')
        and identity.get('revision') is None
    )


def key(identity: dict[str, Any]) -> str:
    """Returns a short digest of an identity, without its preset, which only names it."""
    return _digest({name: value for name, value in identity.items() if name != 'preset'})


def text(identity: dict[str, Any] | None) -> str:
    """Returns an identity in a few words, such as `kaggle/1 open` or `league@3fa9c1d200a1`."""
    if identity is None:
        identity = DEFAULT
    name = identity.get('format', '?')
    if identity.get('digest'):
        name = f'{name}@{identity["digest"]}'
    if identity.get('preset'):
        return f'{name} {identity["preset"]}'
    return name


def model_suffix(identity: dict[str, Any] | None, control: dict[str, Any] | None) -> str | None:
    """Returns the suffix of a ratings model under a format, or `None` under Kaggle open.

    Args:
        identity: The format's identity, `None` for Kaggle open.
        control: The effective control, after the verdict's `players`, or `None` for the team's
            own, which Kaggle open always plays.
    """
    if is_default(identity) and control is None:
        return None
    return _digest({'identity': key(identity or DEFAULT), 'control': control})


def model(base: str, identity: dict[str, Any] | None, control: dict[str, Any] | None) -> str:
    """Returns the identifier of a ratings model, a team at a checkpoint under a format.

    `base` is the team key and its checkpoint, as `_store.ratings.model` joins them.
    """
    suffix = model_suffix(identity, control)
    return base if suffix is None else f'{base}{SEPARATOR}{suffix}'


def verdict_key(
    checkpoint: str | None,
    identity: dict[str, Any],
    every_option: dict[str, Any],
    scenario: str | None,
    size: str,
    control: dict[str, Any],
    full: bool,
    renderer: str,
    gpu: bool,
) -> str:
    """Returns the key a verdict is kept under, design v6 7.

    It holds the checkpoint, the format's identity, every option including those that reach
    only the check, the scenario or the size's default, the effective control, `full`, the
    renderer's version and whether a GPU is visible.
    """
    return _digest(
        {
            'checkpoint': checkpoint,
            'identity': key(identity),
            'options': every_option,
            'scenario': scenario or {'5v5': '5_vs_5', '11v11': '11_vs_11_kaggle'}.get(size),
            'control': control,
            'full': bool(full),
            'renderer': renderer,
            'gpu': bool(gpu),
        },
        32,
    )
