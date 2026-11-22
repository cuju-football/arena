# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of a tournament seeded by rating, the evidence it asks and its seeding phase."""

import json
import threading
import time

import numpy as np
import pytest

import cuju.errors
from cuju.arena._config import layers as _layers
from cuju.arena._evaluate import ratings as _evidence
from cuju.arena._evaluate import runner as _runner
from cuju.arena._store import ratings as _ratings
from cuju.arena._tournaments import tournament as rules
from cuju.arena._tournaments import tournament_runner as runner

_BUILTIN = {
    'id': 'builtin',
    'kind': 'builtin',
    'name': 'Built-in AI',
    'style': {'kit': {'primary': [255, 111, 0], 'secondary': [255, 255, 255]}},
}
_TEAMS = [
    _BUILTIN,
    *(
        {'id': name, 'kind': 'submission', 'name': name.title(), 'format': '11v11'}
        for name in ('reds', 'blues')
    ),
]
_SCENARIO = 'academy_empty_goal_close'
_STAGE = {'format': 'knockout', 'scenario': _SCENARIO, 'settle': ['lots']}
_ANCHOR = 'builtin:0.60'


def _entrants(*levels, **fields):
    return [{'kind': 'builtin', 'difficulty': level, **fields} for level in levels]


def _config(entrants, **options):
    return {
        'name': 'Seeded cup',
        'seed': 21,
        'seeding': 'rating',
        'entrants': entrants,
        'stages': [dict(_STAGE)],
        # The ratings that a test states read on the scale of the tables, with or without
        # `evidence`.
        'rating_scale': 'table',
        **options,
    }


def _strength_player(calls=None):
    """A player of the draw in whose matches the side of the higher difficulty wins."""

    def player(match, teams, provenance, progress):
        if calls is not None:
            calls.append(match['key'])
        home, away = match['ability']
        return {'score': [1, 0] if home > away else [0, 1], 'match': None}

    return player


def _rater(calls=None, gate=None, fail=()):
    """A player of the seeding phase in whose matches the higher difficulty wins by a goal."""

    def player(spec):
        if gate is not None:
            gate(spec)
        if calls is not None:
            calls.append((spec['number'], spec['home'], spec['away'], spec['index']))
        if spec['number'] in fail:
            raise RuntimeError('the policy of Reds failed, ValueError: no')
        home, away = (float(key.split(':')[1]) for key in (spec['home'], spec['away']))
        goals = [1, 0] if home > away else [0, 1] if home < away else [1, 1]
        return _runner.Played([goals], [3], np.zeros((3, 1, 0), dtype=np.uint8))

    return player


def _rate(*levels, **run):
    """Gathers the evidence that the settings ask for the built-in AI at some difficulties."""
    found = _evidence.settings({'ratings': {'run': {'seed': 2, 'mode': 'on_demand', **run}}})
    sides = [_evidence.builtin_side(level) for level in levels]
    document = _evidence.create(sides, _SCENARIO, found)
    return _evidence.execute(document['id'], player=_rater())


def _cups(calls=None, raters=None, **options):
    return runner.Tournaments(
        player=_strength_player(calls), rater=_rater(raters), start=False, interval=0.01, **options
    )


@pytest.fixture
def on_demand(tmp_path, monkeypatch):
    """Has the rating runs play on demand, where the built-in AI alone would play in batches."""
    path = tmp_path / 'on-demand.toml'
    path.write_text('[ratings.run]\nmode = "on_demand"\n', encoding='utf-8')
    monkeypatch.setenv(_layers.CONFIG_VARIABLE, str(path))


