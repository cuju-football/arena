# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The one mechanism through which code of a person's own joins the arena.

A plugin offers named implementations of the protocols the arena publishes, called kinds. Each
kind has a protocol major, so a later protocol preserves what an earlier plugin reads.
`KINDS` lists the supported protocols and the members that each requires.

Plugins are found two ways.

- Entry points of the group `cuju.arena.plugins`, installed by pip. The entry point names a
  module.
- Folders `<home>/plugins/<id>/`, added by `add`, `cuju arena plugin add` and the routes, after
  the line `TRUST`.

The standards that ship with the package are the plugin `builtin`, of `_builtin_plugins`, which
registers through the same `register` function as any other. The package declares it as its own
entry point, and the registry loads it first by name, so a copy of the sources that pip has not
installed still finds it.

A plugin declares itself with `id`, `requires`, an arena range as formats have, and `provides`,
a mapping of `"<kind>/<major>"` to the names it offers. A folder holds that in `plugin.toml`,
beside `module`, the file that holds `register`, by default `plugin.py`. A module holds it in
`PLUGIN`, a dictionary of the same keys, beside `register(registry)`, which hands each
implementation over with `registry.provide(kind, name, implementation)`.

A plugin of the identifier `acme` that provides `glicko` is chosen as `acme.glicko`, and the
standards of `builtin` are chosen by their bare names, such as `points`.

A plugin whose declaration does not fit, whose `requires` fails, whose protocol major is not the
one this arena serves, or whose `register` raises, is broken. It offers nothing, and no context
can choose it. A plugin whose code raised while it ran is marked failing, with its message, which
`failing` reads until the plugin is replaced or removed. The settings key `[plugins] disabled`
turns plugins off by identifier, the plugin `builtin` excepted.

A plugin is Python that runs with the permissions of the person who runs the arena, in the
arena's own processes, never inside a submission's process. It reads states and frames and never
writes one. The integrity checks of the evidence, the stepping and the ceilings stay with the
arena. Every place that adds a plugin shows `TRUST`.
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from ._store import home as _home
from ._text.messages import MessageError
from ._version import __version__

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

# The group of the entry points that name plugins.
GROUP = 'cuju.arena.plugins'
# The identifier of the plugin of the standards that ship with the package, whose names are bare.
BUILTIN = 'builtin'
# The module of that plugin, which the package also declares as its entry point.
BUILTIN_MODULE = 'cuju.arena._builtin_plugins'
# The line every place that adds a plugin shows.
TRUST = 'A plugin is Python that runs with your permissions. Add only plugins you trust.'
# The file of a folder plugin's declaration, the module it names by default, and the folder of
# the plugins under the home folder.
FILE = 'plugin.toml'
MODULE = 'plugin.py'
FOLDER = 'plugins'
# The ceilings of a plugin's folder.
MAXIMUM_BYTES = 16 * 1024 * 1024
MAXIMUM_FILES = 1000
# The file of the plugins that failed, under the home folder.
_STATUS = 'plugin-status.json'
_IDENTIFIER = re.compile(r'[a-z][a-z0-9-]{0,39}')
_NAME = re.compile(r'[a-z][a-z0-9_]{0,39}')
_PROVIDES = re.compile(r'([a-z_]+)/([0-9]+)')
_LOCK = threading.RLock()


@dataclasses.dataclass(frozen=True)
class Kind:
    """A protocol the arena publishes.

    Attributes:
        name: The kind, such as `rating_method`.
        major: The protocol major this arena serves.
        members: The names an implementation must have as callables.
        what: One line that says what an implementation does.
    """

    name: str
    major: int
    members: tuple[str, ...]
    what: str


