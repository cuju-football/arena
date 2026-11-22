# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The team registry, Kaggle submissions kept in the home folder.

A team is a submission of Kaggle's GRF competition, a folder whose `main.py` defines the agent, as
Kaggle receives it, also accepted as one Python file or an archive. The built-in AI of GRF is always
a team as well. A submission is code, which runs with the permissions of the process that plays
it.

Each team carries its identity as a submission: a size, `5v5` or `11v11`, the players it controls,
its home scenario, its name and the names and numbers of its players. The identity lives in the
folder `cuju-team` inside the submission, beside the earlier checkpoints, so a team exported as a
zip carries it along and a submission folder that holds one brings it back. The manifest
`cuju-team/team.json` is read and written by `manifest.py`, field by field.

Every other folder of a team whose name starts with `cuju-` belongs to a tool, such as the look
that cuju render keeps in `cuju-look`. The registry keeps such a folder, carries it in exports,
leaves it out of submissions, checkpoints and digests, and never reads it.
"""

from __future__ import annotations

import bz2
import contextlib
import functools
import gzip
import io
import json
import logging
import os
import re
import shutil
import struct
import sys
import tarfile
import tempfile
import threading
import time
import types
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

from cuju.errors import ArgumentError

from .._store import home as _home
from .._store import runs as _runs
from .._text import messages as _messages
from .._text.messages import MessageError
from . import checkpoints as _checkpoints
from . import looks as _looks
from . import manifest as _manifest

try:
    import lzma
except ImportError:
    # A Python built without lzma reads no xz archive, as tarfile does then.
    lzma = None  # type: ignore[assignment]
_LZMA_ERRORS = () if lzma is None else (lzma.LZMAError,)

BUILTIN = 'builtin'
# The words that stand for something other than a team where a team's identifier goes, the random
# side of a requested match, as _playing/draw.py names it. A folder of teams by such a name, which
# only a hand could make, is left out of the registry, so no request mistakes one for the other.
RESERVED = frozenset({'random'})
# The side of a tournament's random entrant, which plays one player with actions at random. A
# requested match names a team to draw at random as `random`, so the entrant's side has a name of
# its own.
RANDOM_ACTIONS = 'random-actions'
# The start of a submission folder as the page uploads it, an index and the files' bytes.
FOLDER_MAGIC = b'CUJDIR1\n'
# The built-in AI's name until a person renames it, a label that the page shows in its language.
_BUILTIN_NAME = 'server.names.builtin'
_BUILTIN_ENTRY = {
    'id': BUILTIN,
    'name': _messages.text(_BUILTIN_NAME),
    'kind': 'builtin',
    'created': None,
}
# Kaggle: the file that an archive's agent lives in.
_MAIN = 'main.py'
# Submissions larger than this are refused, as Kaggle limits them too.
_MAXIMUM_BYTES = 100 * 1024 * 1024
# An archive may unpack to this much and this many files, so a small upload cannot fill the disk.
_MAXIMUM_UNPACKED = 500 * 1024 * 1024
_MAXIMUM_FILES = 10000
# The bytes of a tar archive after decompression that an upload reads at most, its members and
# their headers, and the bytes of the headers of one member, so a small compressed archive cannot
# make the server read or hold without end.
_MAXIMUM_TAR_BYTES = _MAXIMUM_UNPACKED + 64 * 1024 * 1024
_MAXIMUM_TAR_HEADER = 1024 * 1024
_LOGGER = logging.getLogger('cuju')
# One lock serializes every change of the registry, which the server makes from several threads.
_LOCK = threading.RLock()

# The sizes of a team for competition, the players a side of `5_vs_5` and of the full matches.
FORMATS = {'5v5': 5, '11v11': 11}
ROLES = ('GK', 'CB', 'LB', 'RB', 'DM', 'CM', 'LM', 'RM', 'AM', 'CF')
# The players that a submission of each format may control, as agents of its side. GRF: the
# goalkeeper of `5_vs_5` is not controllable, every player of the eleven a side scenarios is.
CONTROLLABLE = {'5v5': 4, '11v11': 11}
# A submission controls the active player alone by default, as on Kaggle.
DEFAULT_CONTROL = {
    'players': 1,
    'assignment': 'dynamic',
    'selection': [],
    'goalkeeper': 'scenario',
    'action_set': 'default',
}
# The folder of a submission that holds the registry's files of its team.
TEAM_FOLDER = 'cuju-team'
# The longest file of a team's entry that the registry reads, in bytes.
_MAXIMUM_ENTRY_BYTES = 1024 * 1024
# An imported file that fails its checks keeps its bytes under its name followed by this.
REJECTED = '.rejected'
# Windows: these names open a device in any folder, whatever their extension or case, the ports
# with a superscript digit as well.
_SUPERSCRIPTS = r'\N{SUPERSCRIPT ONE}\N{SUPERSCRIPT TWO}\N{SUPERSCRIPT THREE}'
_DEVICES = re.compile(
    rf'(con|prn|aux|nul|conin\$|conout\$|(com|lpt)[0-9{_SUPERSCRIPTS}])', re.IGNORECASE
)


def _player(index: int) -> dict[str, Any]:
    """Returns the default name and number of the player at `index` of a team."""
    return {'name': '', 'number': index + 1}


def default_roster(format: str = '11v11') -> list[dict[str, Any]]:
    """Returns the names and numbers of the players of a new team of `format`."""
    return [_player(index) for index in range(FORMATS[format])]


def _printable(text: str) -> str:
    """Returns `text` with every character that prints nothing of its own turned into a space.

    These are the control characters, the escapes of a terminal and line breaks among them, and
    the format characters, the marks that turn the direction of text around among them, which
    `str.isprintable` finds, and every space but the plain one.
    """
    return ''.join(character if character.isprintable() else ' ' for character in text)


def _check_name(name: str) -> str:
    if '@' in name:
        error = MessageError('server.teams.name_separator', name=repr(name))
        error.hint = '@ separates a team from its checkpoint'
        raise error
    identifier = _printable(name).strip()[:80].casefold()
    kind, separator, level = identifier.partition(':')
    reserved = identifier in (BUILTIN, 'random', RANDOM_ACTIONS)
    if kind == BUILTIN and separator:
        try:
            reserved = 0 <= float(level) <= 1
        except ValueError:
            reserved = level in ('', 'scenario')
    if reserved:
        error = MessageError('server.teams.name_reserved', name=repr(name))
        error.hint = 'choose a name that differs from the built-in team identifiers'
        raise error
    return name


def _field(name: str) -> dict[str, Any]:
    """Returns the name of a field of a team in a message, the label `server.teams.field.<name>`."""
    return _messages.label(f'server.teams.field.{name}')


def _choice(value: Any, choices: tuple[str, ...], name: Any) -> str:
    """Returns `value` if it is one of `choices`, or raises `ArgumentError`."""
    if value not in choices:
        raise MessageError(
            'server.teams.choice', name=name, choices=', '.join(choices), value=repr(value)
        )
    return value


def check_roster(value: Any, current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Returns the roster with the changes of `value`, a list of per-player dictionaries.

    Each dictionary changes the `name` and the `number` of the player at its index. A shirt
    number is the squad's alone, checked once every change is in, so two players may swap numbers.
    A number that an older file repeats stays until somebody changes it.

    Raises:
        ArgumentError: If `value` is no list of at most one entry for each player, or an entry
            names another key or holds a name or number outside its limits.
    """
    if not isinstance(value, list) or len(value) > len(current):
        raise MessageError('server.teams.players', count=len(current), value=repr(value))
    roster = [dict(player) for player in current]
    for index, changes in enumerate(value):
        if not isinstance(changes, dict):
            raise MessageError('server.teams.player', index=index, value=repr(changes))
        for key, entry in changes.items():
            if key == 'name':
                if not isinstance(entry, str) or len(entry) > _manifest.LONGEST_PLAYER_NAME:
                    raise MessageError('server.teams.player_name', name=repr(entry))
                roster[index][key] = _printable(entry)
            elif key == 'number':
                if isinstance(entry, bool) or not isinstance(entry, int) or not 0 <= entry <= 99:
                    raise MessageError('server.teams.shirt_number', number=repr(entry))
                roster[index][key] = entry
            else:
                raise MessageError('server.teams.player_entry', key=repr(key))
    for index, changes in enumerate(value):
        if 'number' in changes:
            number = roster[index]['number']
            other = next(
                (i for i, p in enumerate(roster) if i != index and p['number'] == number), None
            )
            if other is not None:
                raise MessageError('server.teams.number_taken', number=number, player=other + 1)
    return roster


def _resize(roster: list[dict[str, Any]], format: str) -> list[dict[str, Any]]:
    """Returns a roster cut or extended to the size of `format`."""
    size = FORMATS[format]
    kept = [dict(player) for player in roster[:size]]
    return kept + [_player(index) for index in range(len(kept), size)]