def test_the_shipped_setting_equals_that_of_the_rules():
    assert _layers.table('tournament') == rules.EVIDENCE_DEFAULTS == {'evidence': 'refuse'}
    assert runner.tournament_settings() == rules.EVIDENCE_DEFAULTS
    config = rules.check(_config(_entrants(0.2, 0.4)), _TEAMS)
    assert config['evidence'] == 'refuse'
    # A tournament takes the setting of its machine, and its own configuration comes first.
    taken = rules.check(_config(_entrants(0.2, 0.4)), _TEAMS, table={'evidence': 'collect'})
    assert taken['evidence'] == 'collect'
    own = rules.check(
        _config(_entrants(0.2, 0.4), evidence='proceed'), _TEAMS, table={'evidence': 'collect'}
    )
    assert own['evidence'] == 'proceed'
    with pytest.raises(rules.TournamentError, match='what to do when an entrant lacks evidence'):
        rules.check(_config(_entrants(0.2, 0.4), evidence='ask'), _TEAMS)
    # The keys of the seeding phase of before are no settings of a tournament any more.
    with pytest.raises(rules.TournamentError):
        rules.check(_config(_entrants(0.2, 0.4), seeding_matches=2), _TEAMS)


def test_the_phase_measures_the_entrants_that_lack_evidence():
    def pool(config, lacking=(), **entrant):
        checked = rules.check(config, _TEAMS)
        for index, found in enumerate(checked['entrants']):
            found['evidence'] = {'lacking': {'matches': 4} if index in lacking else None}
        return rules.seeding_pool(checked)

    given = _entrants(0.1, 0.3, 0.5, 0.7)
    given[1]['rating'] = 1100
    collect = _config(given, evidence='collect')
    # The first and the last lack, and the second states its rating, so it lacks nothing.
    assert pool(collect, lacking=(0, 1, 3)) == ([0, 3], [1, 2])
    assert pool(collect) == ([], [])
    # No phase unless the tournament collects and ratings seed a draw.
    for options in ({'evidence': 'refuse'}, {'evidence': 'proceed'}, {'seeding': 'random'}):
        assert pool({**collect, **options}, lacking=(0, 3)) == ([], [])
    # A later stage that seeds by rating asks for the phase as well.
    stages = [
        {'format': 'league', 'scenario': _SCENARIO, 'advance': 2},
        {**_STAGE, 'seeding': 'rating'},
    ]
    later = {**collect, 'seeding': 'random', 'stages': stages}
    assert pool(later, lacking=(0,)) == ([0], [1, 2, 3])
    assert rules.seeded_by_rating(rules.check(later, _TEAMS))
    assert not rules.seeded_by_rating(rules.check({**collect, 'seeding': 'manual'}, _TEAMS))
    # A person plays no seeding match, and the entrants of one team share its matches.
    mixed = [
        {'kind': 'person', 'name': 'Ana'},
        {'kind': 'team', 'team': 'reds'},
        {'kind': 'team', 'team': 'reds'},
        {'kind': 'team', 'team': 'blues'},
    ]
    assert pool(_config(mixed, evidence='collect'), lacking=(0, 1, 2)) == ([1], [3])
    # A file written before the evidence names none and has no phase.
    old = rules.check(collect, _TEAMS)
    assert rules.seeding_pool(old) == ([], [])


