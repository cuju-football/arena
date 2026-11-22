# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the attachments of a recording, the protocol `attachment/1`, and of bundles.

A recording holds the game alone, and its annotations, overlays and look lie in attachments
beside it, each keyed by the digest of the recording. The tests hold the writer to the game, the
key to the recording, the library to keep attachments with their recording, the readers to take
the parts of an older recording as embedded attachments, and a bundle to carry a recording and
its attachments and to read back.
"""

import json
import logging
import shutil
import zipfile

import pytest

import cuju.arena
import cuju.errors
from cuju.arena import plugins as _plugins
from cuju.arena._format import attachments as _attachments
from cuju.arena._format import recording as _recording
from cuju.arena._import import core as _import
from cuju.arena._store import home as _home
from cuju.arena._store import library as _library
from cuju.arena._text.messages import MessageError
from support import plugin_files
from support.common import _sample_head

NOTES = [{'f': 1, 't': 'kick off'}, {'f': 4, 'p': 2, 'v': {'value': 0.5}}]
OVERLAYS = [{'f': 2, 'name': 'pressure', 'kind': 'grid', 'values': [[0.25, 0.5], [0.75, 1.0]]}]
TEAMS = [
    {'name': 'Reds', 'color': [200, 30, 30], 'secondary': [255, 255, 255]},
    {'name': 'Blues', 'style': {'kit': {'primary': '#1d4ed8', 'secondary': '#facc15'}}},
]


def _head(count=30, teams=TEAMS):
    """Returns the header of a short match of five a side with teams, and its frames."""
    head, frames = _sample_head('sample-5v5', count)
    document = _recording._document(head)
    given = _recording.header(
        document['title'],
        document['scenario'],
        document['players'],
        teams,
        count,
        provenance={'seed': 7},
    )
    return given, frames


def _write(path, notes=NOTES, overlays=OVERLAYS, version=2):
    head, frames = _head()
    carried = _recording.Frames(frames, overlays=overlays)
    return _attachments.write_recording(path, head, carried, version, notes), frames


def test_a_wrong_key_is_refused_with_a_message(tmp_path, caplog):
    first, second = tmp_path / 'first.cjr', tmp_path / 'second.cjr'
    _write(first)
    head, frames = _head(teams=[{'name': 'Other'}, {}])
    _attachments.write_recording(second, head, frames)
    # The annotations of the first recording laid beside the second are left out with a note.
    shutil.copyfile(
        _attachments.path_of(first, 'annotations'), _attachments.path_of(second, 'annotations')
    )
    with caplog.at_level(logging.WARNING, logger='cuju'):
        found = _attachments.find(second)
    assert 'annotations' not in found.attachments
    assert [(note['kind'], note['reason']) for note in found.notes] == [('annotations', 'key')]
    assert 'second.annotations.cja' in caplog.text
    assert _attachments.annotations(second) == []
    with pytest.raises(MessageError) as refusal:
        _attachments.load(second, 'annotations')
    assert refusal.value.message_id == 'server.library.attachment_key'
    # A writer that names another key is refused, and one that counts other frames is left out.
    with pytest.raises(MessageError, match='attachment of the recording'):
        _attachments.write(second, 'annotations', 1, b'{}', digest_='sha256:' + '0' * 64)
    other = _attachments.Attachment('annotations', 1, _attachments.digest(second), 99, 'w', '')
    _attachments.path_of(second, 'annotations').write_bytes(_attachments.encode(other, b'{}'))
    assert [note['reason'] for note in _attachments.find(second).notes] == ['frames']
    with pytest.raises(MessageError) as refusal:
        _attachments.load(second, 'annotations')
    assert refusal.value.message_id == 'server.library.attachment_frames'
    # A file that is no attachment is left out as well.
    _attachments.path_of(second, 'annotations').write_bytes(b'not an attachment')
    assert [note['reason'] for note in _attachments.find(second).notes] == ['file']
    with pytest.raises(MessageError) as refusal:
        _attachments.load(second, 'annotations')
    assert refusal.value.message_id == 'server.library.attachment_file'


def test_kinds_and_versions(tmp_path):
    for kind in ('annotations', 'look', 'my-kind', 'plugin.kind', 'a1'):
        assert _attachments.check_kind(kind) == kind
    for kind in ('Look', 'a.b.c', '', 'a b', '../look', 'a_b', 7, None, 'x' * 81):
        with pytest.raises(MessageError):
            _attachments.check_kind(kind)
    # A reader passes over a newer major of a kind it reads.
    path = tmp_path / 'match.cjr'
    written, _ = _write(path)
    newer = _attachments.Attachment('annotations', 2, written.key, 30, 'later', '')
    _attachments.path_of(path, 'annotations').write_bytes(_attachments.encode(newer, b'[]'))
    assert _attachments.find(path).attachments['annotations'].version == 2
    assert _attachments.annotations(path) == []


def test_producers_are_plugins_and_a_failing_one_leaves_its_kind_out(arena_home, caplog):
    # The builtin plugin offers producers for the arena.
    found = _attachments.producers()
    assert found['annotations'].plugin == 'builtin' and found['overlays'].plugin == 'builtin'
    assert 'look' not in found
    assert 'attachment' in _plugins.KINDS and _plugins.KINDS['attachment'].major == 1
    # A plugin of the kind attachment/1 writes the attachment of its qualified name.
    code = """
        import json

        class Counted:
            version = 3

            def produce(self, context):
                return json.dumps({'frames': context.header['frame_count']}).encode()

        class Broken:
            def produce(self, context):
                raise ValueError('the producer broke')

        def register(registry):
            registry.provide('attachment/1', 'counted', Counted())
            registry.provide('attachment/1', 'broken', Broken())
        """
    provides = {'attachment/1': ['counted', 'broken']}
    plugin_files.written(arena_home / 'plugins', 'acme', provides, code)
    _plugins.refresh()
    try:
        path = arena_home / 'match.cjr'
        with caplog.at_level(logging.WARNING, logger='cuju'):
            written, frames = _write(path)
        assert 'acme.counted' in written.kinds and 'acme.broken' not in written.kinds
        assert 'acme.broken' in caplog.text and 'the producer broke' in caplog.text
        counted = _attachments.read(_attachments.path_of(path, 'acme.counted'))
        assert counted.version == 3 and counted.writer == 'acme'
        assert json.loads(counted.body) == {'frames': 30}
        assert list(_recording.frames(path)) == frames
        # The plugin whose producer raised is marked failing, and its other producer still writes.
        assert 'acme.broken' in _plugins.failing('acme')['message']
        other = arena_home / 'other.cjr'
        _write(other)
        assert 'acme.counted' in _attachments.files(other)
    finally:
        _plugins.refresh()


def test_the_library_keeps_attachments_with_their_recording(arena_home, tmp_path):
    head, frames = _head()
    saved = _library.save(head, _recording.Frames(frames, overlays=OVERLAYS), annotations=NOTES)
    identifier = saved.stem
    summary = _library.details(identifier)
    assert summary['digest'] == _attachments.digest(saved)
    assert {'annotations', 'overlays'} <= set(summary['attachments'])
    assert {'ANNO', 'GRID'} <= set(summary['has'])
    kinds = set(_attachments.files(saved))
    # A new title, a folder, the Trash and back, and a group leave the attachments in place.
    _library.update(identifier, title='Renamed', folder='League/Week 1', tags=['kept'])
    assert _library.trash(identifier) and _library.trash(identifier, restore=True)
    _library.set_group({'id': 'g-1', 'title': 'A group'}, {identifier: 0})
    assert cuju.arena.delete_group('g-1') == [identifier]
    assert cuju.arena.restore_group('g-1') == [identifier]
    assert set(_attachments.files(saved)) == kinds
    assert _attachments.annotations(saved) == NOTES
    # A copy of the file from elsewhere brings the attachments that fit it.
    outside = tmp_path / 'outside'
    outside.mkdir()
    copy = outside / 'match.cjr'
    shutil.copyfile(saved, copy)
    for kind, file in _attachments.files(saved).items():
        shutil.copyfile(file, _attachments.path_of(copy, kind))
    assert _library.remove(identifier)
    assert _attachments.files(saved) == {}
    added = _library.import_file(copy)
    assert set(_attachments.files(_library.path_of(added))) == kinds
    assert _library.details(added)['attachments'] == sorted(kinds)
    # Deleting a recording of the Trash for good deletes its attachments.
    assert _library.trash(added) and _library.purge(added)
    assert list(_home.folder('matches').glob(f'{added}.*')) == []


def test_a_bundle_carries_a_recording_and_its_attachments_and_reads_back(arena_home, tmp_path):
    head, frames = _head()
    saved = _library.save(head, _recording.Frames(frames, overlays=OVERLAYS), annotations=NOTES)
    data = _library.bundle(saved.stem)
    game = saved.read_bytes()
    bundle = tmp_path / 'match.cjz'
    bundle.write_bytes(data)
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        index = json.loads(archive.read('bundle.json'))
        assert archive.read('recording.cjr') == game
    assert names[:2] == ['bundle.json', 'recording.cjr']
    assert {'attachments/annotations.cja', 'attachments/overlays.cja'} <= set(names)
    assert index['bundle'] == 1 and index['recording'] == _attachments.digest(saved)
    assert _import.kind_of(bundle) == 'cjz' and _attachments.is_bundle(bundle)
    # The import reads it back into the library, the same recording, once.
    assert _library.remove(saved.stem)
    entries = cuju.arena.add_recordings(bundle)
    assert [entry['source'] for entry in entries] == ['cjz']
    path = _library.path_of(entries[0]['id'])
    assert path.read_bytes() == game
    assert _attachments.annotations(path) == NOTES
    assert {'annotations', 'overlays'} <= set(_library.details(path.stem)['attachments'])
    again = cuju.arena.add_recordings(bundle)
    assert again[0]['id'] == entries[0]['id']
    # A bundle whose attachment belongs to another recording is refused whole.
    other = tmp_path / 'other.cjz'
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(other, 'w') as archive:
        for name in source.namelist():
            archive.writestr(name, source.read(name))
        stranger = _attachments.Attachment('extra', 1, 'sha256:' + '1' * 64, 30, 'w', '')
        archive.writestr('attachments/extra.cja', _attachments.encode(stranger, b'{}'))
    with pytest.raises(MessageError) as refusal:
        _library.import_bundle(other)
    assert refusal.value.message_id == 'server.library.attachment_key'
    with pytest.raises(cuju.errors.ArgumentError, match='attachment'):
        cuju.arena.add_recordings(other)
    for broken in (b'PK\x03\x04 not a zip', b'plain bytes'):
        (tmp_path / 'broken.cjz').write_bytes(broken)
        with pytest.raises(cuju.errors.ArgumentError):
            _library.import_bundle(tmp_path / 'broken.cjz')


def test_recordings_export_writes_the_library_bundle(arena_home, tmp_path, capsys):
    from cuju.arena.__main__ import main

    head, frames = _head()
    saved = _library.save(head, frames, annotations=NOTES)
    bundle = tmp_path / 'match.cjz'
    command = ['recording', 'export', saved.stem, '-o', str(bundle)]
    assert main([*command, '--json']) == 0
    assert json.loads(capsys.readouterr().out) == {
        'schema': 'arena.recording.export/1',
        'files': [str(bundle)],
    }
    with zipfile.ZipFile(bundle) as archive:
        assert archive.read('recording.cjr') == saved.read_bytes()
        attachment = _attachments.files(saved)['annotations']
        assert archive.read('attachments/annotations.cja') == attachment.read_bytes()
    assert main(command[:-1]) == 2
    assert 'expected one argument' in capsys.readouterr().err