def _merged(partial: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Returns a read roster with the default of each index under the fields it lacks.

    A roster longer than the team keeps its players, and the registry cuts it to the team's size
    afterwards.
    """
    return [{**_player(index), **player} for index, player in enumerate(partial)]


def _empty_reading() -> dict[str, Any]:
    """Returns the reading of a team without a manifest."""
    return {
        'version': None,
        'team': {},
        'ground': None,
        'refused': {},
        'kept': {},
    }


def _complete(entry: dict[str, Any], reading: dict[str, Any] | None = None) -> dict[str, Any]:
    """Returns an entry with its format, roster, control and home scenario filled.

    The roster may be partial, as a reading of the manifest gives it, and the defaults fill the
    rest. `set_aside` lists the paths of the values that the reading refused.
    """
    reading = reading or _empty_reading()
    entry = dict(entry)
    if not isinstance(entry.get('format'), str) or entry['format'] not in FORMATS:
        entry['format'] = '11v11'
    saved = entry.get('roster') if isinstance(entry.get('roster'), list) else []
    entry['roster'] = _resize(_merged(saved), entry['format'])
    try:
        entry['control'] = check_control(entry.get('control'), entry['format'])
    except ArgumentError:
        entry['control'] = dict(DEFAULT_CONTROL)
    entry['ground'] = entry.get('ground') or reading['ground']
    entry['set_aside'] = sorted(reading['refused'])
    entry['scenario'], fell = _home_scenario(entry)
    if fell:
        # The stored scenario stays in the team's file under `kept.refused`, as every refused value.
        entry['set_aside'] = sorted({*entry['set_aside'], 'scenario'})
    return entry


def _meta_path(identifier: str) -> Path:
    """Returns the file of a team's entry, the built-in AI's included.

    Raises:
        ArgumentError: If the identifier could name a file outside the folder of teams.
    """
    if not isinstance(identifier, str) or not _home.valid_identifier(identifier):
        raise MessageError('server.teams.unknown', identifier=repr(identifier))
    return _home.folder('teams') / identifier / TEAM_FOLDER / 'team.json'


def read_entry(path: Path) -> dict[str, Any]:
    """Returns the stored entry of a team's file, or raises `ValueError` for any other content.

    The file is at most `_MAXIMUM_ENTRY_BYTES` long and holds a JSON object.
    """
    with path.open('rb') as stream:
        data = stream.read(_MAXIMUM_ENTRY_BYTES + 1)
    if len(data) > _MAXIMUM_ENTRY_BYTES:
        raise ValueError('the file of the team is too long')
    try:
        saved = json.loads(data)
    except RecursionError as error:
        raise ValueError('the file of the team nests too deeply') from error
    if not isinstance(saved, dict):
        raise ValueError('the file of the team holds no object')
    return saved


# The path that `set_aside` gives a manifest that holds no JSON object a version reads.
_WHOLE_FILE = 'team.json'


def _read_team(meta: Path) -> dict[str, Any] | None:
    """Returns the reading of a team's manifest, or `None` when the team has none.

    A file that holds no JSON object, or is too long, reads as a team with every default, and the
    reading names the whole file among its refused values. The next write keeps the file beside
    the new one under its name followed by `.rejected`.
    """
    if not (meta.is_file() or meta.is_symlink()):
        return None
    try:
        return _manifest.read(read_entry(meta), check_control)
    except (OSError, ValueError) as error:
        _LOGGER.warning('cuju render reads %s as a team with the defaults, %s', meta, error)
        reading = _empty_reading()
        reading['refused'] = {_WHOLE_FILE: None}
        reading['unreadable'] = True
        return reading


def _builtin() -> dict[str, Any]:
    """Returns the entry of the built-in AI, with the name and roster its file keeps, if any."""
    entry = dict(_BUILTIN_ENTRY)
    reading = _read_team(_meta_path(BUILTIN))
    if reading is not None:
        team = reading['team']
        entry.update({key: team[key] for key in ('roster', 'format') if key in team})
        if team.get('name'):
            entry['name'] = team['name']
    return _complete(_builtin_name(entry), reading)


def _builtin_name(entry: dict[str, Any]) -> dict[str, Any]:
    """Returns the built-in AI's entry with the message of its name while it keeps the default.

    The page sends the name back with every change of the style, so a stored name equal to the
    English default still counts as the default.
    """
    entry = {key: value for key, value in entry.items() if key not in ('name_id', 'name_values')}
    if entry.get('name') == _BUILTIN_ENTRY['name']:
        entry.update(_messages.named('name', _messages.label(_BUILTIN_NAME)))
    return entry


def _reason(error: BaseException) -> dict[str, Any] | str:
    """Returns an error as a value of another message, a label when it carries a message id."""
    message_id = getattr(error, 'message_id', None)
    if isinstance(message_id, str) and message_id in _messages.MESSAGES:
        return _messages.label(message_id, **getattr(error, 'values', {}))
    return str(error)


def shown_name(team: dict[str, Any]) -> dict[str, Any] | str:
    """Returns the name of a team as a message value, a label for a name that cuju made."""
    found = {'id': team.get('name_id'), 'text': team.get('name'), 'values': team.get('name_values')}
    return found if _messages.valid_label(found) else str(team.get('name'))


def teams() -> list[dict[str, Any]]:
    """Returns every team, the built-in AI first, then the submissions from newest to oldest.

    A team's identifier is the name of its folder, whatever its file says, and a folder whose
    name is no identifier, or a word of `RESERVED`, is left out alone. Each field of a team's file
    is read on its own, so a value that fails its check falls back to its default alone.
    """
    entries = []
    for folder in _home.folder('teams').iterdir():
        if folder.name in (BUILTIN, RANDOM_ACTIONS) or not _home.valid_identifier(folder.name):
            continue
        if folder.name in RESERVED:
            _LOGGER.warning(
                'cuju render leaves out the team folder %s, whose name stands for a random side, '
                'rename the folder to keep the team',
                folder.name,
            )
            continue
        meta = folder / TEAM_FOLDER / 'team.json'
        try:
            if not (folder.is_dir() and meta.is_file()):
                continue
            reading = _read_team(meta) or _empty_reading()
            # The reader turns the control and format characters of a name edited by hand, such
            # as escapes and the marks that turn text around, into spaces, so it prints in a
            # terminal too.
            saved = {**reading['team'], 'id': folder.name, 'kind': 'kaggle'}
            saved['created'] = saved.get('created') or ''
            saved['name'] = saved.get('name') or folder.name
            entry = _complete(saved, reading=reading)
        except Exception as error:
            _LOGGER.warning('cuju render leaves out the team %s, %s', folder.name, error)
            continue
        entries.append(entry)
    entries.sort(key=lambda entry: entry.get('created') or '', reverse=True)
    return [_builtin(), *entries]


# The levels of the built-in AI that GRF names, easy, medium and hard.
STANDARD_LEVELS = (0.05, 0.6, 0.95)
# How many of a team's last results its summary holds.
FORM_LENGTH = 5


def listing(scenario: str | None = None) -> list[dict[str, Any]]:
    """Returns every team as `teams` does, each with the `summary` that the grid of teams shows.

    The summary of a team holds the `scenario` of its figures, the one asked or else its home
    scenario, its `rating` there, `{rating, low, high, matches, checkpoint}` of the checkpoint
    that plays or else of its newest one, or `None` before its first counted result, its `form`,
    the results of its last five counted matches there, the oldest first, each `win`, `draw` or
    `loss`, and `check`, the state of the verdict it keeps under Kaggle open, `accepted`,
    `refused` or `not_checked`. The built-in AI holds `levels` in place of `check`, as
    `builtin_levels` gives them, and its rating is that of the anchor.

    The listing reads the ratings store and the kept verdicts alone and never runs a check, and
    each scenario's table is made once for every team that shows it. A part that cannot be read
    leaves that part empty, so the list of teams always answers.
    """
    from .._formats import verdicts as _verdicts

    entries = teams()
    tables: dict[str, Any] = {}
    choice = None
    for entry in entries:
        shown = scenario or entry.get('scenario') or size_default_scenario(entry.get('format'))
        try:
            if choice is None and entry['kind'] != 'builtin':
                choice = _verdicts.choice_of({})
            entry['summary'] = _summary(entry, shown, tables, choice)
        except Exception as error:  # A store that fails leaves the team without figures.
            _LOGGER.warning('cuju render shows no figures for the team %s, %s', entry['id'], error)
            entry['summary'] = {'scenario': shown, 'rating': None, 'form': [], 'check': None}
    return entries


def _summary(
    entry: dict[str, Any], scenario: str, tables: dict[str, Any], choice: Any
) -> dict[str, Any]:
    """Returns the summary of one team for `listing`, with the tables already made."""
    from .._evaluate import ratings as _ratings
    from .._store import ratings as _store_ratings

    if scenario not in tables:
        found = _ratings.table(scenario)
        sources = set(_ratings.settings()['sources'])
        counted = [row for row in _store_ratings.results(scenario) if row['source'] in sources]
        tables[scenario] = (found, counted)
    found, counted = tables[scenario]
    builtin = entry['kind'] == 'builtin'
    if builtin:
        anchor = found['anchor']
        return {
            'scenario': scenario,
            'rating': {
                'rating': anchor.get('rating'),
                'low': None,
                'high': None,
                'matches': anchor.get('matches', 0),
                'checkpoint': None,
            },
            'form': [],
            'levels': builtin_levels(scenario, found),
        }
    rows = [row for row in found['models'] if row['key'] == entry['id']]
    row = next((item for item in rows if item.get('current')), None)
    row = row or next((item for item in rows if item.get('latest')), None)
    row = row or (rows[0] if rows else None)
    form = []
    for result in counted:
        sides = (result['home'].get('key'), result['away'].get('key'))
        if entry['id'] not in sides or sides[0] == sides[1]:
            continue
        own, other = result['score'] if sides[0] == entry['id'] else result['score'][::-1]
        form.append('win' if own > other else 'loss' if own < other else 'draw')
    return {
        'scenario': scenario,
        'rating': None
        if row is None
        else {key: row.get(key) for key in ('rating', 'low', 'high', 'matches', 'checkpoint')},
        'form': form[-FORM_LENGTH:],
        'check': _kaggle_open_state(entry, choice),
    }


def _kaggle_open_state(entry: dict[str, Any], choice: Any) -> str:
    """Returns the state of the verdict a team keeps under Kaggle open, read and never run."""
    from .._formats import verdicts as _verdicts

    point = _checkpoints.current_id(entry['id'])
    if point is None:
        return 'not_checked'
    subject = _verdicts.Subject(
        name=str(entry.get('name') or entry['id']),
        size=entry['format'],
        control=check_control(entry.get('control'), entry['format']),
        checkpoint=point,
        folder=_home.folder('teams') / entry['id'],
        team=entry['id'],
    )
    key = _verdicts.key_of(subject, choice, None, False)
    outcome = _verdicts.lookup(entry['id'], key, subject)
    if outcome is None or outcome.verdict is None:
        return 'not_checked'
    return 'accepted' if outcome.verdict.accepted else 'refused'


def builtin_levels(
    scenario: str | None = None, found: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Returns the levels of the built-in AI with their ratings in a scenario's table.

    Each level holds its `key`, its `difficulty`, its `rating`, `low` and `high`, or `None` before
    its first result, its `matches` and whether it is the `anchor` of the table. The levels are
    GRF's easy, medium and hard, every level the table holds and the anchor. `found` is the table
    when the caller has made it already.
    """
    from .._evaluate import ratings as _ratings

    found = found or _ratings.table(scenario or size_default_scenario('11v11'))
    rows = {row['key']: row for row in found['models'] if row['kind'] == 'builtin'}
    anchor = found['anchor']
    difficulties = {
        *STANDARD_LEVELS,
        *(row['difficulty'] for row in rows.values() if row.get('difficulty') is not None),
    }
    difficulties.add(float(anchor['difficulty']))
    levels = []
    for difficulty in sorted(difficulties):
        key = _ratings.builtin_key(difficulty)
        row = rows.get(key)
        levels.append(
            {
                'key': key,
                'difficulty': difficulty,
                'rating': None if row is None else row['rating'],
                'low': None if row is None else row['low'],
                'high': None if row is None else row['high'],
                'matches': 0 if row is None else row['matches'],
                'anchor': key == anchor['key'],
            }
        )
    return levels


def check_control(control: Any, format: str) -> dict[str, Any]:
    """Returns normalized team control and migrates earlier settings."""
    control = dict(DEFAULT_CONTROL) if control is None else control
    allowed = {'players', 'goalkeeper', 'assignment', 'selection', 'action_set'}
    if not isinstance(control, dict) or set(control) - allowed:
        raise MessageError('server.teams.control.fields')
    players = control.get('players', 1)
    most = FORMATS[format]
    if isinstance(players, bool) or not isinstance(players, int) or not 1 <= players <= most:
        raise MessageError('server.teams.control.players', most=most)
    legacy = 'assignment' not in control
    keeper = _choice(
        control.get('goalkeeper', 'scenario'),
        ('agent', 'builtin', 'scenario'),
        _field('goalkeeper'),
    )
    assignment = control.get('assignment', 'fixed' if keeper == 'builtin' else 'dynamic')
    _choice(assignment, ('dynamic', 'fixed'), _field('assignment'))
    if legacy and keeper == 'builtin' and (format != '11v11' or players != 10):
        raise MessageError('server.teams.control.legacy_goalkeeper')
    if legacy and keeper == 'agent':
        keeper = 'scenario'
    if assignment == 'dynamic' and keeper == 'builtin':
        raise MessageError('server.teams.control.goalkeeper_fixed')
    selection = control.get('selection', [])
    if legacy and assignment == 'fixed':
        selection = list(range(1, players + 1))
    if not isinstance(selection, list):
        raise MessageError('server.teams.control.selection_list')
    if assignment == 'fixed' and len(selection) != players:
        raise MessageError('server.teams.control.selection_count')
    if assignment == 'dynamic' and selection:
        raise MessageError('server.teams.control.selection_empty')
    if selection:
        indices = all(type(item) is int and 0 <= item <= 10 for item in selection)
        roles = all(isinstance(item, str) and item in ROLES for item in selection)
        if not (indices or roles) or (indices and len(set(selection)) != len(selection)):
            raise MessageError('server.teams.control.selection_distinct')
    action_set = _choice(
        control.get('action_set', 'default'), ('default', 'v2'), _field('action_set')
    )
    return dict(
        players=players,
        assignment=assignment,
        selection=list(selection),
        goalkeeper=keeper,
        action_set=action_set,
    )


def scenario_path(scenario: str) -> Path:
    """Returns the source of a shipped or renderer scenario."""
    local = Path(__file__).parents[1] / 'scenarios' / f'{scenario}.py'
    if local.is_file():
        return local
    import cuju

    return Path(cuju.__file__).parent / 'data' / 'scenarios' / f'{scenario}.py'


def renderer_scenarios() -> list[str]:
    """Returns the additional training scenarios."""
    return sorted(path.stem for path in (Path(__file__).parents[1] / 'scenarios').glob('*.py'))


def scenario_players(scenario: str, side: int) -> list[Any]:
    """Returns the initial formation of a side."""
    from cuju.scenarios import Builder, load_scenario

    builder = Builder(0)
    load_scenario(scenario_path(scenario))(builder)
    config = builder.config()
    return config.left_team if side == 0 else config.right_team


@functools.cache
def scenario_sides(scenario: str) -> tuple[tuple[tuple[int, bool], ...], ...]:
    """Returns the role of each player of both sides of a scenario and whether it is controllable.

    The page checks with them which teams can play a side, as `check_scenario` checks it, and a
    scenario loads once for each process, as loading one takes the better part of a second.
    """
    from cuju.scenarios import Builder, load_scenario

    builder = Builder(0)
    load_scenario(scenario_path(scenario))(builder)
    config = builder.config()
    return tuple(
        tuple((int(player.role), bool(player.controllable)) for player in team)
        for team in (config.left_team, config.right_team)
    )


def agent_count(team: dict[str, Any], scenario: str | None = None, side: int = 0) -> int:
    """Returns the engine agents required by a side."""
    if team['kind'] == 'builtin':
        return 0
    if team['kind'] == 'random':
        return 1
    control = check_control(team.get('control'), team['format'])
    if control['assignment'] == 'dynamic':
        return control['players']
    if scenario is None:
        return CONTROLLABLE[team['format']]
    return sum(player.controllable for player in scenario_players(scenario, side))


def selected_players(control: dict[str, Any], formation: list[Any]) -> list[int]:
    """Resolves selections in agent order, with repeated roles in lineup order."""
    available = [index for index, player in enumerate(formation) if player.controllable]
    if control['players'] > len(available):
        raise MessageError('server.teams.control.too_few', count=len(available))
    keeper = [index for index, player in enumerate(formation) if player.role == 0]
    if control['assignment'] == 'dynamic':
        if control['goalkeeper'] == 'agent' and not any(i in available for i in keeper):
            raise MessageError('server.teams.control.goalkeeper_uncontrollable')
        return available
    selected = []
    for item in control['selection']:
        candidates = (
            [item]
            if type(item) is int
            else [index for index in available if formation[index].role == ROLES.index(item)]
        )
        found = next((i for i in candidates if i in available and i not in selected), None)
        if found is None:
            raise MessageError('server.teams.control.select_player', player=item)
        selected.append(found)
    if control['goalkeeper'] == 'builtin' and not any(i in available for i in keeper):
        raise MessageError('server.teams.control.goalkeeper_builtin')
    owns_keeper = any(i in keeper for i in selected)
    if control['goalkeeper'] == 'agent' and not owns_keeper:
        raise MessageError('server.teams.control.goalkeeper_selected')
    if control['goalkeeper'] in ('builtin', 'scenario') and owns_keeper:
        raise MessageError('server.teams.control.goalkeeper_outside')
    if control['goalkeeper'] == 'scenario' and any(i in available for i in keeper):
        raise MessageError('server.teams.control.goalkeeper_scenario')
    return selected


def update(
    identifier: str,
    name: str | None = None,
    roster: list[dict[str, Any]] | None = None,
    format: str | None = None,
    control: dict[str, Any] | None = None,
    scenario: str | None = None,
) -> dict[str, Any]:
    """Renames a team, changes its roster, format, control or home scenario, and returns its entry.

    Args:
        identifier: The team, the built-in AI included.
        name: The new name, or `None` to keep it.
        roster: The changes of the players' names and numbers, a list of dictionaries by index as
            `check_roster` takes them, or `None`.
        format: `5v5` or `11v11`, the size of the team for competition, which sets the length of
            its roster, or `None` to keep it.
        control: The players that the submission controls, as `check_control` takes them, or
            `None` to keep them, or the default where the new format refuses them. The control
            is that of the team's current checkpoint.
        scenario: The home scenario, a scenario that the team's size admits and its control fits,
            or `None` to keep it. The size's default stands for no choice.

    Raises:
        ArgumentError: If the team is unknown or a change is invalid.
    """
    current = entry(identifier)
    if name is not None:
        name = _printable(_check_name(str(name))).strip()[:80]
        if not name:
            raise MessageError('server.teams.name')
        current['name'] = name
        if identifier == BUILTIN:
            current = _builtin_name(current)
        else:
            current.pop('name_id', None)
            current.pop('name_values', None)
    if format is not None:
        current['format'] = _choice(format, tuple(FORMATS), _field('format'))
        current['roster'] = _resize(current['roster'], current['format'])
    if control is not None:
        current['control'] = check_control(control, current['format'])
    else:
        try:
            current['control'] = check_control(current.get('control'), current['format'])
        except ArgumentError:
            current['control'] = dict(DEFAULT_CONTROL)
    if roster is not None:
        current['roster'] = check_roster(roster, current['roster'])
    if scenario is not None:
        check_home(current, scenario)
        current['scenario'] = scenario
        current['set_aside'] = [item for item in current['set_aside'] if item != 'scenario']
    elif current['scenario'] != SIZE_DEFAULTS[current['format']]:
        # A home scenario that the new format or control no longer fits falls back to the default
        # of the size, and the file keeps the old one among its refused values.
        try:
            check_home(current, current['scenario'])
        except ArgumentError:
            current['scenario'] = SIZE_DEFAULTS[current['format']]
            current['set_aside'] = sorted({*current['set_aside'], 'scenario'})
    _write(identifier, current)
    if identifier != BUILTIN:
        _checkpoints.mirror_control(identifier, current['control'])
    found = entry(identifier)
    if 'scenario' in current['set_aside'] and 'scenario' not in found['set_aside']:
        # The change itself sent the home scenario back to the default of the size, which the
        # entry says once, as a read of a file that held an unplayable one does.
        found['set_aside'] = sorted({*found['set_aside'], 'scenario'})
    return found


def drivers(current: dict[str, Any]) -> list[str]:
    """Returns who drives each player of a team, `agent` or `builtin`, from its control.

    The control is that of the checkpoint that plays, and the formation that of the team's home
    scenario. A team without a formation to read, and one whose control the formation refuses,
    shows its agent on every player, which is what the submission asks for.
    """
    size = FORMATS[current['format']]
    if current['kind'] != 'kaggle':
        return ['builtin'] * size
    control = check_control(current.get('control'), current['format'])
    try:
        formation = scenario_players(current['scenario'], 0)
        picked = set(selected_players(control, formation))
    except ArgumentError:
        return ['agent'] * size
    return ['agent' if index in picked else 'builtin' for index in range(size)]


def _replace(path: Path, data: bytes) -> None:
    """Writes `path` through a temporary file of its own, so no reader sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix='.part')
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _document(
    identifier: str, current: dict[str, Any], reading: dict[str, Any] | None
) -> dict[str, Any]:
    """Returns the manifest of a team's entry, carrying on what the file on disk keeps.

    The block `kept` of the file on disk and its ground come along, with the values that its
    reading refused and its unread keys, so a write loses nothing of the file before. What 0.1.0
    wrote and this version no longer reads, its look among it, stays in `kept` as it was.
    """
    reading = reading or _empty_reading()
    kept = json.loads(json.dumps(reading['kept']))
    stored = reading['team'].get('scenario')
    if 'scenario' in current.get('set_aside', ()) and stored is not None:
        saved = kept.setdefault(_manifest.REFUSED, {})
        if isinstance(saved, dict):
            saved['scenario'] = stored
    ground = current.get('ground') or reading['ground']
    if ground is None and identifier not in (BUILTIN, RANDOM_ACTIONS):
        ground = _first_checkpoint_digest(identifier)
    return _manifest.document(current, kept, ground)


def _manifest_bytes(document: dict[str, Any]) -> bytes:
    """Returns a manifest as the file holds it, indented, so a person can read and edit it."""
    return (json.dumps(document, indent=2) + '\n').encode()


def _write(identifier: str, current: dict[str, Any]) -> None:
    """Writes a team's manifest, the second version, from its entry.

    A file on disk that holds no JSON object is kept beside the new one under its name followed
    by `.rejected`, so no team file is ever lost.
    """
    meta = _meta_path(identifier)
    with _LOCK:
        reading = _read_team(meta)
        if reading is not None and reading.get('unreadable'):
            set_aside(meta, 'it holds no team file that cuju render reads')
        document = _document(identifier, current, reading)
        _replace(meta, _manifest_bytes(document))


def _first_checkpoint_digest(identifier: str) -> str | None:
    """Returns the digest of a team's submission as it stands, which names its checkpoint.

    The digest is the one of `runs.team_checkpoint`, over the team's folder without `cuju-team`
    and without the folders of tools, so the look of a team never changes it.
    """
    folder = _home.folder('teams') / identifier
    if not _home.valid_identifier(identifier) or not folder.is_dir():
        return None
    try:
        return _runs.files_digest(folder, (TEAM_FOLDER,))
    except (OSError, ArgumentError):
        return None


def first_checkpoint(identifier: str) -> str | None:
    """Returns the digest of a team's first checkpoint, which seeds the look of its home ground.

    The manifest keeps the digest under `ground`. A team that has none yet, such as a team of
    0.1.0 that was never saved again, takes the digest of its submission as it stands, which is
    then its first checkpoint, and the manifest keeps it from then on. Later checkpoints never
    change it, so a team's home ground stays put as it trains. The built-in AI has no files and
    returns `None`.

    Raises:
        ArgumentError: If the team is unknown.
    """
    current = entry(identifier)
    if current['kind'] != 'kaggle':
        return None
    if current.get('ground'):
        return current['ground']
    with _LOCK:
        _write(identifier, current)
    return entry(identifier).get('ground')


def scenario_size(scenario: str) -> int | None:
    """Returns the players a side of a full-match scenario, 5 or 11, or `None` for the others."""
    for size in (5, 11):
        if scenario.startswith(f'{size}_vs_{size}'):
            return size
    return None


def check_ability(ability: Any) -> tuple[float | None, float | None]:
    """Returns a pair of abilities, each a number from 0 to 1 or `None` for the scenario's.

    Raises:
        ArgumentError: If `ability` is no pair, or a value is no number from 0 to 1.
    """
    if not isinstance(ability, (list, tuple)) or len(ability) != 2:
        raise MessageError('server.teams.ability_pair', ability=repr(ability))
    pair = []
    for value in ability:
        if value is None:
            pair.append(None)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise MessageError('server.teams.ability', value=repr(value))
        pair.append(float(value))
    return (pair[0], pair[1])


def _check_size(team: dict[str, Any], scenario: str) -> None:
    """Checks that a team plays a full-match scenario of its own size, 5v5 or 11v11.

    Raises:
        ArgumentError: If the scenario is a full match of the other size.
    """
    size = scenario_size(scenario.removeprefix('benchmark_full_game_'))
    if size is not None and size != FORMATS[team['format']]:
        raise MessageError(
            'server.teams.scenario_format',
            format=team['format'],
            team=shown_name(team),
            scenario=_messages.scenario(scenario),
        )


def check_scenario(teams_: list[dict[str, Any]], scenario: str) -> None:
    """Checks the format, count, selected players and goalkeeper on each scenario side."""
    for side, team in enumerate(teams_):
        if team['kind'] in ('builtin', 'random'):
            continue
        _check_size(team, scenario)
        control = check_control(team.get('control'), team['format'])
        try:
            selected_players(control, scenario_players(scenario, side))
        except ArgumentError as error:
            raise MessageError(
                'server.teams.cannot_play',
                team=shown_name(team),
                scenario=_messages.scenario(scenario),
                reason=_reason(error),
            ) from error


SIZE_DEFAULTS = _manifest.SIZE_DEFAULTS


def size_default_scenario(format: str) -> str:
    """Returns the scenario that a team of a size plays at home when it names none."""
    return SIZE_DEFAULTS[format]


class _Slot(NamedTuple):
    """A player of a scenario's formation as `selected_players` reads it."""

    role: int
    controllable: bool


def _scenarios() -> list[str]:
    from cuju import bundled_scenarios

    return sorted([*bundled_scenarios(), *renderer_scenarios()])


def check_home(team: dict[str, Any], scenario: str) -> None:
    """Checks that a team can play a scenario on both of its sides, as its home scenario.

    A team can play the scenarios that its size admits and whose players its control fits. This
    is computed from the team and the scenario each time and never stored as a list.

    Raises:
        ArgumentError: If the scenario is no scenario of cuju or the renderer, or a full game of
            the other size, or the team's control does not fit one of its sides.
    """
    if not isinstance(scenario, str) or scenario not in _scenarios():
        raise MessageError('server.teams.scenario', scenario=repr(scenario))
    _check_size(team, scenario)
    control = check_control(team.get('control'), team['format'])
    for side in (0, 1):
        formation = [
            _Slot(role, controllable) for role, controllable in scenario_sides(scenario)[side]
        ]
        try:
            selected_players(control, formation)
        except ArgumentError as error:
            raise MessageError(
                'server.teams.cannot_play',
                team=shown_name(team),
                scenario=_messages.scenario(scenario),
                reason=_reason(error),
            ) from error


def playable_scenarios(team: dict[str, Any]) -> list[str]:
    """Returns the scenarios that a team can play at home, computed from its size and control.

    Every shipped scenario is loaded once for the process, so the first call takes some seconds.
    """
    found = []
    for scenario in _scenarios():
        try:
            check_home(team, scenario)
        except Exception:
            continue
        found.append(scenario)
    return found


def _home_scenario(entry: dict[str, Any]) -> tuple[str, bool]:
    """Returns the home scenario of an entry and whether a stored one fell back.

    The home scenario is the stored one when the team can play it, else the default of its size.
    """
    default = SIZE_DEFAULTS[entry['format']]
    stored = entry.get('scenario')
    if stored is None or stored == default or entry.get('kind') != 'kaggle':
        return default, False
    try:
        check_home(entry, stored)
    except Exception as error:
        _LOGGER.warning(
            'cuju render gives the team %s the home scenario %s, since %s',
            entry['id'],
            default,
            error,
        )
        return default, True
    return stored, False


def task_fit(team: dict[str, Any], scenario: str, side: int = 0) -> dict[str, Any]:
    """Returns how a team plays its side of the scenario of a task, with any extra players out.

    The scenario of a task may hold fewer controllable players than a team controls, as an academy
    drill does for a team trained for full games. The team's extra players are then out, as a
    selected player off the pitch after a red card is, and the team plays the players that the
    scenario has. With dynamic assignment the team plays as many agents as the side has
    controllable players. With fixed assignment the players of its selection that the side holds
    play, each found as before, and the others are out, while the built-in AI plays the side's
    players outside the selection, its goalkeeper among them. The policy is still called in its
    own form, one observation for each player it controls, and a player out sees the observation
    of the team's first player that plays, whose answer for the player out counts for nothing.

    Args:
        team: The registry entry, or the entry of a policy with its control.
        scenario: The scenario of the task.
        side: The side the team plays, 0 for the left one.

    Returns:
        `agents`, the engine agents of the side, `players`, the players the team controls,
        `playing`, those that play, `slots`, the place among the players that play of each player
        the team controls in the order of its submission, `None` for a player out, or `None`
        itself when every player plays, and `selected`, the formation indices of the players of a
        fixed assignment that play, or `None` for dynamic assignment.

    Raises:
        ArgumentError: If the scenario is a full match of the other size, or the team cannot play
            the side, such as a fixed selection of which the side holds no player.
    """
    if team['kind'] == 'builtin':
        return {'agents': 0, 'players': 0, 'playing': 0, 'slots': None, 'selected': None}
    _check_size(team, scenario)
    control = check_control(team.get('control'), team['format'])
    formation = scenario_players(scenario, side)
    available = [index for index, player in enumerate(formation) if player.controllable]
    count = control['players']

    def refused(error: BaseException) -> MessageError:
        return MessageError(
            'server.teams.cannot_play',
            team=shown_name(team),
            scenario=_messages.scenario(scenario),
            reason=_reason(error),
        )

    if control['assignment'] == 'dynamic':
        if count <= len(available):
            try:
                selected_players(control, formation)
            except ArgumentError as error:
                raise refused(error) from error
            return {
                'agents': count,
                'players': count,
                'playing': count,
                'slots': None,
                'selected': None,
            }
        if not available:
            raise refused(MessageError('server.teams.control.too_few', count=0))
        playing = len(available)
        slots = [*range(playing), *[None] * (count - playing)]
        return {
            'agents': playing,
            'players': count,
            'playing': playing,
            'slots': slots,
            'selected': None,
        }
    chosen: list[int] = []
    slots: list[int | None] = []
    for item in control['selection']:
        candidates = (
            [item]
            if type(item) is int
            else [index for index in available if formation[index].role == ROLES.index(item)]
        )
        found = next((i for i in candidates if i in available and i not in chosen), None)
        slots.append(None if found is None else len(chosen))
        if found is not None:
            chosen.append(found)
    if None not in slots:
        try:
            selected = selected_players(control, formation)
        except ArgumentError as error:
            raise refused(error) from error
        return {
            'agents': len(available),
            'players': count,
            'playing': count,
            'slots': None,
            'selected': selected,
        }
    if not chosen:
        raise refused(MessageError('server.teams.control.none_present'))
    keeper = [index for index, player in enumerate(formation) if player.role == 0]
    if control['goalkeeper'] in ('builtin', 'scenario') and any(i in keeper for i in chosen):
        raise refused(MessageError('server.teams.control.goalkeeper_outside'))
    return {
        'agents': len(available),
        'players': count,
        'playing': len(chosen),
        'slots': slots,
        'selected': chosen,
    }


def game_agents(teams_: list[dict[str, Any]], scenario: str, humans: Any) -> list[int]:
    """Returns the agents of the team on each side of a game that people play, after checks.

    A side without a person belongs to its team, the built-in AI or a submission, as in a requested
    match. A side with a person takes its team as the person's teammates, a submission with dynamic
    assignment whose players join the person's one, so the side needs one controllable player more
    than the team asks for.

    Raises:
        ArgumentError: If a team cannot play its side of the scenario, or a team beside a person
            uses fixed assignment or asks for more players than the side has left.
    """
    check_scenario(teams_, scenario)
    agents = []
    for side, team in enumerate(teams_):
        count = agent_count(team, scenario, side)
        if humans[side] and count:
            control = check_control(team.get('control'), team['format'])
            if control['assignment'] != 'dynamic':
                raise MessageError('server.teams.beside_dynamic', team=shown_name(team))
            available = sum(player.controllable for player in scenario_players(scenario, side))
            if count + 1 > available:
                raise MessageError(
                    'server.teams.beside_count',
                    team=shown_name(team),
                    scenario=_messages.scenario(scenario),
                    count=available,
                )
        agents.append(count)
    return agents


def match_teams(left: str, right: str) -> list[dict[str, Any]]:
    """Returns the two teams of a match as a recording's header holds them.

    Each team keeps its identity, and the producers of `looks.TEAM` lay its look over it.
    """
    return [header_team(left), header_team(right)]


def header_team(identifier: str) -> dict[str, Any]:
    """Returns a team's identifier, name, size, control and roster for a recording."""
    team = side_entry(identifier)
    # A team at an earlier checkpoint names it in the header, and the current one never does, so
    # recordings of today's teams keep their header.
    point = {'checkpoint': team['checkpoint']} if 'entrant' in team else {}
    return {
        'id': team['id'],
        **point,
        **_messages.named('name', shown_name(team)),
        'format': team['format'],
        'control': team.get('control', dict(DEFAULT_CONTROL)),
        'roster': [dict(player) for player in team['roster']],
    }


def folder_bundle(files: list[tuple[str, bytes]]) -> bytes:
    """Returns the bytes of a submission folder as the page uploads it.

    Args:
        files: The path of each file relative to the folder, with `/` between parts, and its bytes.
    """
    index = json.dumps([{'path': path, 'size': len(data)} for path, data in files]).encode()
    return (
        FOLDER_MAGIC + struct.pack('<I', len(index)) + index + b''.join(data for _, data in files)
    )


def _unbundle(data: bytes, target: Path) -> None:
    """Writes the files of a folder bundle into `target`, without the folder's own name.

    A browser names each file of a chosen folder with the folder in front, such as
    `submission/main.py`, so a single top folder above `main.py` is dropped.
    """
    if len(data) < len(FOLDER_MAGIC) + 4:
        raise MessageError('server.teams.folder.short')
    (length,) = struct.unpack_from('<I', data, len(FOLDER_MAGIC))
    start = len(FOLDER_MAGIC) + 4
    try:
        index = json.loads(data[start : start + length])
    except ValueError as error:
        raise MessageError('server.teams.folder.index', error=error) from error
    if not isinstance(index, list) or len(index) > _MAXIMUM_FILES:
        raise MessageError('server.teams.folder.index_list')
    paths = [_check_member(str(entry['path'])) for entry in index]
    tops = {path.parts[0] for path in paths if len(path.parts) > 1}
    if (
        PurePosixPath(_MAIN) not in paths
        and len(tops) == 1
        and all(len(p.parts) > 1 for p in paths)
    ):
        paths = [PurePosixPath(*path.parts[1:]) for path in paths]
    position = start + length
    for path, entry in zip(paths, index, strict=True):
        size = int(entry['size'])
        if size < 0 or position + size > len(data):
            raise MessageError('server.teams.folder.files')
        destination = _inside(target, path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data[position : position + size])
        position += size


def _extract(data: bytes, filename: str, target: Path, main: bool = True) -> None:
    """Writes a submission into `target`, a folder bundle, a Python file or an archive.

    `main` asks for the `main.py` of Kaggle open at the top, the rule of a team that no format
    judges. A team added under a format leaves its entry to the format's pre-check.
    """
    lower = filename.lower()
    if data.startswith(FOLDER_MAGIC):
        _unbundle(data, target)
    elif lower.endswith('.py'):
        # A file of its own is Kaggle open's main.py, and keeps its name under a format.
        own = _check_member(Path(filename).name) if not main else PurePosixPath(_MAIN)
        _inside(target, own).write_bytes(data)
    elif lower.endswith(('.tar.gz', '.tgz', '.tar')):
        _untar(data, target)
    elif lower.endswith('.zip'):
        # zipfile reads every entry of the central directory as it opens, so their count comes
        # first, from the bytes alone.
        _check_unpacked(_zip_entries(data), 0)
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, OSError, ValueError, EOFError) as error:
            raise MessageError('server.teams.archive.zip', error=error) from error
        with archive:
            members = archive.infolist()
            _check_unpacked(len(members), sum(member.file_size for member in members))
            for member in members:
                _inside(target, _check_member(member.filename))
            archive.extractall(target)
    else:
        raise MessageError('server.teams.submission.kind', filename=repr(filename))
    if main and not (target / _MAIN).is_file():
        raise MessageError('server.teams.submission.main_top', main=_MAIN)


