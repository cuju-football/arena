# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Checks of requested matches, games, the demo and how a team fits an academy task."""

import json

import pytest
import torch

import cuju
import cuju.errors
import cuju.state
from cuju.arena._format import recording as _recording
from cuju.arena._registry import teams as _teams
from cuju.arena._store import library as _library
from support.common import (
    _SINGLE,
    _control,
    _short_matches,
    _toy_team,
)


def test_requested_match_records_resolved_seed(arena_home, monkeypatch):
    from cuju.arena._playing import play as _play

    _short_matches(monkeypatch, 2)
    monkeypatch.setattr(_play.time, 'time', lambda: 123456)
    path = _play.play('builtin', 'builtin', '5_vs_5', ability=(0.3, 0.7))
    provenance = _recording.read_header(path)['provenance']
    assert provenance['source'] == 'requested_match'
    assert provenance['seed'] == 123456
    assert provenance['teams'] == ['builtin', 'builtin']
    assert provenance['agent_counts'] == [0, 0]
    assert provenance['action_set'] == 'default'
    assert provenance['ability'] == [0.3, 0.7]
    assert provenance['cuju_version'] == cuju.__version__
    assert (
        json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))['provenance']
        == provenance
    )
    # A recording copied into the folder without its summary keeps its provenance in the list.
    path.with_suffix('.json').unlink()
    assert _library.matches()[0]['provenance'] == provenance


def test_play_keeps_checkpoint_snapshots(arena_home, tmp_path, monkeypatch, capsys):
    from cuju.arena.__main__ import main
    from cuju.arena._registry import checkpoints

    _short_matches(monkeypatch, 2)
    team = _toy_team(tmp_path, 'Greens', _SINGLE, '5v5')['id']
    first = checkpoints.current_id(team)
    checkpoints.change(team, first, label='First', step=10)
    latest = checkpoints.add(team, 'main.py', _SINGLE.encode() + b'\n', 'Latest', 20)['current']
    expected = [
        {'checkpoint': {'id': first, 'label': 'First', 'step': 10}},
        {'checkpoint': {'id': latest, 'label': 'Latest', 'step': 20}},
    ]
    assert (
        main(
            [
                'play',
                '--left',
                'Greens@First',
                '--right',
                'Greens',
                '--scenario',
                '5_vs_5',
                '--seed',
                '7',
                '--json',
            ]
        )
        == 0
    )
    played = json.loads(capsys.readouterr().out)
    assert played['schema'] == 'arena.play/1'
    assert played['left_checkpoint'] == expected[0]['checkpoint']
    assert played['right_checkpoint'] == expected[1]['checkpoint']
    checkpoints.change(team, first, label='Edited', step=50)
    checkpoints.change(team, latest, label='Edited again', step=60)
    assert _recording.read_header(played['path'])['provenance']['sides'] == expected
    assert main(['recording', 'show', played['match'], '--json']) == 0
    assert json.loads(capsys.readouterr().out)['provenance']['sides'] == expected


@pytest.mark.parametrize('side', [0, 1])
def test_fixed_routing_survives_mixed_set_pieces(device, side):
    """Selected players keep their observation order as GRF changes agents at kickoff."""
    from cuju.arena._playing import play as _play

    counts = (11, 1) if side == 0 else (1, 11)
    batch = cuju.BatchEnvironment(
        '11_vs_11_kaggle',
        environment_count=1,
        device=device,
        representation='raw',
        agent_counts=[counts],
        action_set='v2',
        seed=2,
        auto_reset=False,
    )
    try:
        batch.reset()
        first = 0 if side == 0 else 1
        columns = list(range(first, first + 11))
        selected = [9, 3, 1]
        assignments = set()
        modes = set()
        cleared = 0
        for _ in range(150):
            raw = {name: tensor.cpu() for name, tensor in batch.raw().items()}
            active = raw['active'][0, columns].tolist()
            actions = torch.zeros((1, 12), dtype=torch.int32, device=batch.torch_device)
            actions[:, columns] = 19
            found = _play._player_columns(raw, columns, selected, {})
            if found is None:
                # GRF clears the side's selection at a set piece, so every agent reports no player
                # until it hands the players out again, and the built-in AI plays the step.
                assert -1 in active
                cleared += 1
            else:
                chosen, missing = found
                assert missing == []
                assert sorted(active) == list(range(11))
                assignments.add(tuple(active))
                assert raw['active'][0, chosen].tolist() == selected
                actions[:, chosen] = torch.tensor(
                    [3, 7, 5], dtype=torch.int32, device=batch.torch_device
                )
                assert all(
                    int(actions[0, column]) == 19 for column in columns if column not in chosen
                )
            modes.add(int(raw['game_mode'][0, first]))
            batch.step(actions)
        # The set pieces cleared the selection, and the players came back to the agents in the
        # same order or, on one side, in another, which the routing follows.
        assert cleared > 0 and assignments
        assert len(modes) > 1
    finally:
        batch.close()


