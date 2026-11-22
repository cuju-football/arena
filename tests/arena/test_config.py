# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the layered settings, their files, their shipped defaults and their command."""

import json
import os
import pickle
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from cuju.arena._config import files as _files
from cuju.arena._config import layers as _layers
from cuju.arena._config import schema as _schema
from cuju.arena._config.layers import FROM_CONFIG, Config, config, resolve, table
from cuju.arena._store import home as _home
from cuju.errors import ChoiceError, ConfigurationError, MissingFileError

ROOT = Path(__file__).resolve().parents[2]

# The shipped tables follow the folder of defaults, so a new table needs no change here. Their
# documented values sit in one file per table, which a new table adds beside the others.
SHIPPED = sorted(path.stem for path in _schema.FOLDER.glob('*.toml'))
DOCUMENTED = ROOT / 'tests' / 'support' / 'defaults' / 'arena'


def test_two_adopters_leave_one_complete_arena_home(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'render'
    target = tmp_path / 'arena'
    matches = source / 'matches'
    matches.mkdir(parents=True)
    (matches / 'sample.cjr').write_bytes(b'match')
    (source / 'render.toml').write_text('[recorder]\nevery = 2\n', encoding='utf-8')
    ready = Barrier(2)
    tables = _home._arena_tables

    def read(path):
        found = tables(path)
        ready.wait(timeout=10)
        return found

    monkeypatch.setattr(_home, '_arena_tables', read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        callers = [pool.submit(_home.adopt, target, source) for _ in range(2)]
        assert sorted(caller.result(timeout=10) for caller in callers) == [
            [],
            ['matches', 'the tables of render.toml'],
        ]

    assert (target / 'matches' / 'sample.cjr').read_bytes() == b'match'
    assert (target / 'arena.toml').read_text(encoding='utf-8') == '[recorder]\nevery = 2\n'
    assert (source / 'render.toml.bak').read_text(encoding='utf-8') == '[recorder]\nevery = 2\n'
    assert len(capsys.readouterr().err.splitlines()) == 1


def _leaves(node, path=''):
    """Returns the keys of a table with the tables they sit in, each entry of a list of tables
    by its index, as `Config.sources` names them."""
    if isinstance(node, dict):
        found = {}
        for key, entry in node.items():
            found.update(_leaves(entry, f'{path}.{key}' if path else key))
        return found
    if isinstance(node, list) and node and all(isinstance(entry, dict) for entry in node):
        found = {}
        for index, entry in enumerate(node):
            found.update(_leaves(entry, f'{path}.{index}'))
        return found
    return {path: node}


def _one_of(names):
    """Returns names as the refusals list them, as in `a, b and c`."""
    return ', '.join(names[:-1]) + ' and ' + names[-1]


# A table that later streams bring, with a table of its own, an open table and a list of tables.
TRIAL = """
[trial]
name = "none"
episodes = 100
tables = ["tracking", "events"]
policy_options = {}

[trial.batch]
representation = "simple115v2"

[trial.record]
keep = "losses"
limit = 32

[[trial.opponents]]
kind = "builtin"
difficulty = 0.05
[[trial.opponents]]
kind = "builtin"
difficulty = 1.0
team = "none"

[accepts.trial]
name = { type = "string" }
episodes = { minimum = 1 }
tables = { items = { choices = ["tracking", "events", "spadl"] } }

[accepts.trial.batch]
"*" = { type = "any" }

[accepts.trial.record]
keep = { choices = ["all", "none", "losses"] }

[accepts.trial.opponents]
kind = { choices = ["builtin", "team"] }
difficulty = { minimum = 0.0, maximum = 1.0 }
team = { type = "string" }
"""


@pytest.fixture(autouse=True)
def alone(arena_home, monkeypatch):
    """Keeps every test to a home folder of its own and away from the variable of the machine."""
    monkeypatch.delenv(_layers.CONFIG_VARIABLE, raising=False)
    return arena_home


@pytest.fixture
def defaults(tmp_path, monkeypatch):
    """Returns a copy of the shipped defaults that the settings read, for a test to add to."""
    folder = tmp_path / 'defaults'
    shutil.copytree(_schema.FOLDER, folder)
    monkeypatch.setattr(_layers.ARENA, 'defaults_folder', folder)
    return folder


@pytest.fixture
def trial(defaults):
    (defaults / 'trial.toml').write_text(TRIAL, encoding='utf-8')
    return defaults


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def _refusal(call, kind=ConfigurationError):
    with pytest.raises(kind) as caught:
        call()
    return str(caught.value)


# The shipped defaults.


def test_the_shipped_defaults_resolve_to_the_documented_values():
    settings = config()
    assert settings.layers == ('defaults',)
    assert list(settings) == SHIPPED
    # Each table has its documented values in a file of its own, and a value that changes there
    # without a change of that file fails here.
    files = sorted(path.stem for path in DOCUMENTED.glob('*.json'))
    assert files == SHIPPED
    documented = {
        name: json.loads((DOCUMENTED / f'{name}.json').read_text(encoding='utf-8'))
        for name in SHIPPED
    }
    assert settings.to_dict() == documented
    assert documented['recorder']['min_reward'] is None and documented['evaluate']['settle'] == 0.9
    assert set(settings.sources().values()) == {'defaults'}


def test_each_defaults_file_holds_its_table_with_every_key_commented():
    paths = sorted(_schema.FOLDER.iterdir())
    assert [path.suffix for path in paths] == ['.toml'] * len(paths)
    # The first file by name tells how the folder is read, and the others point at it.
    first = paths[0].read_text(encoding='utf-8')
    for word in ('arena.toml', 'CUJU_ARENA_CONFIG', 'overrides', '"none"', 'accepts', 'choices'):
        assert word in first.split('[', 1)[0], word
    for path in paths:
        text = path.read_text(encoding='utf-8')
        assert path == paths[0] or text.startswith(f'# The top of {paths[0].name} tells'), path
        table_text = text.split(f'[{path.stem}]\n', 1)[1].split(f'[accepts.{path.stem}')[0]
        lines = table_text.splitlines()
        keys = [index for index, line in enumerate(lines) if re.match(r'[a-z_]+ = ', line)]
        assert keys, path
        for index in keys:
            assert lines[index - 1].startswith('# ') if index else False, (path, lines[index])
        # A comment is prose in words, commas and periods, with code and quoted values.
        for line in text.splitlines():
            if line.startswith('#'):
                assert not re.search(r'[;:()!?]|--| - ', line), (path, line)


def test_a_guide_names_every_table_and_key_of_the_shipped_defaults():
    guides = '\n'.join(path.read_text(encoding='utf-8') for path in (ROOT / 'docs').rglob('*.md'))
    for name, keys in _schema.load().defaults.items():
        assert f'[{name}]' in guides, name
        for key in keys:
            assert f'`{key}`' in guides, f'{name}.{key}'


def test_python_before_3_11_gets_a_reader_of_toml():
    text = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    assert re.search(r'"tomli>=2; python_version < \'3\.11\'"', text)


# The layers and their order.


def test_layers_apply_in_order_and_source_names_the_layer_of_each_key(alone, tmp_path, monkeypatch):
    home = _write(_home.home() / 'arena.toml', '[recorder]\nevery = 2\nkeep = "goals"\n')
    first = _write(tmp_path / 'first.toml', '[recorder]\nkeep = "shot"\nversion = 2\n')
    second = _write(tmp_path / 'second.json', '{"recorder": {"version": 1}, "benchmark": {}}')
    call = _write(tmp_path / 'call.toml', '[recorder]\ncapture = "actions"\n[search]\nwindow = 3\n')
    monkeypatch.setenv(_layers.CONFIG_VARIABLE, os.pathsep.join([str(first), '', str(second)]))
    settings = config(call, overrides=['search.surprise=0.2', 'recorder.capture=state'])
    files = (str(home), str(first), str(second), str(call))
    assert settings.layers == ('defaults', *files, 'overrides')
    recorder = settings['recorder']
    assert (recorder['every'], recorder['keep'], recorder['version']) == (2, 'shot', 1)
    assert recorder['capture'] == 'state' and recorder['inspector'] == 'auto'
    assert settings['search'] == {
        'value_drop': 0.3,
        'window': 3.0,
        'surprise': 0.2,
        'detectors': ('cuju', 'value_drop', 'surprise', 'entropy'),
    }
    assert settings.source('recorder.every') == str(home)
    assert settings.source('recorder.keep') == str(first)
    assert settings.source('recorder.version') == str(second)
    assert settings.source('recorder.capture') == 'overrides'
    assert settings.source('recorder.inspector') == 'defaults'
    assert settings.source('search.window') == str(call)
    # A table answers with the highest layer that set one of its keys.
    assert settings.source('recorder') == 'overrides' and settings.source('benchmark') == 'defaults'
    assert settings.source('annotations') == 'defaults'
    assert settings.sources()['search.surprise'] == 'overrides'
    assert len(settings.sources()) == len(_leaves(settings.to_dict()))
    assert 'expected a table or a key' in _refusal(lambda: settings.source('recorder.kept'))
    # A later file wins over an earlier one of the same call.
    assert config(first, second)['recorder.version'] == 1
    assert config(second, first)['recorder.version'] == 2


def test_a_missing_file_and_a_file_that_does_not_read_are_named(alone, tmp_path, monkeypatch):
    missing = tmp_path / 'missing.toml'
    assert str(missing) in _refusal(lambda: config(missing), MissingFileError)
    monkeypatch.setenv(_layers.CONFIG_VARIABLE, str(missing))
    assert str(missing) in _refusal(config, MissingFileError)
    monkeypatch.delenv(_layers.CONFIG_VARIABLE)
    broken = _write(tmp_path / 'broken.toml', '[recorder\n')
    assert _refusal(lambda: config(broken)).startswith(f'expected TOML in {broken}, ')
    broken = _write(tmp_path / 'broken.json', '{"recorder": ')
    assert _refusal(lambda: config(broken)).startswith(f'expected JSON in {broken}, ')
    other = _write(tmp_path / 'settings.yaml', 'recorder: {}\n')
    assert 'ends in .toml or .json' in _refusal(lambda: config(other))
    listed = _write(tmp_path / 'list.json', '[1]')
    assert _refusal(lambda: config(listed)) == f'expected a table at the top of {listed}'
    dated = _write(tmp_path / 'dated.toml', '[recorder]\nkeep = 2026-11-17\n')
    assert 'expected recorder.keep as a text, a number' in _refusal(lambda: config(dated))
    # The file of the home folder is checked as any other, and named.
    home = _write(_home.home() / 'arena.toml', '[recorder]\nevery = 0\n')
    assert _refusal(config) == (
        f'expected recorder.every as a whole number of at least 1, found 0 in {home}'
    )


# Unknown names.


def test_an_unknown_key_is_refused_with_its_name_and_the_keys_of_its_table(tmp_path):
    path = _write(tmp_path / 'typo.toml', '[recorder]\nkep = "goals"\n')
    message = _refusal(lambda: config(path), ChoiceError)
    assert message == (
        'expected a key of [recorder], one of capture, every, inspector, keep, min_reward and '
        f'version, found kep in {path}'
    )
    path = _write(tmp_path / 'table.json', '{"recorders": {"keep": "goals"}}')
    assert _refusal(lambda: config(path), ChoiceError) == (
        f'expected a table of settings, one of {_one_of(SHIPPED)}, found recorders in {path}'
    )
    assert _refusal(lambda: config(overrides='search.windows=3'), ChoiceError) == (
        'expected a key of [search], one of detectors, surprise, value_drop and window, '
        'found windows in the overrides'
    )
    assert 'found accepts in the mapping' in _refusal(lambda: resolve({'accepts': {}}), ChoiceError)
    # The refusal is an error of cuju, a ValueError and a KeyError, and reads without quotes.
    with pytest.raises(KeyError):
        config(overrides='nothing.here=1')
    with pytest.raises(ValueError, match=r'^expected a table of settings'):
        config(overrides='nothing.here=1')


# One schema for TOML and JSON, and the unset value.


def test_json_and_toml_give_one_result(tmp_path):
    toml = _write(
        tmp_path / 'settings.toml',
        '[recorder]\nkeep = "goals"\nevery = 3\nmin_reward = 1.5\ninspector = false\n'
        '[annotations]\nprobabilities = "all"\n[search]\nwindow = 4\n'
        '[benchmark]\nkeep_recordings = true\n',
    )
    document = {
        'recorder': {'keep': 'goals', 'every': 3, 'min_reward': 1.5, 'inspector': False},
        'annotations': {'probabilities': 'all'},
        'search': {'window': 4},
        'benchmark': {'keep_recordings': True},
    }
    as_json = _write(tmp_path / 'settings.json', json.dumps(document))
    first, second = config(toml), config(as_json)
    assert first == second and first.to_dict() == second.to_dict()
    assert first.to_toml() == second.to_toml()
    assert resolve(document) == first
    assert first != config() and first == first.to_dict()
    # A whole number becomes a number with decimals where the default is one, in both.
    assert isinstance(first['search.window'], float) and isinstance(second['search.window'], float)
    assert first.source('search.window') == str(toml)
    assert second.source('search.window') == str(as_json)


def test_none_stands_for_unset_where_a_key_can_be_unset(tmp_path):
    assert config()['recorder']['min_reward'] is None
    set_ = _write(tmp_path / 'set.toml', '[recorder]\nmin_reward = 2\n')
    assert config(set_)['recorder.min_reward'] == 2
    unset = _write(tmp_path / 'unset.toml', '[recorder]\nmin_reward = "none"\n')
    null = _write(tmp_path / 'null.json', '{"recorder": {"min_reward": null}}')
    for path in (unset, null):
        settings = config(set_, path)
        assert settings['recorder.min_reward'] is None
        assert settings.source('recorder.min_reward') == str(path)
        assert 'min_reward = "none"' in settings.to_toml()
    assert config(set_, overrides='recorder.min_reward=none')['recorder.min_reward'] is None
    # A key with a value of its own refuses the word, in either spelling.
    assert _refusal(lambda: config(overrides='recorder.every=none')) == (
        'expected recorder.every as a whole number of at least 1, found "none" in the overrides'
    )
    assert _refusal(lambda: resolve({'recorder': {'keep': None}})) == (
        'expected recorder.keep as a text, found null in the mapping'
    )
    assert _refusal(lambda: config(overrides='recorder.min_reward=low')) == (
        'expected recorder.min_reward as a number or "none", found "low" in the overrides'
    )


# The rules of the keys.


@pytest.mark.parametrize(
    ('override', 'expected'),
    [
        ('recorder.every=two', 'a whole number of at least 1, found "two"'),
        ('recorder.every=1.5', 'a whole number of at least 1, found 1.5'),
        ('recorder.every=true', 'a whole number of at least 1, found true'),
        ('recorder.version=3', '1 or 2, found 3'),
        ('recorder.inspector=1', '"auto", true or false, found 1'),
        ('recorder.capture=frames', '"state" or "actions", found "frames"'),
        ('recorder.keep=4', 'a text, found 4'),
        ('benchmark.keep_recordings=1', 'true or false, found 1'),
        ('evaluate.settle=1.5', 'a number from 0.5 to 0.9999 or "none", found 1.5'),
        ('evaluate.settle=nan', 'a number from 0.5 to 0.9999 or "none", found nan'),
        ('annotations.probabilities=0', 'a whole number of at least 1 or "all", found 0'),
        ('annotations.probabilities=every', 'a whole number of at least 1 or "all", found "every"'),
        ('annotations.perspective=["pitch"]', '"agent" or "pitch", found ["pitch"]'),
        ('search.window=-1', 'a number of at least 0, found -1'),
    ],
)
def test_a_value_that_its_key_refuses_is_named_with_what_the_key_accepts(override, expected):
    key = override.partition('=')[0]
    assert _refusal(lambda: config(overrides=override)) == (
        f'expected {key} as {expected} in the overrides'
    )


def test_overrides_are_typed_as_toml_and_keep_other_text(trial):
    settings = config(
        overrides=[
            'recorder.every=4',
            'search.window = 2',
            'search.surprise=0.25',
            'benchmark.keep_recordings=true',
            'recorder.inspector=false',
            'recorder.keep=goal team:left',
            ' recorder.capture = "actions" ',
            'annotations.probabilities=all',
            'trial.tables=["spadl"]',
            'trial.batch={ representation = "extracted", stacked = true }',
            'trial.batch.rewards=scoring,checkpoints',
        ]
    )
    recorder = settings['recorder']
    assert recorder['every'] == 4 and type(recorder['every']) is int
    assert settings['search.window'] == 2.0 and type(settings['search.window']) is float
    assert settings['search.surprise'] == 0.25
    assert settings['benchmark.keep_recordings'] is True and recorder['inspector'] is False
    assert recorder['keep'] == 'goal team:left' and recorder['capture'] == 'actions'
    assert settings['annotations.probabilities'] == 'all'
    assert settings['trial.tables'] == ('spadl',)
    assert settings['trial.batch'] == {
        'representation': 'extracted',
        'stacked': True,
        'rewards': 'scoring,checkpoints',
    }
    # One text is one override, and a whole table takes an inline table.
    assert config(overrides='recorder={ every = 5 }')['recorder.every'] == 5
    for bad in ('recorder.every', '=4', 'recorder..every=4'):
        assert 'expected an override as key=value' in _refusal(
            lambda bad=bad: config(overrides=bad)
        )
    assert 'expected each override as a text' in _refusal(lambda: config(overrides=[4]))
    assert 'recorder.every holds no table' in _refusal(
        lambda: config(overrides='recorder.every.more=1')
    )


def test_a_later_table_file_dropped_into_the_folder_is_picked_up(defaults, tmp_path):
    assert 'trial' not in config()
    path = _write(tmp_path / 'run.toml', '[trial]\nepisodes = 20\n')
    assert f'one of {_one_of(SHIPPED)},' in _refusal(lambda: config(path))
    (defaults / 'trial.toml').write_text(TRIAL, encoding='utf-8')
    settings = config(path)
    assert list(settings) == sorted([*SHIPPED, 'trial'])
    assert settings['trial.episodes'] == 20 and settings['trial.name'] is None
    assert settings['trial.record'] == {'keep': 'losses', 'limit': 32}
    assert settings.source('trial.episodes') == str(path)
    assert settings.source('trial.record.limit') == 'defaults'
    # A choice of none reads as unset, as the word does everywhere.
    assert config(overrides='trial.record.keep=none')['trial.record.keep'] is None
    refused = _refusal(lambda: config(overrides='trial.episodes=0'))
    assert 'expected trial.episodes as a whole number of at least 1, found 0' in refused


def test_a_list_of_tables_replaces_the_default_list_and_fills_from_its_first_entry(trial, tmp_path):
    settings = config()
    assert settings['trial.opponents'] == (
        {'kind': 'builtin', 'difficulty': 0.05},
        {'kind': 'builtin', 'difficulty': 1.0, 'team': None},
    )
    path = _write(
        tmp_path / 'run.toml',
        '[[trial.opponents]]\ndifficulty = 0.6\n'
        '[[trial.opponents]]\nkind = "team"\nteam = "t-1"\n'
        '[[trial.opponents]]\n',
    )
    settings = config(path)
    assert settings['trial.opponents'] == (
        {'kind': 'builtin', 'difficulty': 0.6},
        {'kind': 'team', 'difficulty': 0.05, 'team': 't-1'},
        {'kind': 'builtin', 'difficulty': 0.05},
    )
    assert settings.source('trial.opponents.0.difficulty') == str(path)
    assert settings.source('trial.opponents.0.kind') == 'defaults'
    assert settings.source('trial.opponents.1.team') == str(path)
    assert settings.source('trial.opponents') == str(path)
    same = _write(
        tmp_path / 'run.json',
        json.dumps(
            {'trial': {'opponents': [{'difficulty': 0.6}, {'kind': 'team', 'team': 't-1'}, {}]}}
        ),
    )
    assert config(same) == settings
    # An override changes one key of one entry, or lays a table over the entry.
    changed = config(path, overrides=['trial.opponents.1.difficulty=1'])
    assert changed['trial.opponents.1'] == {'kind': 'team', 'difficulty': 1.0, 'team': 't-1'}
    assert changed['trial.opponents.0'] == settings['trial.opponents.0']
    assert changed.source('trial.opponents.1.difficulty') == 'overrides'
    changed = config(path, overrides=['trial.opponents.2={ kind = "team", team = "t-2" }'])
    assert changed['trial.opponents.2'] == {'kind': 'team', 'difficulty': 0.05, 'team': 't-2'}
    assert config(overrides='trial.opponents=[{ difficulty = 0.5 }]')['trial.opponents'] == (
        {'kind': 'builtin', 'difficulty': 0.5},
    )
    assert _refusal(lambda: config(path, overrides='trial.opponents.3.kind=team')) == (
        'expected an entry of trial.opponents from 0 to 2, found 3 in the overrides'
    )
    assert _refusal(lambda: config(overrides='trial.opponents.0.rank=1'), ChoiceError) == (
        'expected a key of [trial.opponents.0], one of difficulty, kind and team, '
        'found rank in the overrides'
    )
    assert _refusal(lambda: config(overrides='trial.opponents=3')) == (
        'expected trial.opponents as a list of tables, found 3 in the overrides'
    )
    assert 'expected trial.opponents.0.kind as "builtin" or "team"' in _refusal(
        lambda: config(overrides='trial.opponents.0.kind=person')
    )


def test_tables_merge_open_tables_take_new_keys_and_lists_check_their_entries(trial, tmp_path):
    path = _write(
        tmp_path / 'run.toml',
        '[trial]\ntables = ["events", "spadl"]\npolicy_options = { depth = 2 }\n'
        '[trial.batch]\nstacked = true\nrewards = { scoring = 1.0 }\n'
        '[trial.record]\nlimit = 8\n',
    )
    settings = config(path, overrides='trial.policy_options.width=3')
    trial_table = settings['trial']
    assert trial_table['tables'] == ('events', 'spadl')
    assert trial_table['policy_options'] == {'depth': 2, 'width': 3}
    assert trial_table['batch'] == {
        'representation': 'simple115v2',
        'stacked': True,
        'rewards': {'scoring': 1.0},
    }
    assert trial_table['record'] == {'keep': 'losses', 'limit': 8}
    assert settings.source('trial.batch.stacked') == str(path)
    assert settings.source('trial.batch.representation') == 'defaults'
    assert settings.source('trial.policy_options') == 'overrides'
    assert _refusal(lambda: config(overrides='trial.tables=["tracking", "frames"]')) == (
        'expected trial.tables as a list of entries that are each "tracking", "events" or '
        '"spadl", found ["tracking", "frames"] in the overrides'
    )
    assert _refusal(lambda: config(overrides='trial.record=3')) == (
        'expected trial.record as a table, found 3 in the overrides'
    )
    assert 'expected trial.batch.representation as a text' in _refusal(
        lambda: config(overrides='trial.batch.representation=3')
    )


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('[other]\nkey = 1\n', 'expected the table [broken] and at most the rules'),
        ('[broken]\nkey = 1\n[accepts.other]\n', 'expected the table [broken] and at most'),
        ('[broken]\nkey = "none"\n', 'expected a rule with type or choices at accepts.broken.key'),
        ('[broken]\nkey = 1\n[accepts.broken]\nkee = {}\n', 'expected a key of [broken], one of'),
        ('[broken]\nkey = 1\n[accepts.broken]\nkey = { kind = "x" }\n', 'expected a rule at'),
        ('[broken]\nkey = 1\n[accepts.broken]\nkey = { type = "word" }\n', 'expected a rule at'),
        ('[broken]\nkey = 1\n[accepts.broken]\nkey = { choices = [] }\n', 'expected a rule at'),
        ('[broken]\nkey = 1\n[accepts.broken]\nkey = { minimum = "1" }\n', 'expected a rule at'),
        ('[broken]\nkey = [1]\n[accepts.broken]\nkey = { items = {} }\n', 'expected a rule at'),
        ('[broken]\nkey = 1\n[accepts.broken]\n"*" = {}\n', 'expected a rule at accepts.broken.*'),
        ('[broken]\nkey = 1\n[accepts.broken]\nkey = { minimum = 2 }\n', 'expected broken.key as'),
    ],
)
def test_a_defaults_file_out_of_shape_is_refused_with_its_path(defaults, text, expected):
    path = _write(defaults / 'broken.toml', text)
    message = _refusal(config)
    assert expected in message and str(path) in message


