# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of the four protocols that the standards and third parties implement.

The standards are held byte for byte to what they gave before they were plugins, and a plugin of
a third party, in a folder and as an installed package, is held to each kind, with the paths of a
plugin that is broken, that fails and that is turned off.
"""

import json
import os
import py_compile
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

import cuju.errors
from cuju.arena import plugins as _plugins
from cuju.arena._evaluate import ratings as _ratings
from cuju.arena._evaluate import schedule as _schedule
from cuju.arena._store import ratings as _store
from cuju.arena._tournaments import tournament as rules
from support import standards_cases
from support.plugin_files import installed as _installed
from support.plugin_files import written as _written

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parents[1] / 'support' / 'plugins' / 'standards.json'
EXAMPLE = ROOT / 'docs' / 'plugins' / 'goal-rating'
SCENARIO = standards_cases.SCENARIO
TEAMS = [{'id': 'builtin', 'kind': 'builtin', 'name': 'Built-in AI', 'style': {}}]


@pytest.fixture(autouse=True)
def fresh(arena_home):
    _plugins.refresh()
    yield
    _plugins.refresh()


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
    found = json.loads(standards_cases.text())
    assert set(found) == set(expected) == {'ratings', 'seeding', 'pairing'}
    for group in expected:
        assert found[group] == expected[group], group
    assert len(expected['ratings']) == 8 and len(expected['seeding']) == 27
    assert len(expected['pairing']) == 10
    assert GOLDEN.read_text(encoding='utf-8') == standards_cases.text()


def test_every_standard_is_registered_by_the_plugin_builtin():
    built = _plugins.plugin('builtin')
    assert built.broken is None and built.requires is None
    assert built.names('rating_method') == ['points', 'bradley_terry']
    assert built.names('evidence') == ['default']
    assert built.names('seeding') == ['random', 'manual', 'rating', 'standing']
    assert built.names('pairing') == ['least_settled']
    assert _plugins.names('rating_method') == ['points', 'bradley_terry']
    assert built.as_dict()['provides']['seeding/1'] == ['random', 'manual', 'rating', 'standing']


# Rating methods.

_GOALS = {'rating_method/1': ['rating']}


def _results():
    standards_cases._keep_results()


def test_a_folder_plugin_adds_a_rating_method_that_ranks_a_table():
    _plugins.add(EXAMPLE)
    _results()
    table = _ratings.table(SCENARIO, method='goals.rating')
    assert table['method'] == 'goals.rating'
    assert table['methods']['goals.rating'] == {'rule': 'plugin', 'plugin': 'goals'}
    anchor = next(row for row in table['models'] if row['anchor'])
    assert (anchor['rating'], anchor['low'], anchor['high'], anchor['deviation']) == (
        1000.0,
        1000.0,
        1000.0,
        0.0,
    )
    for row in table['models']:
        assert row['rating'] == row['goals.rating']['rating']
        assert row['low'] <= row['rating'] <= row['high'] and set(row) >= {
            'points',
            'bradley_terry',
        }
    assert [row['rating'] for row in table['models']] == sorted(
        (row['rating'] for row in table['models']), reverse=True
    )
    # The standards give the same rows beside it, so a view that compares them keeps working.
    standard = _ratings.table(SCENARIO)
    for row, other in zip(
        sorted(table['models'], key=lambda row: row['id']),
        sorted(standard['models'], key=lambda row: row['id']),
        strict=True,
    ):
        assert row['points'] == other['points'] and row['bradley_terry'] == other['bradley_terry']


def test_a_rating_method_is_chosen_by_the_settings_and_as_an_installed_package(
    tmp_path, monkeypatch
):
    code = (EXAMPLE / 'plugin.py').read_text(encoding='utf-8')
    _results()
    settings = ['ratings.method="goals.rating"']
    with monkeypatch.context() as patch:
        _installed(tmp_path, patch, 'goals', _GOALS, code)
        assert _plugins.plugin('goals').source == 'entry point'
        table = _ratings.table(SCENARIO, overrides=settings)
        assert table['method'] == 'goals.rating' and table['anchor']['rating'] == 1000.0
        assert _ratings.overview(overrides=settings)['method'] == 'goals.rating'
    _plugins.refresh()
    with pytest.raises(cuju.errors.ArgumentError):
        _ratings.table(SCENARIO, overrides=settings)
    # The same plugin as a folder gives the same table.
    _plugins.add(EXAMPLE)
    assert _ratings.table(SCENARIO, overrides=settings) == table


def test_an_unknown_or_unusable_method_is_refused_with_the_choices(arena_home):
    for method in ('glicko', 'goals.rating'):
        with pytest.raises(cuju.errors.ArgumentError) as caught:
            _ratings.table(SCENARIO, method=method)
        assert caught.value.message_id == 'server.ratings.method'
        assert 'points, bradley_terry' in str(caught.value)
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _ratings.settings(overrides=['ratings.method="glicko"'])
    assert caught.value.message_id == 'server.ratings.method'
    _plugins.add(EXAMPLE)
    off = ['plugins.disabled=["goals"]', 'ratings.method="goals.rating"']
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _ratings.table(SCENARIO, overrides=off)
    assert caught.value.message_id == 'server.ratings.method'
    assert 'goals.rating' not in str(caught.value).split(', found')[0]


@pytest.mark.parametrize(
    ('answer', 'reason'),
    [
        ('None', 'a mapping of each model'),
        ('{}', 'a rating of the model'),
        ('{k: {"rating": 1000.0} for k in keys}', 'number as deviation'),
        ('{k: {"rating": float("nan"), "deviation": 1.0} for k in keys}', 'finite'),
        ('{k: {"rating": 1000.0, "deviation": -1.0} for k in keys}', 'deviation of'),
        ('{k: {"rating": 1000.0, "deviation": 5.0, "low": 1100.0} for k in keys}', 'in that order'),
        ('{k: {"rating": 900.0, "deviation": 0.0} for k in keys}', 'the anchor at 1000'),
        ('{k: {"rating": 1000.0, "deviation": 3.0} for k in keys}', 'the anchor at 1000'),
        ('{k: {"rating": True, "deviation": 3.0} for k in keys}', 'number as rating'),
    ],
)
def test_a_rating_method_that_answers_badly_is_marked_failing(answer, reason):
    code = f"""
        class Bad:
            def fit(self, rows, anchor, options):
                keys = {{key for row in rows for key in row[:2]}} | {{anchor}}
                return {answer}

        def register(registry):
            registry.provide('rating_method/1', 'bad', Bad())
    """
    _written(_plugins._folder(), 'badly', _GOALS | {'rating_method/1': ['bad']}, code)
    _results()
    error = _failing(lambda: _ratings.table(SCENARIO, method='badly.bad'), 'badly')
    assert reason in str(error) and 'badly' in str(error)
    assert 'badly' in _plugins.plugin('badly').as_dict()['id']


def test_a_rating_method_that_raises_ends_the_table_with_its_message():
    code = """
        class Hurt:
            def fit(self, rows, anchor, options):
                raise ArithmeticError('divided by the matches')

        def register(registry):
            registry.provide('rating_method/1', 'hurt', Hurt())
    """
    _written(_plugins._folder(), 'hurts', {'rating_method/1': ['hurt']}, code)
    _results()
    error = _failing(lambda: _ratings.table(SCENARIO, method='hurts.hurt'), 'hurts')
    assert 'divided by the matches' in str(error)
    # The standards still work, and the tables that choose them are not touched.
    assert _ratings.table(SCENARIO)['method'] == 'points'


def test_a_broken_rating_method_cannot_be_chosen():
    _written(_plugins._folder(), 'broke', {'rating_method/2': ['x']}, 'x = 1\n')
    assert 'protocol major 1' in _plugins.plugin('broke').broken
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _ratings.table(SCENARIO, method='broke.x')
    assert caught.value.message_id == 'server.ratings.method'


def test_the_fit_of_a_method_gets_a_copy_of_the_games_and_the_options():
    code = """
        seen = []

        class Spy:
            def fit(self, rows, anchor, options):
                seen.append((list(rows), anchor, dict(options)))
                keys = {key for row in rows for key in row[:2]} | {anchor}
                rows.clear()
                return {k: {'rating': options['anchor_rating'], 'deviation': 0.0} for k in keys}

        def register(registry):
            registry.provide('rating_method/1', 'spy', Spy())
    """
    _written(_plugins._folder(), 'spy', {'rating_method/1': ['spy']}, code)
    _results()
    first = _ratings.table(SCENARIO, method='spy.spy')
    # Clearing the games it was given changed no row of the table.
    assert first['results'] == 60 and first['anchor']['matches'] > 0
    method = _plugins.get('rating_method', 'spy.spy')
    module = sys.modules[type(method).__module__]
    games, anchor, options = module.seen[0]
    assert len(games) == 60 and anchor == 'builtin:0.60'
    assert options['anchor_rating'] == 1000.0 and options['confidence'] == 0.95
    assert options['quantile'] == pytest.approx(1.95996, abs=1e-4)


def test_a_replaced_method_changes_the_table_that_was_cached():
    def plugin(factor):
        return f"""
            class Scale:
                def fit(self, rows, anchor, options):
                    keys = {{key for row in rows for key in row[:2]}} | {{anchor}}
                    return {{
                        k: {{
                            'rating': 1000.0 + (0 if k == anchor else {factor}),
                            'deviation': 0.0 if k == anchor else 1.0,
                        }}
                        for k in keys
                    }}

            def register(registry):
                registry.provide('rating_method/1', 'scale', Scale())
        """

    first = _written(_plugins._folder(), 'scales', {'rating_method/1': ['scale']}, plugin(10))
    script = first / 'plugin.py'
    second = 1_700_000_000_000_000_000
    os.utime(script, ns=(second, second))
    py_compile.compile(str(script), doraise=True)
    _results()
    one = _ratings.table(SCENARIO, method='scales.scale')
    assert any(row['rating'] == 1010.0 for row in one['models'])
    script.write_text(textwrap.dedent(plugin(20)), encoding='utf-8')
    os.utime(script, ns=(second, second + 500_000_000))
    two = _ratings.table(SCENARIO, method='scales.scale')
    assert any(row['rating'] == 1020.0 for row in two['models'])


# Evidence rules.


def test_the_evidence_rule_default_keeps_the_sources_that_count():
    _results()
    everything = _ratings.table(SCENARIO)
    only = _ratings.table(SCENARIO, overrides=['ratings.sources=["match"]'])
    assert everything['results'] == 60 and only['results'] == 15
    assert _ratings.settings()['rule'] == 'default'


def test_a_plugin_evidence_rule_drops_rows_after_the_integrity_checks():
    code = """
        class Recent:
            def keep(self, rows, options):
                assert options['sources']
                return [row for row in rows if row['time'] >= 1030.0]

        def register(registry):
            registry.provide('evidence/1', 'recent', Recent())
    """
    _written(_plugins._folder(), 'fresh', {'evidence/1': ['recent']}, code)
    _results()
    # The arena drops a repeat of a result before any rule runs, so the rule never sees it.
    again = _store.results(SCENARIO)[0]
    assert _store.record({**again, 'time': 5000.0}) is None
    table = _ratings.table(SCENARIO, overrides=['ratings.rule="fresh.recent"'])
    assert table['results'] == 30
    assert table['results'] == len([row for row in _store.results(SCENARIO) if row['time'] >= 1030])
    scenarios = _ratings.scenarios(overrides=['ratings.rule="fresh.recent"'])
    assert scenarios[0]['results'] == 30


@pytest.mark.parametrize(
    ('answer', 'reason'),
    [
        ('None', 'a list of rows'),
        ('list(reversed(rows))', 'in their order'),
        ('rows + rows[:1]', 'each once'),
        ('[dict(row) for row in rows]', 'those given'),
        ('[{"source": "match"}]', 'those given'),
    ],
)
def test_an_evidence_rule_cannot_add_change_or_reorder_a_row(answer, reason):
    code = f"""
        class Cheat:
            def keep(self, rows, options):
                return {answer}

        def register(registry):
            registry.provide('evidence/1', 'cheat', Cheat())
    """
    _written(_plugins._folder(), 'cheats', {'evidence/1': ['cheat']}, code)
    _results()
    error = _failing(
        lambda: _ratings.table(SCENARIO, overrides=['ratings.rule="cheats.cheat"']), 'cheats'
    )
    assert reason in str(error)


def test_an_unknown_evidence_rule_is_refused_by_the_settings():
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _ratings.settings(overrides=['ratings.rule="loose"'])
    assert caught.value.message_id == 'server.plugins.unknown'
    assert 'default' in str(caught.value)


def test_an_evidence_rule_as_an_installed_package(tmp_path, monkeypatch):
    code = """
        class Home:
            def keep(self, rows, options):
                return [row for row in rows if row['home']['key'] != 'rex']

        def register(registry):
            registry.provide('evidence/1', 'home', Home())
    """
    _installed(tmp_path, monkeypatch, 'orbit', {'evidence/1': ['home']}, code)
    _results()
    table = _ratings.table(SCENARIO, overrides=['ratings.rule="orbit.home"'])
    assert 0 < table['results'] < 60
    assert any(row['home']['key'] == 'rex' for row in _store.results(SCENARIO))


# Seeding rules.


def _config(seeding='random', later='standing', count=8):
    return rules.check(
        {
            'name': 'Cup',
            'seed': 4,
            'seeding': seeding,
            'entrants': standards_cases._entrants(count),
            'rating_scale': 'table',
            'stages': [
                {'format': 'league', 'scenario': SCENARIO, 'advance': 4},
                {'format': 'knockout', 'scenario': SCENARIO, 'seeding': later, 'settle': ['lots']},
            ],
        },
        TEAMS,
    )


_REVERSE = """
    class Reverse:
        def order(self, entrants, context):
            assert set(context) == {'stage', 'seed', 'rng'}
            return list(reversed(range(len(entrants))))

    def register(registry):
        registry.provide('seeding/1', 'reverse', Reverse())