def test_a_tournament_names_who_lacks_what_and_refuses_by_default(arena_home):
    _rate(0.8)
    cups = _cups()
    given = _entrants(0.2, 0.4, 0.8)
    given[1]['rating'] = 990
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        cups.create(_config(given))
    assert caught.value.message_id == 'server.tournament.evidence'
    # The refusal names the entrant that lacks, what it misses, and the two ways on.
    assert str(caught.value) == (
        'expected every entrant to hold the evidence that a draw by rating asks on '
        'academy_empty_goal_close, found that Built-in AI 0.20 lacks 4 matches, 2 matches '
        'against the anchor. Set evidence to collect to gather it in a seeding phase first, or '
        'to proceed to seed the draw with the ratings as they stand'
    )
    assert cups.entries() == []
    # The preview reports the same without refusing, for the offer to collect.
    preview = runner.preview(_config(given))
    assert preview['evidence'] == {
        'scenario': _SCENARIO,
        'anchor': _ANCHOR,
        'method': 'points',
        'required': {'matches': 4, 'anchor_matches': 2, 'width': None},
        'seeded': True,
        'lacking': [
            {
                'entrant': 0,
                'key': 'builtin:0.20',
                'model': 'builtin:0.20',
                'name': 'Built-in AI 0.20',
                'name_id': 'server.tournament.label.builtin_ability',
                'name_values': {'ability': '0.20'},
                'lacking': {'matches': 4, 'anchor_matches': 2},
            }
        ],
        'complete': False,
    }
    # The command prints the same report as text, who lacks what and the two ways on.
    from cuju.arena._tournaments import tournament_cli

    lines = tournament_cli.preview_lines(preview)
    start = lines.index(
        f'Evidence on the table of {_SCENARIO}, by points, with {_ANCHOR} at 1000, each entrant '
        'needs 4 matches, 2 matches against the anchor'
    )
    assert lines[start + 1 :] == [
        '  Built-in AI 0.20 lacks 4 matches, 2 matches against the anchor',
        '  Run with --evidence collect to gather the evidence in a seeding phase first, or with '
        '--evidence proceed to seed the draw with the ratings as they stand.',
        '  As it stands the tournament is refused.',
    ]
    collected = tournament_cli.preview_lines(runner.preview(_config(given, evidence='collect')))
    assert collected[-1] == '  The tournament gathers it in a seeding phase before the draw.'
    plain = tournament_cli.preview_lines(runner.preview(_config(given, seeding='random')))
    assert not any(line.startswith('Evidence on') for line in plain)
    first, second, third = preview['config']['entrants']
    assert first['evidence']['lacking'] and first['rating'] == 1000.0 and first['model']
    # A rating that the configuration states wins, and the entrant lacks nothing.
    assert second['rating'] == 990.0 and second['rated'] and second['evidence']['lacking'] is None
    held = _evidence.table(_SCENARIO)['models']
    known = next(row for row in held if row['id'] == 'builtin:0.80')
    assert third['rating'] == known['rating'] and third['evidence']['matches'] == known['matches']
    assert third['evidence']['lacking'] is None
    # A draw that no rating seeds asks for no evidence.
    plain = cups.create(_config(given, seeding='random'), run=False)
    assert cups.get(plain['id'])['evidence']['lacking'] == []
    assert cups.get(plain['id'])['seeding'] is None
    cups.close()


