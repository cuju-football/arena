# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of Compare: `cuju.arena.compare`, its command, its route and the pair of samples."""

import hashlib

import pytest

from cuju.arena._format import recording as _recording
from cuju.arena._store import library as _library
from cuju.arena._store import samples as _samples

# The SHA-256 of the frames of each recording of the pair, as for the other samples in
# tests/test_recordings.py.
_PAIR_DIGESTS = {
    'sample-5v5-a': 'f3c8bddcd8121b9737684a978d0eb156a2262458d7ef4e84e0378078322ba3e0',
    'sample-5v5-b': '1fc2de25867bc3d268687c4e5b9c6cfaf6e73e0feb0f83f55c9e14a51ebbdb89',
}


@pytest.mark.parametrize('name', sorted(_PAIR_DIGESTS))
def test_the_pair_of_samples_holds_every_section_and_decodes_to_its_frames(name):
    path = _samples.pair_path(name)
    document = _recording.read_header(path)
    frames = list(_recording.frames(path))
    assert hashlib.sha256(b''.join(frames)).hexdigest() == _PAIR_DIGESTS[name]
    assert document['version'] == 2 and len(frames) == document['frame_count'] == 3001
    assert document['sections'] == ['ANNO', 'PLYR', 'ACTS', 'STAT']
    assert document['run_key']['agents'] == [1, 0] and document['action_set'] == 'v2'
    # The kick off of the file is the state that its run key names.
    assert _recording.state(path)
    # The pair stays out of the samples that the library receives at its first start.
    assert name not in _library.samples() and name not in _samples.names()


def test_the_pair_shares_its_seed_its_kick_off_and_its_sky():
    a, b = (_recording.read_header(_samples.pair_path(name)) for name in _samples.PAIR)
    keys = [dict(header['run_key']) for header in (a, b)]
    assert keys[0] == keys[1]
    assert a['sky'] == b['sky'] and a['teams'] == b['teams']
    assert a['title'] != b['title']