# The mapping that the settings are.


def test_the_settings_are_a_read_only_mapping(tmp_path):
    settings = config(overrides='recorder.every=2')
    assert isinstance(settings, Config) and len(settings) == len(SHIPPED)
    assert 'recorder' in settings and 'recorder.every' in settings
    assert 'trial' not in settings and 'recorder.kept' not in settings and 3 not in settings
    assert settings.get('recorder.kept', 7) == 7
    assert dict(settings)['search'] == {
        'value_drop': 0.3,
        'window': 2.0,
        'surprise': 0.1,
        'detectors': ('cuju', 'value_drop', 'surprise', 'entropy'),
    }
    with pytest.raises(TypeError):
        settings['recorder']['every'] = 3
    with pytest.raises(TypeError):
        settings['recorder'] = {}
    with pytest.raises(AttributeError):
        settings.extra = 1
    # A copy may change and leaves the settings as they were.
    copy = settings.to_dict()
    copy['recorder']['every'] = 9
    assert settings['recorder.every'] == 2
    assert repr(settings) == f"Config(tables={SHIPPED}, layers=['defaults', 'overrides'])"
    again = pickle.loads(pickle.dumps(settings))
    assert again == settings and again.sources() == settings.sources()


def test_the_settings_equal_their_plain_form_when_a_key_holds_a_list(trial):
    # A list reads back as a tuple, and the settings still equal their plain dictionaries.
    settings = config()
    assert isinstance(settings['trial.tables'], tuple)
    assert settings == settings.to_dict() and settings.to_dict() == settings
    assert settings == config() and settings == dict(settings)
    assert settings != config(overrides='trial.tables=["events"]')
    assert settings != {**settings.to_dict(), 'trial': {}} and settings != [1]
    with pytest.raises(TypeError):
        hash(settings)
    assert pickle.loads(pickle.dumps(FROM_CONFIG)) is FROM_CONFIG
    assert repr(FROM_CONFIG) == 'FROM_CONFIG'


