# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Play, judge and record matches of cuju.

The arena keeps teams, submission formats, match recordings, evaluations, ratings and
tournaments. Public names load the modules they need on first use."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from cuju import engine_status as _engine_status

from ._version import __version__ as __version__

if TYPE_CHECKING:
    from ._config.layers import Config, config
    from ._evaluate.benchmark import benchmark
    from ._evaluate.evaluate import Evaluation, evaluate
    from ._evaluate.ratings import rate
    from ._format.frames import Frames, frames
    from ._import.core import add_recordings
    from ._moments.listing import moments
    from ._registry.teams import add_folder as add_team
    from ._store.home import home
    from ._store.runs import list as runs
    from ._store.stats import of as statistics
    from ._watch.annotate import annotate
    from ._watch.recorder import Recorder, record

_LAZY = {
    'Config': ('._config.layers', 'Config'),
    'config': ('._config.layers', 'config'),
    'benchmark': ('._evaluate.benchmark', 'benchmark'),
    'Evaluation': ('._evaluate.evaluate', 'Evaluation'),
    'evaluate': ('._evaluate.evaluate', 'evaluate'),
    'rate': ('._evaluate.ratings', 'rate'),
    'frames': ('._format.frames', 'frames'),
    'moments': ('._moments.listing', 'moments'),
    'Frames': ('._format.frames', 'Frames'),
    'formats': ('.formats', None),
    'add_recordings': ('._import.core', 'add_recordings'),
    'add_team': ('._registry.teams', 'add_folder'),
    'home': ('._store.home', 'home'),
    'runs': ('._store.runs', 'list'),
    'statistics': ('._store.stats', 'of'),
    'annotate': ('._watch.annotate', 'annotate'),
    'Recorder': ('._watch.recorder', 'Recorder'),
    'record': ('._watch.recorder', 'record'),
    'plugins': ('.plugins', None),
}
_STAMPED = frozenset(_LAZY) - {'Config', 'config', 'formats', 'plugins'}

__all__ = [
    'Config',
    'Evaluation',
    'Frames',
    'PolicyError',
    'Recorder',
    'add_recordings',
    'add_team',
    'annotate',
    'benchmark',
    'captions',
    'config',
    'delete_group',
    'evaluate',
    'formats',
    'frames',
    'home',
    'moments',
    'play',
    'plugins',
    'rate',
    'record',
    'recordings',
    'restore_group',
    'runs',
    'search',
    'statistics',
]


def play(
    left: str,
    right: str,
    scenario: str = '11_vs_11_kaggle',
    seed: int | None = None,
    title: str | None = None,
    device: str = 'cpu',
    difficulty: tuple[float | None, float | None] = (None, None),
    *,
    ability: tuple[float | None, float | None] | None = None,
) -> Path:
    """Plays a match between two teams of the registry, saves it and returns the recording.

    Args:
        left: The team of the left side, a registry identifier or `builtin`.
        right: The team of the right side.
        scenario: A shipped scenario, without agent counts, which the teams decide.
        seed: The seed of the match, or `None` for one drawn from the clock.
        title: The title of the recording.
        device: `cpu` or `cuda`.
        difficulty: The difficulty of the built-in AI on the left and on the right, each from 0
            to 1, or `None` for the scenario's, GRF's team difficulty for every player the
            built-in AI controls.
        ability: The earlier name of `difficulty`, which reads the same.

    Returns:
        The path of the recording, a `Path` whose `summary` holds what the library keeps of the
        match, as `recordings` lists it, with the `score` of the left and the right team, the
        `teams`, the `scenario` and the `provenance` with the seed.

    Raises:
        ArgumentError: If a team is unknown, plays another format than the scenario's, or an
            difficulty lies outside 0 to 1, or `difficulty` and `ability` name different values.
    """
    from ._playing.play import play as run

    return run(left, right, scenario, seed, title, device, difficulty, ability=ability)


