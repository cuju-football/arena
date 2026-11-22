# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The settings of a package, resolved from layers, each laid over the last.

A `Settings` is made once for each package from its folder of defaults, the name of its file in
the home folder and the variable that names more files. Its layers are the shipped defaults, the
file of the home folder, the files that the variable names, the files that a call or a command
names, and the overrides. `config` resolves them into a `Config`, and `resolve` and `table` let a
function accept `config=` and `overrides=` and keep its keyword arguments as the last layer.

The arena's own instance is `ARENA`, with `defaults/`, `arena.toml` and `CUJU_ARENA_CONFIG`, and
the module-level `config`, `resolve` and `table` are its. Each package is strict about its own
tables only, so a file of one package names no table of another.
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import json
import os
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from cuju.errors import ChoiceError, ConfigurationError

from .._store import home as _home
from . import files as _files
from . import schema as _schema

# The variable that names files of settings of the arena, separated as the paths of PATH are.
CONFIG_VARIABLE = 'CUJU_ARENA_CONFIG'
# The file of settings of the arena's home folder.
HOME_FILE = 'arena.toml'
# The names of the layers that are no file, as `Config.source` gives them.
DEFAULTS = 'defaults'
MAPPING = 'mapping'
OVERRIDES = 'overrides'
_WORDS = (DEFAULTS, MAPPING, OVERRIDES)


class _FromConfig:
    """The kind of `FROM_CONFIG`, which reads as its meaning in a signature."""

    __slots__ = ()

    def __repr__(self) -> str:
        return 'FROM_CONFIG'

    def __reduce__(self) -> str:
        return 'FROM_CONFIG'


# The default of a keyword argument that leaves its value to the settings.
FROM_CONFIG: Any = _FromConfig()


def place_of(layer: str) -> str:
    """Returns a layer as a refusal names it, a file by its path."""
    return f'the {layer}' if layer in _WORDS else layer


def _frozen(value: Any) -> Any:
    """Returns a view of plain data that refuses changes, tables as mappings and lists as tuples."""
    if isinstance(value, dict):
        return MappingProxyType({key: _frozen(entry) for key, entry in value.items()})
    if isinstance(value, list):
        return tuple(_frozen(entry) for entry in value)
    return value


