# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the stage types and tiebreakers of a tournament as plugins.

The standards are held byte for byte to what they gave before they were plugins, and a stage type
and a tiebreaker of a third party, in a folder and as an installed package, are held to their kind,
with the paths of a plugin that is broken, that fails, that is turned off and that is gone when a
file is read again.
"""

import json
import sys
import time
from pathlib import Path

import pytest

import cuju.errors
from cuju.arena import plugins as _plugins
from cuju.arena._evaluate import ratings as _ratings
from cuju.arena._store import ratings as _store
from cuju.arena._tournaments import tournament as rules
from cuju.arena._tournaments import tournament_runner as runner
from support import stage_cases
from support.plugin_files import installed as _installed
from support.plugin_files import written as _written

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parents[1] / 'support' / 'plugins' / 'stages.json'
EXAMPLE = ROOT / 'docs' / 'plugins' / 'league-final'
SCENARIO = stage_cases.SCENARIO
TEAMS = stage_cases.TEAMS
_PROVIDES = {'stage_type/1': ['league_final'], 'tiebreaker/1': ['clean_sheets']}


@pytest.fixture(autouse=True)
def fresh(arena_home):
    _plugins.refresh()
    yield
    _plugins.refresh()


def _config(stages, count=6, seed=4, **options):
    return rules.check(
        {
            'name': 'Third party',
            'seed': seed,
            'seeding': 'manual',
            'entrants': stage_cases.entrants(count),
            'rating_scale': 'table',
            'stages': [{'scenario': SCENARIO, **stage} for stage in stages],
            **options,
        },
        TEAMS,
    )


def _level(count, score=(1, 1)):
    """Returns a result for every match of a league of `count` entrants, each with `score`."""
    view = rules.evaluate(_config([{'format': 'league'}], count=count), {})
    return {
        match['key']: {'score': list(score), 'cards': [[0, 0], [0, 0]]} for match in view['matches']
    }


def _failing(call, identifier):
    """Runs a call that a plugin ruins and returns the error, with the plugin marked failing."""
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        call()
    assert caught.value.message_id == 'server.plugins.failed'
    assert _plugins.failing(identifier) is not None
    return caught.value


# The standards, byte for byte.


def test_the_standards_give_the_bytes_they_gave_before_they_were_plugins():
    expected = json.loads(GOLDEN.read_text(encoding='utf-8'))
    found = json.loads(stage_cases.text())
    assert set(found) == set(expected) == {'stages', 'tiebreakers', 'tournaments'}
    for group in expected:
        assert found[group] == expected[group], group
    # Every variant of every stage type over eight counts and three seeds, each tiebreaker, and
    # whole tournaments.
    variants = sum(len(each) for each in stage_cases.VARIANTS.values())
    assert len(expected['stages']) == variants * len(stage_cases.COUNTS) * len(stage_cases.SEEDS)
    assert len(expected['tiebreakers']) >= 9 * 3 * 3 * 2
    assert len(expected['tournaments']) >= 24
    assert GOLDEN.read_text(encoding='utf-8') == stage_cases.text()


def test_every_standard_is_registered_by_the_plugin_builtin():
    built = _plugins.plugin('builtin')
    assert built.broken is None
    assert built.names('stage_type') == list(rules.STAGE_TYPES)
    assert built.names('tiebreaker') == list(rules.TIEBREAKERS)
    assert _plugins.names('stage_type') == list(rules.STAGE_TYPES)
    assert built.as_dict()['provides']['stage_type/1'] == list(rules.STAGE_TYPES)
    assert built.as_dict()['provides']['tiebreaker/1'] == list(rules.TIEBREAKERS)
    # One object for each standard, whether the registry or the module hands it over.
    for name in rules.STAGE_TYPES:
        assert _plugins.get('stage_type', name) is rules.STANDARD_STAGES[name]
    for name in rules.TIEBREAKERS:
        assert _plugins.get('tiebreaker', name) is rules.STANDARD_TIEBREAKERS[name]


def test_the_options_of_the_standards_are_settings_of_a_stage_with_their_defaults():
    named = set()
    for name, kind in rules.STANDARD_STAGES.items():
        options = kind.options()
        assert options, name
        for option, spec in options.items():
            assert spec == {'default': rules.STAGE_DEFAULTS[option]}
            named.add(option)
    shared = {'format', 'name', 'scenario', 'length', 'ability', 'legs', 'settle', 'replays'}
    shared |= {'decider_scenario', 'decider_length', 'points', 'tiebreakers', 'seeding', 'advance'}
    # Each setting of a stage is shared by every type or read by a type that names it.
    assert shared | named == set(rules.STAGE_DEFAULTS)


def test_the_standards_plan_the_matches_that_they_play():
    config = _config([{'format': 'league', 'cycles': 2}], count=5)
    view = rules.evaluate(config, {})
    assert view['expected'] == 20 and len(view['matches']) == 20
    for name in rules.STAGE_TYPES:
        for count in (5, 8):
            config = _config([{'format': name, 'third_place': name == 'knockout'}], count=count)
            # Every home side wins, so no replay or deciding match adds to the plan.
            results = {}
            while True:
                view = rules.evaluate(config, results)
                ready = [m['key'] for m in view['matches'] if m['result'] is None]
                if not ready:
                    break
                results.update({key: {'score': [1, 0]} for key in ready})
            assert len(view['matches']) == rules._planned(config['stages'][0], count), (name, count)
            assert view['expected'] == view['played']


# Stage types of a third party.


def _results(view, score=(1, 0)):
    return {
        match['key']: {'score': list(score), 'cards': [[0, 0], [0, 0]]}
        for match in view['matches']
        if match['result'] is None
    }


def test_a_folder_plugin_adds_a_stage_type_and_a_tiebreaker_that_play_a_tournament():
    found = _plugins.add(EXAMPLE)
    assert found.broken is None and found.version == '1.0'
    assert 'finale.league_final' in _plugins.names('stage_type')
    assert 'finale.clean_sheets' in _plugins.names('tiebreaker')
    config = _config(
        [
            {
                'format': 'finale.league_final',
                'final_name': 'Grand final',
                'tiebreakers': ['finale.clean_sheets', 'goal_difference'],
                'advance': 3,
            },
            {'format': 'gauntlet'},
        ],
        count=5,
    )
    stage = config['stages'][0]
    assert stage['final_name'] == 'Grand final'
    assert stage['tiebreakers'] == ['finale.clean_sheets', 'goal_difference', 'lots']
    assert config['plugins'] == [{'name': 'finale', 'version': '1.0'}]
    views = stage_cases.play(config)
    last = views[-1]
    assert last['complete'] and sorted(last['ranking']) == list(range(5))
    first = last['stages'][0]
    assert [r['name'] for r in first['rounds']] == ['League', 'Grand final']
    final = first['rounds'][1]['fixtures'][0]
    assert first['ranking'][:2] == [final['winner'], final['loser']]
    assert first['format'] == 'finale.league_final'
    # The league of five plays ten matches, the final one more, and the stage after it follows.
    assert len(first['rounds'][0]['fixtures']) == 10 and final['state'] == 'done'
    assert last['stages'][1]['state'] == 'done' and first['entrants'] == list(range(5))
    # The matches are keyed by the stage and the plan, and expected counts every one.
    keys = {match['key'] for match in last['matches']}
    assert '0/mg1' in keys and any(key.startswith('0/tfinal/') for key in keys)
    assert last['expected'] == last['played'] == len(last['matches'])
    # Before any result the stage waits on its league, with no final yet.
    start = views[0]['stages'][0]
    assert [r['name'] for r in start['rounds']] == ['League'] and start['state'] == 'ready'
    assert views[0]['stages'][1]['state'] == 'waiting' and views[0]['expected'] > 10


def test_a_tiebreaker_of_a_plugin_orders_level_entrants():
    _plugins.add(EXAMPLE)
    config = _config([{'format': 'league', 'tiebreakers': ['finale.clean_sheets']}], count=4)
    # Every match ends level, so the points are level. Entrant 0 keeps two clean sheets, entrants
    # 1 and 2 one each and entrant 3 none, since only the matches among 0, 1 and 2 end 0-0.
    nil = ({0, 1}, {0, 2})
    results = {
        match['key']: {'score': [0, 0] if {match['home'], match['away']} in nil else [1, 1]}
        for match in rules.evaluate(_config([{'format': 'league'}], count=4), {})['matches']
    }
    nil_keys = [key for key, result in results.items() if result['score'] == [0, 0]]
    assert len(nil_keys) == 2
    rows = rules.evaluate(config, results)['stages'][0]['tables'][0]['rows']
    assert [row['points'] for row in rows] == [3, 3, 3, 3]
    assert rows[0]['entrant'] == 0 and rows[3]['entrant'] == 3
    assert {rows[1]['entrant'], rows[2]['entrant']} == {1, 2}
    # The plugin put 0 above both, and 1 and 2 stay level for lots, which end the order.
    assert [row['tiebreak'] for row in rows[:-1]] == [
        'finale.clean_sheets',
        'lots',
        'finale.clean_sheets',
    ]


def test_options_of_a_stage_type_are_checked_with_the_rules_that_it_declares():
    code = """
        class Rungs:
            def options(self):
                return {
                    'size': {'type': 'integer', 'default': 2, 'minimum': 1, 'maximum': 4},
                    'weight': {'type': 'number', 'default': 0.5, 'minimum': 0, 'maximum': 1},
                    'strict': {'type': 'boolean', 'default': False},
                    'mode': {'default': 'fast', 'choices': ['fast', 'slow']},
                }

            def schedule(self, plan):
                return {'rounds': []}

            def complete(self, plan, scheduled):
                return False

            def standings(self, plan, scheduled):
                return {'ranking': None}

        def register(registry):
            registry.provide('stage_type/1', 'rungs', Rungs())
    """
    _written(_plugins._folder(), 'ladders', {'stage_type/1': ['rungs']}, code)
    stage = _config([{'format': 'ladders.rungs', 'size': 3, 'mode': 'slow'}])['stages'][0]
    assert (stage['size'], stage['weight'], stage['strict'], stage['mode']) == (
        3,
        0.5,
        False,
        'slow',
    )
    assert _config([{'format': 'ladders.rungs'}])['stages'][0]['size'] == 2
    for setting, message in (
        ({'size': 9}, 'server.tournament.count'),
        ({'size': 'big'}, 'server.tournament.count'),
        ({'weight': 2}, 'server.tournament.option_number'),
        ({'weight': True}, 'server.tournament.option_number'),
        ({'strict': 1}, 'server.tournament.flag'),
        ({'mode': 'medium'}, 'server.tournament.choice'),
        ({'bracket_size': 4, 'nonsense': 1}, 'server.tournament.stage_settings'),
    ):
        with pytest.raises(rules.TournamentError) as caught:
            _config([{'format': 'ladders.rungs', **setting}])
        assert caught.value.message_id == message, setting
    # The options belong to their own type, so another type refuses them.
    with pytest.raises(rules.TournamentError) as caught:
        _config([{'format': 'league', 'size': 3}])
    assert caught.value.message_id == 'server.tournament.stage_settings'
    # A tournament's defaults reach every stage, so they name only what the stage types read.
    both = _config([{'format': 'ladders.rungs'}], defaults={'size': 4})
    assert both['stages'][0]['size'] == 4
    with pytest.raises(rules.TournamentError):
        _config([{'format': 'ladders.rungs'}, {'format': 'league'}], defaults={'size': 4})


@pytest.mark.parametrize(
    ('options', 'reason'),
    [
        ('None', 'a mapping'),
        ('{"Bad": {"default": 1}}', 'no setting of a stage uses'),
        ('{"legs": {"default": 1}}', 'no setting of a stage uses'),
        ('{"x": 3}', 'with a default'),
        ('{"x": {"type": "string"}}', 'with a default'),
        ('{"x": {"default": 1, "type": "decimal"}}', 'a known one'),
        ('{"x": {"default": 1, "type": "integer"}}', 'a minimum and a maximum'),
        (
            '{"x": {"default": 9, "type": "integer", "minimum": 1, "maximum": 4}}',
            'fit its own rules',
        ),
        ('{"x": {"default": "a", "choices": []}}', 'as a list'),
        ('{"x": {"default": "a", "colour": 1}}', 'with a default'),
    ],
)
def test_a_stage_type_whose_options_do_not_fit_is_marked_failing(options, reason):
    code = f"""
        class Odd:
            def options(self):
                return {options}

            def schedule(self, plan):
                return {{'rounds': []}}

            def complete(self, plan, scheduled):
                return False

            def standings(self, plan, scheduled):
                return {{'ranking': None}}

        def register(registry):
            registry.provide('stage_type/1', 'odd', Odd())
    """
    _written(_plugins._folder(), 'oddity', {'stage_type/1': ['odd']}, code)
    error = _failing(lambda: _config([{'format': 'oddity.odd'}]), 'oddity')
    assert reason in str(error)


_STAGE_HEAD = """
    class Mine:
        def options(self):
            return {}

        def schedule(self, plan):
            first, second = plan.seeds[:2]
            fixture = plan.fixture('one', 'Only match', first, second)
            plan.state['fixture'] = fixture
            return __SCHEDULED__

        def complete(self, plan, scheduled):
            return __COMPLETE__

        def standings(self, plan, scheduled):
            return __STANDINGS__

    def register(registry):
        registry.provide('stage_type/1', 'mine', Mine())