class _Bounded:
    """The bytes of a tar archive after decompression, read within limits.

    No read or move goes past `_MAXIMUM_TAR_BYTES` from the start. While `scanning` holds, the
    reads after each move, which are the headers of one member, take at most
    `_MAXIMUM_TAR_HEADER` bytes, so a long name, pax records or a sparse map never grows without
    end. tarfile moves past the bytes of each member to its next header, so the bytes of the
    members are never read while it scans.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._start = 0
        self.scanning = True

    def tell(self) -> int:
        return self._inner.tell()

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence != 0 or offset > _MAXIMUM_TAR_BYTES:
            raise MessageError('server.teams.archive.limits')
        self._start = offset
        return self._inner.seek(offset)

    def read(self, size: int = -1) -> bytes:
        end = _MAXIMUM_TAR_BYTES
        if self.scanning:
            end = min(end, self._start + _MAXIMUM_TAR_HEADER)
        if size is None or size < 0 or self._inner.tell() + size > end:
            raise MessageError('server.teams.archive.limits')
        return self._inner.read(size)


def _decompressed(data: bytes) -> Any:
    """Returns a file of a tar archive's bytes, decompressed as they are read when compressed.

    gzip, bzip2 and xz are told apart by their first bytes, as tarfile tells them apart.
    """
    stream = io.BytesIO(data)
    if data.startswith(b'\x1f\x8b'):
        return gzip.GzipFile(fileobj=stream, mode='rb')
    if data.startswith(b'BZh'):
        return bz2.BZ2File(stream)
    if data.startswith(b'\xfd7zXZ\x00') and lzma is not None:
        return lzma.LZMAFile(stream)
    return stream


def _untar(data: bytes, target: Path) -> None:
    """Writes the members of a tar archive into `target`, after checking every one of them.

    The members are read one at a time and counted against the limits of an archive as they come,
    so a small compressed archive of a great many members, or of members that claim a great size,
    is refused at the first member past a limit, before any member is written.
    """
    stream = _Bounded(_decompressed(data))
    unreadable = (tarfile.TarError, EOFError, OSError, zlib.error, RecursionError, *_LZMA_ERRORS)
    try:
        archive = tarfile.open(fileobj=stream, mode='r:')
    except unreadable as error:
        raise MessageError('server.teams.archive.tar', error=error) from error
    with archive:
        count = size = 0
        try:
            while (member := archive.next()) is not None:
                count += 1
                size += member.size
                _check_unpacked(count, size)
                _inside(target, _check_member(member.name))
                if not (member.isfile() or member.isdir()):
                    raise MessageError('server.teams.archive.plain', member=member.name)
        except unreadable as error:
            raise MessageError('server.teams.archive.tar', error=error) from error
        stream.scanning = False
        try:
            # Python 3.12, 3.11.4 and 3.10.12 brought the data filter. Older versions write each
            # member without its mode, owner or times, and the checks above cover the rest.
            if hasattr(tarfile, 'data_filter'):
                archive.extractall(target, filter='data')
            else:
                for member in archive.getmembers():
                    archive.extract(member, target, set_attrs=False)
        except (tarfile.TarError, EOFError, zlib.error, *_LZMA_ERRORS) as error:
            raise MessageError('server.teams.archive.tar', error=error) from error


def _zip_entries(data: bytes) -> int:
    """Returns at least as many entries as the central directory of a zip file can hold.

    Every entry that zipfile reads from the central directory starts with the signature of an
    entry, and no two overlap, so the signatures of the whole file bound the entries, whichever end
    record and zip64 record a version of zipfile trusts.
    """
    return data.count(b'PK\x01\x02')


def add_folder(
    folder: str | Path,
    name: str | None = None,
    format: str = '11v11',
    control: dict[str, Any] | None = None,
    submission_format: Any = None,
    *,
    scenario: str | None = None,
) -> dict[str, Any]:
    """Stores a submission folder or archive as a new team and returns its entry.

    Args:
        folder: A submission folder, submission file or exported archive.
        name: The name of the team, by default the folder's name.
        format: `5v5` or `11v11`, the size of the team for competition.
        control: The players that the submission controls, as `check_control` takes them, or
            `None` for the active player alone, or for the setting of an exported folder.
        submission_format: The submission format the folder is added under, or `None`. Without
            one the folder needs Kaggle open's `main.py` at its top. Under a format the format's
            pre-check judges its entry, such as an `agent.py` or a `policy.toml`.
        scenario: The home scenario of the new team.
    """
    return _add_folder(folder, name, format, control, submission_format, scenario=scenario)


def _add_folder(
    folder: str | Path,
    name: str | None,
    format: str,
    control: dict[str, Any] | None,
    submission_format: Any,
    *,
    scenario: str | None = None,
    size_override: str | None = None,
) -> dict[str, Any]:
    folder = Path(folder)
    main = submission_format is None
    if folder.is_file():
        return add(
            name or '',
            folder.name,
            folder.read_bytes(),
            format,
            control,
            submission_format=submission_format,
            scenario=scenario,
            size_override=size_override,
        )
    if not folder.is_dir():
        raise ArgumentError(
            f'expected a submission folder or archive, found nothing at {str(folder)!r}'
        )
    if main and not (folder / _MAIN).is_file():
        raise MessageError(
            'server.teams.submission.main_folder', main=_MAIN, folder=repr(str(folder))
        )
    return add(
        name or folder.resolve().name,
        folder.name,
        folder_bundle(_folder_files(folder)),
        format,
        control,
        submission_format=submission_format,
        scenario=scenario,
        size_override=size_override,
    )


def _folder_files(folder: Path) -> list[tuple[str, bytes]]:
    """Returns the files of a submission folder as paths and bytes, in the order of the paths.

    A link inside the folder could point anywhere, such as at a key of the user's, and its target
    would then travel with every export of the team, so links are left out and logged.
    """
    paths = []
    for root, folders, names in os.walk(folder):
        here = Path(root)
        for item in [*folders, *names]:
            if (here / item).is_symlink():
                _LOGGER.warning(
                    'cuju render leaves out %s of the submission folder, since it is a link',
                    (here / item).relative_to(folder).as_posix(),
                )
        folders[:] = [item for item in folders if item != '__pycache__']
        paths += [here / item for item in names if not (here / item).is_symlink()]
    return [
        (path.relative_to(folder).as_posix(), path.read_bytes())
        for path in sorted(paths)
        if path.is_file()
    ]


# The file of the home that remembers which team each folder of `add_folders` made.
FOLDERS_FILE = 'team-folders.json'


def _folders_path() -> Path:
    return _home.home() / FOLDERS_FILE


def _read_folders() -> dict[str, dict[str, str]]:
    try:
        document = json.loads(_folders_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    found = document.get('folders') if isinstance(document, dict) else None
    if not isinstance(found, dict):
        return {}
    return {
        key: item
        for key, item in found.items()
        if isinstance(item, dict) and isinstance(item.get('team'), str)
    }


def _write_folders(found: dict[str, dict[str, str]]) -> None:
    path = _folders_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.part')
    temporary.write_text(json.dumps({'version': 1, 'folders': found}, indent=1), encoding='utf-8')
    os.replace(temporary, path)


def _known_team(identifier: str) -> bool:
    with contextlib.suppress(ArgumentError):
        return entry(identifier)['kind'] == 'kaggle'
    return False


def _submission_folders(directory: Path) -> list[Path]:
    """Returns the folders of a directory that hold submissions, sorted, hidden ones left out."""
    return [
        item
        for item in sorted(directory.iterdir())
        if item.is_dir() and not item.name.startswith('.') and item.name != '__pycache__'
    ]


def _fold(name: str) -> str:
    return ' '.join(name.split()).casefold()


def _reuse(folder: Path, digest: str, index: dict[str, dict[str, str]]) -> str | None:
    """Returns the team that a folder made before, found by its path, else by its digest."""
    found = index.get(str(folder))
    if found is not None and _known_team(found['team']):
        return found['team']
    for held in index.values():
        if held.get('digest') == digest and _known_team(held['team']):
            return held['team']
    return None


def _renew(team: str, folder: Path, digest: str) -> tuple[str, str]:
    """Makes the files of a folder the current checkpoint of a team.

    Returns:
        What happened, `same` when the files already play, `checkpoint` otherwise, and the
        checkpoint that plays now.
    """
    point = digest[: _checkpoints.ID_LENGTH]
    if _checkpoints.current_id(team) == point:
        return 'same', point
    if _checkpoints.exists(team, point):
        _checkpoints.change(team, point, current=True)
        return 'checkpoint', point
    _checkpoints.add(team, folder.name, folder_bundle(_folder_files(folder)), folder.name)
    return 'checkpoint', point


def add_folders(
    directory: str | Path,
    choice: Any,
    scenario: str | None = None,
    size: str | None = None,
    control: dict[str, Any] | None = None,
    full: bool = False,
    isolate: bool = True,
) -> dict[str, Any]:
    """Registers every submission folder of a directory as a team, if its format accepts it.

    Each folder is checked under the format first, in a process of its own. A folder that the
    format refuses is left out with the format's reason, and a team is named by what the format's
    manifest states, else by its `cuju-team` file, else by its folder. A name that two accepted
    folders share excludes both of them as `duplicate team name`, and no name is ever numbered.
    A folder that an earlier run made a team of is found by its path, else by its digest. It
    keeps that team, and files that changed become a new checkpoint of it, so a tournament over
    the folder resumes. The verdict of each accepted folder is kept for its team.

    Args:
        directory: The folder whose folders are submissions.
        choice: The format and its options, as `side.choose` gives them.
        scenario: The scenario of the checks, or `None` for the size's own.
        size: `5v5` or `11v11` for the teams, by default each folder's own or `11v11`.
        control: The control of a new team, or `None` for the one its folder brings.
        full: Whether to ask for the full check.
        isolate: Whether each check runs in a process of its own.

    Returns:
        `{directory, format, teams, excluded}`. A team is `{path, team, name, action,
        checkpoint}`, `action` one of `added`, `same` and `checkpoint`. An excluded folder is
        `{path, name, reason, reason_id, environment}`.

    Raises:
        ArgumentError: If the directory is no folder, or the format's own code fails.
    """
    from .._formats import verdicts as _verdicts

    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise MessageError('server.team_folders.directory', directory=repr(str(directory)))
    scenario = _verdicts.check_scenario(scenario)
    accepted: list[tuple[Path, Any, str]] = []
    excluded: list[dict[str, Any]] = []

    def leave(folder: Path, name: str, reason: str, reason_id: str | None, **more: Any) -> None:
        excluded.append(
            {
                'path': str(folder),
                'name': name,
                'reason': reason,
                'reason_id': reason_id,
                'environment': False,
                **more,
            }
        )

    for folder in _submission_folders(root):
        try:
            subject = _verdicts.subject_of_folder(folder, scenario, size)
        except ArgumentError as error:
            leave(folder, folder.name, str(error), getattr(error, 'message_id', None))
            continue
        outcome = _verdicts.check(subject, choice, scenario, full, isolate=isolate, keep=False)
        if outcome.failed is not None:
            raise MessageError(
                'server.formats.failed', format=choice.format.label, reason=outcome.failed
            )
        verdict = outcome.verdict
        if not verdict.accepted:
            first = next(found for found in verdict.problems if found.level == 'error')
            leave(
                folder,
                subject.name,
                first.message,
                None,
                kind=first.kind,
                environment=verdict.environment,
            )
            continue
        accepted.append((folder, outcome, verdict.name or subject.name))
    names: dict[str, list[Path]] = {}
    for folder, _, name in accepted:
        names.setdefault(_fold(name), []).append(folder)
    teams_made: list[dict[str, Any]] = []
    index = _read_folders()
    for folder, outcome, name in accepted:
        if len(names[_fold(name)]) > 1:
            leave(
                folder,
                name,
                'duplicate team name',
                'server.team_folders.duplicate',
                same=[str(other) for other in names[_fold(name)] if other != folder],
            )
            continue
        digest = _runs.files_digest(folder, (TEAM_FOLDER,))
        team = _reuse(folder, digest, index)
        if team is None:
            # The format's pre-check judged the folder's entry, so the folder needs no
            # main.py of its own unless its format asks for one.
            made = _add_folder(
                folder,
                name,
                size or outcome.subject.size,
                control,
                submission_format=choice,
                size_override=size,
            )
            team, action, point = made['id'], 'added', digest[: _checkpoints.ID_LENGTH]
        else:
            action, point = _renew(team, folder, digest)
        index = {key: held for key, held in index.items() if held['team'] != team}
        index[str(folder)] = {'team': team, 'digest': digest}
        _write_folders(index)
        _keep_verdict(team, outcome, choice, scenario, full, _verdicts)
        teams_made.append(
            {
                'path': str(folder),
                'team': team,
                'name': entry(team)['name'],
                'action': action,
                'checkpoint': point,
            }
        )
    return {
        'directory': str(root),
        'format': choice.format.label,
        'teams': teams_made,
        'excluded': excluded,
    }


def _keep_verdict(
    team: str, outcome: Any, choice: Any, scenario: str | None, full: bool, verdicts: Any
) -> None:
    """Keeps the verdict that a folder's check found for the team that the folder became."""
    if outcome.unkept is not None:
        return
    with contextlib.suppress(ArgumentError, OSError):
        subject = verdicts.subject_of_team(team)
        outcome.key = verdicts.key_of(subject, choice, scenario, full)
        verdicts.store(team, outcome)


