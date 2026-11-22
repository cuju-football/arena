# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The manifest of a team, the file `cuju-team/team.json`, read and written field by field.

The manifest holds the identity of a team as a submission, its name, its date, its size under
`size`, which 0.1.0 and the drafts of this version spelled `format`, the players it controls, its
home scenario, the names and numbers of its players and the checkpoint that its home ground draws
from. The look of a team is another program's, which keeps it in a
folder of its own beside `cuju-team`, as cuju render keeps `cuju-look`.

The registry writes the second version of the file, marked `"identity": 2`, and reads both it and
the file of cuju-render 0.1.0, which carries no version. Reading cleans each field on its own, so a
value of the wrong type or outside its choices falls back to its default alone, and every other
field keeps its value. Each key that this version does not read, and each value it refuses, is
copied word for word into the block `kept`, which a later write carries on, so a team that passes
through this version loses nothing. The look that a file of 0.1.0 holds lands in `kept` that way,
where the program that draws the team finds it.

This module reads and writes documents alone. The registry in `teams.py` finds the files of a team,
fills the defaults and writes the result.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any

# The version of the manifest that this module writes.
IDENTITY = 2
# The name of the manifest inside the folder `cuju-team` of a team.
FILE = 'team.json'
FORMATS = ('5v5', '11v11')
# The scenario that a team of each size plays at home when it names none.
SIZE_DEFAULTS = {'5v5': '5_vs_5', '11v11': '11_vs_11_kaggle'}
SCENARIO = re.compile(r'[A-Za-z0-9_]{1,80}')
LONGEST_PLAYER_NAME = 24
LONGEST_TEAM_NAME = 80

_DIGEST = re.compile(r'[0-9a-f]{64}')
_PLAYER_KEYS = ('name', 'number')

# The keys that each version reads, nested as in its file. `True` stands for a value read whole,
# a one-item list for a list whose items have the keys of the item, and `'*'` for any key.
_READ_2: dict[str, Any] = {
    'identity': True,
    'name': True,
    'created': True,
    'size': True,
    'format': True,
    'control': True,
    'scenario': True,
    'roster': [{key: True for key in _PLAYER_KEYS}],
    'ground': {'checkpoint': True},
    'kept': True,
}
# The file of cuju-render 0.1.0. The registry reads its names, its size, its control and the names
# and numbers of its players. Its look, the rest of its style and its model, is left unread, so it
# lands in `kept`, where a later save carries it on and the program that draws the team finds it.
_READ_1: dict[str, Any] = {
    'id': True,
    'kind': True,
    'name': True,
    'name_id': True,
    'name_values': True,
    'created': True,
    'format': True,
    'control': True,
    'images': True,
    'sounds': True,
    'sounds_version': True,
    'clips': True,
    'style': {'players': [{key: True for key in _PLAYER_KEYS}]},
}
# The keys that cuju-render 0.1.0 wrote or accepted, whether or not this version reads them, so
# `unread_keys` names none of them.
_STYLE_1 = {
    **dict.fromkeys(
        ('model', 'badge', 'number', 'celebrations', 'crowd', 'colors', 'hidden_numbers'), True
    ),
    'kit': {key: True for key in ('pattern', 'primary', 'secondary', 'shorts', 'socks')},
    'goalkeeper': {key: True for key in ('primary', 'secondary', 'shorts', 'socks')},
    'players': [
        dict.fromkeys(
            (*_PLAYER_KEYS, 'skin', 'hair', 'hair_color', 'pose', 'celebration', 'model'), True
        )
    ],
}
_KNOWN_1: dict[str, Any] = {
    **_READ_1,
    **dict.fromkeys(
        ('color', 'secondary', 'joints', 'presentation', 'source', 'filename', 'imported'), True
    ),
    'model': {'joints': True, 'bytes': True},
    'style': _STYLE_1,
}
# The key of `kept` that holds the values a read refused, by their paths in the file.
REFUSED = 'refused'


class _Reading:
    """What one read of a manifest found, filled as the fields are cleaned."""

    def __init__(self) -> None:
        self.refused: dict[str, Any] = {}

    def refuse(self, path: str, value: Any) -> None:
        self.refused[path] = copy.deepcopy(value)


# The deepest nesting of objects and lists that a manifest may hold.
DEEPEST = 64


def depth(value: Any) -> int:
    """Returns how deeply objects and lists nest in a value, counted without recursion."""
    deepest = 0
    stack = [(value, 1)]
    while stack:
        item, level = stack.pop()
        if isinstance(item, dict):
            children = item.values()
        elif isinstance(item, list):
            children = item
        else:
            continue
        deepest = max(deepest, level)
        if deepest > DEEPEST:
            return deepest
        stack.extend((child, level + 1) for child in children)
    return deepest


def version(document: Any) -> int | None:
    """Returns the version of a manifest, 2 or later as it says, 1 for 0.1.0, or `None`.

    A document that is no JSON object has no version.
    """
    if not isinstance(document, dict):
        return None
    identity = document.get('identity')
    if isinstance(identity, int) and not isinstance(identity, bool) and identity >= 2:
        return identity
    return 1


