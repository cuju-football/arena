# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The reader of recordings of cuju-render 0.1.0, kept as it shipped.

`recording_010.py`, `codec_010.py` and `annotations_010.py` are the modules `recording.py`,
`codec.py` and `annotations.py` of `src/cuju/render/_format` at the tag `v0.1.0`, byte for byte.
The tests read the files of the current writer through them, so a recording that the current
release writes stays one that the first release opens. The copies stay as they are, and a later
release adds its own beside them.
"""

import functools
import importlib.util
import sys
import types
from pathlib import Path

from cuju.arena._text import messages as _messages

_HERE = Path(__file__).parent
_PACKAGE = 'cuju_render_010'


def _package(name):
    """Returns an empty package of `name`, which the copies import each other through."""
    module = types.ModuleType(name)
    module.__path__ = []
    sys.modules[name] = module
    return module


def _load(package, name):
    """Loads the copy `<name>_010.py` as the module `name` of the package of the format."""
    full = f'{package.__name__}.{name}'
    spec = importlib.util.spec_from_file_location(full, _HERE / f'{name}_010.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    setattr(package, name, module)
    return module


@functools.cache
def reader_010():
    """Returns the module `recording` of cuju-render 0.1.0, with its codec and annotations.

    The copy imports its codec and its annotations from the copies beside it. The names of the
    messages come from the current release, since the reader uses them for the labels of titles
    and team names alone.
    """
    root = _package(_PACKAGE)
    text = _package(f'{_PACKAGE}._text')
    text.messages = _messages
    sys.modules[f'{_PACKAGE}._text.messages'] = _messages
    root._text = text
    package = _package(f'{_PACKAGE}._format')
    root._format = package
    _load(package, 'codec')
    _load(package, 'annotations')
    return _load(package, 'recording')