def _check_member(name: str) -> PurePosixPath:
    """Returns an archive member's path, refusing one that could land outside the team's folder.

    Windows reads `D:name` as a path on another drive and a backslash as a separator, so a part
    may hold neither a colon, a backslash nor a null, whatever system unpacks it. A part that
    opens a device on Windows, or ends in a dot or a space, is refused on every system too, so a
    submission imports alike everywhere.
    """
    path = PurePosixPath(name)
    if (
        not name
        or not path.parts
        or path.is_absolute()
        or any(part in ('', '.', '..') or any(c in part for c in ':\\\0') for part in path.parts)
    ):
        raise MessageError('server.teams.archive.outside', member=repr(name))
    if any(_device(part) for part in path.parts):
        raise MessageError('server.teams.archive.device', member=repr(name))
    return path


def _device(part: str) -> bool:
    """Returns whether a part of a path opens a device on Windows or names another file there.

    Windows reads the part before the first dot, without the spaces at its end, as a device's
    name, and drops the dots and spaces at the end of a part, so `nul.txt`, `con .py` and
    `main.py.` all name something else than they seem.
    """
    return part.endswith(('.', ' ')) or bool(_DEVICES.fullmatch(part.split('.')[0].rstrip(' ')))


def _inside(target: Path, path: PurePosixPath) -> Path:
    """Returns `target / path`, refusing it unless it resolves inside `target`."""
    destination = target.joinpath(*path.parts)
    if not destination.resolve().is_relative_to(target.resolve()):
        raise MessageError('server.teams.archive.outside', member=repr(str(path)))
    return destination