def test_a_tournament_that_proceeds_seeds_with_the_ratings_as_they_stand(arena_home):
    _rate(0.8)
    calls, raters = [], []
    cups = _cups(calls, raters)
    given = _entrants(0.2, 0.4, 0.8)
    given[1]['rating'] = 990
    entry = cups.create(_config(given, evidence='proceed'), run=False)
    view = cups.get(entry['id'])
    assert view['phase'] == 'main' and view['seeding'] is None and view['queue'] is None
    table = {row['id']: row for row in _evidence.table(_SCENARIO)['models']}
    elo = view['elo']
    assert elo['method'] == 'points' and elo['scale'] == 'table'
    assert elo['scenario'] == _SCENARIO and elo['anchor'] == _ANCHOR
    # The file keeps the rating of each entrant with where it came from.
    assert elo['entrants']['builtin:0.20']['seeding'] == {
        'rating': 1000.0,
        'interval': [view['config']['entrants'][0]['evidence'][name] for name in ('low', 'high')],
        'matches': 0,
        'origin': 'lacking',
        'measured': False,
        'anchor_matches': 0,
        'checkpoint': None,
    }
    assert elo['entrants']['builtin:0.40']['seeding'] == {
        'rating': 990.0,
        'interval': None,
        'matches': 0,
        'origin': 'given',
        'measured': False,
        'anchor_matches': 0,
        'checkpoint': None,
    }
    known = elo['entrants']['builtin:0.80']['seeding']
    assert known['origin'] == 'known' and known['rating'] == table['builtin:0.80']['rating']
    assert known['interval'] == [table['builtin:0.80']['low'], table['builtin:0.80']['high']]
    assert known['matches'] == table['builtin:0.80']['matches'] >= 4
    assert known['anchor_matches'] == table['builtin:0.80']['anchor_matches'] >= 2
    # The ratings seed the draw, the best first.
    assert rules.seeding(view['config']) == [2, 0, 1]
    assert cups.resume(entry['id'])['state'] == 'running'
    assert cups.run(entry['id'], report=lambda line: None) == 'done'
    done = cups.get(entry['id'])
    cups.close()
    # No seeding match played, and the matches of the draw are evidence of the scenario.
    assert raters == [] and len(calls) == done['played'] == 2
    rows = [row for row in _ratings.results(_SCENARIO) if row['source'] == 'tournament']
    assert sorted(row['ref'] for row in rows) == sorted(f'{entry["id"]}/{key}' for key in calls)
    assert all(row['phase'] == 'main' for row in rows)
    # The rating after the tournament stands beside the one that seeded it, with its interval.
    after = {row['id']: row for row in _evidence.table(_SCENARIO)['models']}
    final = done['elo']['entrants']['builtin:0.80']['final']
    assert final == {
        'rating': after['builtin:0.80']['rating'],
        'interval': [after['builtin:0.80']['low'], after['builtin:0.80']['high']],
        'matches': after['builtin:0.80']['matches'],
    }
    assert done['elo']['entrants']['builtin:0.80']['seeding'] == known
    assert done['elo']['entrants']['builtin:0.20']['final']['matches'] >= 1


def test_a_tournament_reads_known_ratings_and_plays_no_seeding(arena_home):
    _rate(0.2, 0.4, 0.8)
    raters = []
    cups = _cups(raters=raters)
    # Every entrant holds the evidence asked, so the tournament is made under any setting.
    for evidence in ('refuse', 'collect'):
        table = {row['id']: row for row in _evidence.table(_SCENARIO)['models']}
        entry = cups.create(_config(_entrants(0.2, 0.4, 0.8), evidence=evidence))
        view = cups.get(entry['id'])
        assert view['phase'] == 'main' and view['seeding'] is None
        assert view['evidence']['complete'] and view['evidence']['lacking'] == []
        for entrant in view['config']['entrants']:
            assert entrant['rating'] == table[entrant['key']]['rating']
            seeding = view['elo']['entrants'][entrant['key']]['seeding']
            assert seeding['origin'] == 'known' and seeding['rating'] == entrant['rating']
            assert seeding['matches'] == table[entrant['key']]['matches']
        assert rules.seeding(view['config']) == [2, 1, 0]
        assert cups.run(entry['id'], report=lambda line: None) == 'done'
    cups.close()
    assert raters == []


