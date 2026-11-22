# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Check the arena's generated references."""

import importlib.util
from pathlib import Path

import cuju.arena

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('make_reference', ROOT / 'ci/make_reference.py')
make = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(make)


def test_the_arena_references_equal_the_public_api_and_command_tree():
    assert make.check() == []


def test_a_parameter_named_with_an_underscore_stays_private():
    """`Evaluation` reads with its identifier alone, as its `_process` belongs to the scheduler."""
    page = (make.DOCS / 'reference.md').read_text(encoding='utf-8')
    assert '`Evaluation(identifier)`' in page
    assert make.signature(cuju.arena.Evaluation, 'Evaluation') == 'Evaluation(identifier)'