def _check_unpacked(count: int, size: int) -> None:
    """Refuses an archive of too many files or too large once unpacked."""
    if count > _MAXIMUM_FILES or size > _MAXIMUM_UNPACKED:
        raise MessageError('server.teams.archive.limits')


def add(
    name: str,
    filename: str,
    data: bytes,
    format: str = '11v11',
    control: dict[str, Any] | None = None,
    submission_format: Any = None,
    *,
    scenario: str | None = None,
    size_override: str | None = None,
) -> dict[str, Any]:
    """Stores a Kaggle submission as a new team and returns its entry.

    Args:
        name: The name that the page and recordings show.
        filename: The name of the uploaded file, whose extension says how to read it.
        data: The file's bytes.
        format: `5v5` or `11v11`, the size of the team for competition, which decides the
            scenarios it plays and the length of its roster.
        control: The players that the submission controls, as `check_control` takes them, or
            `None` for the active player alone, or for the setting of an exported folder.
        submission_format: The submission format the team is added under, or `None`. Without
            one the submission needs Kaggle open's `main.py` at its top. Under a format its
            pre-check judges the submission's entry, and the team keeps the format, so the
            check of each later upload runs under it.

        scenario: The home scenario of the new team.
        size_override: The size to use after reading the imported identity.

    Raises:
        ArgumentError: If the submission is too large, of another kind, holds no `main.py`
            where no format is given, or the control does not fit the format.
    """
    if len(data) > _MAXIMUM_BYTES:
        raise MessageError('server.teams.submission.size', size=len(data))
    _choice(format, tuple(FORMATS), _field('format'))
    if control is not None:
        check_control(control, format)
    name = (name or '').strip()
    identifier, folder = _new_folder()
    # Every failure after the folder is made removes it, so no half imported folder, with the
    # unchecked team file of an archive, is ever left for the list of teams to read.
    try:
        try:
            _extract(data, filename, folder, main=submission_format is None)
        except OSError as error:
            raise MessageError('server.teams.submission.unpack', error=error) from error
        entry = {
            'id': identifier,
            'kind': 'kaggle',
            'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'format': format,
        }
        # A folder exported from the page brings its format, control, roster and home scenario
        # along, each read as an upload is, and the folders of tools come along as they are.
        try:
            reading = _import(folder / TEAM_FOLDER, entry)
        except ArgumentError:
            raise
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            raise MessageError('server.teams.submission.unpack', error=error) from error
        imported_name = reading['team'].get('name') if reading is not None else None
        if not name and imported_name:
            entry['name'] = _check_name(imported_name)
        else:
            name = _check_name(name or Path(filename).stem.strip() or 'team')
            entry['name'] = _printable(name).strip()[:80]
        if size_override is not None:
            entry['format'] = size_override
        if control is not None:
            entry['control'] = check_control(control, entry['format'])
        if scenario is not None:
            check_home(entry, scenario)
            entry['scenario'] = scenario
        # The submission as it came is the team's first checkpoint, unless the team brings the
        # ground of its own first one.
        entry['ground'] = entry.get('ground') or _first_checkpoint_digest(identifier)
        completed = _complete(entry, reading=reading)
        _write(identifier, completed)
        if submission_format is not None:
            from .._formats import verdicts as _verdicts

            # The team keeps the format it was added under, which checks each later upload.
            _verdicts.remember_upload(identifier, submission_format)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    _looks.added(identifier)
    return completed


