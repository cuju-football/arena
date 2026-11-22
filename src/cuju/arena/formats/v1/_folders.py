# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Helpers that read a submission's folder, load its code as Kaggle does and seed its generators."""

from __future__ import annotations

import ast
import contextlib
import functools
import hashlib
import importlib
import importlib.util
import json
import os
import random
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ._verdicts import Forfeit, ManifestError, Missing

# Kaggle: the file of a submission that holds its agent.
KAGGLE_MAIN = 'main.py'
# Kaggle: the time an agent may overrun across a match, which every observation holds.
KAGGLE_OVERAGE = 60.0
# The largest manifest that `read_manifest` reads.
MANIFEST_BYTES = 1024 * 1024


@contextlib.contextmanager
def from_folder(folder: Path | str) -> Iterator[None]:
    """Runs a block from a submission's folder, first on `sys.path`, as Kaggle runs an agent.

    The working folder is the submission's for the block, so its own modules import and its
    working files resolve, and both come back afterwards.
    """
    folder = str(folder)
    previous = os.getcwd()
    sys.path.insert(0, folder)
    os.chdir(folder)
    try:
        yield
    finally:
        os.chdir(previous)
        with contextlib.suppress(ValueError):
            sys.path.remove(folder)


def _forget(folder: Path, before: set[str]) -> None:
    """Removes the modules imported from a folder since `before`, so another submission with
    modules of the same names gets its own."""
    resolved = folder.resolve()
    for name in set(sys.modules) - before:
        origin = getattr(sys.modules.get(name), '__file__', None)
        if origin and Path(origin).resolve().is_relative_to(resolved):
            del sys.modules[name]


def _synthetic(folder: Path, file: str) -> str:
    digest = hashlib.sha256(str(folder.resolve()).encode()).hexdigest()[:12]
    stem = ''.join(character if character.isalnum() else '_' for character in Path(file).stem)
    return f'cuju_submission_{digest}_{stem}'


def _inside(folder: Path, file: str) -> Path:
    path = (folder / file).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise Forfeit('load', f'{file} lies outside the submission', detail='entry')
    if not path.is_file():
        raise Forfeit('load', f'{file} is missing', detail='entry')
    return path


def load_module(folder: Path | str, file: str) -> types.ModuleType:
    """Returns a submission's file as a module, imported under a name of its own.

    The name is `cuju_submission_<digest>_<stem>`, so no submission's module shadows another or a
    module of the format. The folder comes first on `sys.path` and is the working folder while
    the file runs, and the modules it imports from its folder leave `sys.modules` afterwards.

    Raises:
        Forfeit: `load`, if the file is missing or lies outside the folder.
    """
    folder = Path(folder)
    path = _inside(folder, file)
    name = _synthetic(folder, file)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    with from_folder(folder):
        before = set(sys.modules)
        spec.loader.exec_module(module)
        _forget(folder, before)
    return module


def kaggle_agent(folder: Path | str) -> Any:
    """Returns a submission's agent as Kaggle finds it, the last callable its `main.py` defines.

    This is the body of the renderer's loading of a team before formats. The file runs as Kaggle's
    `get_last_callable` runs it, from its bytes so its own encoding line decides how it reads,
    with the folder first on `sys.path` and as the working folder. The last callable of all the
    module's values is the agent, and every call of it runs from the folder again. The modules
    the file imports from its folder leave `sys.modules` afterwards.

    Raises:
        Forfeit: `load`, if `main.py` is missing or defines no callable.
    """
    folder = Path(folder)
    main = _inside(folder, KAGGLE_MAIN)
    module = types.ModuleType(_synthetic(folder, KAGGLE_MAIN))
    module.__file__ = str(main)
    with from_folder(folder):
        before = set(sys.modules)
        exec(compile(main.read_bytes(), str(main), 'exec'), module.__dict__)
        _forget(folder, before)
    callables = [value for value in module.__dict__.values() if callable(value)]
    if not callables:
        raise Forfeit('load', 'main.py defines no callable agent', detail='entry')
    agent = callables[-1]

    @functools.wraps(agent)
    def run(*arguments: Any) -> Any:
        with from_folder(folder):
            return agent(*arguments)

    return run


class KaggleStruct(dict):
    """A dictionary whose keys are also attributes, as Kaggle's `Struct` that agents receive.

    A missing attribute raises `AttributeError`, as Kaggle's own and cuju.kaggle's do.
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _lists(entry: dict[str, Any]) -> dict[str, Any]:
    """Returns a GRF raw dictionary with its numpy arrays as lists, as Kaggle's JSON makes them."""
    import numpy as np

    return {
        name: value.tolist() if isinstance(value, np.ndarray) else value
        for name, value in entry.items()
    }