def test_the_phase_measures_only_the_entrants_that_lack(arena_home, on_demand):
    _rate(0.4, 0.8)
    before = len(_ratings.results(_SCENARIO))
    calls, raters = [], []
    cups = _cups(calls, raters)
    entry = cups.create(_config(_entrants(0.2, 0.4, 0.6, 0.8, 0.9), evidence='collect'), run=False)
    identifier = entry['id']
    waiting = cups.get(identifier)
    assert waiting['phase'] == 'seeding' and waiting['seeding']['state'] == 'waiting'
    assert [entry['key'] for entry in waiting['evidence']['lacking']] == [
        'builtin:0.20',
        'builtin:0.90',
    ]
    # The file tells from the start which ratings are known, and the phase adds the others.
    assert set(waiting['elo']['entrants']) == {'builtin:0.40', 'builtin:0.60', 'builtin:0.80'}
    assert waiting['elo']['entrants']['builtin:0.40']['seeding']['origin'] == 'known'
    assert cups.resume(identifier)['state'] == 'running'
    assert cups.run(identifier, report=lambda line: None) == 'done'
    done = cups.get(identifier)
    cups.close()
    seeding = done['seeding']
    assert done['phase'] == 'main' and seeding['state'] == 'done' and seeding['done']
    assert seeding['phase'] == 'seeding' and seeding['mode'] == 'on_demand'
    assert seeding['method'] == 'points' and seeding['anchor'] == _ANCHOR
    assert seeding['measured'] == ['builtin:0.20', 'builtin:0.90'] and seeding['matches'] == 8
    run = _evidence.get(seeding['run'])
    assert run['owner'] == f'tournament:{identifier}' and run['phase'] == 'seeding'
    assert run['models'] == ['builtin:0.20', 'builtin:0.90'] and run['state'] == 'done'
    # The two entrants that lack play, each the anchor first, and the others only as opponents.
    assert len(raters) == seeding['played'] == len(seeding['history']) > 0
    first = {}
    for _, home, away, _ in raters:
        for side, other in ((home, away), (away, home)):
            first.setdefault(side, []).append(other)
    assert first['builtin:0.20'][:2] == [_ANCHOR, _ANCHOR]
    assert first['builtin:0.90'][:2] == [_ANCHOR, _ANCHOR]
    measured = {'builtin:0.20', 'builtin:0.90'}
    assert all(measured & {home, away} for _, home, away, _ in raters)
    ratings = seeding['ratings']
    assert ratings['builtin:0.20']['measured'] and ratings['builtin:0.90']['measured']
    assert not ratings['builtin:0.40']['measured'] and not ratings[_ANCHOR]['measured']
    assert ratings[_ANCHOR]['rating'] == ratings[_ANCHOR]['low'] == 1000.0
    for number, entry in enumerate(seeding['history']):
        assert entry['number'] == number and set(entry['ratings']) == {entry['home'], entry['away']}
        for rating in entry['ratings'].values():
            assert rating['low'] <= rating['rating'] <= rating['high']
    # Each side of the phase names its entrant, the anchor too, which is an entrant here.
    assert seeding['entrants']['builtin:0.20']['index'] == 0
    assert seeding['entrants']['builtin:0.20']['name'] == 'Built-in AI 0.20'
    assert seeding['entrants']['builtin:0.20']['color'] == done['config']['entrants'][0]['color']
    assert seeding['entrants'][_ANCHOR]['index'] == 2
    # The results of the phase joined the table, as matches of a rating run of the phase.
    rows = _ratings.results(_SCENARIO)[before:]
    phase = [row for row in rows if row['source'] == 'rating']
    assert len(phase) == seeding['played'] and all(row['phase'] == 'seeding' for row in phase)
    assert all(row['ref'].startswith(f'{seeding["run"]}/') for row in phase)
    # The file keeps the rating that seeded each entrant, measured for this tournament or known.
    elo = done['elo']['entrants']
    seeded = {entrant['key']: entrant['rating'] for entrant in done['config']['entrants']}
    for key in ('builtin:0.20', 'builtin:0.90'):
        kept = elo[key]['seeding']
        assert kept['origin'] == 'measured' and kept['measured'] and kept['rating'] == seeded[key]
        assert kept['matches'] >= 4 and kept['anchor_matches'] >= 2
        assert kept['interval'][0] < kept['rating'] < kept['interval'][1]
        assert elo[key]['final']['matches'] >= kept['matches']
    assert elo['builtin:0.40']['seeding']['origin'] == 'known'
    assert elo[_ANCHOR]['seeding'] == {
        'rating': 1000.0,
        'interval': [1000.0, 1000.0],
        'matches': elo[_ANCHOR]['seeding']['matches'],
        'origin': 'known',
        'measured': False,
        'anchor_matches': 0,
        'checkpoint': None,
    }
    # The ratings seed the draw, the strongest first.
    order = rules.seeding(done['config'])
    assert order == sorted(range(5), key=lambda index: -done['config']['entrants'][index]['rating'])
    assert order[-1] == 0
    # No match of the draw played before the phase had ended.
    assert len(calls) == done['played'] > 0
    stored = json.loads((runner.folder() / f'{identifier}.json').read_text(encoding='utf-8'))
    assert stored['seeding']['state'] == 'done' and stored['seeding']['run'] == seeding['run']
    # A tournament opened later shows the same seeding without playing it again.
    again = _cups(raters=raters)
    assert again.get(identifier)['seeding'] == seeding
    assert again.get(identifier)['elo'] == done['elo']
    again.close()
    assert len(raters) == seeding['played']
    # The command shows the phase with the rating of each side, the best first, and where the
    # rating of each side that the phase did not measure came from.
    from cuju.arena._tournaments import tournament_cli

    lines = tournament_cli.describe(done)
    title = (
        f'Seeding phase, on demand, done, {seeding["played"]} of {seeding["total"]} matches played'
    )
    start = lines.index(title)
    shown = lines[start + 1 : start + 6]
    assert any(line.startswith('  Built-in AI 0.90  ') and ' matches' in line for line in shown)
    assert '  Built-in AI 0.60  1000, the anchor of the table' in shown
    known = [line for line in shown if line.startswith('  Built-in AI 0.40  ')]
    assert len(known) == 1 and known[0].endswith(', known from the table of the scenario')
    assert not any(line.endswith(', given in the configuration') for line in shown)