def _new_folder() -> tuple[str, Path]:
    """Creates the folder of a new team and returns its identifier and path.

    Identifiers sort by the second they are made in, so teams added in the same second differ by
    their random end alone. Creating the folder claims the identifier, and a draw that is taken
    is drawn again.
    """
    while True:
        identifier = _home.new_identifier('t')
        folder = _home.folder('teams') / identifier
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        return identifier, folder


def set_aside(path: Path, reason: str) -> None:
    """Renames a file of an imported `cuju-team` folder that fails its checks, and logs why.

    The file keeps its bytes under its name followed by `.rejected`, which the page never reads,
    so the team plays without it and the owner still finds it in the folder.
    """
    target = path.with_name(f'{path.name}{REJECTED}')
    count = 1
    while target.exists() or target.is_symlink():
        count += 1
        target = path.with_name(f'{path.name}.{count}{REJECTED}')
    try:
        path.rename(target)
    except OSError as error:
        _LOGGER.warning('cuju render leaves out %s of an imported team, %s', path.name, error)
        return
    _LOGGER.warning(
        'cuju render sets aside %s of an imported team as %s, since %s',
        path.name,
        target.name,
        reason,
    )


def _kept(path: Path) -> bool:
    """Returns whether a file of an imported `cuju-team` folder may stay, and removes it if not.

    A link could point outside the team's folder and a name such as `con.txt` opens a device on
    Windows, so either is removed, the link alone and never what it points to, and logged. Every
    other file stays.
    """
    if path.is_symlink():
        reason = 'it is a link'
    elif _device(path.name):
        reason = 'its name opens a device on Windows or ends in a dot or a space'
    else:
        return True
    _LOGGER.warning('cuju render removes %s of an imported team, since %s', path.name, reason)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
    return False