class Config(Mapping):
    """The resolved settings, a read-only mapping of each table by its name.

    A table reads as a mapping and a list as a tuple, and neither takes changes. A key may name
    its tables with periods and the entries of a list by their index, as in
    `settings['recorder.keep']`. A key that can be unset reads as `None` when it is.
    """

    __slots__ = ('_layers', '_sources', '_values')

    def __init__(
        self, values: dict[str, Any], sources: dict[str, Any], layers: tuple[str, ...]
    ) -> None:
        """Holds resolved settings, which `config` and `resolve` make.

        Args:
            values: The tables.
            sources: For each key of the tables, the index of the layer that set it.
            layers: The names of the layers, the lowest first.
        """
        self._values = values
        self._sources = sources
        self._layers = layers

    def _find(self, key: Any, tree: Any) -> Any:
        if not isinstance(key, str):
            raise ChoiceError(f'expected the name of a table or a key, found {key!r}')
        node = tree
        for part in key.split('.'):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdecimal() and int(part) < len(node):
                node = node[int(part)]
            else:
                raise ChoiceError(f'expected a table or a key of the settings, found {key}')
        return node

    def __getitem__(self, key: str) -> Any:
        return _frozen(self._find(key, self._values))

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __eq__(self, other: object) -> bool:
        # A list reads as a tuple, so the settings compare by their plain form.
        if not isinstance(other, Mapping):
            return NotImplemented
        theirs = other.to_dict() if isinstance(other, Config) else _files.plain(other)
        return self.to_dict() == theirs

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f'Config(tables={list(self._values)}, layers={list(self._layers)})'

    def __reduce__(self) -> Any:
        return (Config, (self._values, self._sources, self._layers))

    @property
    def layers(self) -> tuple[str, ...]:
        """The layers that the settings came from, the lowest first.

        The shipped defaults read `defaults`, a file reads as its path, a mapping reads `mapping`
        and the overrides read `overrides`.
        """
        return self._layers

    def source(self, key: str) -> str:
        """Returns the layer that set a key, as `layers` names it.

        Args:
            key: A key with its tables, such as `recorder.keep`. For a table or a list of tables
                the answer is the highest layer that set one of its keys.

        Raises:
            ChoiceError: If the settings hold no such key.
        """
        return self._layers[_highest(self._find(key, self._sources))]

    def sources(self) -> dict[str, str]:
        """Returns the layer of every key, each key with its tables, in the order of the tables."""
        found: dict[str, str] = {}
        _leaves(self._sources, '', found)
        return {key: self._layers[index] for key, index in found.items()}

    def to_dict(self) -> dict[str, Any]:
        """Returns the settings as plain dictionaries and lists, a copy that may change."""
        return copy.deepcopy(self._values)

    def to_toml(self, sources: bool = False) -> str:
        """Returns the settings as TOML, which reads back to the same settings.

        Args:
            sources: Whether each key ends with a comment that names its layer.
        """
        lines: list[str] = []
        for name, table in self._values.items():
            names = self._layers if sources else None
            _write(lines, f'[{name}]', name, table, self._sources[name], names)
        return '\n'.join(lines).strip('\n') + '\n'

    def table(self, name: str, **arguments: Any) -> dict[str, Any]:
        """Returns a table as a plain dictionary, with keyword arguments of a call laid over it.

        Args:
            name: The table, such as `recorder`.
            **arguments: The keyword arguments of the call that are keys of the table. One that
                holds `FROM_CONFIG` leaves its key to the settings, and any other value replaces
                the key as it is, unchecked, so the call checks it as it always did.

        Raises:
            ChoiceError: If the settings hold no such table, or the table no such key.
        """
        if name not in self._values:
            raise ChoiceError(
                f'expected a table of settings, one of {_schema.listed(sorted(self._values))}, '
                f'found {name}'
            )
        found = copy.deepcopy(self._values[name])
        for key, value in arguments.items():
            if key not in found:
                raise ChoiceError(
                    f'expected a key of [{name}], one of {_schema.listed(sorted(found))}, '
                    f'found the argument {key}'
                )
            if value is not FROM_CONFIG:
                found[key] = value
        return found


def _highest(node: Any) -> int:
    """Returns the highest layer index of the keys under a node of the sources."""
    if isinstance(node, dict):
        return max((_highest(entry) for entry in node.values()), default=0)
    if isinstance(node, list):
        return max((_highest(entry) for entry in node), default=0)
    return node


def _leaves(node: Any, path: str, found: dict[str, int]) -> None:
    if isinstance(node, dict):
        for key, entry in node.items():
            _leaves(entry, f'{path}.{key}' if path else key, found)
    elif isinstance(node, list):
        for index, entry in enumerate(node):
            _leaves(entry, f'{path}.{index}', found)
    else:
        found[path] = node


def _write(
    lines: list[str],
    header: str,
    path: str,
    table: dict[str, Any],
    sources: dict[str, Any],
    layers: tuple[str, ...] | None,
) -> None:
    """Writes a table as TOML, its own keys, then its tables, then its lists of tables."""
    lines += ['', header]
    later = []
    for key, value in table.items():
        source = sources[key]
        if isinstance(source, (dict, list)):
            later.append((key, value, source))
            continue
        line = f'{_files.key_text(key)} = {_files.value_text(value)}'
        lines.append(line if layers is None else f'{line}  # {layers[source]}')
    for key, value, source in later:
        name = f'{path}.{_files.key_text(key)}'
        if isinstance(source, dict):
            _write(lines, f'[{name}]', name, value, source, layers)
        else:
            for entry, entry_sources in zip(value, source, strict=True):
                _write(lines, f'[[{name}]]', name, entry, entry_sources, layers)


def _table_error(path: str, value: Any, place: str, words: str = 'a table') -> ConfigurationError:
    found = _schema.shown(value)
    return ConfigurationError(f'expected {path} as {words}, found {found} in {place}')