# The kinds, by name. A later step adds a line here for each kind it publishes.
KINDS: dict[str, Kind] = {
    kind.name: kind
    for kind in (
        Kind(
            'rating_method',
            1,
            ('fit',),
            '`fit(rows, anchor, options)` gives `{key: {rating, low, high, deviation}}`',
        ),
        Kind('evidence', 1, ('keep',), '`keep(rows, options)` gives the rows that count'),
        Kind('seeding', 1, ('order',), '`order(entrants, context)` gives an order of entrants'),
        Kind('pairing', 1, ('next_pairs',), '`next_pairs(state, slots)` gives the next pairings'),
        Kind(
            'stage_type',
            1,
            ('options', 'schedule', 'standings', 'complete'),
            '`options()`, `schedule(plan)`, `standings(plan, scheduled)` and '
            '`complete(plan, scheduled)` make a stage of a tournament',
        ),
        Kind(
            'tiebreaker',
            1,
            ('key',),
            '`key(rows, options)` gives a sort key for each of the entrants that are level',
        ),
        Kind('moments', 1, ('detect',), '`detect(recording)` gives moments'),
        Kind('statistics', 1, ('compute',), '`compute(recording)` gives named values'),
        Kind('analytics', 1, ('table',), '`table(recording, options)` gives table columns'),
        Kind(
            'attachment',
            1,
            ('produce',),
            '`produce(context)` gives the body of an attachment of a recording, or None',
        ),
    )
}


@dataclasses.dataclass
class Plugin:
    """A plugin the arena knows.

    Attributes:
        id: Its identifier, `builtin` for the standards that ship with the package.
        source: `builtin`, `entry point` or `folder`.
        where: The entry point's value or the folder, for a message.
        version: Its own version as it declares it, or `None`.
        requires: The arena range it declares, or `None`.
        provides: What it declares, `{"<kind>/<major>": [name, ...]}`.
        broken: Why it offers nothing, or `None`.
        provided: The implementations that `register` handed over, by kind and bare name.
    """

    id: str
    source: str
    where: str
    version: str | None = None
    requires: str | None = None
    provides: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    broken: str | None = None
    provided: dict[tuple[str, str], Any] = dataclasses.field(default_factory=dict)

    @property
    def builtin(self) -> bool:
        """Whether it is the plugin of the standards that ship with the package."""
        return self.source == 'builtin'

    def qualify(self, name: str) -> str:
        """Returns the name that a context chooses, bare for `builtin`, else `<id>.<name>`."""
        return name if self.builtin else f'{self.id}.{name}'

    def names(self, kind: str | None = None) -> list[str]:
        """Returns the qualified names it offers, of one kind or of every kind."""
        return [
            self.qualify(name) for (made, name) in self.provided if kind is None or made == kind
        ]

    def as_dict(self) -> dict[str, Any]:
        """Returns what the routes and the command line show of the plugin."""
        offers: dict[str, list[str]] = {}
        for kind, name in self.provided:
            offers.setdefault(f'{kind}/{KINDS[kind].major}', []).append(self.qualify(name))
        return {
            'id': self.id,
            'source': self.source,
            'where': self.where,
            'version': self.version,
            'requires': self.requires,
            'builtin': self.builtin,
            'broken': self.broken,
            'failing': failing(self.id),
            'disabled': self.id in disabled(),
            'provides': offers,
        }


class Registry:
    """What `register(registry)` of a plugin receives, which takes its implementations."""

    def __init__(self, plugin: Plugin) -> None:
        self._plugin = plugin

    def provide(self, kind: str, name: str, implementation: Any) -> None:
        """Hands over one implementation.

        Args:
            kind: The kind with its major, such as `rating_method/1`.
            name: The bare name, as the declaration lists it.
            implementation: An object that has every member of the kind as a callable.

        Raises:
            ValueError: If the kind is not declared, the name is not listed under it, the name
                is given twice, or a member is missing.
        """
        found = _PROVIDES.fullmatch(kind)
        if found is None or kind not in self._plugin.provides:
            raise ValueError(f'expected a kind the plugin declares, found {kind!r}')
        if name not in self._plugin.provides[kind]:
            raise ValueError(f'expected a name that the plugin lists under {kind}, found {name!r}')
        made = found.group(1)
        if (made, name) in self._plugin.provided:
            raise ValueError(f'expected {name!r} once under {kind}')
        missing = [
            member
            for member in KINDS[made].members
            if not callable(getattr(implementation, member, None))
        ]
        if missing:
            raise ValueError(f'expected {kind} {name!r} to have {", ".join(missing)}')
        self._plugin.provided[(made, name)] = implementation