"""


def test_a_seeding_rule_of_a_plugin_orders_the_first_stage_and_the_later_ones():
    _written(_plugins._folder(), 'draws', {'seeding/1': ['reverse']}, _REVERSE)
    config = _config('draws.reverse', 'draws.reverse')
    assert rules.seeding(config) == list(reversed(range(8)))
    assert rules._reseed(config, config['stages'][1], 1, [5, 2, 7, 0]) == [0, 7, 2, 5]
    assert _config('manual')['seeding'] == 'manual'
    assert rules.seeding(_config('manual')) == list(range(8))


def test_a_seeding_rule_as_an_installed_package_gets_the_context_of_its_stage(
    tmp_path, monkeypatch
):
    code = """
        class Shuffle:
            def order(self, entrants, context):
                found = list(range(len(entrants)))
                context['rng'].shuffle(found)
                return found

        def register(registry):
            registry.provide('seeding/1', 'shuffle', Shuffle())
    """
    _installed(tmp_path, monkeypatch, 'orbit', {'seeding/1': ['shuffle']}, code)
    mine = _config('orbit.shuffle', 'orbit.shuffle')
    standard = _config('random', 'random')
    # The generator is the one of the standard, so the same shuffle gives the same order.
    assert rules.seeding(mine) == rules.seeding(standard)
    assert rules._reseed(mine, mine['stages'][1], 1, [3, 1, 0, 2]) == rules._reseed(
        standard, standard['stages'][1], 1, [3, 1, 0, 2]
    )


def test_a_seeding_name_that_no_plugin_offers_is_refused_with_the_choices():
    with pytest.raises(rules.TournamentError, match=r'orbit\.shuffle'):
        _config('orbit.shuffle')
    with pytest.raises(rules.TournamentError, match='manual, rating'):
        _config('loose')
    with pytest.raises(rules.TournamentError):
        _config('random', 'manual')


@pytest.mark.parametrize(
    ('answer', 'reason'),
    [
        ('None', 'each position'),
        ('[0, 0, 1, 2, 3, 4, 5, 6]', 'each position'),
        ('[0, 1, 2]', 'each position'),
        ('list(range(9))', 'each position'),
    ],
)
def test_a_seeding_rule_that_loses_an_entrant_is_marked_failing(answer, reason):
    code = f"""
        class Lossy:
            def order(self, entrants, context):
                return {answer}

        def register(registry):
            registry.provide('seeding/1', 'lossy', Lossy())
    """
    _written(_plugins._folder(), 'lossy', {'seeding/1': ['lossy']}, code)
    config = _config('lossy.lossy')
    error = _failing(lambda: rules.seeding(config), 'lossy')
    assert reason in str(error)


def test_a_turned_off_seeding_plugin_leaves_the_names_and_refuses_the_draw(arena_home):
    _written(_plugins._folder(), 'draws', {'seeding/1': ['reverse']}, _REVERSE)
    config = _config('draws.reverse')
    (arena_home / 'arena.toml').write_text('[plugins]\ndisabled = ["draws"]\n', encoding='utf-8')
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        rules.seeding(config)
    assert caught.value.message_id == 'server.plugins.disabled'
    with pytest.raises(rules.TournamentError):
        _config('draws.reverse')


# Pairing rules.

_FIRST = """
    class First:
        def next_pairs(self, state, slots):
            assert slots == 1
            for first, second in state.pairs:
                if state.limit is None or state.meetings(first, second) < state.limit:
                    return [(first, second)]
            return []

    def register(registry):
        registry.provide('pairing/1', 'first', First())
