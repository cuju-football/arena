# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The entry of a format process and of a format's child, which answers frames until it closes.

The renderer starts it through the bootstrap of `cuju.arena._formats.launch`, and its first frame,
`hello`, says which it is.

- `role = "format"` loads a format's script as a package of its own, `cuju_format_<digest>`, so
  its `from . import` statements find the format's other files and no format shadows a module of
  a submission. It then answers `accept`, `start`, `reset`, `act`, `end` and `close`, turning the
  JSON of each into the contract's objects and holding every step's raw observations to the
  fixed table.
- `role = "child"` puts the submission's folder first on `sys.path` and works from it, imports a
  file of the format by its path under a synthetic name, builds `factory(folder, *args)` and
  answers each `call` of a method of it.

An exception crosses as `{kind, message}`, with `kind` one of `forfeit`, `missing`, `invalid`,
`crash` for one whose innermost owned frame lies in the submission, or `format` for the format's
own. Its traceback goes to standard error, which is the terminal of the side.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import threading
import time
import traceback
import types
from pathlib import Path
from typing import Any, BinaryIO

from cuju.arena._formats import blame as _blame
from cuju.arena._formats import frames as _frames

from . import _context
from ._objects import Match, Result, Step, Team
from ._verdicts import Forfeit, Invalid, Missing, Verdict

# The largest answer of a child, in bytes of its frame.
ANSWER_BYTES = 1024 * 1024
# The frameworks whose versions a side reports for its ratings rows.
FRAMEWORKS = ('numpy', 'torch', 'tensorflow', 'jax', 'onnxruntime', 'sklearn', 'gfootball')


def versions() -> dict[str, str]:
    """Returns the versions of the frameworks this process has imported, by name."""
    found = {}
    for name in FRAMEWORKS:
        version = getattr(sys.modules.get(name), '__version__', None)
        if version is not None:
            found[name] = str(version)
    return found


def package_name(folder: Path) -> str:
    """Returns the synthetic name of a format's package, from its folder."""
    digest = hashlib.sha256(str(Path(folder).resolve()).encode()).hexdigest()[:12]
    return f'cuju_format_{digest}'


