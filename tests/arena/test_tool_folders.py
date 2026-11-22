# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks that the folders `cuju-*` of a team stay out of its digests, ids and ground seeds.

A team without such a folder keeps the checkpoint id and the ground seed that every earlier
version computed, held here as literals. A folder `cuju-look` beside `cuju-team` changes neither.
"""

import hashlib

import pytest

from cuju.arena._formats import side as _side
from cuju.arena._registry import checkpoints as _checkpoints
from cuju.arena._registry import teams as _teams
from cuju.arena._store import home as _home
from cuju.arena._store import runs as _runs
from cuju.arena.formats.builtin import strict as _strict

FILES = {'main.py': b'def agent(obs):\n    return [1]\n', 'lib/util.py': b'X = 1\n'}
# The digest of those files, written out by the rule that every version has used.
DIGEST = 'bf5f041371471234a326e440932e3e1c5e5764b92101c958b1ceb7b7b09457d8'


def _reference(files):
    digest = hashlib.sha256()
    for name in sorted(files):
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(4, 'little') + encoded)
        digest.update(len(files[name]).to_bytes(8, 'little') + files[name])
    return digest.hexdigest()


def _write(folder, files):
    for name, data in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)


def _team(arena_home, tmp_path, look=False):
    source = tmp_path / 'source'
    source.mkdir()
    _write(source, FILES)
    team = _teams.add_folder(source, 'toy', '11v11')
    folder = _home.folder('teams') / team['id']
    if look:
        _write(folder / 'cuju-look', {'look.json': b'{"look": 1}\n', 'crest.png': b'\x89PNG'})
        _write(folder / 'cuju-other' / 'deep', {'a.bin': b'a', 'b.bin': b'b'})
    return team['id'], folder


def test_the_reference_digest_is_the_literal():
    assert _reference(FILES) == DIGEST


def test_a_look_folder_changes_no_digest_id_or_seed(arena_home, tmp_path):
    identifier, folder = _team(arena_home, tmp_path)
    before = (
        _runs.files_digest(folder, ('cuju-team',)),
        _runs.files_digest(folder),
        _runs.team_checkpoint(identifier),
        _checkpoints.current_id(identifier),
        _checkpoints._measure(folder),
        _strict.folder_digest(identifier),
        _teams.first_checkpoint(identifier),
    )
    _write(folder / 'cuju-look', {'look.json': b'{"look": 1}\n', 'crest.png': b'\x89PNG'})
    _write(folder / 'cuju-other' / 'deep', {'a.bin': b'a'})
    after = (
        _runs.files_digest(folder, ('cuju-team',)),
        _runs.files_digest(folder),
        _runs.team_checkpoint(identifier),
        _checkpoints.current_id(identifier),
        _checkpoints._measure(folder),
        _strict.folder_digest(identifier),
        _teams.first_checkpoint(identifier),
    )
    assert after == before
    assert after[0] == DIGEST


def test_a_file_named_like_a_tool_folder_still_counts(tmp_path):
    folder = tmp_path / 'team'
    folder.mkdir()
    _write(folder, {'main.py': b'x', 'cuju-notes.txt': b'y'})
    assert _runs.files_digest(folder) == _reference({'main.py': b'x', 'cuju-notes.txt': b'y'})


def test_the_copies_of_a_submission_leave_tool_folders_out(arena_home, tmp_path):
    _, folder = _team(arena_home, tmp_path, look=True)
    copy = _side.frozen(folder)
    try:
        assert sorted(path.name for path in copy.iterdir()) == ['lib', 'main.py']
    finally:
        _side.thaw(copy)
    target = tmp_path / 'target'
    _strict.frozen_copy_of(folder, target)
    assert sorted(path.name for path in target.iterdir()) == ['lib', 'main.py']


def test_a_swap_leaves_tool_folders_in_place(arena_home, tmp_path):
    identifier, folder = _team(arena_home, tmp_path, look=True)
    incoming = folder / 'cuju-team' / 'incoming-test'
    incoming.mkdir(parents=True)
    _write(incoming, {'main.py': b'def agent(obs):\n    return [2]\n'})
    document = {'current': DIGEST[:16]}
    _checkpoints._swap(identifier, document, incoming, 'f' * 16)
    assert (folder / 'cuju-look' / 'look.json').is_file()
    assert (folder / 'cuju-other' / 'deep' / 'a.bin').is_file()
    assert not (folder / 'cuju-team' / 'checkpoints' / DIGEST[:16] / 'cuju-look').exists()
    assert (folder / 'main.py').read_bytes().endswith(b'[2]\n')


@pytest.mark.parametrize('name', ['cuju-look', 'cuju-team', 'cuju-x'])
def test_tool_part_names_folders_only(name):
    assert _runs.tool_part((name, 'a'))
    assert not _runs.tool_part((name,))
