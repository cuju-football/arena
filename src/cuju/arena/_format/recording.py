# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Turns GRF states into the frames of a recording, and writes and reads recording files.

A frame holds what a viewer draws for one step, the world transform of every joint of every player,
the ball, the score, the clock and the game mode, all in the frame of the pitch, and for every
player its state bits, the kind of its animation and its touch of the ball. Beside its frames a
recording of version 2 keeps optional sections, the lanes of the players, the actions of the
agents and the state of the kick off, and a recording of version 3 keeps the tracks of the
players in place of the frames. A recording holds the game alone, and what a model or a viewer
adds, its annotations, overlays and look, travels in attachments beside it, which `attachments`
reads and writes. The format is in the repository's `docs/recording-format.md`.
"""

from __future__ import annotations

import functools
import gzip
import hashlib
import json
import math
import struct
import zlib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cuju import __version__
from cuju.errors import StateError

from .._text import messages as _messages
from . import KEYFRAME_INTERVAL

if TYPE_CHECKING:
    import numpy as np

    from cuju import state as _state


def __getattr__(name: str) -> Any:
    if name == '_codec':
        from . import codec

        return codec
    if name == '_state':
        from cuju import state

        return state
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


MAGIC = b'CUJREC1\n'
# The version that `write` gives a recording by default. Version 1 stores the frames one after
# another, version 2 stores them coded in sections, about a third of the size, and version 3
# stores the tracks of the players in place of the frames, about a tenth of the size again.
VERSION = 2
VERSIONS = (1, 2, 3)
# The versions whose frames hold every joint, which the readers of cuju-render 0.1.0 open.
JOINT_VERSIONS = (1, 2)
# The tag and length that open every section of a recording of version 2 or 3.
_SECTION = struct.Struct('<4sI')
FRAMES_TAG = b'FRMS'
STATE_TAG = b'STAT'
# What the picture of a recording rests on, every joint of every player or their positions alone.
JOINTS, POSITIONS = 'joints', 'positions'
# How the capture of a recording began, at the reset of its episode or in the middle of it.
START_RESET, START_MID = 'reset', 'mid'
# The limits that the readers hold a recording to, far above any recording that cuju writes. A
# header of 22 players takes about 4 KB, GRF plays at most 11 a side, and the longest match, one
# of a tournament, lasts 5000 steps, 22 MB once decompressed in version 1 and 41 MB in version 2.
# The page holds a recording to the same players, frames, header and keyframe interval, and
# `tests/support/limits.json` keeps the two sides equal. The decompressed bytes alone differ, 256
# MiB here, where the frames are read in parts, and 128 MB on the page, which decodes every frame
# into the memory of a tab.
MAX_HEADER_BYTES = 1 << 20
MAX_PLAYERS = 32
MAX_FRAMES = 50_000
MAX_BYTES = 256 << 20
# The sections and the fields of the players that a header may name.
MAX_SECTIONS = 64
# The frames of version 2 that the reader decodes at once, ten keyframe intervals.
_DECODE_FRAMES = 10 * KEYFRAME_INTERVAL
# The bytes that the reader passes over at once in a section that it skips.
_SKIP_BYTES = 1 << 20
# The messages that a recording names its title and its teams by, those of the names that cuju
# makes, so a header names no other message of the page.
_LABEL_PREFIXES = ('server.names.', 'server.tournament.label.')
NODES = (
    'player',
    'body',
    'middle',
    'neck',
    'left_shoulder',
    'left_elbow',
    'right_shoulder',
    'right_elbow',
    'left_thigh',
    'left_knee',
    'left_ankle',
    'right_thigh',
    'right_knee',
    'right_ankle',
)
# GRF: the skeleton of `player.object`, in pre-order, so every parent comes before its children.
PARENTS = (-1, 0, 1, 2, 2, 4, 2, 6, 1, 8, 9, 1, 11, 12)
# GRF: a node holds a position, a rotation, a scale and a local mode, 44 bytes in all.
_NODE_BYTES = 44
_NODE_SPAN = _NODE_BYTES * len(NODES)
_LOCAL_MODE_RELATIVE = 0
# GRF: `e_PlayerRole_GK`, the first role.
_GOALKEEPER = 0
# GRF: a step runs ten ticks, `physics_steps_per_frame`, and the raw observation gives a movement
# over one step, x divided by 54.4 and y by -83.6 in double precision.
_TICKS = 10
# GRF: an agent acts through a keyboard, and the keyboards of the right team count from 11.
_TEAM_KEYBOARDS = 11
# GRF: the directions of the eight sticky directions, in the order of `_players.STICKY`, and the
# buttons of its other sticky actions, by their place among the buttons of a keyboard.
_STICKY_DIRECTIONS = (
    [-1, 0, 0],
    [-1, 1, 0],
    [0, 1, 0],
    [1, 1, 0],
    [1, 0, 0],
    [1, -1, 0],
    [0, -1, 0],
    [-1, -1, 0],
)
_STICKY_BUTTONS = [9, 10, 4, 6, 7]
_BUTTONS = 11
# The head of a frame, eight integers, the ball's seven floats and four integers of the referee.
_FRAME_HEAD = struct.Struct('<8i7f4i')
# A team without a name of its own takes the name of its side, which the page shows in its own
# language, with the English text in `name` as before.
_DEFAULT_TEAMS = (
    {
        **_messages.named('name', _messages.label('server.names.left')),
        'color': [214, 48, 49],
        'secondary': [255, 255, 255],
    },
    {
        **_messages.named('name', _messages.label('server.names.right')),
        'color': [9, 132, 227],
        'secondary': [255, 255, 255],
    },
)


# A joint's position is stored in steps of 2 mm, which spans 65 m either way of the center spot,
# and its rotation in steps of 1 / 32767, so each joint takes seven 16-bit integers.
POSITION_SCALE = 500
ROTATION_SCALE = 32767


def frame_bytes(players: int) -> int:
    """Returns the size of one frame of a recording of `players` players."""
    return _FRAME_HEAD.size + players * len(NODES) * 14 + 3 * (-(-players // 4) * 4)


@functools.lru_cache(maxsize=1)
def _player_scales() -> tuple[Any, ...]:
    import numpy as np

    from . import players as _players

    return (
        np.float32(_TICKS),
        np.array([54.4, -83.6]),
        np.array(_STICKY_DIRECTIONS, np.float32),
        1 << np.arange(len(_players.STICKY)),
    )


def _quantize(nodes: np.ndarray) -> np.ndarray:
    """Returns joints `[..., 7]` as the 16-bit integers that a recording stores."""
    import numpy as np

    scale = np.array([POSITION_SCALE] * 3 + [ROTATION_SCALE] * 4, np.float64)
    return np.clip(np.rint(nodes * scale), -32767, 32767).astype('<i2')


# GRF: the state names each type as C++'s `typeid` does, and stores it little-endian.
_DTYPES = {
    'f': ('<f4', ()),
    'i': ('<i4', ()),
    'j': ('<u4', ()),
    'b': ('u1', ()),
    'c': ('i1', ()),
    'm': ('<u8', ()),
    'N7blunted7Vector3E': ('<f4', (3,)),
    'N7blunted10QuaternionE': ('<f4', (4,)),
}


@functools.cache
def _layout() -> dict[str, _state.Field]:
    """Returns the layout of the match in memory, read once."""
    from cuju import state as _state

    return _state.layout()


def _field(path: str) -> _state.Field:
    """Returns the `Field` of a path of the layout."""
    return _layout()[path]


def _dtype(field: _state.Field) -> tuple[str, tuple[int, ...]]:
    """Returns the numpy type and shape of a field of the layout."""
    import numpy as np

    dtype, shape = _DTYPES.get(field.type, (None, ()))
    # GRF: an enumeration is a C++ `enum`, four bytes on every platform it runs on.
    if dtype is None and field.size == 4 and field.type[:1].isdigit():
        dtype = '<i4'
    if dtype is None or np.dtype(dtype).itemsize * int(np.prod(shape)) != field.size:
        return 'u1', (field.size,)
    return dtype, shape


class StateReader:
    """Reads the frames of a recording out of the states of one scenario's matches.

    The reader gathers every field it needs from a state in one call into the library, and derives
    the world transform of each joint from the local ones, as GRF's renderer does. The same call
    gathers what the lanes of the players hold, and `lanes` returns them for the state that
    `frame` read last.

    Attributes:
        players: The number of players of the matches.
        engine_seed: The engine seed of the episode of the state that `frame` or `lineup` read
            last, GRF's `game_engine_random_seed`, or `None` before any state.
        reverse: Whether that state plays with GRF's reverse team processing, or `None` before
            any state.
        agents: The numbers of agents of the left and the right team of that state, or `None`
            before any state.
    """

    _HEAD = (
        ('reverse', 'environment.scenario.reverseTeamProcessing'),
        ('first', 'match.firstTeam'),
        ('left', 'environment.scenario.leftTeamSize'),
        ('right', 'environment.scenario.rightTeamSize'),
        ('step', 'environment.step'),
        ('goals_first', 'match.goalCount[0]'),
        ('goals_second', 'match.goalCount[1]'),
        ('in_set_piece', 'match.inSetPiece'),
        ('set_piece', 'match.referee.buffer.desiredSetPiece'),
        ('touch_team', 'match.lastTouchTeamID'),
        ('touch_first', 'match.teams[0].lastTouchPlayer'),
        ('touch_second', 'match.teams[1].lastTouchPlayer'),
        ('clock', 'match.matchTimeMilliseconds'),
        ('actual', 'match.actualTimeMilliseconds'),
        ('ball_position', 'match.ball.positionBuffer'),
        ('ball_rotation', 'match.ball.orientationBuffer'),
        ('foul_player', 'match.referee.foul.foulPlayer'),
        ('foul_victim', 'match.referee.foul.foulVictim'),
        ('foul_type', 'match.referee.foul.foulType'),
        ('offside_count', 'match.referee.offsidePlayers.count'),
        ('offside_first', 'match.referee.offsidePlayers[0]'),
        ('buffer_active', 'match.referee.buffer.active'),
        ('scorer', 'match.lastGoalScorer'),
        # GRF: every reset writes the seed of its episode into the scenario of the state.
        ('seed', 'environment.scenario.gameEngineRandomSeed'),
        ('left_agents', 'environment.scenario.leftAgents'),
        ('right_agents', 'environment.scenario.rightAgents'),
        ('ball_momentum', 'match.ball.momentum'),
        # GRF: `teams` holds the team processed first at index 0, with its gamers, one for each
        # agent, and its designated player, an index among the match's players.
        ('designated_first', 'match.teams[0].mainSelectedPlayer'),
        ('designated_second', 'match.teams[1].mainSelectedPlayer'),
        ('gamers_first', 'match.teams[0].humanGamers.count'),
        ('gamers_second', 'match.teams[1].humanGamers.count'),
    )
    _PLAYER = (
        ('active', 'base.isActive'),
        ('cards', 'cards'),
        ('possession', 'hasPossession'),
        ('controller', 'base.externalController'),
        # GRF: `e_FunctionType`, the kind of the animation that the player runs, which is what
        # its body does and not the action that an agent chose.
        ('animation', 'base.humanoid.currentAnimation.functionType'),
        ('touch_time', 'base.lastTouchTimeMilliseconds'),
        ('touch_type', 'base.lastTouchType'),
    )

    def __init__(self, players: int) -> None:
        """Prepares the fields of matches with `players` players in all."""
        import numpy as np

        from cuju import state as _state

        self.players = players
        self.engine_seed: int | None = None
        self.reverse: bool | None = None
        self.agents: tuple[int, int] | None = None
        # The fields and the order of the players of the state that `frame` read last.
        self._last: tuple[np.void, list[tuple[int, int]]] | None = None
        fields, layout = [], []
        for name, path in self._HEAD:
            fields.append(_field(path))
            layout.append((name, *_dtype(fields[-1])))
        for player in range(players):
            prefix = f'match.players[{player}]'
            first = _field(f'{prefix}.base.humanoid.nodes[0].position')
            # The nodes lie one after another in memory, so one field covers all fourteen.
            fields.append(
                _state.Field(f'{prefix}.base.humanoid.nodes', first.offset, _NODE_SPAN, 'nodes')
            )
            layout.append((f'nodes{player}', 'u1', (_NODE_SPAN,)))
            for name, path in self._PLAYER:
                fields.append(_field(f'{prefix}.{path}'))
                layout.append((f'{name}{player}', *_dtype(fields[-1])))
        known = _layout()

        def block(name: str, paths: list[str], kind: str, *shape: int) -> None:
            # The fields of a block lie one after another in what `gather` returns, so the block
            # reads as one array, every player or every gamer at once.
            each = shape[len(shape) - _TRAILING.get(name, 0) :]
            for path in paths:
                if _dtype(known[path]) != (kind, each):
                    raise StateError(f'expected {path} as {kind} in the layout of the state')
                fields.append(known[path])
            layout.append((name, kind, shape))

        every = [f'match.players[{player}].base' for player in range(players)]
        block(
            'movement',
            [f'{base}.humanoid.spatialState.movement' for base in every],
            '<f4',
            players,
            3,
        )
        block('fatigue', [f'{base}.fatigueFactorInverse' for base in every], '<f4', players)
        block('controllers', [f'{base}.externalController' for base in every], '<i4', players)
        # GRF: a team has room for eleven gamers, and a state fills those of its agents.
        self._gamers = 0
        while f'match.teams[0].humanGamers[{self._gamers}].keyboardIndex' in known:
            self._gamers += 1
        gamers = [
            f'match.teams[{team}].humanGamers[{gamer}]'
            for team in range(2)
            for gamer in range(self._gamers)
        ]
        block('keyboard', [f'{gamer}.keyboardIndex' for gamer in gamers], '<i4', 2, self._gamers)
        keyboards = [f'{gamer}.controller.keyboard' for gamer in gamers]
        block('mirror', [f'{keyboard}.mirror' for keyboard in keyboards], '<f4', 2, self._gamers)
        block(
            'pressed',
            [f'{keyboard}.direction' for keyboard in keyboards],
            '<f4',
            2,
            self._gamers,
            3,
        )
        for keyboard in keyboards:
            # The buttons lie one after another in memory, a byte each, so one field covers them.
            first = known[f'{keyboard}.buttonsPressed[0]']
            last = known[f'{keyboard}.buttonsPressed[{_BUTTONS - 1}]']
            if (last.offset - first.offset, first.size, last.size) != (_BUTTONS - 1, 1, 1):
                raise StateError('expected the buttons of a keyboard as eleven bytes in the state')
            fields.append(_state.Field(f'{keyboard}.buttonsPressed', first.offset, _BUTTONS, 'b'))
        layout.append(('buttons', 'u1', (2, self._gamers, _BUTTONS)))
        slots = 0
        while f'environment.scenario.leftTeam[{slots}].role' in known:
            slots += 1
        block(
            'scenario_roles',
            [
                f'environment.scenario.{side}Team[{index}].role'
                for side in ('left', 'right')
                for index in range(slots)
            ],
            '<i4',
            2,
            slots,
        )
        self._fields = fields
        self._dtype = np.dtype(layout)
        self._roles = {
            (team, index): known[path]
            for team, side in enumerate(('left', 'right'))
            for index in range(players)
            if (path := f'environment.scenario.{side}Team[{index}].role') in known
        }

    def _values(self, state: bytes, frame: str) -> np.void:
        """Returns the gathered fields of a state as one structured record."""
        import numpy as np

        from cuju import state as _state

        values = np.frombuffer(_state.gather(state, self._fields, frame), self._dtype, 1)[0]
        self.engine_seed = int(values['seed'])
        self.reverse = bool(values['reverse'])
        self.agents = (int(values['left_agents']), int(values['right_agents']))
        return values

    def _order(self, values: np.void) -> list[tuple[int, int]]:
        """Returns the team and the index in its team of each player of the state.

        GRF: the state lists the team processed first before the other, and its ID flips with the
        reverse team processing.
        """
        first = int(values['first']) ^ int(bool(values['reverse']))
        sizes = (int(values['left']), int(values['right']))
        if sum(sizes) != self.players:
            raise StateError(f'expected a match of {self.players} players, found {sizes}')
        return [(first, index) for index in range(sizes[first])] + [
            (1 - first, index) for index in range(sizes[1 - first])
        ]

    def lineup(self, state: bytes) -> list[dict[str, Any]]:
        """Returns the header entry of each player, its team, index, role and control.

        Args:
            state: A state of the match, as `get_state` returns it.
        """
        from cuju import state as _state

        values = self._values(state, 'rest')
        order = self._order(values)
        roles = _state.read(state, [self._roles[entry] for entry in order])
        return [
            {
                'team': team,
                'index': index,
                'goalkeeper': int(role) == _GOALKEEPER,
                'controlled': int(values[f'controller{player}']) >= 0,
            }
            for player, ((team, index), role) in enumerate(zip(order, roles, strict=True))
        ]

    def frame(self, state: bytes, flags: int = 0) -> bytes:
        """Returns the frame of a state, as a recording stores it.

        Args:
            state: A state of the match, as `get_state` returns it.
            flags: The frame's flags, 1 when the episode ends on this step and 2 when an episode
                ended just before it, as an automatic reset shows.
        """
        import numpy as np

        values = self._values(state, 'pitch')
        order = self._order(values)
        self._last = (values, order)
        first = order[0][0] if order else 0
        score = [0, 0]
        score[first], score[1 - first] = int(values['goals_first']), int(values['goals_second'])
        # GRF: `teams` holds the team processed first at index 0, as the goal counts do.
        touch = {first: int(values['touch_first']), 1 - first: int(values['touch_second'])}
        touch_team = int(values['touch_team'])
        nodes = _world_transforms(
            np.stack([values[f'nodes{player}'] for player in range(self.players)])
        )
        padded = -(-self.players // 4) * 4
        states = np.zeros(padded, np.uint8)
        animations = np.zeros(padded, np.uint8)
        touches = np.zeros(padded, np.uint8)
        # GRF: a step runs ten ticks of 10 ms, so a touch of this step lies within its last 100 ms.
        step_start = int(values['actual']) - 100
        owner = -1
        for player in range(self.players):
            cards = int(values[f'cards{player}'])
            # GRF: the engine moves a gamer's controller to the designated player as play moves,
            # so 8 marks the player that an agent or a person controls on this step.
            states[player] = (
                (1 if values[f'active{player}'] else 0)
                | (2 if cards > 0 else 0)
                | (4 if cards > 1 else 0)
                | (8 if int(values[f'controller{player}']) >= 0 else 0)
            )
            animations[player] = int(values[f'animation{player}']) & 0xFF
            if int(values[f'touch_time{player}']) > step_start:
                touches[player] = int(values[f'touch_type{player}']) + 1
            team = order[player][0]
            # GRF: the raw observation's owner, a player in possession whose team touched last.
            if values[f'possession{player}'] and touch_team == team and touch[team] == player:
                owner = player
        game_mode = int(values['set_piece']) if values['in_set_piece'] else 0
        called = (
            bool(values['buffer_active'])
            and int(values['set_piece']) == 3
            and int(values['foul_type']) == 0
            and touch_team in touch
        )
        offside = (
            touch[touch_team]
            if called
            else (int(values['offside_first']) if int(values['offside_count']) > 0 else -1)
        )
        flags |= 4 if called else 0
        flags |= (int(values['scorer']) + 1) << 8
        head = _FRAME_HEAD.pack(
            # GRF: the engine counts -1 after a reset and adds one per step.
            int(values['step']) + 1,
            game_mode,
            score[0],
            score[1],
            touch_team if owner >= 0 else -1,
            owner,
            int(values['clock']),
            int(flags),
            *values['ball_position'].tolist(),
            *values['ball_rotation'].tolist(),
            # GRF: a foul type of 1 is a foul, 2 a yellow card and 3 a red card, and both players
            # index the match's players, as the header lists them.
            int(values['foul_player']) if int(values['foul_type']) else -1,
            int(values['foul_victim']) if int(values['foul_type']) else -1,
            int(values['foul_type']),
            offside,
        )
        return head + b''.join(
            (_quantize(nodes).tobytes(), states.tobytes(), animations.tobytes(), touches.tobytes())
        )

    def lanes(self, roles: Mapping[str, Any] | None = None) -> bytes:
        """Returns the lanes of the players of the state that `frame` read last, as one row.

        The row holds what `_players.row_type` lists, the direction, tiredness, sticky actions,
        agent and role of each player in the order of the header, the designated player of each
        team and the momentum of the ball. The values are those of GRF's raw observation of a
        left agent, quantized as the format describes. The sticky actions name their directions
        in the frame of the pitch, so those of an agent of the right team read turned by half a
        turn against its own observation.

        Args:
            roles: The roles that the players play, as `roles` of a batch returns them for the
                environment of the state, `left_team_roles` and `right_team_roles`. Without them
                the lanes hold the roles of the scenario, which leave out the hand-over of a
                goalkeeper who was sent off.

        Raises:
            StateError: If `frame` has read no state yet.
        """
        import numpy as np

        from . import players as _players

        if self._last is None:
            raise StateError('expected a frame read from a state before its lanes, found none')
        values, order = self._last
        teams = np.array([team for team, _ in order], np.intp)
        indices = np.array([index for _, index in order], np.intp)
        row = np.zeros((), _players.row_type(self.players))

        ticks, raw_scale, sticky_directions, sticky_bits = _player_scales()

        def raw(vector: np.ndarray) -> np.ndarray:
            # GRF: the movement of a step divides in single precision and its scales in double.
            step = vector[..., :2] / ticks
            return (step.astype(np.float64) / raw_scale).astype(np.float32).astype(np.float64)

        def steps(numbers: Any, least: int = -32767) -> np.ndarray:
            return np.clip(np.rint(numbers), least, 32767)

        row['direction'] = steps(raw(values['movement']) * _players.DIRECTION_SCALE)
        tired = (np.float32(1) - values['fatigue']).astype(np.float64)
        row['tired'] = steps(tired * _players.TIRED_SCALE, 0)
        # GRF: a player that an agent controls names its gamer, counted over both teams with
        # those of the team processed first before the others.
        controllers = values['controllers']
        first_gamers = int(values['gamers_first'])
        gamer = np.where(
            controllers < first_gamers, controllers, self._gamers + controllers - first_gamers
        )
        held = (controllers >= 0) & (gamer < 2 * self._gamers)
        gamer = np.where(held, gamer, 0)
        keyboards = values['keyboard'].reshape(-1)[gamer]
        row['agent'] = np.where(held, keyboards % _TEAM_KEYBOARDS + 1, 0)
        # GRF: a keyboard holds its direction times its mirror sign, 1 or -1, so one more
        # multiplication gives the direction in the frame of the pitch, exactly.
        # The gamers past those of the agents hold no value of the match, so whatever bytes they
        # hold multiply without a warning, and no player names them.
        with np.errstate(all='ignore'):
            chosen = values['pressed'] * values['mirror'][..., None]
        directions = (chosen[:, :, None, :] == sticky_directions).all(-1)
        buttons = values['buttons'][:, :, _STICKY_BUTTONS] != 0
        masks = (np.concatenate((directions, buttons), -1) * sticky_bits).sum(-1).reshape(-1)
        row['sticky'] = np.where(held, masks[gamer], 0)
        if roles is None:
            row['role'] = values['scenario_roles'][teams, indices]
        else:
            sides = [
                np.asarray(roles[name]).reshape(-1)
                for name in ('left_team_roles', 'right_team_roles')
            ]
            row['role'] = [int(sides[team][index]) for team, index in order]
        first = order[0][0] if order else 0
        for team, name in ((first, 'designated_first'), (1 - first, 'designated_second')):
            player = int(values[name])
            row['designated'][team] = player + 1 if 0 <= player < self.players else 0
        momentum = values['ball_momentum']
        row['ball_momentum'][:2] = steps(raw(momentum) * _players.DIRECTION_SCALE)
        height = np.float64(momentum[2] / ticks)
        row['ball_momentum'][2] = steps(height * _players.HEIGHT_SCALE)
        return row.tobytes()


# The trailing axes of a block that belong to each of its fields, where a field is a vector.
_TRAILING = {'movement': 1, 'pressed': 1}


def _multiply(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Returns the Hamilton products of quaternions stored x, y, z, w, as GRF multiplies them."""
    import numpy as np

    x1, y1, z1, w1 = np.moveaxis(first, -1, 0)
    x2, y2, z2, w2 = np.moveaxis(second, -1, 0)
    return np.stack(
        (
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 + y1 * w2 + z1 * x2 - x1 * z2,
            w1 * z2 + z1 * w2 + x1 * y2 - y1 * x2,
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        ),
        -1,
    )


