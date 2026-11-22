# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the team manifest, the identity of a team, its two versions and the block `kept`."""

import copy
import json

import pytest

from cuju.arena._registry import manifest as _manifest

_ROSTER = [{'name': 'Keeper', 'number': 1}, {'name': 'Playmaker-v3', 'number': 10}]
_TEAM = {
    'name': 'Redwood',
    'created': '2026-11-17T14:02:00',
    'format': '5v5',
    'scenario': '5_vs_5_counterattack',
    'roster': _ROSTER,
}
_DIGEST = 'ab' * 32


def _document(**changes):
    document = _manifest.document(_TEAM, {}, _DIGEST)
    document.update(changes)
    return document


def test_a_manifest_reads_back_what_it_wrote():
    """The second version holds the identity alone and reads back to the same team."""
    document = _document()
    assert document == {
        'identity': 2,
        'name': 'Redwood',
        'created': '2026-11-17T14:02:00',
        'size': '5v5',
        'roster': _ROSTER,
        'ground': {'checkpoint': _DIGEST},
        'kept': {},
        'scenario': '5_vs_5_counterattack',
    }
    reading = _manifest.read(json.loads(json.dumps(document)))
    assert reading['version'] == 2
    assert reading['team'] == _TEAM
    assert reading['ground'] == _DIGEST
    assert reading['refused'] == {} and reading['kept'] == {}
    # The default home scenario of the size is never written.
    assert 'scenario' not in _manifest.document({**_TEAM, 'scenario': '5_vs_5'}, {}, None)


@pytest.mark.parametrize(
    ('path', 'value', 'field'),
    [
        ('roster.0.number', 120, ('roster', 0, 'number')),
        ('roster.1.name', 'x' * 25, ('roster', 1, 'name')),
        ('size', '7v7', ('format',)),
        ('scenario', '../5_vs_5', ('scenario',)),
        ('created', 5, ('created',)),
    ],
)
def test_a_manifest_with_one_bad_value_loses_only_that_value(path, value, field):
    """One value out of its choices falls back alone, and the file keeps it under `kept`."""
    document = _document()
    target = document
    parts = path.split('.')
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    target[parts[-1]] = value
    reading = _manifest.read(document)
    assert reading['refused'] == {path: value}
    assert reading['kept'] == {'refused': {path: value}}
    expected = copy.deepcopy(_TEAM)
    holder = expected
    for part in field[:-1]:
        holder = holder[part]
    del holder[field[-1]]
    assert reading['team'] == expected


def test_unread_keys_are_kept_word_for_word_through_a_write():
    """Keys of a later version, at any depth, travel on in `kept` and back out on the next read.

    The look keys that a draft of this version wrote, before the look moved to `cuju-look`, are
    such keys too, and so is a player's look.
    """
    document = _document(stadium={'name': 'Redwood Park'}, code='RDW')
    document['wardrobe'] = {'uniform': {'shirt': '#d63031'}}
    document['roster'][0]['skin'] = 'tan'
    document['kept'] = {'style': {'crowd': {'colors': [[1, 2, 3]]}}}
    reading = _manifest.read(document)
    assert reading['refused'] == {}
    assert reading['kept'] == {
        'style': {'crowd': {'colors': [[1, 2, 3]]}},
        'stadium': {'name': 'Redwood Park'},
        'code': 'RDW',
        'wardrobe': {'uniform': {'shirt': '#d63031'}},
        'roster': [{'skin': 'tan'}, {}],
    }
    written = _manifest.document(_TEAM, reading['kept'], None)
    assert _manifest.read(written)['kept'] == reading['kept']
    assert _manifest.unread_keys(document) == ['code', 'roster.0.skin', 'stadium', 'wardrobe']