def test_the_settings_write_toml_that_reads_back_to_the_same_settings(trial, tmp_path):
    path = _write(
        tmp_path / 'run.toml',
        '[recorder]\nkeep = "goal \\"late\\""\nmin_reward = 0.5\n'
        '[trial]\npolicy_options = { depth = 2 }\n[trial.batch]\nstacked = true\n'
        '[[trial.opponents]]\ndifficulty = 0.6\n[[trial.opponents]]\nkind = "team"\n',
    )
    settings = config(path, overrides='evaluate.settle=0.6')
    text = settings.to_toml()
    assert '#' not in text and text.endswith('\n') and not text.startswith('\n')
    assert '[trial.batch]\n' in text and text.count('[[trial.opponents]]\n') == 2
    assert config(_write(tmp_path / 'again.toml', text)) == settings
    marked = settings.to_toml(sources=True)
    assert 'every = 1  # defaults\n' in marked
    assert f'min_reward = 0.5  # {path}\n' in marked
    assert 'settle = 0.6  # overrides\n' in marked
    assert config(_write(tmp_path / 'marked.toml', marked)) == settings


# The helper of the functions that take config= and overrides=.


def test_resolve_accepts_a_path_a_list_a_config_and_a_mapping(alone, tmp_path):
    _write(_home.home() / 'arena.toml', '[recorder]\nevery = 2\n')
    path = _write(tmp_path / 'run.toml', '[recorder]\nkeep = "goals"\n')
    assert resolve() == config() and resolve(None).layers == config().layers
    assert resolve(path) == resolve(str(path)) == resolve([path]) == config(path)
    assert resolve(path, 'recorder.every=3') == config(path, overrides=['recorder.every=3'])
    # A mapping lies over the standing layers, for a notebook, with tuples as lists.
    mapped = resolve({'recorder': {'keep': 'goals'}})
    assert mapped == config(path) and mapped.layers[-1] == 'mapping'
    assert mapped.source('recorder.keep') == 'mapping' and mapped['recorder.every'] == 2
    both = resolve([path, {'recorder': {'version': 1}}, MappingLike()], ('search.window=1',))
    assert both.layers[2:] == (str(path), 'mapping', 'mapping', 'overrides')
    assert (both['recorder.keep'], both['recorder.version']) == ('goals', 1)
    assert both['benchmark.keep_recordings'] is True
    # A Config holds its layers already, and overrides lay over a copy of it.
    settings = config(path)
    assert resolve(settings) is settings
    over = resolve(settings, ['recorder.keep=all'])
    assert over['recorder.keep'] == 'all' and settings['recorder.keep'] == 'goals'
    assert over.layers == (*settings.layers, 'overrides')
    assert over.source('recorder.keep') == 'overrides'
    assert over.source('recorder.every') == settings.source('recorder.every')
    for bad in (3, [3], [settings], object()):
        message = _refusal(lambda bad=bad: resolve(bad))
        assert message.startswith(('expected config as', 'expected the path of a file')), message
    assert _refusal(lambda: resolve({'recorder': 3})) == (
        'expected recorder as a table, found 3 in the mapping'
    )


