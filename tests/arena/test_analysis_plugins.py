# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Checks the analysis protocols through recordings, search, export and HTTP."""

import shutil

import pytest

from cuju.arena import plugins, statistics
from cuju.arena._moments import search
from cuju.arena._text.messages import MessageError
from support import analysis_cases
from support.plugin_files import entry, written

FIXTURE = analysis_cases.RECORDINGS[-1]
PROVIDES = {
    'moments/1': ['marks'],
    'statistics/1': ['notes'],
    'analytics/1': ['notes'],
}
CODE = """
class Marks:
    def detect(self, recording):
        assert recording.sections['ANNO']
        return [{'kind': 'marker', 'step': 1, 'player': 0, 'data': {'score': []}}]

class Notes:
    calls = 0

    def compute(self, recording):
        self.calls += 1
        return {'notes': {'label': 'Notes', 'unit': 'records', 'value': len(recording.annotations)}}

class Table:
    def table(self, recording, options):
        return [('recording', 'text', [options['recording']]),
                ('notes', 'int', [len(recording.annotations)])]

def register(registry):
    registry.provide('moments/1', 'marks', Marks())
    registry.provide('statistics/1', 'notes', Notes())
    registry.provide('analytics/1', 'notes', Table())
"""


@pytest.fixture
def library(arena_home):
    matches = arena_home / 'matches'
    matches.mkdir(parents=True)
    for name in ('fixture', 'same'):
        shutil.copyfile(FIXTURE, matches / f'{name}.cjr')
    yield arena_home
    plugins.refresh()


def _install(source, tmp_path, monkeypatch, home, code=CODE):
    if source == 'entry point':
        declaration = {'id': 'sideline', 'version': '1.2', 'provides': PROVIDES}
        entry(tmp_path, monkeypatch, 'sideline', module=f'PLUGIN = {declaration!r}\n' + code)
        return None
    folder = written(home / 'plugins', 'sideline', PROVIDES, code)
    manifest = folder / 'plugin.toml'
    manifest.write_text(
        'version = "1.2"\n' + manifest.read_text(encoding='utf-8'), encoding='utf-8'
    )
    return folder


def _select(home):
    (home / 'arena.toml').write_text('[search]\ndetectors = ["sideline.marks"]\n', encoding='utf-8')


def test_signal_queries_use_the_selected_detectors():
    terms = search.parse('surprise')
    signals = {'unlikely': {'0': [[2, 0.05]]}}
    settings = search.thresholds()
    assert search.signal_moments(terms, signals, 10.0, settings)[0]['id'] == 'surprise-2-0'
    assert search.signal_moments(terms, signals, 10.0, {**settings, 'detectors': ['cuju']}) == []


def test_edits_invalidate_the_moments_index_and_statistics_cache(library, tmp_path, monkeypatch):
    folder = _install('folder', tmp_path, monkeypatch, library)
    _select(library)
    assert search.entry('fixture')['moments'][0]['step'] == 1
    assert statistics('fixture', name='sideline.notes')['notes']['value'] == 2
    (folder / 'plugin.py').write_text(
        CODE.replace("'step': 1", "'step': 10").replace('len(recording.annotations)', '22'),
        encoding='utf-8',
    )
    assert search.entry('fixture')['moments'][0]['step'] == 10
    assert statistics('fixture', name='sideline.notes')['notes']['value'] == 22
    with pytest.raises(MessageError) as refused:
        search.search('kind:sideline.marker', overrides='plugins.disabled=["sideline"]')
    assert refused.value.message_id == 'server.plugins.disabled'


def test_an_annotation_attachment_refreshes_plugins_for_the_same_game(
    library, tmp_path, monkeypatch
):
    from cuju.arena._format import attachments
    from cuju.arena._store import library as recordings

    _install('folder', tmp_path, monkeypatch, library)
    _select(library)
    name = 'sideline.notes'
    assert statistics('fixture', name=name)['notes']['value'] == 2
    before = search.entry('fixture')['signature']
    path = recordings.path_of('fixture')
    game = path.read_bytes()
    kind = attachments.ANNOTATIONS
    notes = [{'f': 1, 't': 'replacement annotation'}]
    attachments.write(path, kind, 1, attachments.body(kind, notes))
    assert path.read_bytes() == game
    assert statistics('fixture', name=name)['notes']['value'] == 1
    after = search.entry('fixture')
    assert after['signature'] != before and 'ANNO' in after['has']
