# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the settings of each package and of the two home folders."""

import pytest

from cuju.arena._config import layers as _layers
from cuju.errors import ConfigurationError


def test_the_plugins_table_lists_the_disabled_ones_as_texts(tmp_path):
    assert _layers.config()['plugins.disabled'] == ()
    assert _layers.config(overrides='plugins.disabled=["a", "b"]')['plugins.disabled'] == (
        'a',
        'b',
    )
    with pytest.raises(ConfigurationError):
        _layers.config(overrides='plugins.disabled=[1]')