class MappingLike(dict):
    """A mapping of another class, as a notebook may hand over."""

    def __init__(self):
        super().__init__(benchmark={'keep_recordings': True})


def test_table_lays_the_keyword_arguments_of_a_call_over_the_settings(tmp_path):
    path = _write(tmp_path / 'run.toml', '[recorder]\nkeep = "goals"\nevery = 3\n')

    def recorder(
        keep=FROM_CONFIG, every=FROM_CONFIG, min_reward=FROM_CONFIG, config=None, overrides=()
    ):
        # The shape of a function that takes config= and keeps its arguments as overrides.
        return table('recorder', config, overrides, keep=keep, every=every, min_reward=min_reward)

    assert recorder() == config()['recorder']
    assert recorder(config=path) == {**config()['recorder'], 'keep': 'goals', 'every': 3}
    chosen = recorder(config=path, keep=len, min_reward=None, overrides=['recorder.every=5'])
    # An argument replaces the key as it is, an object included, and None is a value.
    assert chosen['keep'] is len and chosen['every'] == 5 and chosen['min_reward'] is None
    assert recorder(config=path, every=7)['every'] == 7
    assert recorder(config={'recorder': {'min_reward': 2}})['min_reward'] == 2
    found = recorder(config=path)
    found['keep'] = 'changed'
    assert recorder(config=path)['keep'] == 'goals'
    assert _refusal(lambda: table('recorder', kept=1), ChoiceError) == (
        'expected a key of [recorder], one of capture, every, inspector, keep, min_reward and '
        'version, found the argument kept'
    )
    assert 'found recorders' in _refusal(lambda: table('recorders'), ChoiceError)


