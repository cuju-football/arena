# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Check the arena message and changelog fragments."""

import ast
import importlib.util
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('fragments', ROOT / 'ci/fragments.py')
fragments = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fragments)


def test_the_fragments_of_the_tree_fold():
    assert fragments.check(ROOT) == []
    source = (ROOT / fragments.MESSAGES).read_text(encoding='utf-8')
    assert fragments.fold_messages(source, {}) == source


def test_an_entry_of_the_registry_reads_as_the_formatter_writes_it():
    source = (ROOT / fragments.MESSAGES).read_text(encoding='utf-8')
    node, registry = fragments._registry(source)
    lines = source.split('\n')
    rows = [key.lineno - 1 for key in node.keys] + [node.end_lineno - 1]
    same = 0
    for index, (key, value) in enumerate(registry.items()):
        written = [
            line
            for line in lines[rows[index] : rows[index + 1]]
            if not line.lstrip().startswith('#')
        ]
        made = fragments.entry_lines(key, value)
        assert all(len(line) <= fragments.WIDTH for line in made), key
        assert ast.literal_eval('{' + '\n'.join(made) + '}') == {key: value}
        # A text too long for one line may be cut at another space than the source chose.
        if len(written) == 1 or isinstance(value, dict):
            assert made == written, key
        same += made == written
    assert same >= len(registry) - 20 or len(registry) < 100
    # Double quotes save the escapes of an apostrophe, and other characters keep their escapes.
    assert fragments.entry_lines('a.b', "the team's kit") == ["""    'a.b': "the team's kit","""]
    assert fragments.entry_lines('a.b', 'say "no"') == ["""    'a.b': 'say "no"',"""]
    assert fragments.entry_lines('a.b', 'a\\b\nc') == ["""    'a.b': 'a\\\\b\\nc',"""]


def test_the_arena_folds_its_messages_and_captions(tmp_path):
    paths = (fragments.MESSAGES, fragments.CAPTIONS, fragments.CHANGELOG)
    for name in paths:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    strings = tmp_path / 'strings'
    strings.mkdir()
    fragment = strings / 'sample.json'
    fragment.write_text(
        json.dumps({'server.teams.extra': 'Another team', 'caption.extra': 'Kick off, {team}'}),
        encoding='utf-8',
    )
    assert fragments.fold(tmp_path, in_place=True) == []
    messages = fragments._tables(tmp_path)[fragments.MESSAGES]
    captions = fragments._captions(tmp_path)
    assert messages['server.teams.extra'] == 'Another team'
    assert captions['caption.extra'] == 'Kick off, {team}'
    assert list(captions)[list(captions).index('caption.extra') - 1].startswith('caption.')
    folded = [(tmp_path / name).read_bytes() for name in paths]
    assert fragments.fold(tmp_path, in_place=True) == []
    assert [(tmp_path / name).read_bytes() for name in paths] == folded
    fragment.write_text(json.dumps({'caption.extra': 'A different text'}), encoding='utf-8')
    assert any(fragments.CAPTIONS in line for line in fragments.check(tmp_path))