def _rotate(quaternion: np.ndarray, vector: np.ndarray) -> np.ndarray:
    """Returns each `vector` rotated by its `quaternion`, with GRF's two cross products."""
    import numpy as np

    part = quaternion[..., :3]
    first = np.cross(part, vector)
    second = np.cross(part, first)
    return vector + first * (2.0 * quaternion[..., 3:]) + second * 2.0


def _world_transforms(blocks: np.ndarray) -> np.ndarray:
    """Returns `[players, nodes, 7]`, the world position and rotation of every node of each player.

    Speed: the nodes come in pre-order, so each node needs its parent alone, and every player goes
    at once.

    Args:
        blocks: `[players, nodes * 44]` bytes, the fourteen nodes of each player as the state holds
            them, each a position, a rotation, a scale and a local mode.
    """
    import numpy as np

    count = blocks.shape[0]
    records = np.ascontiguousarray(blocks, np.uint8).reshape(count, len(NODES), _NODE_BYTES)
    floats = records[..., :40].copy().view('<f4').astype(np.float64)
    modes = records[..., 40:44].copy().view('<i4')[..., 0]
    positions, rotations, scales = floats[..., 0:3], floats[..., 3:7], floats[..., 7:10]
    world = np.empty((count, len(NODES), 7))
    world_scale = np.empty((count, len(NODES), 3))
    for node, parent in enumerate(PARENTS):
        world[:, node, 3:] = rotations[:, node]
        world_scale[:, node] = scales[:, node]
        world[:, node, :3] = positions[:, node]
        if parent < 0:
            continue
        relative = modes[:, node] == _LOCAL_MODE_RELATIVE
        if not relative.any():
            continue
        rotation = _multiply(world[:, parent, 3:], rotations[:, node])
        rotation /= np.linalg.norm(rotation, axis=-1, keepdims=True)
        scale = world_scale[:, parent] * scales[:, node]
        position = (
            _rotate(world[:, parent, 3:], world_scale[:, parent] * positions[:, node])
            + world[:, parent, :3]
        )
        world[relative, node, 3:] = rotation[relative]
        world_scale[relative, node] = scale[relative]
        world[relative, node, :3] = position[relative]
    return world.astype(np.float32)