def _lay_table(
    values: dict[str, Any],
    sources: dict[str, Any],
    overlay: Any,
    table: _schema.Table,
    layer: int,
    place: str,
) -> None:
    """Lays a table of a layer over the same table of the layers before it, key by key."""
    if not isinstance(overlay, dict):
        raise _table_error(table.path, overlay, place)
    for key, value in overlay.items():
        _lay_key(values, sources, key, value, table, layer, place)


def _lay_key(
    values: dict[str, Any],
    sources: dict[str, Any],
    key: str,
    value: Any,
    table: _schema.Table,
    layer: int,
    place: str,
) -> None:
    """Lays one key of a layer into its table.

    A table changes the keys that it names. A list of tables replaces the list, and each of its
    entries starts from the first entry of the defaults. Any other value replaces the value.
    """
    path = f'{table.path}.{key}'
    if key not in table.defaults:
        if not table.open:
            raise table.unknown(key, place)
        rule = table.accepts.get(_schema.WILDCARD) or {'type': 'any'}
        values[key] = _schema.accept(value, _schema.NOTHING, rule, path, place)
        sources[key] = layer
        return
    default = table.defaults[key]
    if isinstance(default, dict):
        if not isinstance(values.get(key), dict):
            values[key], sources[key] = {}, {}
        _lay_table(values[key], sources[key], value, table.child(key), layer, place)
    elif _schema.is_tables(default):
        if not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
            raise _table_error(path, value, place, 'a list of tables')
        entries: list[dict[str, Any]] = []
        entry_sources: list[dict[str, Any]] = []
        for index, entry in enumerate(value):
            child = table.child(key, f'{path}.{index}')
            entries.append({})
            entry_sources.append({})
            _lay_table(entries[-1], entry_sources[-1], default[0], child, 0, place_of(DEFAULTS))
            _lay_table(entries[-1], entry_sources[-1], entry, child, layer, place)
        values[key], sources[key] = entries, entry_sources
    else:
        values[key] = _schema.accept(value, default, table.accepts.get(key), path, place)
        sources[key] = layer


@dataclasses.dataclass
class _Tree:
    """The settings while the layers are laid, with the schema that checks them."""

    schema: _schema.Schema
    values: dict[str, Any]
    sources: dict[str, Any]
    layers: list[str]

    def lay(self, name: str, data: Any) -> None:
        """Lays the tables of one layer over those before it, under the name of the layer."""
        self.layers.append(name)
        place = place_of(name)
        if not isinstance(data, dict):
            raise ConfigurationError(f'expected tables of settings in {place}')
        for table, overlay in data.items():
            if table not in self.schema.defaults:
                raise self.schema.unknown(table, place)
            self._lay(table, overlay, place)

    def _lay(self, table: str, overlay: Any, place: str) -> None:
        layer = len(self.layers) - 1
        _lay_table(
            self.values[table], self.sources[table], overlay, self.schema.table(table), layer, place
        )

    def override(self, assignments: tuple[str, ...]) -> None:
        """Lays overrides, each `key=value`, as the last layer."""
        if not assignments:
            return
        self.layers.append(OVERRIDES)
        for assignment in assignments:
            self._override(assignment)

    def _override(self, assignment: str) -> None:
        place = place_of(OVERRIDES)
        layer = len(self.layers) - 1
        key, separator, text = assignment.partition('=')
        parts = key.strip().split('.')
        if not separator or not all(parts):
            raise ConfigurationError(
                f'expected an override as key=value, such as recorder.every=4, found {assignment!r}'
            )
        value = _files.scalar(text)
        if parts[0] not in self.schema.defaults:
            raise self.schema.unknown(parts[0], place)
        if len(parts) == 1:
            self._lay(parts[0], value, place)
            return
        table = self.schema.table(parts[0])
        values, sources = self.values[parts[0]], self.sources[parts[0]]
        index = 1
        # Walks the tables that the key names, down to the table that holds its last part.
        while index < len(parts) - 1:
            part = parts[index]
            default = table.defaults.get(part)
            if isinstance(default, dict):
                if not isinstance(values.get(part), dict):
                    values[part], sources[part] = {}, {}
                table = table.child(part)
                values, sources = values[part], sources[part]
                index += 1
            elif _schema.is_tables(default):
                entries = values[part]
                number = parts[index + 1]
                if not number.isdecimal() or int(number) >= len(entries):
                    raise ConfigurationError(
                        f'expected an entry of {table.path}.{part} from 0 to {len(entries) - 1}, '
                        f'found {number} in {place}'
                    )
                table = table.child(part, f'{table.path}.{part}.{number}')
                values, sources = entries[int(number)], sources[part][int(number)]
                index += 2
            elif part in table.defaults or table.open:
                raise ConfigurationError(
                    f'expected {key.strip()} to name a key of a table, and {table.path}.{part} '
                    f'holds no table, in {place}'
                )
            else:
                raise table.unknown(part, place)
        if index == len(parts):
            # The key ends at an entry of a list of tables, which a table changes.
            _lay_table(values, sources, value, table, layer, place)
        else:
            _lay_key(values, sources, parts[index], value, table, layer, place)