# The reading of a plugin.


def _declaration(data: Any, plugin: Plugin) -> None:
    """Reads `id`, `requires` and `provides` into the plugin, raising ValueError when they fail."""
    if not isinstance(data, dict):
        raise ValueError('expected a declaration of id, requires and provides')
    if data.get('id') != plugin.id:
        raise ValueError(f'expected the id {plugin.id!r}, found {data.get("id")!r}')
    requires = data.get('requires')
    if requires is not None and not isinstance(requires, str):
        raise ValueError(f'expected requires to be a range such as ">=0.1.0", found {requires!r}')
    plugin.requires = requires
    version = data.get('version')
    plugin.version = version if isinstance(version, str) else None
    provides = data.get('provides')
    if not isinstance(provides, dict):
        raise ValueError('expected provides to map each kind and major to its names')
    for kind, names in provides.items():
        found = _PROVIDES.fullmatch(kind) if isinstance(kind, str) else None
        if found is None:
            raise ValueError(f'expected a kind such as "rating_method/1", found {kind!r}')
        if found.group(1) not in KINDS:
            raise ValueError(f'expected one of {", ".join(sorted(KINDS))}, found {kind!r}')
        major = KINDS[found.group(1)].major
        if int(found.group(2)) != major:
            raise ValueError(
                f'expected {found.group(1)} at protocol major {major}, found {found.group(2)}'
            )
        if not isinstance(names, list) or not names or len(set(names)) != len(names):
            raise ValueError(f'expected {kind} to list its names once each, found {names!r}')
        for name in names:
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise ValueError(f'expected a name of lowercase letters and digits, found {name!r}')
    plugin.provides = {kind: list(names) for kind, names in provides.items()}
    if requires is not None:
        from ._formats import contract as _contract

        try:
            fits = _contract.satisfies(__version__, requires)
        except _contract.ContractError as error:
            raise ValueError(str(error)) from None
        if not fits:
            raise ValueError(f'needs {_contract.needs(requires)}')


def _finish(plugin: Plugin, register: Any) -> Plugin:
    if not callable(register):
        plugin.broken = 'expected a function register(registry)'
        return plugin
    try:
        register(Registry(plugin))
    except Exception as error:  # a plugin is anyone's code, and any failure leaves it broken
        plugin.provided.clear()
        plugin.broken = f'register raised {type(error).__name__}, {_oneline(error)}'
        return plugin
    absent = [
        f'{name} of {kind}'
        for kind, names in plugin.provides.items()
        for name in names
        if (kind.split('/')[0], name) not in plugin.provided
    ]
    if absent:
        plugin.provided.clear()
        plugin.broken = f'register did not provide {", ".join(absent)}'
    return plugin


def _oneline(error: object) -> str:
    return ' '.join(str(error).split())[:300]


def _read_module(identifier: str, source: str, where: str, module: Any) -> Plugin:
    plugin = Plugin(identifier, source, where)
    try:
        _declaration(getattr(module, 'PLUGIN', None), plugin)
    except ValueError as error:
        plugin.broken = str(error)
        return plugin
    return _finish(plugin, getattr(module, 'register', None))


def _read_entry(entry: importlib.metadata.EntryPoint) -> Plugin:
    plugin = Plugin(entry.name, 'entry point', entry.value)
    try:
        module = entry.load()
    except Exception as error:
        plugin.broken = f'importing {entry.value} raised {type(error).__name__}, {_oneline(error)}'
        return plugin
    return _read_module(entry.name, 'entry point', entry.value, module)