def test_fixed_routing_keeps_a_player_off_the_pitch():
    """A selected player that no agent reports keeps its agent, while a cleared selection skips."""
    from cuju.arena._playing import play as _play

    def raw(*active):
        # The side's agents are columns 4 to 6, after four agents of the other side.
        return {'active': torch.tensor([[0, 0, 0, 0, *active]], dtype=torch.int32)}

    columns, selected, held = [4, 5, 6], [9, 3, 1], {}
    assert _play._player_columns(raw(1, 9, 3), columns, selected, held) == ([5, 6, 4], [])
    # Player 3 leaves the pitch, and its agent now holds player 4.
    assert _play._player_columns(raw(1, 9, 4), columns, selected, held) == ([5, 6, 4], [6])
    # Two agents report player 9, so player 3 takes its last agent while that one is free.
    assert _play._player_columns(raw(1, 9, 9), columns, selected, held) == ([5, 6, 4], [6])
    assert _play._player_columns(raw(1, -1, 4), columns, selected, held) is None


def test_task_fit_puts_extra_players_out_for_dynamic_and_fixed_control(arena_home, tmp_path):
    """A team that controls more players than the side of a task holds plays with the others
    out."""
    four = _toy_team(tmp_path, 'four', _SINGLE, '5v5', control=_control(4))
    fit = _teams.task_fit(four, '1_vs_1_easy')
    assert fit == {
        'agents': 1,
        'players': 4,
        'playing': 1,
        'slots': [0, None, None, None],
        'selected': None,
    }
    # The empty goal holds a goalkeeper and a defender, both controllable.
    fit = _teams.task_fit(four, 'academy_empty_goal_close')
    assert fit['agents'] == 2 and fit['playing'] == 2 and fit['slots'] == [0, 1, None, None]
    pair = _toy_team(tmp_path, 'pair', _SINGLE, '11v11', control=_control(2))
    fit = _teams.task_fit(pair, 'academy_empty_goal_close')
    assert fit == {'agents': 2, 'players': 2, 'playing': 2, 'slots': None, 'selected': None}
    # A fixed selection keeps the players that the side holds, by role or by index, in the order
    # of the submission, and the built-in AI plays the goalkeeper outside it.
    roles = _toy_team(
        tmp_path,
        'roles',
        _SINGLE,
        '11v11',
        control=_control(3, 'fixed', ['CF', 'CB', 'LM'], goalkeeper='builtin'),
    )
    fit = _teams.task_fit(roles, 'academy_empty_goal_close')
    assert fit == {
        'agents': 2,
        'players': 3,
        'playing': 1,
        'slots': [None, 0, None],
        'selected': [1],
    }
    indices = _toy_team(
        tmp_path, 'indices', _SINGLE, '11v11', control=_control(3, 'fixed', [1, 2, 3])
    )
    fit = _teams.task_fit(indices, 'academy_pass_and_shoot_with_keeper')
    assert fit['playing'] == 2 and fit['slots'] == [0, 1, None] and fit['selected'] == [1, 2]
    # A selection that the side holds whole plays as it does in a match.
    whole = _teams.task_fit(roles, 'academy_corner')
    assert whole['slots'] is None and whole['playing'] == 3 and whole['agents'] == 11
    wings = _toy_team(
        tmp_path, 'wings', _SINGLE, '11v11', control=_control(2, 'fixed', ['LM', 'RM'])
    )
    with pytest.raises(cuju.errors.ArgumentError) as caught:
        _teams.task_fit(wings, 'academy_empty_goal_close')
    assert caught.value.message_id == 'server.teams.cannot_play'
    assert caught.value.values['reason']['id'] == 'server.teams.control.none_present'
    # The built-in AI plays the whole side.
    builtin = _teams.task_fit(_teams.entry(_teams.BUILTIN), '1_vs_1_easy')
    assert builtin['agents'] == 0 and builtin['slots'] is None


def test_task_fit_keeps_the_size_rule(arena_home, tmp_path):
    """A five a side team never plays an eleven a side match, nor the reverse, while the academy
    drills that both sizes play stay open to both."""
    five = _toy_team(tmp_path, 'five', _SINGLE, '5v5', control=_control(4))
    eleven = _toy_team(tmp_path, 'eleven', _SINGLE, '11v11', control=_control(4))
    for team, scenario in ((five, '11_vs_11_kaggle'), (eleven, '5_vs_5')):
        with pytest.raises(cuju.errors.ArgumentError) as caught:
            _teams.task_fit(team, scenario)
        assert caught.value.message_id == 'server.teams.scenario_format'
    for team in (five, eleven):
        assert _teams.task_fit(team, 'academy_3_vs_1_with_keeper')['playing'] == 4
        assert _teams.task_fit(team, '1_vs_1_easy')['playing'] == 1


