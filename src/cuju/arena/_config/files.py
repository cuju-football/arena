# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The files of settings, read as TOML or JSON into the same plain data and written as TOML.

A file whose name ends in `.toml` is TOML and one whose name ends in `.json` is JSON, and both
give tables as dictionaries, lists, texts, numbers and truth values. TOML has no empty value, so
the text `none` stands for an unset key in either kind of file, as the JSON null does.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cuju.errors import ConfigurationError, MissingFileError

if sys.version_info >= (3, 11):
    import tomllib
else:
    # Python 3.10 has no reader of TOML, and tomli is the same reader under its first name.
    import tomli as tomllib

# The text that stands for an unset key.
UNSET = 'none'
_BARE = re.compile(r'[A-Za-z0-9_-]+')
_SCALARS = (str, int, float, bool)


def read(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Reads a file of settings, TOML or JSON by the end of its name, as a dictionary.

    Raises:
        MissingFileError: If the file does not exist.
        ConfigurationError: If the name ends in neither `.toml` nor `.json`, the file does not
            read as its kind, its top is not a table, or it holds a value that settings never
            take, such as a date.
    """
    path = Path(path).expanduser()
    kind = path.suffix.lower()
    if kind not in ('.toml', '.json'):
        raise ConfigurationError(
            f'expected a file of settings whose name ends in .toml or .json, found {path}'
        )
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise MissingFileError(f'expected a file of settings at {path}') from None
    try:
        text = data.decode('utf-8-sig')
        found = tomllib.loads(text) if kind == '.toml' else json.loads(text)
    except (UnicodeDecodeError, ValueError) as error:
        name = 'TOML' if kind == '.toml' else 'JSON'
        raise ConfigurationError(f'expected {name} in {path}, {error}') from error
    if not isinstance(found, dict):
        raise ConfigurationError(f'expected a table at the top of {path}')
    _check_plain(found, '', str(path))
    return found


def _check_plain(value: Any, key: str, place: str) -> None:
    """Refuses a value that is no table, list, text, number, truth value or null."""
    if isinstance(value, dict):
        for name, entry in value.items():
            _check_plain(entry, f'{key}.{name}' if key else str(name), place)
    elif isinstance(value, list):
        for index, entry in enumerate(value):
            _check_plain(entry, f'{key}.{index}', place)
    elif value is not None and not isinstance(value, _SCALARS):
        raise ConfigurationError(
            f'expected {key} as a text, a number, true or false, a list or a table, '
            f'found {value} in {place}'
        )


def plain(value: Any) -> Any:
    """Returns a copy of nested mappings and sequences as dictionaries and lists."""
    if isinstance(value, Mapping):
        return {str(key): plain(entry) for key, entry in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(entry) for entry in value]
    return value


def unset(value: Any) -> Any:
    """Returns a copy of plain data with `None` for each text `none`, at any depth."""
    if isinstance(value, dict):
        return {key: unset(entry) for key, entry in value.items()}
    if isinstance(value, list):
        return [unset(entry) for entry in value]
    return None if value == UNSET and isinstance(value, str) else value


def scalar(text: str) -> Any:
    """Returns the value that the text of an override gives, read as TOML, else the text itself.

    `200` gives a whole number, `0.5` a number, `true` a truth value, `[1, 2]` a list and
    `{ a = 1 }` a table, while `goals`, which is no TOML value, gives the text `goals`.
    """
    try:
        found = tomllib.loads(f'value = {text}')['value']
        _check_plain(found, 'value', 'an override')
    except (ValueError, ConfigurationError):
        return text.strip()
    return found


def key_text(key: str) -> str:
    """Returns a key as TOML writes it, bare where it may be and quoted otherwise."""
    return key if _BARE.fullmatch(key) else _text(key)


def _text(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace('\x7f', '\\u007f')


def value_text(value: Any) -> str:
    """Returns a value as TOML writes it on one line, a table as an inline table.

    Raises:
        ConfigurationError: If the value is no plain data.
    """
    if value is None:
        return _text(UNSET)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return 'nan'
        if math.isinf(value):
            return 'inf' if value > 0 else '-inf'
        return repr(value)
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(value_text(entry) for entry in value) + ']'
    if isinstance(value, Mapping):
        if not value:
            return '{}'
        entries = (f'{key_text(str(key))} = {value_text(entry)}' for key, entry in value.items())
        return '{ ' + ', '.join(entries) + ' }'
    raise ConfigurationError(f'expected plain data to write as TOML, found {value!r}')