class Frames(list):
    """The frames of an episode, with what a recording keeps of the episode beside them.

    It is the list of the frames, so everything that takes the frames of a recording takes it,
    and `encode` writes each part that it carries as a section of its own. A recording of
    version 1 has room for the frames alone.

    Attributes:
        players: The lanes of the players, one row for each frame as `StateReader.lanes` returns
            it, or the lanes by field as `_players.stack` returns them, or `None`.
        actions: The actions of the agents, `[frames + 1, agents]` as `_actions` describes them,
            or `None`.
        state: The state of the kick off of the episode, as `get_state` returned it at the reset,
            or `None` when the capture began after it.
        overlays: The overlays of a policy, as `overlays` describes its records, or `None`. The
            library writes them to the attachment `overlays` beside the recording.
    """

    def __init__(
        self,
        frames: Iterable[bytes] = (),
        players: Sequence[bytes] | Mapping[str, Any] | None = None,
        actions: Any = None,
        state: bytes | None = None,
        overlays: Iterable[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(frames)
        self.players = players
        self.actions = actions
        self.state = state
        self.overlays = overlays


def run_key(
    scenario: str,
    agents: Sequence[int],
    action_set: str | None,
    engine_seed: int | None,
    reverse: bool | None,
    difficulty: Sequence[float] | None = None,
    stacked: bool = False,
    start: str = START_RESET,
) -> dict[str, Any]:
    """Returns the key of a run, what an episode needs beside its actions to play again.

    Two recordings with equal keys started from the same kick off, so they align step for step
    while their agents choose the same actions. `encode` adds `state_digest`, the SHA-256 of the
    state of the kick off, when the recording keeps that state.

    Args:
        scenario: The scenario of the batch, as its `scenario_name` names it.
        agents: The numbers of agents of the left and the right team.
        action_set: The name of the action set, `default`, `v2` or `full`, or `None`.
        engine_seed: The engine seed of the episode, or `None` when no state told it.
        reverse: Whether the episode plays with GRF's reverse team processing.
        difficulty: The difficulties that play on the left and on the right, or `None`.
        stacked: Whether the state of the kick off holds GRF's stack of observations, which a
            batch that stacks the same observations loads.
        start: `START_RESET` when the first frame is the reset of the episode, and `START_MID`
            when the capture began later, which leaves the recording without the state.
    """
    key: dict[str, Any] = {
        'start': start,
        'scenario': scenario,
        'agents': [int(count) for count in agents],
    }
    if action_set is not None:
        key['action_set'] = action_set
    if engine_seed is not None:
        key['engine_seed'] = engine_seed
    if reverse is not None:
        key['reverse'] = bool(reverse)
    if difficulty is not None:
        key['difficulty'] = [float(f'{value:.6g}') for value in difficulty]
    if stacked:
        key['stacked'] = True
    return key


def header(
    title: str | dict[str, Any],
    scenario: str,
    lineup: list[dict[str, Any]],
    teams: list[dict[str, Any]] | None = None,
    frame_count: int | None = None,
    timing: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    agents: Sequence[int] | None = None,
    action_set: str | None = None,
    key: dict[str, Any] | None = None,
    home: str | Mapping[str, Any] | None = None,
) -> bytes:
    """Returns the magic, the header length and the header of a recording, as version 1 has it.

    A live stream sends the header as it is, and `write` turns it into the version it writes.
    What a team wears, its colors and its style, stays in this header as the teams give it, and
    `write` takes it out of the file, as `split` tells, for the attachment `look`.

    Args:
        title: The title that the viewer shows, text that a person gave or a label that
            `_messages.label` returns for a title that cuju made, which the header keeps as its
            English `title` with `title_id` and `title_values` beside it.
        scenario: The name of the scenario.
        lineup: The entry of each player, as `StateReader.lineup` returns it.
        teams: The name, `color` and `secondary` color of the left and right team, each optional,
            beside any other field of the team, such as its `id` and `style`. A name is text or a
            label, as the title is, and a team without one takes the name of its side, Left or
            Right, as a label.
        frame_count: The number of frames, or None for a live stream.
        timing: The optional scenario timing fields.
        provenance: What is known of where the recording comes from, such as its source, seed
            and configuration. The version of cuju joins it, and a field left out stays unknown.
        agents: The numbers of agents of the left and the right team, which the actions of the
            recording count by, or `None` to leave them out.
        action_set: The name of the action set that numbers the actions and orders the sticky
            actions, or `None` to leave it out.
        key: The key of the run, as `run_key` returns it, or `None` to leave it out.
        home: The side at home, an entrant, a mapping of `entrant` with the `ability` that the
            built-in AI played at or the `key` of a policy, which the provenance keeps as `home`
            for the look of the match, or `None` for the left side as the provenance names it.
    """
    given = [*(teams or ()), {}, {}]
    sides = []
    for side in range(2):
        team = {**_DEFAULT_TEAMS[side], **given[side]}
        if 'name' in given[side]:
            # A name given as text is a person's and travels alone, a label keeps its message.
            team = {key: value for key, value in team.items() if key not in _NAME_FIELDS}
            team.update(_name_fields(given[side]))
        sides.append(team)
    document = {
        'version': 1,
        'engine_scorer': True,
        'offside_called': True,
        **(timing or {}),
        **_messages.named('title', title),
        'scenario': scenario,
        'steps_per_second': 10,
        'nodes': list(NODES),
        'parents': list(PARENTS),
        'players': lineup,
        'teams': sides,
        'frame_bytes': frame_bytes(len(lineup)),
        'frame_count': frame_count,
        'provenance': {**(provenance or {}), 'cuju_version': __version__},
    }
    if home is not None:
        document['provenance']['home'] = dict(home) if isinstance(home, Mapping) else home
    if agents is not None:
        document['agents'] = [int(count) for count in agents]
    if action_set is not None:
        document['action_set'] = action_set
    if key is not None:
        document['run_key'] = dict(key)
    # JavaScript holds integers exactly only up to 2**53 - 1, so a larger seed travels as text.
    seed = document['provenance'].get('seed')
    if isinstance(seed, int) and not isinstance(seed, bool) and seed > 2**53 - 1:
        document['provenance']['seed'] = str(seed)
    return _encode_header(document)


# The fields of a name that cuju made, beside its English text.
_NAME_FIELDS = ('name_id', 'name_values')


def _name_fields(team: dict[str, Any]) -> dict[str, Any]:
    """Returns the name of a team as a header keeps it, its text and, for a label, its message.

    The name is text, a label that `_messages.label` returns, or text beside the `name_id` and
    `name_values` of an entry of the registry, such as the built-in AI's.
    """
    name = team.get('name')
    if isinstance(name, dict):
        return _messages.named('name', name)
    found = {'name': name}
    if isinstance(team.get('name_id'), str) and isinstance(team.get('name_values'), dict):
        found.update(name_id=team['name_id'], name_values=dict(team['name_values']))
    return found


def label_of(document: dict[str, Any], field: str) -> dict[str, Any] | str | None:
    """Returns a name of a header or summary as a label when it holds a valid one, else as text.

    A file of before, or a name that a person gave, holds the text alone and returns it.
    """
    text = document.get(field)
    found = {
        'id': document.get(f'{field}_id'),
        'text': text,
        'values': document.get(f'{field}_values'),
    }
    return found if _messages.valid_label(found) else text


def _document(head: bytes) -> dict[str, Any]:
    """Returns the header of the bytes that `header` returns."""
    return json.loads(head[len(MAGIC) + 4 :])


def _encode_header(document: dict[str, Any]) -> bytes:
    """Returns the magic, the length and a header, padded so that what follows aligns to four."""
    text = json.dumps(document, separators=(',', ':')).encode()
    # Spaces after the JSON align the frames to four bytes, so a reader views their floats in place.
    text += b' ' * (-(len(MAGIC) + 4 + len(text)) % 4)
    return MAGIC + struct.pack('<I', len(text)) + text


# The fields of a header and of its teams that tell how a match looks, which a recording leaves to
# its attachment `look`. A recording of an earlier writer holds them, and the readers of
# attachments take them from there as its embedded look.
PRESENTATION = ('kits', 'sky', 'ground')
TEAM_PRESENTATION = ('color', 'secondary', 'style')


def _roster(style: Any) -> list[dict[str, Any]]:
    """Returns the name and number of each player that a team's style lists, by player index."""
    players = style.get('players') if isinstance(style, Mapping) else None
    roster = []
    for player in players if isinstance(players, list) else []:
        entry: dict[str, Any] = {}
        if isinstance(player, Mapping):
            if isinstance(player.get('name'), str):
                entry['name'] = player['name']
            number = player.get('number')
            if isinstance(number, int) and not isinstance(number, bool):
                entry['number'] = number
        roster.append(entry)
    return roster if any(roster) else []


def split(document: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns a header as a file keeps it, the game alone, and what it held beside the game.

    The second part holds the `kits`, `sky` and `ground` that the header held, and `teams`, what
    each team wore, its `color`, `secondary` and `style`, by side, or nothing when the header held
    none of them. The name and number of each player stay in the first part as the team's
    `roster`, since the captions, the search and the statistics read them.
    """
    game = dict(document)
    presentation = {key: game.pop(key) for key in PRESENTATION if key in game}
    teams = game.get('teams')
    if isinstance(teams, list):
        kept, worn = [], []
        for team in teams:
            if not isinstance(team, Mapping):
                kept.append(team)
                worn.append({})
                continue
            rest = {key: value for key, value in team.items() if key not in TEAM_PRESENTATION}
            roster = _roster(team.get('style'))
            if roster and 'roster' not in rest:
                rest['roster'] = roster
            kept.append(rest)
            worn.append({key: team[key] for key in TEAM_PRESENTATION if key in team})
        game['teams'] = kept
        if any(worn):
            presentation['teams'] = worn
    return game, presentation


def _section(tag: bytes, payload: bytes) -> bytes:
    """Returns a section of version 2, its tag, its length, the payload and zeros to four bytes."""
    return _SECTION.pack(tag, len(payload)) + payload + bytes(-len(payload) % 4)


def encode(head: bytes, frames: list[bytes], version: int = VERSION) -> bytes:
    """Returns a recording before compression, in version 1, 2 or 3, which holds the game alone.

    Versions 2 and 3 write a section for the lanes, the actions and the state that `frames`
    carries as a `Frames`, and their header lists the tags of those sections in `sections`.
    Version 3 writes the tracks of the frames in place of the frames. What the header holds
    beside the game, as `split` tells, and the overlays that `frames` carries stay out of the
    file, for the attachments that the library writes beside it.

    Args:
        head: The magic, length and header, as `header` returns them.
        frames: The frames, each as `StateReader.frame` returns it, plain or as a `Frames` with
            the lanes, actions and state of the episode. Version 3 also takes track frames, as
            `_tracks.of_frame` returns them.
        version: The version of the file.

    Raises:
        StateError: If the version is unknown, or a part that the frames carry does not fit the
            frames, the players or the agents of the header.
    """
    import numpy as np

    from . import actions as _actions
    from . import codec as _codec
    from . import players as _players
    from . import tracks as _tracks

    if version == 1:
        return _encode_header(split(_document(head))[0]) + b''.join(frames)
    if version not in VERSIONS or isinstance(version, bool):
        raise StateError(f'expected a recording version from {VERSIONS}, found {version!r}')
    document = split(_document(head))[0]
    players = len(document['players'])
    count = len(frames)
    document.update(version=version, frame_count=count, keyframe_interval=KEYFRAME_INTERVAL)
    if version == 3:
        size = frame_bytes(players)
        tracked = [
            _tracks.of_frame(frame, players) if len(frame) == size else frame for frame in frames
        ]
        if any(len(frame) != _tracks.frame_bytes(players) for frame in tracked):
            raise StateError(f'expected frames or track frames of {players} players')
        main = _section(_tracks.TAG, _tracks.encode(tracked, players))
        # A recording of positions holds no frame of the size that the joints give.
        document.pop('frame_bytes', None)
        document['fidelity'] = POSITIONS
    else:
        main = _section(FRAMES_TAG, _codec.encode(frames, players))
        document['fidelity'] = JOINTS
    carried = frames if isinstance(frames, Frames) else Frames()
    parts = []
    if carried.players is not None and count:
        lanes = carried.players
        if not isinstance(lanes, Mapping):
            if len(lanes) != count:
                raise StateError(f'expected the lanes of {count} frames, found {len(lanes)}')
            lanes = _players.stack(lanes, players)
        fields = _players.fields_of(lanes)
        if any(len(lanes[name]) != count for name in fields):
            raise StateError(f'expected the lanes of the players for {count} frames')
        if fields:
            document['player_fields'] = fields
            parts.append((_players.TAG, _players.encode(lanes, players)))
    if carried.actions is not None:
        agents = document.get('agents')
        rows = np.asarray(carried.actions)
        if not agents or rows.shape != (count + 1, sum(agents)) or not sum(agents):
            raise StateError(
                f'expected the actions of the agents of the header, {agents}, over {count} '
                f'frames and one more row, found the shape {rows.shape}'
            )
        parts.append((_actions.TAG, _actions.encode(rows)))
    key = document.get('run_key')
    if isinstance(key, dict):
        key.pop('state_digest', None)
    if carried.state is not None:
        key = dict(key) if isinstance(key, dict) else {'start': START_RESET}
        key['state_digest'] = hashlib.sha256(carried.state).hexdigest()
        document['run_key'] = key
        parts.append((STATE_TAG, bytes(carried.state)))
    document['sections'] = [tag.decode('ascii') for tag, _ in parts]
    return _encode_header(document) + main + b''.join(_section(*part) for part in parts)


def write(path: Path, head: bytes, frames: list[bytes], version: int = VERSION) -> str:
    """Writes a recording, gzip-compressed, through a temporary file, and returns its key.

    The key is `sha256:` and the SHA-256 of the bytes of the file once decompressed, which the
    attachments of the recording name.

    Args:
        path: The path of the file.
        head: The magic, length and header, as `header` returns them.
        frames: The frames, each as `StateReader.frame` returns it, plain or as a `Frames`.
        version: The version of the file, 2 by default, 1 for readers of cuju 0.2.0, or 3 for a
            recording of positions.
    """
    data = encode(head, frames, version)
    path = Path(path)
    temporary = path.with_name(path.name + '.part')
    with gzip.open(temporary, 'wb', compresslevel=6) as stream:
        stream.write(data)
    temporary.replace(path)
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def _whole(value: Any) -> bool:
    """Returns whether a value of a header is a whole number, which `True` is not."""
    return isinstance(value, int) and not isinstance(value, bool)


def validate_header(document: Any) -> dict[str, Any]:
    """Returns a header after checking that every field the readers use holds its type.

    A field that a header may leave out, such as the title, the teams or the provenance, may also
    be null, and the readers then take its default.

    Raises:
        StateError: If the header is not an object, names a version that this module does not
            read, holds players, a frame size, a number of frames or a keyframe interval that does
            not fit its version or the limits of a recording, or holds a field of the wrong type.
    """
    if not isinstance(document, dict):
        raise StateError(f'expected a header object, found {type(document).__name__}')
    version = document.get('version', 1)
    if not _whole(version) or version not in VERSIONS:
        raise StateError(f'expected a recording version from {VERSIONS}, found {version!r}')
    players = document.get('players')
    if not isinstance(players, list) or len(players) > MAX_PLAYERS:
        raise StateError(f'expected a list of at most {MAX_PLAYERS} players in the header')
    for player in players:
        if not (
            isinstance(player, dict)
            and _whole(player.get('team'))
            and player['team'] in (0, 1)
            and _whole(player.get('index'))
            and 0 <= player['index'] < MAX_PLAYERS
            and all(
                isinstance(player.get(key, False), bool) for key in ('goalkeeper', 'controlled')
            )
        ):
            raise StateError(
                'expected each player of the header with its team, 0 or 1, its index and flags, '
                f'found {str(player)[:80]}'
            )
    size = document.get('frame_bytes')
    if (version == 1 or (size is not None and version == 2)) and not (
        _whole(size) and size == frame_bytes(len(players))
    ):
        raise StateError(
            f'expected frames of {frame_bytes(len(players))} bytes for {len(players)} players, '
            f'found {size!r}'
        )
    count = document.get('frame_count')
    if (version != 1 or count is not None) and not (_whole(count) and 0 <= count <= MAX_FRAMES):
        raise StateError(f'expected a number of frames from 0 to {MAX_FRAMES}, found {count!r}')
    interval = document.get('keyframe_interval')
    if version != 1 and not (_whole(interval) and interval == KEYFRAME_INTERVAL):
        raise StateError(f'expected a keyframe interval of {KEYFRAME_INTERVAL}, found {interval!r}')
    for field, kinds in (
        ('title', str),
        ('scenario', str),
        ('provenance', dict),
        ('action_set', str),
        ('fidelity', str),
        ('run_key', dict),
        ('sky', dict),
        ('kits', dict),
        ('ground', dict),
    ):
        if document.get(field) is not None and not isinstance(document[field], kinds):
            raise StateError(f'expected the {field} of the header as {kinds.__name__}')
    tags = document.get('sections')
    if tags is not None and not (
        isinstance(tags, list)
        and len(tags) <= MAX_SECTIONS
        and all(isinstance(tag, str) and len(tag) == 4 and tag.isascii() for tag in tags)
    ):
        raise StateError('expected the sections of the header as tags of four letters')
    fields = document.get('player_fields')
    if fields is not None and not (
        isinstance(fields, list)
        and len(fields) <= MAX_SECTIONS
        and all(isinstance(field, str) for field in fields)
    ):
        raise StateError('expected the player fields of the header as a list of names')
    agents = document.get('agents')
    if agents is not None and not (
        isinstance(agents, list)
        and len(agents) == 2
        and all(_whole(side) and 0 <= side <= MAX_PLAYERS for side in agents)
    ):
        raise StateError('expected the agents of the header as the numbers of the two sides')
    speed = document.get('steps_per_second')
    if speed is not None and not (
        isinstance(speed, int | float)
        and not isinstance(speed, bool)
        and math.isfinite(speed)
        and 0 < speed <= 1000
    ):
        raise StateError(f'expected steps per second above 0 and at most 1000, found {speed!r}')
    second = document.get('second_half')
    if second is not None and not (_whole(second) and second >= 0):
        raise StateError(f'expected the step of the second half, found {second!r}')
    teams = document.get('teams')
    if teams is not None and not (
        isinstance(teams, list)
        and all(
            isinstance(team, dict)
            and all(
                team.get(key) is None or isinstance(team[key], kind)
                for key, kind in (('name', str), ('id', str), ('style', dict))
            )
            for team in teams
        )
    ):
        raise StateError('expected the teams of the header, each with a name as text')
    # A label of a message that no recording carries leaves its name as the text beside it.
    for holder, field in [(document, 'title'), *((team, 'name') for team in teams or ())]:
        fields = (f'{field}_id', f'{field}_values')
        if any(key in holder for key in fields) and not _carried(
            {
                'id': holder.get(fields[0]),
                'text': holder.get(field),
                'values': holder.get(fields[1]),
            }
        ):
            for key in fields:
                holder.pop(key, None)
    return document


def _carried(label: Any) -> bool:
    """Returns whether a label names a message of the names of cuju, as each label of its values."""

    def named(value: dict[str, Any]) -> bool:
        return 'kind' in value or (
            value['id'].startswith(_LABEL_PREFIXES)
            and all(isinstance(item, str) or named(item) for item in value['values'].values())
        )

    try:
        return _messages.valid_label(label) and named(label)
    except RecursionError:
        return False


class _Budget:
    """Reads a decompressed recording, counting its bytes against the limit of a recording.

    A read never asks for more than the limit leaves, so a length that a file gives reserves no
    more memory than the limit allows, and a fault of the compression turns into a `StateError`.
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self.left = MAX_BYTES

    def read(self, size: int) -> bytes:
        try:
            data = self._stream.read(min(size, self.left + 1))
        except (EOFError, zlib.error, gzip.BadGzipFile) as error:
            raise StateError(f'expected a whole gzip stream, found {error}') from None
        self.left -= len(data)
        if self.left < 0:
            raise StateError(
                f'expected a recording of at most {MAX_BYTES >> 20} MiB once decompressed'
            )
        return data


def _constant(name: str) -> Any:
    """Refuses NaN and the infinities, which Python reads in JSON and a page does not."""
    raise ValueError(f'expected numbers of JSON, found {name}')


def _parse_header(stream: Any) -> dict[str, Any]:
    """Returns the header at the start of `stream`, after checking the magic and its fields."""
    start = stream.read(len(MAGIC) + 4)
    if start[: len(MAGIC)] != MAGIC:
        raise StateError('expected a recording of cuju, found other bytes')
    if len(start) < len(MAGIC) + 4:
        raise StateError('expected the length of a header, found the recording cut short')
    (length,) = struct.unpack_from('<I', start, len(MAGIC))
    if length > MAX_HEADER_BYTES:
        raise StateError(f'expected a header of at most {MAX_HEADER_BYTES} bytes, found {length}')
    text = stream.read(length)
    if len(text) < length:
        raise StateError('expected a whole header, found the recording cut short')
    try:
        document = json.loads(text, parse_constant=_constant)
    except (ValueError, RecursionError) as error:
        raise StateError(f'expected a header of JSON, found {error}') from None
    return validate_header(document)


def _open(path: Path) -> Any:
    """Opens a recording file to read it decompressed, after a look at its size.

    A file larger than a recording may decompress to is no recording that the readers take, since
    gzip never grows what it packs by more than a few bytes in a thousand, so it is refused by its
    size alone, before any of it is read.

    Raises:
        StateError: If the file is larger than the limit of a recording.
    """
    size = Path(path).stat().st_size
    if size > MAX_BYTES:
        raise StateError(
            f'expected a recording file of at most {MAX_BYTES >> 20} MiB, found one of '
            f'{-(-size >> 20)} MiB'
        )
    return gzip.open(path, 'rb')


def read_header(path: Path) -> dict[str, Any]:
    """Returns the header of a recording file, reading only its start."""
    with _open(path) as stream:
        return _parse_header(_Budget(stream))


def _sections(stream: Any, tags: tuple[bytes, ...] | None) -> dict[bytes, bytes]:
    """Returns the sections of `tags` that follow the header in `stream`, by tag.

    The first section of each tag counts, and the reader passes over any other in chunks. `None`
    takes every section.
    """
    found = {}
    while head := stream.read(_SECTION.size):
        if len(head) < _SECTION.size:
            raise StateError('expected a whole section of the recording, found it cut short')
        tag, length = _SECTION.unpack(head)
        rest = length + (-length % 4)
        if (tags is None or tag in tags) and tag not in found:
            payload = stream.read(length)
            if len(payload) < length:
                raise StateError('expected a whole section of the recording, found it cut short')
            found[tag] = payload
            rest -= length
        while rest > 0:
            skipped = len(stream.read(min(rest, _SKIP_BYTES)))
            if skipped == 0:
                raise StateError('expected a whole section of the recording, found it cut short')
            rest -= skipped
    return found


def _coded_frames(document: dict[str, Any], sections: dict[bytes, bytes]) -> tuple[bytes, int]:
    """Returns the frames section of a recording of version 2 and the number of its players."""
    from . import codec as _codec

    players = len(document['players'])
    data = sections.get(FRAMES_TAG)
    if (
        not players
        or data is None
        or len(data) != _codec.section_bytes(players, document['frame_count'])
    ):
        raise StateError('expected the frames of a recording of version 2, found them malformed')
    return data, players


def _read(path: Path, *tags: bytes) -> tuple[dict[str, Any], dict[bytes, bytes]]:
    """Returns the header of a recording file and its sections of `tags`, every one for none."""
    with _open(path) as raw:
        stream = _Budget(raw)
        document = _parse_header(stream)
        if document.get('version', 1) == 1:
            return document, {}
        return document, _sections(stream, tags or None)


def sections(path: Path) -> dict[str, bytes]:
    """Returns every section of a recording file by its tag, as the file stores it.

    The tags are those of the format, such as `FRMS`, `PLYR`, `ACTS`, `STAT` and `TRKS`, the
    `ANNO` and `GRID` of an earlier writer, and any that a later writer adds. `frames`, `tracks`,
    `players`, `actions` and `state` decode the sections that this module knows, and
    `attachments` reads `ANNO` and `GRID` as embedded attachments. A file of version 1 has no
    sections.

    Raises:
        StateError: If the file is not a recording or reaches past the limits of one.
    """
    return {tag.decode('latin-1'): payload for tag, payload in _read(path)[1].items()}


def players(path: Path, start: int = 0, stop: int | None = None) -> dict[str, np.ndarray]:
    """Returns the lanes of the players of a recording file by field, none for a file without.

    Each field comes as `_players.decode` describes it, for frames `start` to `stop`, in the
    stored whole numbers, which `_players.scaled` turns into the units of GRF's raw observation.

    Args:
        path: The path of the file.
        start: The first frame to return.
        stop: The frame after the last to return, by default the end.

    Raises:
        StateError: If the file is not a recording, or its lanes are shorter than its header says.
    """
    from . import players as _players

    document, found = _read(path, _players.TAG)
    payload = found.get(_players.TAG)
    fields = document.get('player_fields')
    if payload is None or not fields:
        return {}
    count = document['frame_count']
    return _players.decode(
        payload, len(document['players']), count, fields, KEYFRAME_INTERVAL, start, stop
    )


def actions(path: Path) -> np.ndarray | None:
    """Returns the actions of the agents of a recording file, `None` for a file without.

    The array is `[frames + 1, agents]` `uint8`, the agents of the left team first. Row `f` is
    the action that each agent gave on the step that led to frame `f`, and the last row the
    action of the step that ended the episode when an automatic reset replaced its frame. A value
    of 255 stands for no action or for one that the recorder did not see.

    Raises:
        StateError: If the file is not a recording, or its actions do not fit its header.
    """
    from . import actions as _actions

    document, found = _read(path, _actions.TAG)
    payload = found.get(_actions.TAG)
    if payload is None:
        return None
    agents = document.get('agents')
    if not agents:
        raise StateError('expected the agents of the header beside the actions of a recording')
    return _actions.decode(payload, document['frame_count'], sum(agents))


def state(path: Path) -> bytes | None:
    """Returns the state of the kick off of a recording file, `None` for a file without.

    The bytes are those that `get_state` returned at the reset of the episode, which `set_state`
    of a batch of the same configuration loads.

    Raises:
        StateError: If the file is not a recording, or the state differs from the digest that
            the key of the run holds for it.
    """
    document, found = _read(path, STATE_TAG)
    payload = found.get(STATE_TAG)
    if payload is None:
        return None
    digest = (document.get('run_key') or {}).get('state_digest')
    if digest != hashlib.sha256(payload).hexdigest():
        raise StateError('expected the state of the kick off that the key of the run names')
    return payload


def _coded_tracks(document: dict[str, Any], found: dict[bytes, bytes]) -> tuple[bytes, int]:
    """Returns the tracks section of a recording of version 3 and the number of its players."""
    from . import tracks as _tracks

    count = len(document['players'])
    data = found.get(_tracks.TAG)
    if (
        not count
        or data is None
        or len(data) != _tracks.section_bytes(count, document['frame_count'])
    ):
        raise StateError('expected the tracks of a recording of version 3, found them malformed')
    return data, count


def tracks(path: Path, start: int = 0, stop: int | None = None) -> Iterator[bytes]:
    """Yields the track frames of a recording file one after another, for any version.

    A track frame holds the head of a frame, the position of the root joint of each player and
    the closing bytes, as `_tracks` describes it. A file of version 3 stores them, and the frames
    of a file of version 1 or 2 give theirs.

    Args:
        path: The path of the file.
        start: The first frame to yield.
        stop: The frame after the last to yield, by default the end.

    Raises:
        StateError: If the file is not a recording or reaches past the limits of one.
    """
    from . import tracks as _tracks

    document = read_header(path)
    count = len(document['players'])
    if document.get('version', 1) != 3:
        for frame in frames(path, start, stop):
            yield _tracks.of_frame(frame, count)
        return
    data, count = _coded_tracks(document, _read(path, _tracks.TAG)[1])
    total = document['frame_count']
    end = total if stop is None else max(0, min(stop, total))
    at = max(0, start)
    while at < end:
        upto = min(end, (at // _DECODE_FRAMES + 1) * _DECODE_FRAMES)
        for row in _tracks.decode(data, count, total, KEYFRAME_INTERVAL, at, upto):
            yield row.tobytes()
        at = upto


def frames(path: Path, start: int = 0, stop: int | None = None) -> Iterator[bytes]:
    """Yields the frames of a recording file one after another, each as version 1 stores it.

    A file of version 2 decodes from the keyframe before `start`, so the last frames come quickly,
    and a thousand frames at a time, so a long recording takes little memory beyond its file.

    Args:
        path: The path of the file.
        start: The first frame to yield.
        stop: The frame after the last to yield, by default the end.

    Raises:
        StateError: If the file is not a recording, reaches past the limits of one, or is a
            recording of positions, which holds no joints and gives its `tracks`.
    """
    from . import codec as _codec

    with _open(path) as raw:
        stream = _Budget(raw)
        document = _parse_header(stream)
        if document.get('version', 1) == 3:
            raise StateError(
                'expected a recording with the joints of its players, found a recording of '
                'positions, version 3, whose track frames tracks() yields'
            )
        if document.get('version', 1) == 1:
            size = document['frame_bytes']
            index = 0
            # A last frame cut short is left out, as the page leaves it out.
            while (stop is None or index < stop) and len(chunk := stream.read(size)) == size:
                if index >= MAX_FRAMES:
                    raise StateError(f'expected a recording of at most {MAX_FRAMES} frames')
                if index >= start:
                    yield chunk
                index += 1
            return
        data, players = _coded_frames(document, _sections(stream, (FRAMES_TAG,)))
    count = document['frame_count']
    end = count if stop is None else max(0, min(stop, count))
    at = max(0, start)
    while at < end:
        upto = min(end, (at // _DECODE_FRAMES + 1) * _DECODE_FRAMES)
        for row in _codec.decode(data, players, count, KEYFRAME_INTERVAL, at, upto):
            yield row.tobytes()
        at = upto


def ending(path: Path) -> tuple[int, bytes | None]:
    """Returns the number of frames of a recording file and its last frame, `None` for none.

    A file of version 2 decodes its last frame alone, and one of version 1 reads one frame at a
    time, so neither holds more than its file and a few frames at once. A file of version 3
    gives its last track frame, which opens with the same head as a frame, the step, the score
    and the clock among it.

    Raises:
        StateError: If the file is not a recording or reaches past the limits of one.
    """
    document = read_header(path)
    count, last = 0, None
    if document.get('version', 1) != 1:
        count = document['frame_count']
        read = tracks if document['version'] == 3 else frames
        for frame in read(path, start=max(0, count - 1)):
            last = frame
        return count, last
    for frame in frames(path):
        count, last = count + 1, frame
    return count, last


def scenario_timing(state: bytes) -> dict[str, Any]:
    """Returns the scenario timing without changing the state."""
    from cuju import state as _state

    duration, second, score, possession, out = _state.read(
        state,
        [
            'environment.scenario.gameDuration',
            'environment.scenario.secondHalf',
            'environment.scenario.endEpisodeOnScore',
            'environment.scenario.endEpisodeOnPossessionChange',
            'environment.scenario.endEpisodeOnOutOfPlay',
        ],
    )
    duration, second = int(duration), int(second)
    half_ms = (1800 * second) & 0xFFFFFFFF
    return {
        'duration': duration,
        'second_half': second if 0 < second < duration and half_ms < 0x80000000 else None,
        'full_match': duration >= 3000 and not any((score, possession, out)),
    }