def _file_layer(path: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(path, (str, os.PathLike)):
        raise ConfigurationError(f'expected the path of a file of settings, found {path!r}')
    path = Path(path).expanduser()
    return str(path), _files.read(path)


def _assignments(overrides: Any) -> tuple[str, ...]:
    found = (overrides,) if isinstance(overrides, str) else tuple(overrides or ())
    for assignment in found:
        if not isinstance(assignment, str):
            raise ConfigurationError(
                f'expected each override as a text key=value, found {assignment!r}'
            )
    return found


class Settings:
    """The settings of one package: its tables, its home file and its variable.

    One machinery serves every package. Each makes its own instance, and an instance is strict
    about its own tables only, so a file that names a table of another package is refused as a
    file that names an unknown table.
    """

    __slots__ = ('_home', 'defaults_folder', 'home_file', 'variable')

    def __init__(
        self,
        defaults_folder: str | os.PathLike[str],
        home_file: str,
        variable: str,
        home: Callable[[], Path],
    ) -> None:
        """Makes the settings of a package.

        Args:
            defaults_folder: The folder of the shipped defaults, one TOML file for each table.
            home_file: The name of the file of settings in the home folder, such as `arena.toml`.
            variable: The variable that names files of settings, such as `CUJU_ARENA_CONFIG`.
            home: A call that returns the home folder of the package, read on every call so that
                a test or a command can move it.
        """
        self.defaults_folder = Path(defaults_folder)
        self.home_file = home_file
        self.variable = variable
        self._home = home

    def schema(self) -> _schema.Schema:
        """Returns the shipped defaults of the package, read on every call."""
        return _schema.load(self.defaults_folder)

    def _new_tree(self) -> _Tree:
        """Returns the settings of the shipped defaults alone, each checked by its own rule."""
        schema = self.schema()
        tree = _Tree(schema, {}, {}, [DEFAULTS])
        for name, table in schema.defaults.items():
            tree.values[name], tree.sources[name] = {}, {}
            tree._lay(name, table, str(schema.files[name]))
        return tree

    def standing(self) -> list[tuple[str, dict[str, Any]]]:
        """Returns the layers that hold with no call naming them, the home file and the variable."""
        layers = []
        home = self._home() / self.home_file
        if home.is_file():
            layers.append(_file_layer(home))
        for path in os.environ.get(self.variable, '').split(os.pathsep):
            if path.strip():
                layers.append(_file_layer(path.strip()))
        return layers

    @contextlib.contextmanager
    def command_line(self, paths: list[str], overrides: list[str]) -> Iterator[None]:
        """Applies the command line settings to calls and worker processes."""
        variable = self.variable + '_CLI'
        previous = os.environ.get(variable)
        if paths or overrides:
            files = [str(Path(path).expanduser().absolute()) for path in paths]
            os.environ[variable] = json.dumps([files, overrides])
        try:
            self.config()
            yield
        finally:
            if previous is None:
                os.environ.pop(variable, None)
            else:
                os.environ[variable] = previous

    def _resolve(self, layers: list[tuple[str, dict[str, Any]]], overrides: Any) -> Config:
        tree = self._new_tree()
        files, assignments = json.loads(os.environ.get(self.variable + '_CLI', '[[], []]'))
        command_layers = [_file_layer(path) for path in files]
        for name, data in [*self.standing(), *command_layers, *layers]:
            tree.lay(name, data)
        tree.override((*assignments, *_assignments(overrides)))
        return Config(tree.values, tree.sources, tuple(tree.layers))

    def config(self, *paths: str | os.PathLike[str], overrides: Any = ()) -> Config:
        """Returns the settings of the package, resolved from their layers.

        The layers apply in order, each over the last. First come the shipped defaults, then the
        file of the home folder when it exists, then the files that the variable names, then
        `paths` in order, then `overrides`. A file is TOML or JSON by the end of its name and
        names only what it changes.

        Args:
            *paths: Files of settings, each laid over those before it.
            overrides: Overrides as texts `key=value`, such as `recorder.every=4`, or one such
                text. The value reads as TOML, and as a text when it is no TOML value.

        Returns:
            The settings, a read-only mapping whose `source` names the layer of a key.

        Raises:
            MissingFileError: If a named file does not exist.
            ConfigurationError: If a file does not read, or a layer holds a value that its key
                refuses. An unknown table or key raises its subclass `ChoiceError`, with the
                names that the settings hold.
        """
        return self._resolve([_file_layer(path) for path in paths], overrides)

    def resolve(self, config: Any = None, overrides: Any = ()) -> Config:
        """Returns the settings that the arguments `config` and `overrides` of a function give.

        Args:
            config: `None` for the standing layers alone, a path, a `Config`, a mapping of tables
                for a notebook, or a list of paths and mappings, each laid over those before it.
                A `Config` holds its layers already and takes only `overrides` over them.
            overrides: Overrides as texts `key=value`, or one such text, laid last.

        Raises:
            MissingFileError: If a named file does not exist.
            ConfigurationError: If `config` is none of these, or as for `config`.
        """
        if isinstance(config, Config):
            assignments = _assignments(overrides)
            if not assignments:
                return config
            schema = self.schema()
            if set(config) != set(schema.defaults):
                raise ConfigurationError(
                    f'expected settings of the tables {_schema.listed(sorted(schema.defaults))}, '
                    f'found those of {_schema.listed(sorted(config))}'
                )
            tree = _Tree(schema, config.to_dict(), copy.deepcopy(config._sources), [])
            tree.layers += config.layers
            tree.override(assignments)
            return Config(tree.values, tree.sources, tuple(tree.layers))
        if config is None:
            entries = []
        elif isinstance(config, (str, os.PathLike, Mapping)):
            entries = [config]
        elif isinstance(config, (list, tuple)):
            entries = list(config)
        else:
            raise ConfigurationError(
                f'expected config as a path, a list of paths, a Config or a mapping, '
                f'found {config!r}'
            )
        layers = []
        for entry in entries:
            if isinstance(entry, Mapping) and not isinstance(entry, Config):
                layers.append((MAPPING, _files.plain(entry)))
            else:
                layers.append(_file_layer(entry))
        return self._resolve(layers, overrides)

    def table(
        self, name: str, config: Any = None, overrides: Any = (), **arguments: Any
    ) -> dict[str, Any]:
        """Returns one table of the settings for a call, its keyword arguments laid over last.

        This is the one call that a function with `config=` and `overrides=` makes. The function
        gives its keyword arguments that are keys of the table the default `FROM_CONFIG`, and
        hands them on here.

        Args:
            name: The table, such as `recorder`.
            config: As `resolve` takes it.
            overrides: As `resolve` takes them.
            **arguments: Keyword arguments of the call, as `Config.table` takes them.

        Returns:
            The table as a plain dictionary.
        """
        return self.resolve(config, overrides).table(name, **arguments)


# The settings of the arena, and the calls that its functions make.
ARENA = Settings(_schema.FOLDER, HOME_FILE, CONFIG_VARIABLE, _home.home)
config = ARENA.config
resolve = ARENA.resolve
table = ARENA.table