def test_play_reports_progress_only_to_its_function(arena_home, tmp_path, monkeypatch, capsys):
    """A match in the caller's process prints no progress, and the command prints one line."""
    from cuju.arena.__main__ import main
    from cuju.arena._playing import play as _play

    _short_matches(monkeypatch, 120)
    seen = []
    path = _play.play('builtin', 'builtin', '5_vs_5', seed=1, progress=seen.append)
    assert path.is_file() and seen and all(0 < share < 1 for share in seen)
    assert 'progress' not in capsys.readouterr().out
    assert main(['play', '--scenario', '5_vs_5', '--seed', '1', '--json']) == 0
    printed = json.loads(capsys.readouterr().out)
    assert (arena_home / 'matches' / f'{printed["match"]}.cjr').is_file()
    assert printed['path'].endswith(f'{printed["match"]}.cjr')
    crash = _toy_team(
        tmp_path, 'crash', 'def agent(obs):\n    raise RuntimeError("broke")\n', '5v5'
    )
    assert main(['play', '--left', crash['id'], '--scenario', '5_vs_5']) == 1
    captured = capsys.readouterr()
    assert 'cuju arena play: error: crash forfeits, ' in captured.err, captured.err
    assert 'broke' in captured.err
    # A side that chose no format plays Kaggle open, which decides that a crash is a forfeit.
    with pytest.raises(_play.ForfeitError) as caught:
        cuju.arena.play(crash['id'], 'builtin', '5_vs_5', seed=1)
    assert isinstance(caught.value, cuju.arena.PolicyError)
    assert (caught.value.rule['side'], caught.value.rule['kind']) == ('home', 'crash')
    assert caught.value.rule['submission_format'] == 'kaggle/1'


def test_play_progress_follows_the_length_of_the_match(arena_home):
    """The progress of a match of 100 steps reaches most of the way, not a thirtieth of it."""
    from cuju.arena._playing import play as _play

    seen = []
    _play.play('builtin', 'builtin', '5_vs_5', seed=1, length=100, progress=seen.append)
    assert seen and max(seen) > 0.4 and all(0 < share < 1 for share in seen), seen


def test_policy_errors_carry_message_ids():
    from cuju.arena._playing import play as _play
    from cuju.arena._text import messages as _messages

    outside = IndexError('expected an action from -19 to 18, found 25')
    error = _play._policy_error(outside, 'Reds')
    assert str(error) == (
        'the policy of Reds failed, it answered the action 25, outside its action set of 19 '
        'actions, 0 to 18'
    )
    assert isinstance(error, cuju.arena.PolicyError)
    assert _messages.id_fields(error) == {
        'error_id': 'server.policy.failed_action',
        'error_values': {'player': 'Reds', 'value': '25', 'count': '19', 'last': '18'},
    }
    error = _play._policy_error(ValueError('no'))
    assert str(error) == 'the policy failed, ValueError: no'
    assert (error.message_id, error.values) == ('server.policy.error', {'reason': 'ValueError: no'})


def test_tournament_failures_carry_message_ids(arena_home):
    from cuju.arena._playing import play as _play
    from cuju.arena._text import messages as _messages
    from cuju.arena._tournaments import tournament_runner as runner

    def player(match, teams, provenance, progress):
        if match['key'] == '0/w4-1/1':
            raise _play.PolicyMessageError(
                'server.policy.failed', player='Reds', reason='ValueError: no'
            )
        return None

    entrants = [{'kind': 'builtin', 'ability': index / 4} for index in range(4)]
    stage = {'format': 'knockout', 'scenario': 'academy_empty_goal_close', 'settle': ['lots']}
    config = {'name': 'Spring cup', 'seed': 9, 'entrants': entrants, 'stages': [stage]}
    cups = runner.Tournaments(player=player, start=False)
    try:
        entry = cups.create(config)
        assert cups.run(entry['id'], report=lambda line: None) == 'stopped'
        view = cups.get(entry['id'])
        matches = {match['key']: match for match in view['matches']}
        first, second = matches['0/w4-1/1'], matches['0/w4-2/1']
        assert first['error'] == 'the policy of Reds failed, ValueError: no'
        assert (first['error_id'], first['error_values']) == (
            'server.policy.failed',
            {'player': 'Reds', 'reason': 'ValueError: no'},
        )
        assert (second['error'], second['error_id']) == (
            'the match gave no result',
            'server.tournament.no_result',
        )
        assert view['error'] == (
            'Semi-final 1 failed, the policy of Reds failed, ValueError: no, and 1 more match '
            'failed'
        )
        assert view['error_id'] == 'server.tournament.match_failed_one_more'
        assert _messages.text(view['error_id'], **view['error_values']) == view['error']
        # A file of an earlier version, without the ids of its failures, loads all the same.
        stored = json.loads((runner.folder() / f'{entry["id"]}.json').read_text(encoding='utf-8'))
        del stored['failure_ids']
        (runner.folder() / f'{entry["id"]}.json').write_text(json.dumps(stored), encoding='utf-8')
        cups.close()
        cups = runner.Tournaments(player=player, start=False)
        failed = {match['key']: match for match in cups.get(entry['id'])['matches']}
        assert failed['0/w4-1/1']['error'] == first['error']
        assert failed['0/w4-1/1']['error_id'] is None
        cups.cancel(entry['id'])
        with pytest.raises(cuju.errors.ArgumentError) as caught:
            cups.cancel(entry['id'])
        assert str(caught.value) == 'expected a tournament that runs or stopped, found it cancelled'
        assert caught.value.message_id == 'server.tournament.cancel_state'
    finally:
        cups.close()