"""


_RANKED = "{'tables': [], 'ranking': list(plan.seeds) if fixture['state'] == 'done' else None}"


def _mine(scheduled=None, complete=None, standings=None, identifier='mining'):
    code = (
        _STAGE_HEAD.replace(
            '__SCHEDULED__', scheduled or "{'rounds': [plan.round('Day', [fixture])]}"
        )
        .replace('__COMPLETE__', complete or "fixture['state'] == 'done'")
        .replace(
            '__STANDINGS__',
            standings or _RANKED,
        )
        .replace("fixture['state']", "plan.state['fixture']['state']")
    )
    _written(_plugins._folder(), identifier, {'stage_type/1': ['mine']}, code)
    return _config([{'format': f'{identifier}.mine'}], count=2)


def test_a_minimal_stage_type_of_a_plugin_plays_through_the_arena():
    config = _mine()
    views = stage_cases.play(config)
    assert views[-1]['complete'] and views[-1]['ranking'] == [0, 1]
    assert views[0]['stages'][0]['state'] == 'ready'
    assert [m['key'] for m in views[-1]['matches']] == ['0/mone']
    # The stage that a plugin made shows the same fields as one that ships.
    standard = stage_cases.play(_config([{'format': 'league'}], count=2))[-1]
    named = {key for key in standard['stages'][0] if not key.startswith('name')}
    assert {key for key in views[-1]['stages'][0] if not key.startswith('name')} == named


@pytest.mark.parametrize(
    ('scheduled', 'complete', 'standings', 'reason'),
    [
        ('None', None, None, "a mapping of 'rounds'"),
        ("{'rounds': 3}", None, None, 'a list of at most'),
        ("{'rounds': [], 'tables': []}", None, None, "a mapping of 'rounds'"),
        ("{'rounds': [], 'bracket': 'spiral'}", None, None, "'knockout' or 'double'"),
        ("{'rounds': [{'fixtures': []}]}", None, None, 'a mapping with a name'),
        ("{'rounds': [{'name': 'Day', 'fixtures': 7}]}", None, None, 'the fixtures of round 1'),
        (
            "{'rounds': [{'name': 'Day', 'fixtures': [{'id': 'x'}]}]}",
            None,
            None,
            'as the plan makes it',
        ),
        (
            "{'rounds': [plan.round('Day', [{**fixture, 'matches': ['0/zzz']}])]}",
            None,
            None,
            'registered matches',
        ),
        (
            "{'rounds': [plan.round('Day', [{**fixture, 'winner': 99}])]}",
            None,
            None,
            'to be an entrant',
        ),
        (None, 'True', None, 'every fixture done'),
        (None, "'yes'", None, 'true or false'),
        (None, None, 'None', "a mapping of 'tables'"),
        (None, None, "{'tables': {}, 'ranking': None}", 'a list of at most'),
        (None, None, "{'tables': [{'name': 'T'}], 'ranking': None}", 'as the plan makes it'),
        (
            None,
            None,
            "{'tables': [plan.standing('T', [{'entrant': 0}])], 'ranking': None}",
            'as the plan makes it',
        ),
        (None, None, "{'ranking': [0, 1]}", 'only once the stage is complete'),
        (
            None,
            "True if plan.state['fixture']['state'] == 'done' else False",
            "{'ranking': None}",
            'a ranking once',
        ),
        (
            None,
            None,
            "{'ranking': [0, 0] if plan.state['fixture']['state'] == 'done' else None}",
            'each entrant of the stage once',
        ),
    ],
)
def test_a_stage_type_that_answers_badly_is_marked_failing(scheduled, complete, standings, reason):
    config = _mine(scheduled, complete, standings, identifier='badly')
    # Both before the match is played and after it, since some answers wait for it to be done.
    played = {'0/mone': {'score': [1, 0], 'cards': [[0, 0], [0, 0]]}}
    error = _failing(lambda: [rules.evaluate(config, results) for results in ({}, played)], 'badly')
    assert reason in str(error) and 'badly' in str(error)


def test_a_stage_type_that_raises_ends_the_evaluation_with_its_message():
    code = """
        class Hurt:
            def options(self):
                return {}

            def schedule(self, plan):
                raise ArithmeticError('divided by the entrants')

            def complete(self, plan, scheduled):
                return False

            def standings(self, plan, scheduled):
                return {}

        def register(registry):
            registry.provide('stage_type/1', 'hurt', Hurt())
    """
    _written(_plugins._folder(), 'hurts', {'stage_type/1': ['hurt']}, code)
    config = _config([{'format': 'hurts.hurt'}])
    error = _failing(lambda: rules.evaluate(config, {}), 'hurts')
    assert 'divided by the entrants' in str(error)
    # The standards still play, and a tournament that chooses them is not touched.
    assert stage_cases.play(_config([{'format': 'league'}]))[-1]['complete']


def test_a_plan_refuses_what_a_stage_type_may_not_do():
    attempts = {
        'a match of one entrant': "plan.match('x', 'Match', 0, 0)",
        'an entrant outside the stage': "plan.match('x', 'Match', 0, 9)",
        'a name with capitals': "plan.match('Final', 'Match', 0, 1)",
        'a name with a slash': "plan.match('a/b', 'Match', 0, 1)",
        'a tie of no legs': "plan.tie('x', 'Tie', 0, 1, legs=0)",
        'a holder outside the tie': "plan.tie('x', 'Tie', 0, 1, holder=2)",
        'a side that is not later': "plan.tie('x', 'Tie', 0, 'two')",
    }
    for number, (what, call) in enumerate(attempts.items()):
        code = f"""
            class Reach:
                def options(self):
                    return {{}}

                def schedule(self, plan):
                    {call}
                    return {{'rounds': []}}

                def complete(self, plan, scheduled):
                    return False

                def standings(self, plan, scheduled):
                    return {{'ranking': None}}

            def register(registry):
                registry.provide('stage_type/1', 'reach', Reach())
        """
        identifier = f'reach{number}'
        _written(_plugins._folder(), identifier, {'stage_type/1': ['reach']}, code)
        config = _config([{'format': f'{identifier}.reach'}], count=3)
        _failing(lambda config=config: rules.evaluate(config, {}), identifier)
        assert what


def test_the_plan_gives_a_stage_type_copies_of_the_settings_and_the_answers():
    code = """
        seen = []

        class Spy:
            def options(self):
                return {}

            def schedule(self, plan):
                plan.settings['legs'] = 9
                plan.seeds.reverse()
                round_ = plan.round('Day', [plan.fixture('one', 'Match', 0, 1)])
                seen.append(round_)
                return {'rounds': [round_]}

            def complete(self, plan, scheduled):
                scheduled['rounds'].clear()
                return False

            def standings(self, plan, scheduled):
                return {'ranking': None}

        def register(registry):
            registry.provide('stage_type/1', 'spy', Spy())
    """
    _written(_plugins._folder(), 'spying', {'stage_type/1': ['spy']}, code)
    config = _config([{'format': 'spying.spy'}], count=3)
    view = rules.evaluate(config, {})
    # Neither the settings, the seeds nor the rounds it kept reach the evaluation.
    assert config['stages'][0]['legs'] == 1 and view['stages'][0]['entrants'] == [0, 1, 2]
    assert len(view['stages'][0]['rounds']) == 1
    seen = next(
        module
        for name, module in sys.modules.items()
        if name.startswith('cuju_plugin_spying') and name.endswith('.plugin')
    ).seen
    seen[0]['fixtures'].clear()
    assert len(rules.evaluate(config, {})['stages'][0]['rounds'][0]['fixtures']) == 1


def test_a_stage_type_cannot_choose_a_broken_disabled_or_unknown_name():
    _plugins.add(EXAMPLE)
    stage = [{'format': 'finale.league_final'}]
    assert _config(stage)['plugins']
    _written(_plugins._folder(), 'broke', {'stage_type/2': ['x'], 'tiebreaker/2': ['y']}, 'x = 1\n')
    assert 'protocol major 1' in _plugins.plugin('broke').broken
    for name in ('broke.x', 'finale.nothing', 'nothing', ''):
        with pytest.raises(rules.TournamentError) as caught:
            _config([{'format': name}])
        assert caught.value.message_id == 'server.tournament.choice'
        assert (
            'knockout' in str(caught.value) and 'broke' not in str(caught.value).split('found')[0]
        )
    with pytest.raises(rules.TournamentError) as caught:
        _config([{'tiebreakers': ['broke.y']}])
    assert caught.value.message_id == 'server.tournament.choice'
    # Turned off, the plugin leaves the choices and the options of its types with them.
    with pytest.raises(cuju.errors.ArgumentError):
        _plugins.get('stage_type', 'finale.league_final', overrides=['plugins.disabled=["finale"]'])
    _plugins.refresh()


# The same kinds as an installed package.


def test_a_stage_type_and_a_tiebreaker_install_as_a_package(tmp_path, monkeypatch):
    code = (EXAMPLE / 'plugin.py').read_text(encoding='utf-8')
    config = {
        'stages': [
            {
                'format': 'finale.league_final',
                'tiebreakers': ['finale.clean_sheets'],
                'final_name': 'Cup final',
            }
        ]
    }
    with monkeypatch.context() as patch:
        _installed(tmp_path, patch, 'finale', _PROVIDES, code)
        assert _plugins.plugin('finale').source == 'entry point'
        made = _config(config['stages'], count=4)
        assert made['plugins'] == [{'name': 'finale', 'version': None}]
        assert stage_cases.play(made)[-1]['complete']
    _plugins.refresh()
    with pytest.raises(rules.TournamentError):
        _config(config['stages'], count=4)
    # The same plugin as a folder gives the same tournament.
    _plugins.add(EXAMPLE)
    again = _config(config['stages'], count=4)
    assert stage_cases.play(again)[-1]['ranking'] == stage_cases.play(made)[-1]['ranking']


# Tiebreakers of a third party.


@pytest.mark.parametrize(
    ('answer', 'reason'),
    [
        ('None', 'a sort key for each'),
        ('{}', 'a sort key for each'),
        ('{m: "high" for m in rows}', 'finite number'),
        ('{m: float("nan") for m in rows}', 'finite number'),
        ('{m: True for m in rows}', 'finite number'),
        ('{m: () for m in rows}', 'finite number'),
        ('{m: (1, 2) if m % 2 else 3 for m in rows}', 'one shape'),
        ('{m: (1,) * 9 for m in rows}', 'finite number'),
        ('{**{m: 1 for m in rows}, 99: 2}', 'a sort key for each'),
    ],
)
def test_a_tiebreaker_that_answers_badly_is_marked_failing(answer, reason):
    code = f"""
        class Bad:
            def key(self, rows, options):
                return {answer}

        def register(registry):
            registry.provide('tiebreaker/1', 'bad', Bad())
    """
    _written(_plugins._folder(), 'tie-bad', {'tiebreaker/1': ['bad']}, code)
    config = _config([{'format': 'league', 'tiebreakers': ['tie-bad.bad']}], count=4)
    error = _failing(lambda: rules.evaluate(config, _level(4)), 'tie-bad')
    assert reason in str(error)


def test_a_tiebreaker_that_raises_ends_the_evaluation_with_its_message():
    code = """
        class Hurt:
            def key(self, rows, options):
                raise KeyError('goals')

        def register(registry):
            registry.provide('tiebreaker/1', 'hurt', Hurt())
    """
    _written(_plugins._folder(), 'tie-hurt', {'tiebreaker/1': ['hurt']}, code)
    config = _config([{'format': 'league', 'tiebreakers': ['tie-hurt.hurt']}], count=3)
    # The first call is made as soon as two entrants are level, which is before any result.
    error = _failing(lambda: rules.evaluate(config, {}), 'tie-hurt')
    assert 'goals' in str(error)
    assert stage_cases.play(_config([{'format': 'league'}], count=3))[-1]['complete']


def test_the_key_of_a_tiebreaker_gets_copies_of_the_rows_and_the_matches():
    code = """
        kept = []

        class Spy:
            def key(self, rows, options):
                kept.append((sorted(rows), sorted(options), list(options['entries'])))
                for row in rows.values():
                    row['points'] = 99
                options['entries'].clear()
                return {m: -m for m in rows}

        def register(registry):
            registry.provide('tiebreaker/1', 'spy', Spy())
    """
    _written(_plugins._folder(), 'tie-spy', {'tiebreaker/1': ['spy']}, code)
    config = _config([{'format': 'league', 'tiebreakers': ['tie-spy.spy']}], count=3)
    view = rules.evaluate(config, _level(3))
    # Level on points and ranked by the plugin's key, the higher key first, so entrant 0 leads.
    # The 99 points that it wrote into its rows never reached the table.
    rows = view['stages'][0]['tables'][0]['rows']
    assert [row['entrant'] for row in rows] == [0, 1, 2] and {row['points'] for row in rows} == {2}
    module = next(
        module
        for name, module in sys.modules.items()
        if name.startswith('cuju_plugin_tie_spy') and name.endswith('.plugin')
    )
    members, names, entries = module.kept[-1]
    assert members == [0, 1, 2]
    assert names == ['entries', 'points', 'purpose', 'seed']
    # Each match that it was given names its sides, its score and its cards, and no more.
    assert len(entries) == 3
    assert all(set(entry) == {'home', 'away', 'score', 'cards'} for entry in entries)


def test_a_tiebreaker_key_may_be_a_tuple_and_sorts_by_each_part():
    code = """
        class Pair:
            def key(self, rows, options):
                return {m: (-rows[m]['played'], m % 2) for m in rows}

        def register(registry):
            registry.provide('tiebreaker/1', 'pair', Pair())
    """
    _written(_plugins._folder(), 'tie-pair', {'tiebreaker/1': ['pair']}, code)
    config = _config([{'format': 'league', 'tiebreakers': ['tie-pair.pair']}], count=4)
    rows = rules.evaluate(config, _level(4, (0, 0)))['stages'][0]['tables'][0]['rows']
    # The odd entrants have the higher second part, and lots order those that stay level.
    assert {rows[0]['entrant'], rows[1]['entrant']} == {1, 3}
    assert {rows[2]['entrant'], rows[3]['entrant']} == {0, 2}
    assert [row['tiebreak'] for row in rows[:-1]] == ['lots', 'tie-pair.pair', 'lots']


# Plugin versions in the files that a plugin made.


def test_a_tournament_that_uses_a_plugin_records_its_name_and_version():
    standard = _config([{'format': 'league'}])
    assert 'plugins' not in standard
    _plugins.add(EXAMPLE)
    both = _config(
        [{'format': 'finale.league_final', 'tiebreakers': ['finale.clean_sheets']}],
        seeding='manual',
    )
    assert both['plugins'] == [{'name': 'finale', 'version': '1.0'}]
    assert _plugins.stamp(['points', 'finale.x', 'finale.y']) == [
        {'name': 'finale', 'version': '1.0'}
    ]
    assert _plugins.stamp(['absent.x']) == [{'name': 'absent', 'version': None}]
    assert _plugins.stamp(['points']) == []
    assert _plugins.stamps([{'name': 'a', 'version': '1'}, {'name': 3}, 'x', {'name': 'B'}]) == [
        {'name': 'a', 'version': '1'}
    ]
    assert _plugins.stamps('x') == [] and _plugins.stamps(None) == []
    assert _plugins.unavailable(both['plugins']) == []


def _cup(count=4):
    return {
        'name': 'Plugin cup',
        'seed': 5,
        'seeding': 'manual',
        'entrants': stage_cases.entrants(count),
        'rating_scale': 'table',
        'stages': [{'format': 'finale.league_final', 'scenario': SCENARIO}],
    }


def _player(match, teams, provenance, progress):
    seed = match['seed']
    return {'score': [seed % 3, seed // 3 % 3], 'cards': [[0, 0], [0, 0]], 'match': None}


def test_a_tournament_of_a_plugin_stays_readable_and_read_only_without_the_plugin():
    _plugins.add(EXAMPLE)
    cups = runner.Tournaments(player=_player, start=False)
    entry = cups.create(_cup(), run=True)
    identifier = entry['id']
    assert cups.run(identifier, report=lambda line: None) == 'done'
    shown = cups.get(identifier)
    assert shown['state'] == 'done' and shown['champion'] is not None
    stored = json.loads((runner.folder() / f'{identifier}.json').read_text(encoding='utf-8'))
    assert stored['config']['plugins'] == [{'name': 'finale', 'version': '1.0'}]
    assert stored['view']['complete'] is True
    cups.close()
    # The plugin goes. The file still shows what it showed, and changes nothing.
    _plugins.remove('finale')
    again = runner.Tournaments(player=_player, start=False, interval=0.01)
    later = again.get(identifier)
    assert later is not None and later['champion_index'] == shown['champion_index']
    assert later['ranking'] == shown['ranking'] and later['played'] == shown['played']
    assert [m['key'] for m in later['matches']] == [m['key'] for m in shown['matches']]
    assert later['state'] == 'stopped' and 'finale 1.0' in later['error']
    assert later['error_id'] == 'server.tournament.plugins_gone'
    for call in (
        lambda: again.resume(identifier),
        lambda: again.add_legs(identifier, 1),
        lambda: again.forfeit(identifier, '0/mg1', 'home'),
    ):
        with pytest.raises(cuju.errors.ArgumentError) as caught:
            call()
        assert caught.value.message_id == 'server.tournament.plugins_gone'
    again.tick()
    assert again.get(identifier)['played'] == shown['played']
    # The plugin comes back, and the tournament plays and changes again.
    _plugins.add(EXAMPLE)
    back = runner.Tournaments(player=_player, start=False, interval=0.01)
    assert back.get(identifier)['champion_index'] == shown['champion_index']
    back.close()
    again.close()
    assert again.remove(identifier) is True


def test_a_tournament_of_a_plugin_that_is_gone_before_it_ends_opens_stopped():
    _plugins.add(EXAMPLE)
    cups = runner.Tournaments(player=_player, start=False)
    entry = cups.create(_cup(), run=True)
    identifier = entry['id']
    cups.tick()
    assert _wait(lambda: cups.get(identifier)['played'] >= 1)
    cups.close()
    _plugins.remove('finale')
    again = runner.Tournaments(player=_player, start=False, interval=0.01)
    later = again.get(identifier)
    assert later['state'] == 'stopped' and later['played'] >= 1 and later['champion'] is None
    again.tick()
    assert again.get(identifier)['played'] == later['played']
    again.close()


def _wait(predicate, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_a_file_that_names_a_missing_plugin_and_keeps_no_view_is_skipped(caplog):
    _plugins.add(EXAMPLE)
    cups = runner.Tournaments(player=_player, start=False)
    entry = cups.create(_cup(), run=False)
    cups.close()
    path = runner.folder() / f'{entry["id"]}.json'
    stored = json.loads(path.read_text(encoding='utf-8'))
    del stored['view'], stored['seal']
    path.write_text(json.dumps(stored), encoding='utf-8')
    _plugins.remove('finale')
    again = runner.Tournaments(player=_player, start=False, interval=0.01)
    assert again.get(entry['id']) is None
    again.close()


def test_the_rating_method_and_rating_run_settings_record_the_plugin_that_made_them():
    from support import standards_cases

    code = """
        class Flat:
            def fit(self, rows, anchor, options):
                keys = {key for row in rows for key in row[:2]} | {anchor}
                return {
                    k: {'rating': 1000.0, 'deviation': 0.0 if k == anchor else 1.0} for k in keys
                }

        def register(registry):
            registry.provide('rating_method/1', 'flat', Flat())
    """
    _written(_plugins._folder(), 'flats', {'rating_method/1': ['flat']}, code)
    standards_cases._keep_results()
    elo = _store.tournament_elo(None)
    assert 'plugins' not in elo
    assert _store.keep_method(elo, 'points') == {**elo, 'method': 'points'} and 'plugins' not in elo
    _store.keep_method(elo, 'flats.flat')
    assert elo['method'] == 'flats.flat' and elo['plugins'] == [{'name': 'flats', 'version': None}]
    # Read without the plugin, the field still shows the method and the plugin it came from.
    _plugins.remove('flats')
    read = _store.tournament_elo(json.loads(json.dumps(elo)))
    assert read['method'] == 'flats.flat' and read['plugins'] == elo['plugins']
    assert (
        _store.tournament_elo({'plugins': [{'name': 'x', 'version': 3}, 5]})['method'] == 'points'
    )
    assert 'plugins' not in _store.tournament_elo({'plugins': 'x'})
    _store.keep_method(elo, 'points')
    assert 'plugins' not in elo


def test_a_rating_run_keeps_the_plugins_of_its_method_rule_and_pairing():
    from support import standards_cases

    code = """
        class Flat:
            def fit(self, rows, anchor, options):
                keys = {key for row in rows for key in row[:2]} | {anchor}
                return {
                    k: {'rating': 1000.0, 'deviation': 0.0 if k == anchor else 1.0} for k in keys
                }

        class Everything:
            def keep(self, rows, options):
                return rows

        def register(registry):
            registry.provide('rating_method/1', 'flat', Flat())
            registry.provide('evidence/1', 'all', Everything())
    """
    _written(_plugins._folder(), 'runs', {'rating_method/1': ['flat'], 'evidence/1': ['all']}, code)
    standards_cases._keep_results()
    measured = [_ratings.builtin_side(0.2), _ratings.builtin_side(0.8)]
    standard = _ratings.create(measured, SCENARIO, _ratings.settings())
    assert 'plugins' not in standard and 'plugins' not in _ratings.view(standard)
    options = ['ratings.method="runs.flat"', 'ratings.rule="runs.all"']
    document = _ratings.create(measured, SCENARIO, _ratings.settings(overrides=options))
    assert document['plugins'] == [{'name': 'runs', 'version': None}]
    assert document['settings']['method'] == 'runs.flat'
    # A run read without the plugin still shows its settings and the plugin it named.
    _plugins.remove('runs')
    shown = _ratings.view(_store.run(document['id']))
    assert shown['plugins'] == [{'name': 'runs', 'version': None}]
    assert shown['settings']['method'] == 'runs.flat'
    assert _ratings._plugin_fields(['points', 'default', 'least_settled']) == {}
    assert _ratings._plugin_fields_of({'plugins': 'x'}) == {}