def test_a_seeding_match_that_fails_counts_as_played_and_moves_no_rating(arena_home):
    cups = runner.Tournaments(
        player=_strength_player(), rater=_rater(fail=(1,)), start=False, interval=0.01
    )
    identifier = cups.create(_config(_entrants(0.2, 0.9), evidence='collect'))['id']
    assert cups.run(identifier, report=lambda line: None) == 'done'
    seeding = cups.get(identifier)['seeding']
    cups.close()
    failed = seeding['history'][1]
    assert failed['score'] is None and 'the policy of Reds failed' in failed['error']
    before = seeding['history'][0]['ratings']
    for key, rating in failed['ratings'].items():
        if key in before:
            assert rating['rating'] == before[key]['rating']
    # The failed match is no evidence, and the others are.
    rows = [row for row in _ratings.results(_SCENARIO) if row['source'] == 'rating']
    assert len(rows) == seeding['played'] - 1
    assert f'{seeding["run"]}/1' not in {row['ref'] for row in rows}


def test_no_match_of_the_draw_plays_during_the_phase(arena_home, on_demand):
    release = threading.Event()
    calls = []
    cups = runner.Tournaments(
        player=_strength_player(calls),
        rater=_rater(gate=lambda spec: release.wait(10)),
        start=False,
        interval=0.01,
    )
    people = [
        *_entrants(0.2, 0.9),
        {'kind': 'person', 'name': 'Ana'},
        {'kind': 'person', 'name': 'Bo'},
    ]
    identifier = cups.create(_config(people, evidence='collect'))['id']
    cups.tick()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not cups.get(identifier)['seeding']['playing']:
        time.sleep(0.01)
    view = cups.get(identifier)
    assert view['phase'] == 'seeding' and view['seeding']['state'] == 'running'
    assert len(view['seeding']['playing']) == 1 and view['played'] == 0
    assert view['queue']['playing'][0]['number'] == 0 and calls == []
    # The people play no seeding match, and lack nothing.
    assert set(view['seeding']['entrants']) == {'builtin:0.20', 'builtin:0.90', _ANCHOR}
    assert view['seeding']['entrants'][_ANCHOR]['index'] is None
    key = view['matches'][0]['key']
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        cups.forfeit(identifier, key, 'home')
    assert caught.value.message_id == 'server.tournament.seeding_first'
    release.set()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and cups.get(identifier)['phase'] == 'seeding':
        cups.tick()
        time.sleep(0.01)
    assert cups.get(identifier)['seeding']['state'] == 'done'
    # A cancelled tournament ends the rating run of its phase.
    release.clear()
    other = cups.create(_config(_entrants(0.1, 0.3), evidence='collect'))['id']
    cups.tick()
    run = cups.get(other)['seeding']['run']
    assert cups.cancel(other)
    release.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _evidence.get(run)['state'] != 'cancelled':
        time.sleep(0.01)
    assert _evidence.get(run)['state'] == 'cancelled'
    cups.close()


