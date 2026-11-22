# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks that recordings of cuju-render 0.1.0 still open, a plain one, one of a challenge run and
one whose teams wear what left.

The writer is the one of 0.1.0, kept in tests/support/frozen, and the header and the group of the
challenge run are those of tests/support/format/v010_challenge_header.json. The page, the
command and the routes of the challenges left with a later release, so the title and the group of
such a recording read as the English text they were saved with.
"""

import json

import cuju.arena
from cuju.arena._format import recording as _recording
from cuju.arena._store import home as _home
from support.common import _ATTEMPT_PLAYERS, _attempt_frames
from support.frozen import reader_010

_TIMING = {'duration': 500, 'second_half': None, 'full_match': False}


def _old_recording(name, title, provenance, extra=None, version=2):
    """Writes a recording into the library with the writer of 0.1.0 and returns its frames."""
    old = reader_010()
    frames = _attempt_frames(goal_at=12, length=30)
    head = old.header(
        title, '1_vs_1_easy', _ATTEMPT_PLAYERS, None, len(frames), _TIMING, provenance
    )
    document = old._document(head)
    # The file names the version of cuju that wrote it, where the provenance gives one.
    document['provenance'].update(provenance)
    document.update(extra or {})
    path = _home.folder('matches') / f'{name}.cjr'
    old.write(path, old._encode_header(document), frames, version)
    return path, frames


def test_a_recording_of_0_1_0_still_opens(arena_home):
    for version in (1, 2):
        name = f'm-20261110-12000{version}-aaaa'
        provenance = {'source': 'requested_match', 'seed': 7}
        path, frames = _old_recording(name, 'Reds against Blues', provenance, version=version)
        header = _recording.read_header(path)
        assert header['version'] == version and header['title'] == 'Reds against Blues'
        assert header['frame_count'] == 30 and header['provenance']['source'] == 'requested_match'
        assert list(_recording.frames(path)) == frames
        assert _recording.ending(path) == (30, frames[-1])
        # A recording copied into the folder alone lists under its title, and the summary that
        # 0.1.0 wrote beside it adds its score and its steps.
        entry = next(item for item in cuju.arena.recordings() if item['id'] == name)
        assert entry['title'] == 'Reds against Blues' and 'group' not in entry
        summary = {**entry, 'score': [1, 0], 'steps': 30}
        path.with_suffix('.json').write_text(json.dumps(summary), encoding='utf-8')
        entry = next(item for item in cuju.arena.recordings() if item['id'] == name)
        assert entry['title'] == 'Reds against Blues' and entry['score'] == [1, 0]
        assert entry['steps'] == 30 and entry['outcome'] == 'win'