def kaggle_observation(
    raw: list[dict[str, Any]], step: int = 0, overage: float = KAGGLE_OVERAGE
) -> KaggleStruct:
    """Returns Kaggle's observation of the players of a step, as cuju.kaggle builds it.

    Args:
        raw: The GRF raw observations of the controlled players, `step.raw`.
        step: Kaggle's `step`, the calls of the agent so far in the match, from 0.
        overage: Kaggle's `remainingOverageTime`, the seconds of the overage bank left.

    Returns:
        `players_raw`, one entry per player with GRF's arrays as lists, `controlled_players`,
        `step` and `remainingOverageTime`, a dictionary whose keys are also attributes.
    """
    return KaggleStruct(
        players_raw=[_lists(entry) for entry in raw],
        controlled_players=len(raw),
        step=step,
        remainingOverageTime=overage if overage != int(overage) else int(overage),
    )


def _yaml() -> Any:
    try:
        return importlib.import_module('yaml')
    except ImportError:
        return None


def read_manifest(folder: Path | str, file: str) -> dict[str, Any]:
    """Returns a submission's manifest as a dictionary, read by its extension.

    A `.toml` file reads with the standard library, a `.json` file too, and a `.yml` or `.yaml`
    file with PyYAML's safe loader, which only builds plain values.

    Raises:
        ManifestError: If the file is missing, too large, does not read, needs PyYAML where it is
            missing, or holds no mapping at its top.
    """
    folder = Path(folder)
    path = (folder / file).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        raise ManifestError(f'{file} is missing')
    if path.stat().st_size > MANIFEST_BYTES:
        raise ManifestError(f'{file} is larger than 1 MiB')
    text = path.read_bytes()
    suffix = path.suffix.lower()
    try:
        if suffix == '.toml':
            if sys.version_info >= (3, 11):
                import tomllib
            else:  # pragma: no cover, Python 3.10
                import tomli as tomllib
            data = tomllib.loads(text.decode('utf-8'))
        elif suffix == '.json':
            data = json.loads(text.decode('utf-8'))
        elif suffix in ('.yml', '.yaml'):
            yaml = _yaml()
            if yaml is None:
                raise ManifestError(f'reading {file} needs PyYAML, install pyyaml')
            data = yaml.safe_load(text.decode('utf-8'))
        else:
            raise ManifestError(f'{file} is no TOML, YAML or JSON file')
    except ManifestError:
        raise
    except Exception as error:
        line = ' '.join(str(error).split())
        raise ManifestError(f'{file} does not read, {line}') from None
    if not isinstance(data, dict):
        raise ManifestError(f'{file} holds no mapping at its top')
    return data


def require(*modules: str, hint: str | None = None) -> Any:
    """Returns the modules a format needs, or raises an environment refusal that names them.

    Raised from `accept`, the refusal belongs to the machine, so the renderer never keeps it and
    checks again where the package is installed. Raised from `start`, it forfeits as `load`.

    Args:
        modules: The modules, such as `torch` or `gfootball`.
        hint: What installs them, such as `install cuju[gfootball]`.

    Returns:
        The one module, or a tuple of them in order.

    Raises:
        Missing: If any of them cannot be imported.
    """
    found, missing = [], []
    for name in modules:
        try:
            found.append(importlib.import_module(name))
        except ImportError:
            missing.append(name)
    if missing:
        raise Missing(missing, hint)
    return found[0] if len(found) == 1 else tuple(found)


def _files(folder: Path | str) -> Iterator[Path]:
    for path in sorted(Path(folder).rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts:
            yield path


def folder_size(folder: Path | str) -> int:
    """Returns the size of a submission's folder in bytes, the caches of Python left out.

    Args:
        folder: The submission's folder.
    """
    return sum(path.stat().st_size for path in _files(folder))


def imports_of(folder: Path | str) -> set[str]:
    """Returns the top level modules that the Python files of a folder import, by name.

    The files are parsed and never run, so a file that does not parse adds nothing, and a relative
    import adds nothing, since it names a file of the folder itself. A format uses it to ask for a
    package only when a submission needs it, as in `require('gfootball')`.

    Args:
        folder: The submission's folder.
    """
    found: set[str] = set()
    for path in _files(folder):
        if path.suffix != '.py':
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
        except (SyntaxError, ValueError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split('.')[0])
    return found


def seed_globals(seed: int) -> None:
    """Seeds the generators a submission may roll, `random`, `numpy.random` and torch.

    torch is seeded only where it is imported already, so seeding never imports it.
    """
    import numpy as np

    seed = int(seed)
    random.seed(seed)
    np.random.seed(seed % 2**32)
    torch = sys.modules.get('torch')
    if torch is not None:
        torch.manual_seed(seed)