def load_format(script: Path, name: str | None = None) -> types.ModuleType:
    """Imports a format's script as a package of its own, its folder holding its other files."""
    script = Path(script).resolve()
    name = name or package_name(script.parent)
    spec = importlib.util.spec_from_file_location(
        name, script, submodule_search_locations=[str(script.parent)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def failure(
    error: BaseException, submission: Path | None, format_folder: Path | None
) -> dict[str, Any]:
    """Returns the frame value of an exception, by its kind and by whose code raised it."""
    if isinstance(error, Forfeit):
        return {'kind': 'forfeit', 'forfeit': error.as_dict()}
    if isinstance(error, Missing):
        return {'kind': 'missing', 'message': str(error), 'modules': error.modules}
    if isinstance(error, Invalid):
        return {'kind': 'invalid', 'message': ' '.join(str(error).split())}
    traceback.print_exception(error, file=sys.stderr)
    who = _blame.blame(error, submission, format_folder)
    return {
        'kind': 'crash' if who == _blame.TEAM else 'format',
        'message': _blame.last_line(error),
    }


class _Format:
    """The format role, a format's module and, once `start` has run, its player."""

    def __init__(self, hello: dict[str, Any], inputs: BinaryIO, answers: BinaryIO) -> None:
        self.folder = Path(hello['folder'])
        self.script = Path(hello['script'])
        self.format_folder = None if hello.get('builtin') else self.script.parent
        _context.CURRENT = _context.Context(
            format_folder=self.script.parent,
            submission=self.folder,
            environment=dict(os.environ),
            imports=[p for p in os.environ.get('CUJU_ARENA_IMPORT', '').split(os.pathsep) if p],
        )
        if hello.get('check'):
            requests = threading.Lock()

            def request(operation: str, **fields: Any) -> Any:
                with requests:
                    _frames.write(answers, 'tree', fields, operation=operation)
                    reply = _frames.read(inputs)
                if reply['kind'] == 'tree.error':
                    raise OSError(reply['value'])
                if reply['kind'] != 'tree.answer':
                    raise EOFError('the host ended the check match watch')
                return reply['value']

            def play_check(**arguments: Any) -> Any:
                from cuju.arena._formats import checks

                return checks.child_play_check(hello, request)(**arguments)

            _context.CURRENT.play_check = play_check
        if hello.get('module'):
            self.module = importlib.import_module(hello['module'])
        else:
            self.module = load_format(self.script, hello.get('name'))
        self.player: Any = None

    def call(self, method: str, arguments: list[Any]) -> Any:
        if method == 'accept':
            team = Team.from_dict(arguments[0])
            _context.CURRENT.team = team
            verdict = self.module.accept(self.folder, team)
            if not isinstance(verdict, Verdict):
                raise TypeError(f'accept returned {type(verdict).__name__}, not a verdict')
            return verdict.as_dict()
        if method == 'start':
            self.player = self.module.start(self.folder, Match.from_dict(arguments[0]))
            revision = getattr(self.player, 'revision', None)
            return {'revision': None if revision is None else str(revision)[:64]}
        if method == 'act':
            value = dict(arguments[0])
            value['raw'] = [_frames.typed(raw) for raw in value.get('raw') or []]
            return self.player.act(Step.from_dict(value))
        if method == 'end':
            found = getattr(self.player, 'end', None)
            forfeit = None if found is None else found(Result.from_dict(arguments[0]))
            if forfeit is not None and not isinstance(forfeit, Forfeit):
                raise TypeError(f'end returned {type(forfeit).__name__}, not a forfeit')
            return None if forfeit is None else forfeit.as_dict()
        if method == 'versions':
            return versions()
        if method in ('reset', 'close'):
            found = getattr(self.player, method, None)
            if found is not None:
                found()
            return None
        raise ValueError(f'a format answers no {method!r}')


class _Child:
    """The child role, an object of a format's file built in the submission's folder."""

    def __init__(self, hello: dict[str, Any]) -> None:
        self.folder = Path(hello['folder'])
        self.format_folder = Path(hello['format_folder']) if hello.get('format_folder') else None
        sys.path.insert(0, str(self.folder))
        os.chdir(self.folder)
        script = Path(hello['script'])
        digest = hashlib.sha256(str(script.resolve()).encode()).hexdigest()[:12]
        name = f'cuju_format_child_{digest}'
        spec = importlib.util.spec_from_file_location(name, script)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        factory = getattr(module, hello['factory'])
        self.target = factory(self.folder, *(hello.get('arguments') or []))

    def call(self, method: str, arguments: list[Any]) -> Any:
        if method.startswith('_'):
            raise ValueError(f'a child answers no {method!r}')
        return getattr(self.target, method)(*arguments)


def main(inputs: BinaryIO, answers: BinaryIO) -> None:
    """Answers frames on `inputs` with frames on `answers` until `close` or the end of the input."""
    try:
        hello = _frames.read(inputs)
    except (EOFError, _frames.FrameError):
        return
    value = hello.get('value') or {}
    role = value.get('role')
    folder = Path(value['folder']) if value.get('folder') else None
    format_folder = Path(value['format_folder']) if value.get('format_folder') else None
    began = time.perf_counter()
    try:
        target: Any = _Format(value, inputs, answers) if role == 'format' else _Child(value)
    except BaseException as error:
        _frames.write(answers, 'error', failure(error, folder, format_folder))
        return
    _frames.write(answers, 'ready', {'seconds': time.perf_counter() - began})
    while True:
        try:
            message = _frames.read(inputs)
        except (EOFError, _frames.FrameError):
            return
        if message['kind'] == 'close':
            if role == 'format':
                try:
                    target.call('close', [])
                except BaseException as error:
                    failure(error, folder, format_folder)
            return
        call = message.get('value') or {}
        try:
            result = target.call(call.get('method', ''), list(call.get('arguments') or []))
            encoded = _frames.encode('answer', result)
            if role != 'format' and len(encoded) > ANSWER_BYTES + 4096:
                raise Invalid(f'an answer of {len(encoded)} bytes, the limit is 1 MiB')
        except _frames.FrameError as error:
            found = {'kind': 'format', 'message': f'the answer cannot cross, {error}'}
            if role != 'format':
                found = {'kind': 'invalid', 'message': f'the answer cannot cross, {error}'}
            _frames.write(answers, 'error', found)
            continue
        except BaseException as error:
            _frames.write(answers, 'error', failure(error, folder, format_folder))
            continue
        answers.write(encoded)
        answers.flush()