def _import(team_folder: Path, entry: dict[str, Any]) -> dict[str, Any] | None:
    """Takes the format, control, home scenario, roster and ground of an exported team into `entry`.

    The team's file is read field by field, a file of 0.1.0 or of this version alike, and a value
    that fails its check falls back to its default alone. The links and the names that open a
    device on Windows go from the folder, and every other file stays as it came.

    Returns:
        The reading of the team's file, or `None` when the folder brings none.
    """
    if not team_folder.is_dir():
        return None
    reading = _read_team(team_folder / 'team.json')
    if reading is not None and reading.get('unreadable'):
        set_aside(team_folder / 'team.json', 'it holds no team file that cuju render reads')
        reading = None
    team = reading['team'] if reading is not None else {}
    for key in ('format', 'control', 'scenario', 'roster'):
        if key in team:
            entry[key] = team[key]
    if reading is not None and reading['ground']:
        entry['ground'] = reading['ground']
    for path in list(team_folder.iterdir()):
        if path.name == 'team.json' or not _kept(path):
            continue
        if path.is_dir():
            # A folder that 0.1.0 wrote and this version never reads stays as it came, and the
            # links and device names inside it go as they do at the top.
            for inner in sorted(path.rglob('*'), reverse=True):
                _kept(inner)
    return reading