def test_render_errors_read_as_public_classes_and_dir_lists_the_public_names():
    import pickle
    import traceback

    from cuju.arena._playing import errors as _errors
    from cuju.arena._playing import play as _play
    from cuju.arena._text import messages as _messages

    error = _messages.MessageError('server.play.scenario', scenario='test')
    assert (type(error).__module__, type(error).__qualname__) == ('cuju.errors', 'ArgumentError')
    assert repr(error).startswith('ArgumentError(')
    shown = traceback.format_exception_only(type(error), error)[-1]
    assert shown.startswith('cuju.errors.ArgumentError: ')
    assert isinstance(error, cuju.errors.ArgumentError) and _messages.kind(error) == 'ArgumentError'
    again = pickle.loads(pickle.dumps(error))
    assert type(again) is _messages.MessageError and again.message_id == 'server.play.scenario'
    assert str(again) == str(error)
    policy = _play.PolicyMessageError('server.play.scenario', scenario='test')
    assert f'{type(policy).__module__}.{type(policy).__qualname__}' == 'cuju.arena.PolicyError'
    assert isinstance(policy, cuju.arena.PolicyError)
    assert _errors.PolicyError.__module__ == 'cuju.arena'
    assert dir(cuju.arena) == sorted(cuju.arena.__all__)
    assert 'threading' not in dir(cuju.arena) and 'Recorder' in dir(cuju.arena)


def _played_again(path):
    """Plays a recording again from the state of its kick off and its actions, in a batch that
    its run key gives, and returns the frames."""
    document = _recording.read_header(path)
    key = document['run_key']
    assert key['start'] == 'reset' and document['sections'][:3] == ['PLYR', 'ACTS', 'STAT']
    actions = _recording.actions(path)
    assert (actions[0] == 255).all() and (actions[-1] == 255).all()
    batch = cuju.BatchEnvironment(
        key['scenario'],
        environment_count=1,
        device='cpu',
        auto_reset=False,
        representation='raw',
        action_set=key['action_set'],
    )
    try:
        assert batch.difficulty[0].tolist() == pytest.approx(key['difficulty'])
        batch.set_state(0, _recording.state(path))
        reader = _recording.StateReader(len(document['players']))
        frames = [reader.frame(batch.get_state(0))]
        for row in actions[1:-1]:
            _, _, done, _ = batch.step(torch.tensor([row.tolist()], dtype=torch.int32))
            frames.append(reader.frame(batch.get_state(0), 1 if bool(done[0]) else 0))
    finally:
        batch.close()
    return frames


def test_requested_match_records_its_actions(arena_home, tmp_path):
    """A requested match keeps the lanes, the actions and the kick off, and plays again exactly."""
    from cuju.arena._playing import play as _play

    answer = '[5 if obs["step"] % 7 else 12, 3 if obs["step"] % 5 else 9]'
    source = f'def agent(obs):\n    return {answer}\n'
    team = _toy_team(tmp_path, 'pair', source, '5v5', {'players': 2})
    path = _play.play(team['id'], 'builtin', scenario='5_vs_5', seed=11, steps=60)
    document = _recording.read_header(path)
    assert document['agents'] == [2, 0] and document['action_set'] == 'default'
    assert document['run_key']['engine_seed'] is not None
    actions = _recording.actions(path)
    assert actions.shape == (62, 2)
    assert {5, 12} <= set(actions[1:-1, 0].tolist()) and {3, 9} <= set(actions[1:-1, 1].tolist())
    recorded = list(_recording.frames(path))
    assert len(recorded) == 61
    assert all(len(lane) == 61 for lane in _recording.players(path).values())
    assert _played_again(path) == recorded