def recordings(group: str | None = None, trash: bool = False) -> list[dict[str, Any]]:
    """Returns the summaries of the library, as the Recordings page lists them, newest first.

    Each summary of a recording of a run that kept its recordings, such as a model's run of a
    task of the benchmark, holds its `group`, with the run's identifier `id`, its `kind`, `title`,
    `competitor` and `result`, the `attempt` of the recording and the run's `task`, `attempts`,
    `state` and `value`.

    Args:
        group: The identifier of a group, to list its recordings alone in the order of their
            attempts.
        trash: Whether to list the Trash in place of the library.
    """
    from ._store import library as _library

    return _library.matches(trash, group)


def delete_group(group: str) -> list[str]:
    """Moves every recording of a group to the Trash at once, and returns their identifiers.

    Each recording restores from the Trash like any other, and `restore_group` restores them all.
    An unknown group, or one already in the Trash, returns an empty list.
    """
    from ._store import library as _library

    return _library.trash_group(group)


def restore_group(group: str) -> list[str]:
    """Restores every recording of a group from the Trash, and returns their identifiers."""
    from ._store import library as _library

    return _library.trash_group(group, restore=True)


def search(
    query: str = '', limit: int | None = None, *, config: Any = None, overrides: Any = ()
) -> dict[str, Any]:
    """Finds moments across every recording of the library, as `cuju arena search` does.

    The query holds words that name kinds of moments, such as `goal`, `shot` or `corner`, other
    words that match the title, scenario, teams and players, filters such as `team:Greens`,
    `player:9`, `after:60`, `score:trailing` or `source:tournament`, and the signals of a policy
    such as `value-drop` or `surprise:0.05`, as the repository's `docs/moments-and-search.md` and
    `docs/policy-search.md` describe.

    Args:
        query: The query, such as `goal team:Greens after:60`.
        limit: The most results, by default the query's `limit:` or 50.
        config: The settings, a file, a mapping of tables, a `Config` or a list of them, whose
            `[search]` table holds the thresholds of the policy signals, as `cuju.arena.config`
            describes.
        overrides: Settings such as `search.value_drop=0.5`, laid over `config`.

    Returns:
        The `results`, each with the recording as `match`, the `kind`, the time `t` in seconds of
        the recording, the `seek` time a few seconds before it that opens Watch, the `minute` of
        the clock, the team, the player's `number` and `name`, the `score`, a plain line of
        `text` and the address `url` of the page, and the `total` before the limit.

    Raises:
        ArgumentError: If the query holds an unknown filter or a value out of place.
        ConfigurationError: If a setting is invalid.
    """
    from ._moments import search as _search

    return _search.search(query, limit, config=config, overrides=overrides)


def captions(recording: str | Path, path: str | Path | None = None) -> list[dict[str, Any]]:
    """Returns the caption cues of a recording and writes them as WebVTT when given a path.

    Args:
        recording: A recording identifier of the library, such as `sample-5v5`, or a `.cjr` file.
        path: A file to write the captions to as WebVTT, or `None`.

    Returns:
        The cues, each with its `start` and `end` in seconds to the millisecond, its message `id`
        and `params` of the table of captions and its `text`.
    """
    from ._moments import search as _search

    cues = _search.captions(recording)
    if path is not None:
        text = _search.captions_vtt(recording)
        Path(path).write_text(text, encoding='utf-8', newline='\n')
    return cues


if _engine_status.intact():
    from ._playing.errors import PolicyError as PolicyError
    from ._text import messages as _messages

    PolicyError.__module__ = __name__
    _messages.public_names()


def __getattr__(name: str) -> Any:
    """Returns a public name that another module defines, importing that module on first use.

    The name is kept in the module afterwards, so later reads cost nothing.

    Raises:
        AttributeError: If the name is no public name of `cuju.arena`.
    """
    if name not in _LAZY:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    import importlib

    module, attribute = _LAZY[name]
    found = importlib.import_module(module, __name__)
    value = found if attribute is None else getattr(found, attribute)
    if name in _STAMPED:
        value.__module__ = __name__
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Returns the public names of the module, those of `__all__`."""
    return sorted(__all__)