def read(document: Any, control: Callable[[Any, str], Any] | None = None) -> dict[str, Any]:
    """Returns what a manifest says of its team, each field cleaned on its own.

    Args:
        document: The JSON object of the file, of either version.
        control: The check of the players a submission controls, which takes the value and the
            team's format and returns the value to keep or raises. Without it the value passes on
            unchecked.

    Returns:
        `version`, 1 or 2. `team`, the fields of the registry's entry that the file gives and that
        passed their checks, among `name`, `created`, `format`, the size that the file names
        `size`, `control`, `scenario` and a partial `roster`, which the registry completes with its
        defaults. `ground`, the digest of the checkpoint that the team's home ground draws from,
        or `None`. `refused`, the value of
        each refused field by its path. `kept`, the block to carry on, the file's own `kept` with
        every unread key and every refused value added.

    Raises:
        ValueError: If the document is no JSON object, or nests deeper than `DEEPEST` levels,
            which no team file needs and which a copy could not hold.
    """
    found = version(document)
    if found is None:
        raise ValueError('the file of the team holds no object')
    if depth(document) > DEEPEST:
        raise ValueError('the file of the team nests too deeply')
    reading = _Reading()
    team: dict[str, Any] = {}
    _common(document, team, control, reading)
    if found == 1:
        style = document.get('style')
        if style is not None and not isinstance(style, dict):
            # A style of another shape holds no players, and `kept` keeps it among the refused.
            reading.refuse('style', style)
        players = _roster(
            style.get('players') if isinstance(style, dict) else None, 'style.players', reading
        )
        kept = unread(document, _READ_1) or {}
        ground = None
    else:
        players = _roster(document.get('roster'), 'roster', reading)
        _scenario(document, team, reading)
        ground = _ground(document, reading)
        kept = _kept_of(document.get('kept'), reading)
        # A file that holds both spellings of the size reads `size`, and `kept` keeps `format`.
        keys = _READ_2 if 'size' not in document else {**_READ_2, 'format': None}
        merge(kept, unread(document, {key: value for key, value in keys.items() if value}) or {})
    if players is not None:
        team['roster'] = players
    if reading.refused:
        refused = kept.get(REFUSED)
        if not isinstance(refused, dict):
            if REFUSED in kept:
                reading.refuse(f'kept.{REFUSED}', refused)
            refused = {}
        kept[REFUSED] = {**refused, **copy.deepcopy(reading.refused)}
    return {
        'version': found,
        'team': team,
        'ground': ground,
        'refused': reading.refused,
        'kept': kept,
    }


