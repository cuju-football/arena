# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the sky of a match, its draw, its place in the look and its reading."""

import io

import pytest

import cuju
import cuju.errors
from cuju.arena._format import recording as _recording
from support.common import _names_lineup


def _document(head):
    return _recording._parse_header(io.BytesIO(head))


def test_reader_refuses_a_sky_that_is_no_object():
    document = _document(_recording.header('Match', '5_vs_5', _names_lineup()))
    document['sky'] = 'night'
    with pytest.raises(cuju.errors.StateError, match='sky'):
        _recording.validate_header(document)