# The files.


def test_files_read_plain_data_and_write_values_as_toml(tmp_path):
    assert _files.scalar('200') == 200 and _files.scalar(' 0.5 ') == 0.5
    assert _files.scalar('true') is True and _files.scalar('[1, "a"]') == [1, 'a']
    assert _files.scalar('{ a = 1 }') == {'a': 1} and _files.scalar(' goals ') == 'goals'
    assert _files.scalar('2026-11-17') == '2026-11-17' and _files.scalar('') == ''
    assert _files.plain({'a': (1, 2), 'b': MappingLike()}) == {
        'a': [1, 2],
        'b': {'benchmark': {'keep_recordings': True}},
    }
    assert _files.unset({'a': 'none', 'b': ['none', {'c': 'none', 'd': 'None'}]}) == {
        'a': None,
        'b': [None, {'c': None, 'd': 'None'}],
    }
    value = {'a b': [1, 2.5, True, None, 'x"y\n'], 'c': {}, 'd-e': {'f': float('inf')}}
    text = _files.value_text(value)
    assert text == '{ "a b" = [1, 2.5, true, "none", "x\\"y\\n"], c = {}, d-e = { f = inf } }'
    assert _files.scalar(text) == {**value, 'a b': [1, 2.5, True, 'none', 'x"y\n']}
    assert 'expected plain data' in _refusal(lambda: _files.value_text(object()))
    marked = tmp_path / 'marked.json'
    marked.write_bytes(b'\xef\xbb\xbf{"benchmark": {"keep_recordings": true}}')
    assert config(marked)['benchmark.keep_recordings'] is True