def _kept_of(value: Any, reading: _Reading) -> dict[str, Any]:
    """Returns the block `kept` of a file as a new object, or an empty one for another value."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        reading.refuse('kept', value)
        return {}
    return copy.deepcopy(value)


def unread(value: Any, keys: Any) -> Any:
    """Returns the parts of `value` that `keys` does not read, nested as in `value`, or `None`.

    A list of items read alike gives a list as long as the value's, with an empty object for each
    item that holds nothing unread, so every unread key keeps its item's place.
    """
    if keys is True:
        return None
    if isinstance(keys, list):
        if not isinstance(value, list):
            return None
        items = [unread(item, keys[0]) for item in value]
        if all(item is None for item in items):
            return None
        return [{} if item is None else item for item in items]
    if not isinstance(value, dict):
        return None
    result = {}
    for key, item in value.items():
        inner = keys.get(key, keys.get('*'))
        if inner is None:
            result[key] = copy.deepcopy(item)
        else:
            left = unread(item, inner)
            if left is not None:
                result[key] = left
    return result or None


def merge(into: dict[str, Any], tree: dict[str, Any]) -> None:
    """Merges `tree` into `into`, objects key by key and lists item by item, `tree` winning."""
    for key, value in tree.items():
        if isinstance(value, dict) and isinstance(into.get(key), dict):
            merge(into[key], value)
        elif isinstance(value, list) and isinstance(into.get(key), list):
            old = into[key]
            for index, item in enumerate(value):
                if index >= len(old):
                    old.append(copy.deepcopy(item))
                elif isinstance(item, dict) and isinstance(old[index], dict):
                    merge(old[index], item)
                elif item != {}:
                    old[index] = copy.deepcopy(item)
        else:
            into[key] = copy.deepcopy(value)


def _printable(text: str) -> str:
    """Returns `text` with every character that prints nothing of its own turned into a space."""
    return ''.join(character if character.isprintable() else ' ' for character in text)


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _name(value: Any, longest: int) -> str | None:
    if not isinstance(value, str):
        return None
    return _printable(value).strip()[:longest]


def _choose(reading: _Reading, path: str, value: Any, choices: tuple[str, ...]) -> Any:
    if value in choices:
        return value
    reading.refuse(path, value)
    return None


def _player(value: Any, path: str, reading: _Reading) -> dict[str, Any]:
    """Returns the name and number of one player, each where it passes its check."""
    if not isinstance(value, dict):
        reading.refuse(path, value)
        return {}
    player: dict[str, Any] = {}
    if 'name' in value:
        item = value['name']
        if isinstance(item, str) and len(item) <= LONGEST_PLAYER_NAME:
            player['name'] = _printable(item)
        else:
            reading.refuse(f'{path}.name', item)
    if 'number' in value:
        item = value['number']
        if _integer(item) and 0 <= item <= 99:
            player['number'] = item
        else:
            reading.refuse(f'{path}.number', item)
    return player


def _roster(value: Any, path: str, reading: _Reading) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        reading.refuse(path, value)
        return None
    return [_player(item, f'{path}.{index}', reading) for index, item in enumerate(value)]


def _common(
    document: dict[str, Any],
    team: dict[str, Any],
    control: Callable[[Any, str], Any] | None,
    reading: _Reading,
) -> None:
    """Reads the fields that both versions keep under the same names."""
    if 'name' in document:
        name = _name(document['name'], LONGEST_TEAM_NAME)
        if name is None:
            reading.refuse('name', document['name'])
        else:
            team['name'] = name
    # The built-in AI of 0.1.0 has no date, which its file gives as null.
    if document.get('created') is not None:
        if isinstance(document['created'], str):
            team['created'] = document['created']
        else:
            reading.refuse('created', document['created'])
    # The size of a team is `size`, and `format`, its spelling in 0.1.0 and the drafts of this
    # version, reads the same where `size` is absent, since a submission format is another thing.
    key = 'size' if 'size' in document else 'format' if 'format' in document else None
    if key is not None:
        chosen = _choose(reading, key, document[key], FORMATS)
        if chosen is not None:
            team['format'] = chosen
    if 'control' in document:
        value = document['control']
        if control is None:
            team['control'] = copy.deepcopy(value)
        else:
            try:
                team['control'] = control(copy.deepcopy(value), team.get('format', '11v11'))
            except Exception:
                # A check that fails in any way refuses the value.
                reading.refuse('control', value)


def _scenario(document: dict[str, Any], team: dict[str, Any], reading: _Reading) -> None:
    if 'scenario' in document:
        scenario = document['scenario']
        if isinstance(scenario, str) and SCENARIO.fullmatch(scenario):
            team['scenario'] = scenario
        else:
            reading.refuse('scenario', scenario)


def _ground(document: dict[str, Any], reading: _Reading) -> str | None:
    """Returns the digest of the checkpoint that the home ground draws from, or `None`."""
    if 'ground' not in document:
        return None
    value = document['ground']
    checkpoint = value.get('checkpoint') if isinstance(value, dict) else None
    if isinstance(checkpoint, str) and _DIGEST.fullmatch(checkpoint):
        return checkpoint
    if not (isinstance(value, dict) and 'checkpoint' not in value):
        reading.refuse(
            'ground.checkpoint' if isinstance(value, dict) else 'ground',
            checkpoint if isinstance(value, dict) else value,
        )
    return None


def document(team: dict[str, Any], kept: dict[str, Any], ground: str | None) -> dict[str, Any]:
    """Returns the manifest of a team, the second version, from the registry's entry.

    Args:
        team: The entry, with its complete roster.
        kept: The block to carry on, word for word.
        ground: The digest of the checkpoint that the team's home ground draws from, or `None`.
    """
    result: dict[str, Any] = {
        'identity': IDENTITY,
        'name': team['name'],
        'created': team.get('created') or '',
        'size': team['format'],
        'control': copy.deepcopy(team.get('control')),
        'roster': [{key: player[key] for key in _PLAYER_KEYS} for player in team['roster']],
        'ground': {'checkpoint': ground} if ground else {},
        'kept': copy.deepcopy(kept),
    }
    if result['control'] is None:
        del result['control']
    # The home scenario is stored only when it is not the default of the team's size, so a team that
    # changes its size takes the default of the new one.
    scenario = team.get('scenario')
    if scenario and scenario != SIZE_DEFAULTS.get(team['format']):
        result['scenario'] = scenario
    return result


def unread_keys(document: Any) -> list[str]:
    """Returns the dotted paths of the keys of a manifest that no version of the registry knows.

    These are the keys of a second version that this version does not read, and the keys of a
    file of 0.1.0 that 0.1.0 never wrote. The keys inside `kept` are left out, since the file
    keeps them on purpose.
    """
    found = version(document)
    if found is None:
        return []
    paths: list[str] = []

    def walk(value: Any, keys: Any, prefix: str) -> None:
        if keys is True:
            return
        if isinstance(keys, list):
            if isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, keys[0], f'{prefix}{index}.')
            return
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            inner = keys.get(key, keys.get('*'))
            if inner is None:
                paths.append(f'{prefix}{key}')
            else:
                walk(item, inner, f'{prefix}{key}.')

    walk(document, _KNOWN_1 if found == 1 else _READ_2, '')
    return sorted(paths)