"""


def _sides():
    return ['p', 'weak', 'level', 'strong']


def _play(schedule, strength):
    rounds = 0
    while not schedule.done:
        for pairing in schedule.due():
            gap = strength[pairing['home']] - strength[pairing['away']]
            schedule.finish(pairing['number'], [1, 0] if gap > 0 else [0, 1] if gap < 0 else [1, 1])
        rounds += 1
        assert rounds < 10000
    return schedule


_STRENGTH = {'p': 2, 'weak': 0, 'level': 2, 'strong': 4}


def test_a_pairing_rule_of_a_plugin_picks_every_next_match():
    _written(_plugins._folder(), 'order', {'pairing/1': ['first']}, _FIRST)
    pairs = [('p', other) for other in _sides()[1:]]
    schedule = _schedule.Schedule(
        _sides(), mode='on_demand', pairs=pairs, limit=2, settle=None, pairing='order.first'
    )
    _play(schedule, _STRENGTH)
    played = [(entry['home'], entry['away']) for entry in schedule.played]
    # The rule always takes the first pair that has meetings left, so each pair meets twice in turn.
    assert played == [pair for pair in pairs for _ in range(2)]
    assert schedule.pairing == 'order.first'


def test_a_pairing_rule_as_an_installed_package_ends_a_phase_by_returning_nothing(
    tmp_path, monkeypatch
):
    code = """
        class Stop:
            def next_pairs(self, state, slots):
                return [] if state.number >= 3 else [state.pairs[0]]

        def register(registry):
            registry.provide('pairing/1', 'stop', Stop())
    """
    _installed(tmp_path, monkeypatch, 'orbit', {'pairing/1': ['stop']}, code)
    pairs = [('p', other) for other in _sides()[1:]]
    schedule = _schedule.Schedule(
        _sides(), mode='on_demand', pairs=pairs, limit=9, settle=None, pairing='orbit.stop'
    )
    _play(schedule, _STRENGTH)
    assert len(schedule.played) == 3 and schedule.done


@pytest.mark.parametrize(
    ('answer', 'reason'),
    [
        ('None', 'at most 1 pairing'),
        ('[("p", "weak"), ("p", "level")]', 'at most 1 pairing'),
        ('[("weak", "p")]', 'a pair that may meet'),
        ('[("p", "nobody")]', 'a pair that may meet'),
        ('[("p", "p")]', 'a pair that may meet'),
        ('[("p",)]', 'a pair of two sides'),
    ],
)
def test_a_pairing_rule_that_answers_a_pair_that_may_not_meet_is_marked_failing(answer, reason):
    code = f"""
        class Lost:
            def next_pairs(self, state, slots):
                return {answer}

        def register(registry):
            registry.provide('pairing/1', 'lost', Lost())
    """
    _written(_plugins._folder(), 'lost', {'pairing/1': ['lost']}, code)
    pairs = [('p', other) for other in _sides()[1:]]
    schedule = _schedule.Schedule(
        _sides(), mode='on_demand', pairs=pairs, limit=2, settle=None, pairing='lost.lost'
    )
    error = _failing(schedule.due, 'lost')
    assert reason in str(error)


def test_a_pair_that_the_phase_did_not_give_as_explicit_may_swap_its_sides():
    code = """
        class Swap:
            def next_pairs(self, state, slots):
                assert not state.explicit
                first, second = state.pairs[0]
                return [(second, first)]

        def register(registry):
            registry.provide('pairing/1', 'swap', Swap())
    """
    _written(_plugins._folder(), 'swaps', {'pairing/1': ['swap']}, code)
    schedule = _schedule.Schedule(
        _sides(), mode='on_demand', each=2, most=1, settle=None, pairing='swaps.swap'
    )
    (pairing,) = schedule.due()
    first, second = schedule._pairs[0]
    assert (pairing['home'], pairing['away']) == (second, first)


def test_a_schedule_refuses_a_pairing_rule_that_no_plugin_offers():
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _schedule.Schedule(['a', 'b'], mode='on_demand', pairs=[('a', 'b')], limit=1, pairing='x')
    assert caught.value.message_id == 'server.plugins.unknown'
    assert 'least_settled' in str(caught.value)


def test_the_state_of_a_pick_is_a_view_of_the_results_its_match_may_know():
    code = """
        picks = []

        class Look:
            def next_pairs(self, state, slots):
                picks.append(
                    (state.number, dict(state.matches), dict(state.games), dict(state.homes))
                )
                return [state.pairs[state.number % len(state.pairs)]]

        def register(registry):
            registry.provide('pairing/1', 'look', Look())
    """
    _written(_plugins._folder(), 'looks', {'pairing/1': ['look']}, code)
    pairs = [('p', other) for other in _sides()[1:]]
    schedule = _schedule.Schedule(
        _sides(),
        mode='on_demand',
        pairs=pairs,
        limit=9,
        settle=None,
        most=6,
        at_once=2,
        pairing='looks.look',
    )
    _play(schedule, _STRENGTH)
    module = next(
        module
        for name, module in sys.modules.items()
        if name.startswith('cuju_plugin_looks') and hasattr(module, 'picks')
    )
    # A pick is asked more than once, by the schedule and by a view of what comes next. Each ask
    # sees the matches started before its number, each started match counting for two sides and
    # one home side, and only the results that were known by then.
    assert sorted({number for number, *_ in module.picks})[:6] == [0, 1, 2, 3, 4, 5]
    for number, matches, games, homes in module.picks:
        assert sum(games.values()) == 2 * number and sum(homes.values()) == number
        assert sum(matches.values()) <= 2 * number and set(matches) == set(_sides())


def test_the_rating_run_and_the_evaluation_choose_the_pairing_by_their_settings():
    _plugins.add(
        _written(Path(_plugins._folder().parent) / 'src', 'order', {'pairing/1': ['first']}, _FIRST)
    )
    found = _ratings.settings(overrides=['ratings.run.pairing="order.first"'])
    assert found['run']['pairing'] == 'order.first'
    sides = [_ratings.builtin_side(0.2), _ratings.builtin_side(0.8), _ratings.anchor_side(found)]
    document = _ratings.create(sides, SCENARIO, found)
    assert document['plan']['pairing'] == 'order.first'
    assert _ratings._schedule_of(document).pairing == 'order.first'
    standard = _ratings.create(sides, SCENARIO, _ratings.settings())
    assert 'pairing' not in standard['plan']
    assert _ratings._schedule_of(standard).pairing == 'least_settled'
    shutil.rmtree(Path(_plugins._folder().parent) / 'src')
