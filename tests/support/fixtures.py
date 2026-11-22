# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Fixtures shared by the arena and renderer tests."""

import pytest

from cuju.arena._store import home as _home

__all__ = ['arena_home', 'isolate_homes']


@pytest.fixture(autouse=True)
def isolate_homes(tmp_path, monkeypatch):
    """Protects the real homes and settings while a test runs."""
    # A test that names no home of its own gets two empty ones, so none reaches the real folders of
    # `~/.cuju`, or the first start that moves the arena's folders out of the renderer's.
    monkeypatch.setenv(_home.HOME_VARIABLE, str(tmp_path / 'default-arena'))
    monkeypatch.setenv('CUJU_RENDER_HOME', str(tmp_path / 'default-render'))
    monkeypatch.delenv('CUJU_ARENA_CONFIG', raising=False)
    monkeypatch.delenv('CUJU_RENDER_CONFIG', raising=False)
    monkeypatch.delenv('CUJU_ARENA_CONFIG_CLI', raising=False)
    monkeypatch.delenv('CUJU_RENDER_CONFIG_CLI', raising=False)


@pytest.fixture
def arena_home(tmp_path, monkeypatch):
    """Moves the arena's home alone, to a folder of the test."""
    monkeypatch.setenv(_home.HOME_VARIABLE, str(tmp_path / 'arena'))
    return tmp_path / 'arena'