def _load_file(identifier: str, folder: Path, script: Path) -> Any:
    """Imports the script of a folder plugin as a module of a package that is the folder."""
    package = f'cuju_plugin_{identifier.replace("-", "_")}'
    root = types.ModuleType(package)
    root.__path__ = [str(folder)]  # type: ignore[attr-defined]
    sys.modules[package] = root
    name = f'{package}.{script.stem}'
    spec = importlib.util.spec_from_file_location(name, script)
    if spec is None or spec.loader is None:
        raise ImportError(f'expected a Python file, found {script.name}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        # Bytecode can keep the old source after a same-size edit within a second.
        code = compile(script.read_bytes(), str(script), 'exec', dont_inherit=True)
        exec(code, module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        sys.modules.pop(package, None)
        raise
    return module


def _read_folder(folder: Path, identifier: str | None = None) -> Plugin:
    identifier = identifier or folder.name
    plugin = Plugin(identifier, 'folder', str(folder))
    try:
        data = tomllib.loads((folder / FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        plugin.broken = f'{FILE} does not read, {_oneline(error)}'
        return plugin
    try:
        _declaration(data, plugin)
        script = data.get('module', MODULE)
        if not isinstance(script, str) or Path(script).name != script or not script.endswith('.py'):
            raise ValueError(
                f'expected module to name a Python file of the folder, found {script!r}'
            )
    except ValueError as error:
        plugin.broken = str(error)
        return plugin
    try:
        module = _load_file(identifier, folder, folder / script)
    except Exception as error:
        plugin.broken = f'importing {script} raised {type(error).__name__}, {_oneline(error)}'
        return plugin
    return _finish(plugin, getattr(module, 'register', None))


# Discovery.

# The plugins as last read, with the stamp of what they were read from.
_CACHE: dict[str, Any] = {}
_CACHE_BUILTIN: dict[str, Plugin] = {}


def _folder() -> Path:
    return _home.home() / FOLDER


def _stamp() -> tuple[Any, ...]:
    folder = _folder()
    found = []
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if path.is_dir():
                for member in _files(path):
                    try:
                        seen = member.stat()
                    except OSError:
                        continue
                    found.append(
                        (member.relative_to(folder).as_posix(), seen.st_mtime_ns, seen.st_size)
                    )
    return (str(folder), tuple(sys.path), tuple(found))


def _entry_points() -> Iterator[importlib.metadata.EntryPoint]:
    for entry in importlib.metadata.entry_points(group=GROUP):
        if entry.value.partition(':')[0] != BUILTIN_MODULE:
            yield entry


def _completion_ids() -> tuple[str, ...]:
    """Return plugin identifiers from the built-in plugin and installed metadata."""
    return (BUILTIN, *sorted({entry.name for entry in _entry_points()} - {BUILTIN}))


def _additional_plugins() -> list[Plugin]:
    found: list[Plugin] = []
    taken = {BUILTIN}
    for entry in sorted(_entry_points(), key=lambda entry: entry.name):
        plugin = _read_entry(entry)
        found.append(_own(plugin, taken))
    folder = _folder()
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if path.is_dir() and _IDENTIFIER.fullmatch(path.name) and (path / FILE).is_file():
                found.append(_own(_read_folder(path), taken))
    return found


def _own(plugin: Plugin, taken: set[str]) -> Plugin:
    """Marks a plugin broken whose identifier another plugin holds or is not allowed."""
    if plugin.id in taken:
        plugin.provided.clear()
        plugin.broken = f'the identifier {plugin.id} is taken by another plugin'
    elif not _IDENTIFIER.fullmatch(plugin.id) or plugin.id == BUILTIN:
        plugin.provided.clear()
        plugin.broken = 'expected an identifier of lowercase letters, digits and hyphens'
    taken.add(plugin.id)
    return plugin


def _builtin() -> Plugin:
    """Returns the plugin of the standards, read once, so a call that names one reads no folder."""
    with _LOCK:
        if 'builtin' not in _CACHE_BUILTIN:
            module = importlib.import_module(BUILTIN_MODULE)
            _CACHE_BUILTIN['builtin'] = _read_module(BUILTIN, 'builtin', BUILTIN_MODULE, module)
        return _CACHE_BUILTIN['builtin']


def _others() -> list[Plugin]:
    stamp = _stamp()
    if _CACHE.get('stamp') != stamp:
        _CACHE['plugins'] = _additional_plugins()
        _CACHE['stamp'] = stamp
    return _CACHE['plugins']


def plugins() -> list[Plugin]:
    """Returns every plugin, `builtin` first, then the others by identifier."""
    with _LOCK:
        return [_builtin(), *_others()]


def _descriptions() -> list[dict[str, Any]]:
    from ._builtin_plugins import PLUGIN

    with _LOCK:
        loaded = _CACHE_BUILTIN.get(BUILTIN)
        if loaded is None:
            builtin = Plugin(BUILTIN, 'builtin', BUILTIN_MODULE)
            _declaration(PLUGIN, builtin)
            described = builtin.as_dict()
            described['provides'] = builtin.provides
        else:
            described = loaded.as_dict()
        return [described, *(item.as_dict() for item in _others())]


def signature() -> Any:
    """Returns a value that changes when a plugin is added, replaced or removed."""
    return hash(_stamp())


def refresh() -> None:
    """Forgets what was read, so the next call reads entry points and folders again."""
    with _LOCK:
        _CACHE.clear()


def plugin(identifier: str) -> Plugin:
    """Returns a plugin by its identifier.

    Raises:
        ArgumentError: If none has it.
    """
    for found in plugins():
        if found.id == identifier:
            return found
    raise MessageError('server.plugins.missing', plugin=repr(identifier))


# The settings and the choice.


def disabled(config: Any = None, overrides: Any = ()) -> list[str]:
    """Returns the identifiers that `[plugins] disabled` turns off."""
    from ._config import layers as _layers

    found = _layers.table('plugins', config, overrides)['disabled']
    return [str(entry) for entry in found if entry != BUILTIN]


def _split(kind: str, name: str) -> tuple[str, str]:
    if kind not in KINDS:
        raise MessageError('server.plugins.kind', kind=repr(kind), kinds=', '.join(KINDS))
    identifier, dot, bare = str(name).partition('.')
    return (identifier, bare) if dot else (BUILTIN, identifier)


def get(kind: str, name: str, config: Any = None, overrides: Any = ()) -> Any:
    """Returns the implementation that a context chose by name.

    Args:
        kind: The kind, such as `rating_method`.
        name: The name, a bare one for the standards, else `<plugin>.<name>`.
        config: The settings that `[plugins] disabled` is read from.
        overrides: Settings laid over `config`.

    Raises:
        ArgumentError: If no plugin offers the name, the plugin is broken, or it is turned off.
    """
    identifier, bare = _split(kind, name)
    held = None
    for found in [_builtin()] if identifier == BUILTIN else plugins():
        if found.id == identifier:
            held = found
            break
    if held is not None and held.broken is not None:
        raise MessageError('server.plugins.broken', plugin=held.id, reason=held.broken)
    if held is not None and not held.builtin and held.id in disabled(config, overrides):
        raise MessageError('server.plugins.disabled', plugin=held.id)
    if held is None or (kind, bare) not in held.provided:
        raise MessageError(
            'server.plugins.unknown',
            kind=kind.replace('_', ' '),
            name=repr(name),
            names=', '.join(names(kind, config, overrides)),
        )
    return held.provided[(kind, bare)]


def names(kind: str, config: Any = None, overrides: Any = ()) -> list[str]:
    """Returns the names a context may choose of a kind, the standards first.

    A name of a broken plugin or of one that `[plugins] disabled` turns off is left out.
    """
    if kind not in KINDS:
        raise MessageError('server.plugins.kind', kind=repr(kind), kinds=', '.join(KINDS))
    off = None
    found = []
    for held in plugins():
        if held.broken is not None:
            continue
        if not held.builtin:
            off = disabled(config, overrides) if off is None else off
            if held.id in off:
                continue
        found.extend(held.names(kind))
    return found


def call(
    kind: str,
    name: str,
    member: str,
    *arguments: Any,
    check: Callable[[Any], Any] | None = None,
    config: Any = None,
    overrides: Any = (),
) -> Any:
    """Runs one member of an implementation and returns what it gave.

    A plugin that raises, or whose answer `check` refuses by raising `ValueError`, is marked
    failing and the call raises an `ArgumentError` that names it, so a run in progress ends with
    that message and the engine and the match process stay untouched. A standard of the package
    that raises is a defect of the package and passes through as it is.

    Args:
        kind: The kind.
        name: The name a context chose.
        member: The member to call, such as `fit`.
        *arguments: Its arguments.
        check: A function that returns the answer, cleaned, or raises `ValueError` to refuse it.
        config: The settings that `[plugins] disabled` is read from.
        overrides: Settings laid over `config`.
    """
    implementation = get(kind, name, config, overrides)
    identifier = _split(kind, name)[0]
    try:
        answer = getattr(implementation, member)(*arguments)
        return answer if check is None else check(answer)
    except Exception as error:
        if identifier == BUILTIN:
            raise
        reason = _oneline(error) or type(error).__name__
        mark_failing(identifier, f'{kind} {name}, {reason}')
        raise MessageError(
            'server.plugins.failed',
            plugin=identifier,
            kind=kind.replace('_', ' '),
            name=repr(name),
            reason=reason,
        ) from error


# The plugins that a file used.


def stamp(qualified: Any) -> list[dict[str, Any]]:
    """Returns the plugins behind some names, each once, as `{name, version}` ordered by name.

    A file that a plugin made keeps this beside the qualified names it uses, so a later read
    without the plugin, or with another version, still tells which it was. The standards of
    `builtin` have bare names and are left out, since they ship with the package. A plugin that
    declares no version stores `None`.

    Args:
        qualified: The names a context chose, such as `acme.glicko`.
    """
    found: dict[str, Any] = {}
    for name in qualified:
        identifier, dot, _ = str(name).partition('.')
        if not dot or identifier in found:
            continue
        try:
            found[identifier] = plugin(identifier).version
        except MessageError:
            found[identifier] = None
    return [{'name': identifier, 'version': found[identifier]} for identifier in sorted(found)]


def stamps(value: Any) -> list[dict[str, Any]]:
    """Returns the plugins a stored file names, those of `value` that read as `{name, version}`.

    A value that does not fit reads as none, so an older file or one edited by hand never fails to
    open.
    """
    found = []
    for entry in value if isinstance(value, list) else ():
        if (
            isinstance(entry, dict)
            and isinstance(entry.get('name'), str)
            and _IDENTIFIER.fullmatch(entry['name'])
            and (entry.get('version') is None or isinstance(entry['version'], str))
        ):
            found.append({'name': entry['name'], 'version': entry.get('version')})
    return found


def unavailable(stored: Any) -> list[dict[str, Any]]:
    """Returns those of the stored plugins that cannot be used now, missing, broken or turned off.

    A file that names such a plugin reads and shows what it kept, and changes nothing.
    A plugin at another version than the stored one still counts as available.
    """
    off = disabled()
    gone = []
    for entry in stamps(stored):
        try:
            held = plugin(entry['name'])
        except MessageError:
            gone.append(entry)
            continue
        if held.broken is not None or held.id in off:
            gone.append(entry)
    return gone


# Failing plugins.


def _status_path() -> Path:
    return _home.home() / _STATUS


def _status() -> dict[str, Any]:
    try:
        document = json.loads(_status_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def _write_status(status: dict[str, Any]) -> None:
    _status_path().parent.mkdir(parents=True, exist_ok=True)
    temporary = _status_path().with_suffix('.part')
    temporary.write_text(json.dumps(status), encoding='utf-8')
    os.replace(temporary, _status_path())


def failing(identifier: str) -> dict[str, Any] | None:
    """Returns the last failure of a plugin's code, `{message, time}`, or `None`."""
    found = _status().get(identifier)
    return found if isinstance(found, dict) else None


def mark_failing(identifier: str, message: str) -> None:
    """Records that a plugin's code failed, which stays until the plugin is replaced."""
    with _LOCK:
        status = _status()
        status[identifier] = {'message': _oneline(message), 'time': time.time()}
        _write_status(status)


def _clear_failing(identifier: str) -> None:
    with _LOCK:
        status = _status()
        if status.pop(identifier, None) is not None:
            _write_status(status)


# Adding and removing.


def _files(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.rglob('*')
        if path.is_file()
        and not path.is_symlink()
        and '__pycache__' not in path.relative_to(folder).parts
        and path.suffix != '.pyc'
    )


def add(source: Path | str, replace: bool = False) -> Plugin:
    """Adds a plugin from a folder and returns it.

    The folder is copied into `<home>/plugins/<id>/`, where the identifier is the `id` of its
    `plugin.toml`. The plugin is read once from the copy, which runs its module, so a plugin that
    does not read, fails its `requires` or serves another protocol major is never added. A plugin
    is Python that runs with the person's permissions, see `TRUST`.

    Args:
        source: The folder that holds `plugin.toml`.
        replace: Whether to replace a plugin of the same identifier.

    Raises:
        ArgumentError: If the folder does not fit, the plugin is broken, or its identifier is
            taken by `builtin`, an entry point or another folder and `replace` is false.
    """
    folder = Path(source)
    if not (folder / FILE).is_file():
        raise MessageError('server.plugins.folder', folder=str(folder))
    if any(path.is_symlink() for path in folder.rglob('*')):
        raise MessageError('server.plugins.links')
    members = _files(folder)
    if len(members) > MAXIMUM_FILES or sum(path.stat().st_size for path in members) > MAXIMUM_BYTES:
        raise MessageError('server.plugins.size', files=MAXIMUM_FILES, mib=16)
    try:
        identifier = tomllib.loads((folder / FILE).read_text(encoding='utf-8')).get('id')
    except (OSError, ValueError) as error:
        raise MessageError('server.plugins.declaration', reason=_oneline(error)) from None
    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
        raise MessageError('server.plugins.identifier', identifier=repr(identifier))
    if identifier == BUILTIN:
        raise MessageError('server.plugins.builtin', identifier=identifier)
    target = _folder() / identifier
    with _LOCK:
        taken = [held for held in plugins() if held.id == identifier and held.source != 'folder']
        if taken:
            raise MessageError('server.plugins.entry', identifier=identifier)
        if target.exists() and not replace:
            raise MessageError('server.plugins.taken', identifier=identifier)
        with tempfile.TemporaryDirectory(prefix='cuju-plugin-') as scratch:
            staged = Path(scratch) / identifier
            shutil.copytree(
                folder, staged, symlinks=False, ignore=shutil.ignore_patterns('__pycache__')
            )
            try:
                found = _read_folder(staged, identifier)
            finally:
                for name in [name for name in sys.modules if name.startswith('cuju_plugin_')]:
                    if name.startswith(f'cuju_plugin_{identifier.replace("-", "_")}'):
                        sys.modules.pop(name, None)
            if found.broken is not None:
                raise MessageError('server.plugins.broken', plugin=identifier, reason=found.broken)
            _folder().mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(staged, target)
        _clear_failing(identifier)
        refresh()
    return plugin(identifier)


def remove(identifier: str) -> bool:
    """Deletes a plugin of the folder and returns whether it existed.

    Raises:
        ArgumentError: For the standards that ship with the package and for an entry point.
    """
    if identifier == BUILTIN:
        raise MessageError('server.plugins.builtin', identifier=identifier)
    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
        return False
    target = _folder() / identifier
    if not target.is_dir():
        if any(held.id == identifier for held in plugins()):
            raise MessageError('server.plugins.entry', identifier=identifier)
        return False
    with _LOCK:
        shutil.rmtree(target)
        _clear_failing(identifier)
        refresh()
    return True