# The command.


def test_config_show_and_check_run_on_a_temporary_home(alone, tmp_path, capsys):
    from cuju.arena.__main__ import main

    # The home folder of the test holds no file, so its settings are the defaults.
    assert main(['config', 'show', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['layers'] == ['defaults']
    assert main(['config', 'check']) == 0
    assert capsys.readouterr().out == 'checked the defaults\nthe settings fit\n'
    assert not (alone / 'arena.toml').exists()
    home = tmp_path / 'other'
    path = _write(tmp_path / 'run.toml', '[recorder]\nkeep = "goals"\n')
    later = _write(tmp_path / 'later.json', '{"recorder": {"every": 3}}')
    assert main(['--home', str(home), 'config', 'show']) == 0
    shown = capsys.readouterr().out
    assert shown.startswith('# The layers, the lowest first, defaults.\n\n[annotations]\n')
    assert 'keep = "all"  # defaults\n' in shown and 'min_reward = "none"  # defaults\n' in shown
    _write(home / 'arena.toml', '[recorder]\nversion = 1\n[benchmark]\nkeep_recordings = true\n')
    arguments = [
        '--home',
        str(home),
        'config',
        'show',
        '--config',
        str(path),
        '--config',
        str(later),
    ]
    assert main([*arguments, '--set', 'search.window=3', '--set', 'recorder.every=4']) == 0
    shown = capsys.readouterr().out
    file = home.resolve() / 'arena.toml'
    layers = f'defaults, {file}, {path}, {later}, overrides'
    assert shown.startswith(f'# The layers, the lowest first, {layers}.\n')
    assert f'version = 1  # {file}\n' in shown and f'keep = "goals"  # {path}\n' in shown
    assert 'every = 4  # overrides\n' in shown and 'window = 3.0  # overrides\n' in shown
    assert main([*arguments, '--json']) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer['layers'] == ['defaults', str(file), str(path), str(later)]
    assert (
        answer['config']['recorder']['every'] == 3
        and answer['config']['benchmark']['keep_recordings'] is True
    )
    assert answer['config']['recorder']['min_reward'] is None
    assert answer['sources']['recorder.every'] == str(later)
    assert answer['sources']['recorder.capture'] == 'defaults'
    # What show prints is a file of settings that resolves to the same.
    again = _write(tmp_path / 'again.toml', shown)
    assert config(again).to_dict()['recorder'] == {
        **config().to_dict()['recorder'],
        'keep': 'goals',
        'version': 1,
        'every': 4,
    }
    # Pass the home to each invocation in this process.
    assert (
        main(['--home', str(home), 'config', 'check', str(path), '--set', 'evaluate.settle=0.6'])
        == 0
    )
    assert capsys.readouterr().out == (
        f'checked the defaults\nchecked {file}\nchecked {path}\nchecked the overrides\n'
        'the settings fit\n'
    )
    assert main(['--home', str(home), 'config', 'check', str(path), '--json']) == 0
    assert json.loads(capsys.readouterr().out) == {
        'schema': 'arena.config.check/1',
        'ok': True,
        'layers': ['defaults', str(file), str(path)],
    }
    typo = _write(tmp_path / 'typo.toml', '[recorder]\nkep = "goals"\n')
    assert main(['config', 'check', str(typo)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert (
        'cuju arena config check: error: expected a key of [recorder], one of capture, every, '
        f'inspector, keep, min_reward and version, found kep in {typo}'
    ) in captured.err
    assert main(['config', 'check', '--config', str(typo), '--json']) == 2
    assert 'found kep in' in json.loads(capsys.readouterr().out)['error']
    assert main(['config', 'check', '--set', 'recorder.every=0']) == 2
    assert 'a whole number of at least 1, found 0' in capsys.readouterr().err
    assert main(['config', 'check', str(tmp_path / 'missing.toml')]) == 3
    assert 'expected a file of settings at' in capsys.readouterr().err
    # A broken file of the home folder is named by every check.
    _write(home / 'arena.toml', '[evaluate]\nsettle = 2\n')
    assert main(['--home', str(home), 'config', 'check']) == 2
    assert f'found 2 in {file}' in capsys.readouterr().err
    assert main(['config', 'edit']) == 2


# The files of a tournament.


def test_the_tournament_config_reads_toml_as_it_reads_json(alone, tmp_path, capsys):
    from cuju.arena.__main__ import main

    document = {
        'name': 'Spring cup',
        'seed': 4,
        'ability': 0.35,
        'entrants': [
            {'kind': 'builtin', 'ability': 0.2},
            {'kind': 'builtin', 'ability': None},
            {'kind': 'person', 'name': 'Ana'},
            {'kind': 'builtin', 'ability': 0.9, 'name': 'Top'},
        ],
        'defaults': {'scenario': '5_vs_5', 'length': 600},
        'stages': [{'format': 'knockout', 'name': 'Cup'}],
    }
    as_json = _write(tmp_path / 'cup.json', json.dumps(document))
    as_toml = _write(
        tmp_path / 'cup.toml',
        'name = "Spring cup"\nseed = 4\nability = 0.35\n\n'
        '[[entrants]]\nkind = "builtin"\nability = 0.2\n'
        '[[entrants]]\nkind = "builtin"\nability = "none"\n'
        '[[entrants]]\nkind = "person"\nname = "Ana"\n'
        '[[entrants]]\nkind = "builtin"\nability = 0.9\nname = "Top"\n\n'
        '[defaults]\nscenario = "5_vs_5"\nlength = 600\n\n'
        '[[stages]]\nformat = "knockout"\nname = "Cup"\n',
    )
    answers = []
    for path in (as_json, as_toml):
        assert main(['tournament', 'check', str(path), '--json']) == 0
        answers.append(json.loads(capsys.readouterr().out))
    assert answers[0] == answers[1]
    assert [entrant['ability'] for entrant in answers[1]['config']['entrants']] == [
        0.2, None, None, 0.9,
    ]  # fmt: skip
    assert main(['tournament', 'check', str(as_toml), '--tournament-preset', 'quick_cup']) == 0
    assert 'Spring cup, seed 4, 4 entrants' in capsys.readouterr().out
    broken = _write(tmp_path / 'broken.toml', 'name = \n')
    assert main(['tournament', 'check', str(broken)]) == 2
    assert (
        f'cuju arena tournament check: error: expected TOML in {broken}, '
        in capsys.readouterr().err
    )
    broken = _write(tmp_path / 'broken.json', '{"name": ')
    assert main(['tournament', 'check', str(broken)]) == 2
    assert (
        'cuju arena tournament check: error: expected a JSON tournament file, '
        f'found {str(broken)!r} with invalid JSON on line 1, column 10' in capsys.readouterr().err
    )
    listed = _write(tmp_path / 'list.json', '[]')
    assert main(['tournament', 'check', str(listed)]) == 2
    assert (
        f'cuju arena tournament check: error: expected a JSON object in {listed}'
        in capsys.readouterr().err
    )


def test_a_guide_names_every_table_and_key_of_the_arena():
    guides = '\n'.join(path.read_text(encoding='utf-8') for path in (ROOT / 'docs').rglob('*.md'))
    for name, keys in _layers.ARENA.schema().defaults.items():
        assert f'[{name}]' in guides, name
        for key in keys:
            assert f'`{key}`' in guides, f'{name}.{key}'