def test_the_file_of_0_1_0_reads_its_identity_and_keeps_its_look():
    """The file of 0.1.0 gives its names and numbers, and its look stays in `kept` whole."""
    style = {
        'model': 'grf',
        'kit': {'pattern': 'stripes', 'primary': [300, 0, 0]},
        'badge': 'OLD',
        'crowd': {'colors': [[1, 2, 3]]},
        'players': [
            {'name': 'Keeper', 'number': 1, 'skin': 'tan', 'model': 'custom'},
            {'name': 'Nine', 'number': 120, 'hair': 'long01'},
        ],
    }
    old = {
        'id': 't-old',
        'kind': 'kaggle',
        'name': 'Old FC',
        'format': '11v11',
        'style': style,
        'model': {'joints': {'body': 'Hips'}, 'bytes': 5},
        'notes': 'kept',
    }
    reading = _manifest.read(old)
    assert reading['version'] == 1
    assert reading['team'] == {
        'name': 'Old FC',
        'format': '11v11',
        'roster': [{'name': 'Keeper', 'number': 1}, {'name': 'Nine'}],
    }
    assert reading['refused'] == {'style.players.1.number': 120}
    assert reading['kept'] == {
        'notes': 'kept',
        'model': {'joints': {'body': 'Hips'}, 'bytes': 5},
        'style': {
            'model': 'grf',
            'kit': {'pattern': 'stripes', 'primary': [300, 0, 0]},
            'badge': 'OLD',
            'crowd': {'colors': [[1, 2, 3]]},
            'players': [{'skin': 'tan', 'model': 'custom'}, {'hair': 'long01'}],
        },
        'refused': {'style.players.1.number': 120},
    }
    # The keys that 0.1.0 wrote itself are known, so only the hand-written one is named.
    assert _manifest.unread_keys(old) == ['notes']


def test_the_size_reads_from_size_and_from_its_old_spelling_format():
    """A team file names its size `size`, and `format`, the spelling of 0.1.0 and of drafts of this
    version, reads the same where `size` is absent. A file that holds both reads `size` and keeps
    `format` word for word, and a save writes `size` alone."""
    document = _document()
    old = {key: value for key, value in document.items() if key != 'size'}
    old['format'] = '5v5'
    reading = _manifest.read(old)
    assert reading['team']['format'] == '5v5' and reading['kept'] == {}
    assert _manifest.unread_keys(old) == []
    written = _manifest.document(reading['team'], reading['kept'], None)
    assert written['size'] == '5v5' and 'format' not in written
    both = _manifest.read({**document, 'format': '11v11'})
    assert both['team']['format'] == '5v5' and both['kept'] == {'format': '11v11'}
    wrong = _manifest.read({**old, 'format': '7v7'})
    assert 'format' not in wrong['team'] and wrong['refused'] == {'format': '7v7'}
    first = _manifest.read({'name': 'Old FC', 'format': '5v5', 'style': {'players': []}})
    assert first['version'] == 1 and first['team']['format'] == '5v5'


def test_control_is_checked_by_the_registry_and_refused_alone():
    """The registry's check of control decides, and a value it refuses goes to `kept` alone."""

    def check(value, format):
        if value != {'players': 1}:
            raise ValueError('expected one player')
        return {**value, 'format': format}

    good = _manifest.read(_document(control={'players': 1}), control=check)
    assert good['team']['control'] == {'players': 1, 'format': '5v5'}
    bad = _manifest.read(_document(control={'players': 'all'}), control=check)
    assert 'control' not in bad['team'] and bad['refused'] == {'control': {'players': 'all'}}
    assert bad['team']['roster'] == _ROSTER


def test_versions_and_files_that_no_version_reads():
    """A later version reads as far as this one understands, and a file that is no team fails."""
    later = _document(identity=3, stands=2)
    reading = _manifest.read(later)
    assert reading['version'] == 3 and reading['team']['name'] == 'Redwood'
    assert reading['kept'] == {'stands': 2}
    for document in ([1], 'team', None):
        with pytest.raises(ValueError):
            _manifest.read(document)
    deep = {}
    inner = deep
    for _ in range(_manifest.DEEPEST + 1):
        inner['a'] = {}
        inner = inner['a']
    with pytest.raises(ValueError, match='deeply'):
        _manifest.read(deep)


def test_a_ground_that_fails_its_check_is_refused_alone():
    """The checkpoint that the home ground draws from is a digest, and another value is refused."""
    reading = _manifest.read(_document(ground={'checkpoint': 'short'}))
    assert reading['refused'] == {'ground.checkpoint': 'short'}
    assert reading['ground'] is None
    assert _manifest.read(_document(ground={}))['refused'] == {}
    assert _manifest.read(_document(ground=7))['refused'] == {'ground': 7}