def duplicate(identifier: str) -> dict[str, Any]:
    """Copies a registered submission as a new team and returns the copy's entry.

    The copy takes the submission's files, its format, controls, home scenario and roster as they
    were last saved, and the folders of tools, such as its look, as they lie, under the name of
    the team followed by `copy`, and from then on each team changes alone.

    Raises:
        ArgumentError: If the team is unknown or is the built-in AI, which has no files to copy.
    """
    team = entry(identifier)
    if team['kind'] == 'builtin':
        raise MessageError('server.teams.duplicate_builtin')
    filename, data = export(identifier)
    return add(f'{team["name"]} copy', filename, data, team['format'])


def export(identifier: str, all_checkpoints: bool = False) -> tuple[str, bytes]:
    """Returns a team as a zip of its submission folder, with its `cuju-team` folder inside.

    The archive holds `main.py` and the submission's files at its top, as Kaggle reads them, and
    `cuju-team` with the team's manifest, which the registry reads back when the folder is
    uploaded again. The manifest is the second version, with the block `kept` of the file on disk,
    and a file on disk that holds no JSON object travels beside it under its name followed by
    `.rejected`. Every other file of the folder travels as it lies, the folders of tools such as
    `cuju-look` among them, so what an older version or a tool kept comes back with the team. The
    built-in AI exports only its `cuju-team` folder and the folders of tools.

    The files at the top are the team's current checkpoint, so the archive stays a Kaggle
    submission. `all_checkpoints` adds the earlier ones with their list.

    Returns:
        A file name for the archive and its bytes.
    """
    team = entry(identifier)
    root = _meta_path(identifier).parent.parent
    meta = _meta_path(identifier)
    with _LOCK:
        reading = _read_team(meta)
        document = _document(identifier, team, reading)
        unread = meta.read_bytes() if reading is not None and reading.get('unreadable') else None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f'{TEAM_FOLDER}/team.json', _manifest_bytes(document))
        if unread is not None:
            archive.writestr(f'{TEAM_FOLDER}/team.json{REJECTED}', unread)
        for path in sorted(root.rglob('*')) if root.is_dir() else []:
            if (
                path.is_file()
                and path != meta
                and not path.name.endswith('.part')
                and '__pycache__' not in path.parts
                and (all_checkpoints or not _earlier(path.relative_to(root)))
            ):
                archive.write(path, path.relative_to(root).as_posix())
    safe = ''.join(character if character.isalnum() else '-' for character in team['name'])
    return f'{safe.strip("-") or "team"}.zip', buffer.getvalue()


def _earlier(path: PurePosixPath | Path) -> bool:
    """Returns whether a path inside a team's folder belongs to the list of checkpoints."""
    parts = path.parts
    return parts[:2] == (TEAM_FOLDER, _checkpoints.FOLDER) or parts == (
        TEAM_FOLDER,
        _checkpoints.FILE,
    )


def remove(identifier: str) -> bool:
    """Deletes a team's folder and returns whether it existed.

    The built-in AI stays, and removing it only resets its name, its roster and the folders of
    tools, such as its look.
    """
    if identifier == BUILTIN:
        shutil.rmtree(_meta_path(BUILTIN).parent.parent, ignore_errors=True)
        return True
    if not _home.valid_identifier(identifier):
        return False
    folder = _home.folder('teams') / identifier
    if not folder.is_dir():
        return False
    shutil.rmtree(folder)
    # The verdicts of a team leave with it.
    (_home.home() / 'verdicts' / f'{identifier}.json').unlink(missing_ok=True)
    return True


def entry(identifier: str) -> dict[str, Any]:
    """Returns the entry of a team, or raises `ArgumentError` for an unknown one.

    The identifier may be an entrant, `team@checkpoint`, which names an earlier checkpoint of the
    team. The entry is then the team's, with `entrant`, `checkpoint` and the `control` that
    checkpoint uses.
    """
    team_id, point = _checkpoints.split(identifier)
    for team in teams():
        if team['id'] == team_id:
            return team if point is None else _at_checkpoint(team, point)
    raise MessageError('server.teams.unknown', identifier=repr(identifier))


def _at_checkpoint(team: dict[str, Any], point: str) -> dict[str, Any]:
    """Returns a team's entry as it plays at a checkpoint."""
    if team['kind'] != 'kaggle':
        raise MessageError('server.checkpoints.none', identifier=repr(team['id']))
    found = _checkpoints.resolve(_checkpoints.entrant(team['id'], point))
    entry = dict(team)
    entry['entrant'] = found['entrant']
    entry['checkpoint'] = found['checkpoint']
    if found['control'] is not None:
        entry['control'] = found['control']
    return entry


def side_entry(identifier: str) -> dict[str, Any]:
    """Returns the entry of a side of a match, a team of the registry or the random entrant.

    The random entrant is no team of the registry, so the Teams page leaves it out, and it plays
    one player of either size with actions drawn at random, its teammates the built-in AI's.
    """
    if identifier == RANDOM_ACTIONS:
        return _random()
    return entry(identifier)


def _random() -> dict[str, Any]:
    """Returns the entry of the random entrant, with a name as a label."""
    entry = {'id': RANDOM_ACTIONS, 'kind': 'random', 'created': None, 'format': '11v11'}
    entry.update(_messages.named('name', _messages.label('server.names.random')))
    return _complete(entry)


def load_agent(identifier: str) -> Any:
    """Loads a submission's agent function as Kaggle does, the last callable its `main.py` defines.

    The identifier may be an entrant, `team@checkpoint`, which loads the files of that checkpoint.

    The agent runs from its folder, which comes first on `sys.path`, as on Kaggle, so its own
    modules import and its working files resolve. The modules it imports from its folder leave
    `sys.modules` afterwards, so a second submission with modules of the same names gets its own.
    """
    team_id, point = _checkpoints.split(identifier)
    folder = _home.folder('teams') / team_id
    if point is not None and _home.valid_identifier(team_id):
        found = _checkpoints.resolve(identifier)['folder']
        folder = found if found is not None else folder
    main = folder / _MAIN
    if not _home.valid_identifier(team_id) or not main.is_file():
        raise MessageError('server.teams.unknown_kaggle', identifier=repr(identifier))
    module = types.ModuleType(f'cuju_team_{identifier.replace("-", "_").replace("@", "_")}')
    module.__file__ = str(main)
    with _from_folder(folder):
        before = set(sys.modules)
        # Kaggle: `get_last_callable` runs the file and takes the last callable it defined. The
        # bytes go to `compile`, so the file's own encoding line or UTF-8 decides how it reads.
        exec(compile(main.read_bytes(), str(main), 'exec'), module.__dict__)
        resolved = folder.resolve()
        for name in set(sys.modules) - before:
            origin = getattr(sys.modules[name], '__file__', None)
            if origin and Path(origin).resolve().is_relative_to(resolved):
                del sys.modules[name]
    callables = [value for value in module.__dict__.values() if callable(value)]
    if not callables:
        raise MessageError('server.teams.agent_function', identifier=repr(identifier))
    agent = callables[-1]

    @functools.wraps(agent)
    def run(*arguments: Any) -> Any:
        with _from_folder(folder):
            return agent(*arguments)

    return run


@contextlib.contextmanager
def _from_folder(folder: Path) -> Any:
    """Runs a block from a submission's folder, first on `sys.path`, as Kaggle runs an agent."""
    previous = os.getcwd()
    sys.path.insert(0, str(folder))
    os.chdir(folder)
    try:
        yield
    finally:
        os.chdir(previous)
        with contextlib.suppress(ValueError):
            sys.path.remove(str(folder))
