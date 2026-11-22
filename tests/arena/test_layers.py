# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Check the arena against the package boundary."""

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('layers', ROOT / 'ci' / 'layers.py')
layers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(layers)


def test_the_arena_uses_only_the_public_engine():
    assert layers.engine_uses(ROOT) == []


def test_the_arena_has_no_renderer_module_names():
    assert layers.render_uses(ROOT) == []


def test_importing_the_arena_keeps_the_renderer_unloaded():
    code = (
        'import sys, cuju.arena; '
        "print([name for name in sys.modules if name.split('.')[:2] == ['cuju', 'render']])"
    )
    result = subprocess.run(
        [sys.executable, '-c', code],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.stdout.strip() == '[]'
