# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The shipped defaults of the settings, a folder of one TOML file for each table, and their rules.

The file `recorder.toml` of the folder `defaults` holds the table `[recorder]` with the default of
every key, and may hold `[accepts.recorder]`, the rules of the keys whose default does not tell
what they accept. The defaults are the schema, so a key that no file of the folder names is
unknown, and a new file in the folder adds its table. The first file of the folder by name tells
the rules in its opening comment.
"""

from __future__ import annotations

import dataclasses
import math
import re
from pathlib import Path
from typing import Any

from cuju.errors import ChoiceError, ConfigurationError

from . import files as _files

# The folder of the shipped defaults, read on every call so that a new file counts at once.
FOLDER = Path(__file__).parents[1] / 'defaults'
# The table of a defaults file that holds the rules of its keys.
ACCEPTS = 'accepts'
# The key of a table of rules that holds the rule of every key that the defaults leave out.
WILDCARD = '*'
_TYPES = ('string', 'integer', 'number', 'boolean', 'list', 'table', 'any')
_RULE_KEYS = ('type', 'choices', 'minimum', 'maximum', 'items')
_NAME = re.compile(r'[a-z][a-z0-9_]*')
_WORDS = {
    'string': 'a text',
    'integer': 'a whole number',
    'number': 'a number',
    'boolean': 'true or false',
    'list': 'a list',
    'table': 'a table',
    'any': 'any value',
}
# Stands for the default of a value that has none, such as an entry of a list.
NOTHING: Any = object()


def listed(names: Any) -> str:
    """Returns names in a row as English writes it, `a, b and c`."""
    names = [str(name) for name in names]
    if len(names) < 2:
        return ''.join(names)
    return f'{", ".join(names[:-1])} and {names[-1]}'


def is_tables(value: Any) -> bool:
    """Returns whether a default is a list of tables, a list with entries that are all tables."""
    return isinstance(value, list) and bool(value) and all(isinstance(e, dict) for e in value)


@dataclasses.dataclass(frozen=True)
class Table:
    """A table of the settings, the default and the rule of each of its keys.

    Attributes:
        path: The name of the table with the tables above it, such as `evaluate.record`.
        defaults: The default of each key. For the entries of a list of tables, the keys of every
            default entry, each with the default of the first entry that names it.
        accepts: The rule of each key that has one, and for a key that holds a table or a list of
            tables the rules of its own keys.
    """

    path: str
    defaults: dict[str, Any]
    accepts: dict[str, Any]

    @property
    def open(self) -> bool:
        """Whether the table takes keys that its defaults leave out."""
        return WILDCARD in self.accepts or not self.defaults

    def child(self, key: str, path: str | None = None) -> Table:
        """Returns the table of the key `key`, or of the entries of its list of tables."""
        default = self.defaults[key]
        if is_tables(default):
            merged: dict[str, Any] = {}
            for entry in default:
                for name, value in entry.items():
                    merged.setdefault(name, value)
            default = merged
        return Table(path or f'{self.path}.{key}', default, self.accepts.get(key) or {})

    def unknown(self, key: str, place: str) -> ChoiceError:
        """Returns the refusal of a key that the table does not hold, with the keys it holds."""
        return ChoiceError(
            f'expected a key of [{self.path}], one of {listed(sorted(self.defaults))}, '
            f'found {key} in {place}'
        )


@dataclasses.dataclass(frozen=True)
class Schema:
    """The defaults of every table, and the file that each came from.

    Attributes:
        defaults: The default table of each name, as its file holds it.
        accepts: The rules of each table that has some.
        files: The file of each table.
    """

    defaults: dict[str, dict[str, Any]]
    accepts: dict[str, dict[str, Any]]
    files: dict[str, Path]

    def table(self, name: str) -> Table:
        """Returns the table `name`."""
        return Table(name, self.defaults[name], self.accepts.get(name) or {})

    def unknown(self, name: Any, place: str) -> ChoiceError:
        """Returns the refusal of a table that no defaults file holds, with those they hold."""
        return ChoiceError(
            f'expected a table of settings, one of {listed(sorted(self.defaults))}, '
            f'found {name} in {place}'
        )


def load(folder: Path | None = None) -> Schema:
    """Reads the defaults of a folder, by default the shipped one, in the order of the file names.

    Raises:
        ConfigurationError: If a file holds more than the table of its name and the rules of
            that table, a rule does not read, or a default of `none` has no rule.
    """
    folder = FOLDER if folder is None else Path(folder)
    defaults: dict[str, dict[str, Any]] = {}
    accepts: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    for path in sorted(folder.glob('*.toml')):
        name = path.stem
        found = _files.read(path)
        rules = found.get(ACCEPTS, {})
        fits = (
            _NAME.fullmatch(name)
            and name != ACCEPTS
            and isinstance(found.get(name), dict)
            and set(found) <= {name, ACCEPTS}
            and isinstance(rules, dict)
            and set(rules) <= {name}
        )
        if not fits:
            raise ConfigurationError(
                f'expected the table [{name}] and at most the rules [{ACCEPTS}.{name}] in {path}, '
                f'found {listed(sorted(found)) or "an empty file"}'
            )
        _check_rules(found[name], rules.get(name, {}), name, str(path))
        defaults[name] = found[name]
        accepts[name] = rules.get(name, {})
        paths[name] = path
    return Schema(defaults, accepts, paths)


def _check_rules(defaults: dict[str, Any], rules: Any, path: str, place: str) -> None:
    """Refuses rules that do not read, and a default of `none` that no rule gives a kind."""
    if not isinstance(rules, dict):
        raise ConfigurationError(f'expected [{ACCEPTS}.{path}] as a table of rules in {place}')
    for key, rule in rules.items():
        if key == WILDCARD:
            _check_rule(rule, f'{path}.{key}', place, entry=True)
        elif key not in defaults:
            raise Table(path, defaults, {}).unknown(key, f'[{ACCEPTS}.{path}] of {place}')
    for key, default in defaults.items():
        rule = rules.get(key)
        if isinstance(default, dict):
            _check_rules(default, {} if rule is None else rule, f'{path}.{key}', place)
        elif is_tables(default):
            merged = Table(path, defaults, {}).child(key).defaults
            _check_rules(merged, {} if rule is None else rule, f'{path}.{key}', place)
        else:
            if rule is not None:
                _check_rule(rule, f'{path}.{key}', place)
            told = rule is not None and ('type' in rule or 'choices' in rule)
            if default == _files.UNSET and not told:
                raise ConfigurationError(
                    f'expected a rule with type or choices at {ACCEPTS}.{path}.{key}, whose '
                    f'default is {_files.UNSET}, in {place}'
                )


def _check_rule(rule: Any, path: str, place: str, entry: bool = False) -> None:
    """Refuses a rule that holds more than the keys of a rule, or one of them out of shape."""
    fits = isinstance(rule, dict) and set(rule) <= set(_RULE_KEYS)
    if fits and 'type' in rule:
        fits = rule['type'] in _TYPES
    if fits and 'choices' in rule:
        choices = rule['choices']
        fits = isinstance(choices, list) and bool(choices)
        fits = fits and all(isinstance(choice, (str, int, float, bool)) for choice in choices)
    for bound in ('minimum', 'maximum'):
        if fits and bound in rule:
            fits = _is_number(rule[bound])
    if fits and entry:
        fits = 'type' in rule or 'choices' in rule
    if not fits:
        raise ConfigurationError(
            f'expected a rule at {ACCEPTS}.{path} with {listed(_RULE_KEYS[:-1])} or '
            f'{_RULE_KEYS[-1]}, the type one of {listed(_TYPES)}, '
            f'found {shown(rule)} in {place}'
        )
    if 'items' in rule:
        _check_rule(rule['items'], f'{path}.items', place, entry=True)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _kind(default: Any) -> str | None:
    """Returns the type that a default tells, or `None` for none, as the text `none` gives."""
    if default is NOTHING or default is None:
        return None
    if isinstance(default, bool):
        return 'boolean'
    if isinstance(default, int):
        return 'integer'
    if isinstance(default, float):
        return 'number'
    if isinstance(default, str):
        return None if default == _files.UNSET else 'string'
    if isinstance(default, list):
        return 'list'
    return 'table'


def _type(default: Any, rule: dict[str, Any]) -> str | None:
    """Returns the type of a key, that of its rule, else that of its default.

    A rule with choices and no type accepts the choices alone, so it gives no type.
    """
    return rule.get('type') or (None if 'choices' in rule else _kind(default))


def _items(default: Any, rule: dict[str, Any]) -> dict[str, Any] | None:
    """Returns the rule of the entries of a list, its own or the kind of the default entries."""
    if 'items' in rule:
        return rule['items']
    if isinstance(default, list) and default:
        kinds = {_kind(entry) for entry in default}
        if len(kinds) == 1 and None not in kinds:
            return {'type': kinds.pop()}
    return None


def _same(value: Any, choice: Any) -> bool:
    """Returns whether a value is a choice, where true is not 1 and false is not 0."""
    return isinstance(value, bool) == isinstance(choice, bool) and value == choice


def _is_unset(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value == _files.UNSET)


def _can_unset(default: Any, rule: dict[str, Any]) -> bool:
    return _is_unset(default) or any(_is_unset(choice) for choice in rule.get('choices', ()))


def expectation(default: Any, rule: dict[str, Any] | None) -> str:
    """Returns in words what a key accepts, for the refusal of a value."""
    rule = rule or {}
    kind = _type(default, rule)
    parts = []
    if kind is not None:
        words = _WORDS[kind]
        low, high = rule.get('minimum'), rule.get('maximum')
        if low is not None and high is not None:
            words += f' from {low:g} to {high:g}'
        elif low is not None:
            words += f' of at least {low:g}'
        elif high is not None:
            words += f' of at most {high:g}'
        items = _items(default, rule) if kind == 'list' else None
        if items is not None:
            words += f' of entries that are each {expectation(NOTHING, items)}'
        parts.append(words)
    parts += [
        _files.value_text(choice) for choice in rule.get('choices', ()) if not _is_unset(choice)
    ]
    if _can_unset(default, rule):
        parts.append(_files.value_text(None))
    if len(parts) < 2:
        return ''.join(parts)
    return f'{", ".join(parts[:-1])} or {parts[-1]}'


def accept(value: Any, default: Any, rule: dict[str, Any] | None, path: str, place: str) -> Any:
    """Returns a value as the settings hold it, checked against the rule of its key.

    The text `none` and a null become `None` where the key can be unset, and a whole number
    becomes a number with decimals where the default is one.

    Args:
        value: The value of a file, a mapping or an override.
        default: The default of the key, or `NOTHING` for an entry of a list or a key that an
            open table takes beside its defaults.
        rule: The rule of the key, or `None` when the kind of the default is the rule.
        path: The key with its tables, for the refusal.
        place: The layer that gave the value, for the refusal.

    Raises:
        ConfigurationError: If the key does not accept the value.
    """
    rule = rule or {}
    kind = _type(default, rule)
    given = value

    def refusal() -> ConfigurationError:
        return ConfigurationError(
            f'expected {path} as {expectation(default, rule)}, found {shown(given)} in {place}'
        )

    if _is_unset(value):
        if _can_unset(default, rule) or kind == 'any':
            return None
        raise refusal()
    if any(_same(value, choice) for choice in rule.get('choices', ())):
        return value
    if kind is None:
        raise refusal()
    if kind == 'any':
        return value
    if kind == 'boolean':
        fits = isinstance(value, bool)
    elif kind == 'integer':
        fits = isinstance(value, int) and not isinstance(value, bool)
    elif kind == 'number':
        fits = _is_number(value) and math.isfinite(value)
        if fits and isinstance(default, float):
            value = float(value)
    elif kind == 'string':
        fits = isinstance(value, str)
    elif kind == 'list':
        fits = isinstance(value, list)
    else:
        fits = isinstance(value, dict)
    if fits and _is_number(value):
        low, high = rule.get('minimum'), rule.get('maximum')
        fits = (low is None or value >= low) and (high is None or value <= high)
    if not fits:
        raise refusal()
    if kind == 'list':
        items = _items(default, rule)
        if items is not None:
            try:
                return [accept(entry, NOTHING, items, path, place) for entry in value]
            except ConfigurationError:
                raise refusal() from None
    return value


def shown(value: Any) -> str:
    """Returns a value for a refusal, as TOML writes it, a null as the word."""
    if value is None:
        return 'null'
    try:
        return _files.value_text(value)
    except ConfigurationError:
        return repr(value)