def test_the_method_of_the_settings_seeds_the_draw(arena_home, tmp_path, monkeypatch):
    late, early, anchor = (_evidence.builtin_side(level) for level in (0.3, 0.7, 0.6))
    # One model loses five times and then wins five, the other wins six and then loses five.
    results = [(late, [0, 1])] * 5 + [(late, [1, 0])] * 5
    results += [(early, [1, 0])] * 6 + [(early, [0, 1])] * 5
    for number, (side, score) in enumerate(results):
        _ratings.record(
            {
                'scenario': _SCENARIO,
                'home': side,
                'away': anchor,
                'score': score,
                'source': 'match',
                'time': float(number),
            }
        )
    rows = {row['id']: row for row in _evidence.table(_SCENARIO)['models']}
    # By points the late run of wins counts for more, and the fit reads the totals alone.
    assert rows['builtin:0.30']['points']['rating'] > rows['builtin:0.70']['points']['rating']
    assert (
        rows['builtin:0.30']['bradley_terry']['rating']
        < rows['builtin:0.70']['bradley_terry']['rating']
    )
    cups = _cups()
    by_points = cups.get(cups.create(_config(_entrants(0.3, 0.7)), run=False)['id'])
    assert by_points['elo']['method'] == 'points' and rules.seeding(by_points['config']) == [0, 1]
    assert by_points['config']['entrants'][0]['rating'] == rows['builtin:0.30']['points']['rating']
    path = tmp_path / 'fit.toml'
    path.write_text('[ratings]\nmethod = "bradley_terry"\n', encoding='utf-8')
    monkeypatch.setenv(_layers.CONFIG_VARIABLE, str(path))
    fitted = cups.get(cups.create(_config(_entrants(0.3, 0.7)), run=False)['id'])
    cups.close()
    # The setting switches the method that seeds, and the file names the method it used.
    assert fitted['elo']['method'] == 'bradley_terry' and rules.seeding(fitted['config']) == [1, 0]
    seeding = fitted['elo']['entrants']['builtin:0.70']['seeding']
    assert seeding['rating'] == rows['builtin:0.70']['bradley_terry']['rating']
    assert seeding['interval'] == [
        rows['builtin:0.70']['bradley_terry']['low'],
        rows['builtin:0.70']['bradley_terry']['high'],
    ]
    assert fitted['evidence']['method'] == 'bradley_terry'


def test_a_phase_of_the_built_in_ai_plays_in_batches_and_keeps_its_actions(arena_home):
    cups = runner.Tournaments(player=_strength_player(), start=False, interval=0.01)
    config = _config(_entrants(0.1, 1.0), evidence='collect')
    identifier = cups.create(config)['id']
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline and cups.get(identifier)['phase'] == 'seeding':
        cups.tick()
        time.sleep(0.05)
    seeding = cups.get(identifier)['seeding']
    cups.close()
    assert seeding['state'] == 'done' and seeding['mode'] == 'batched'
    # Each entrant played its eight matches, the first two against the anchor.
    assert seeding['played'] == seeding['total'] == 10
    assert seeding['ratings']['builtin:0.10']['matches'] == 8
    assert seeding['ratings']['builtin:1.00']['matches'] == 8
    run = _evidence.get(seeding['run'])
    assert all(match['digest'] and match['length'] > 0 for match in run['matches'])
    assert not any(match['recording'] for match in run['matches'])
    # Every match plays again from its seed and its actions to the same score.
    assert _evidence.check(seeding['run'])['same']
