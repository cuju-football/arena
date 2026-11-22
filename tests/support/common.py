# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Helpers that the renderer's tests of several areas share."""

import json
import os
import struct
import threading
import time
import urllib.request

import torch

import cuju
import cuju.arena
import cuju.errors
import cuju.state
from cuju.arena._format import recording as _recording
from cuju.arena._registry import teams as _teams
from cuju.arena._store import home as _home
from cuju.arena._store import library as _library

# The first page of an Ogg Vorbis file, which the server takes as a sound.
_OGG = b'OggS\x00\x02' + bytes(20) + b'\x01\x1e' + b'\x01vorbis' + bytes(23)


def _get(address, path):
    with urllib.request.urlopen(address + path, timeout=10) as response:
        return response.status, response.read()


def _send(address, path, data, method='POST'):
    request = urllib.request.Request(address + path, data=data, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


_CRASH = """
def agent(obs):
    if obs['step'] == 4:
        raise ValueError('the policy broke')
    return [7]
"""


def _until(condition, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


def _play_to_end(games, entry):
    """Plays a game of `_Games` without input until it ends, and returns its last entry."""
    assert games.port(entry['id']) is not None, games.current()
    games.send(entry['id'], {'paused': False})
    _until(lambda: games.current()['state'] in ('ended', 'failed'), 120)
    return games.current()


# A toy submission that controls several players and sends each one a direction of its own, by
# the index of its player, so the test sees each action reach its player.
_SEVERAL = """
def agent(obs):
    actions = []
    for raw in obs['players_raw']:
        player = raw['active']
        assert raw['left_team_roles'][player] != 0, 'a goalkeeper reached the submission'
        actions.append(3 if player % 2 else 7)
    assert len(actions) == obs['controlled_players']
    return actions
"""
_SINGLE = """
def agent(obs):
    return [5 if obs['step'] % 7 else 12]
"""


def _short_matches(monkeypatch, steps):
    """Ends every match that `play` starts after `steps` steps."""
    from cuju.arena._playing import play as _play

    real = _play.BatchEnvironment

    def short(*arguments, **options):
        batch = real(*arguments, **options)
        step, count = batch.step, [0]

        def limited(actions):
            observation, reward, done, information = step(actions)
            count[0] += 1
            return observation, reward, done | (count[0] >= steps), information

        batch.step = limited
        return batch

    monkeypatch.setattr(_play, 'BatchEnvironment', short)


def _decal(width=8, height=8):
    """Returns a PNG with alpha of a size, a crest or a print that the registry takes."""
    import struct
    import zlib

    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body))

    rows = b''.join(b'\0' + b'\x80' * (width * 4) for _ in range(height))
    header = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', header)
        + chunk(b'IDAT', zlib.compress(rows))
        + chunk(b'IEND', b'')
    )


def _toy_team(tmp_path, name, source, format, control=None):
    folder = tmp_path / name
    folder.mkdir()
    (folder / 'main.py').write_text(source, encoding='utf-8')
    team = _teams.add_folder(folder, name, format)
    return _teams.update(team['id'], control=control) if control else team


def _frames(path):
    document = _recording.read_header(path)
    return document, list(_recording.frames(path))


def _control(
    players, assignment='dynamic', selection=None, goalkeeper='scenario', action_set='default'
):
    return dict(
        players=players,
        assignment=assignment,
        selection=selection or [],
        goalkeeper=goalkeeper,
        action_set=action_set,
    )


_SAMPLES = ['sample-11v11', 'sample-5v5']


def _library_ids():
    return sorted(entry['id'] for entry in _library.matches())


def _one_second(monkeypatch):
    """Makes identifiers of one second whose random ends each come three times."""
    import itertools
    import types

    monkeypatch.setattr(
        _home, 'time', types.SimpleNamespace(strftime=lambda form: '20261109-120000')
    )
    lock = threading.Lock()
    source = itertools.chain.from_iterable([f'{value:04x}'] * 3 for value in itertools.count())

    def token_hex(count):
        with lock:
            return next(source)

    monkeypatch.setattr(_home, 'secrets', types.SimpleNamespace(token_hex=token_hex))


def _sample_head(name, count):
    """Returns the header of a shipped sample as `_recording.header` returns it, and its frames."""
    path = _library._SAMPLES / f'{name}.cjr'
    document = _recording.read_header(path)
    document.pop('keyframe_interval', None)
    document.update(version=1, frame_count=count)
    return _recording._encode_header(document), list(_recording.frames(path, stop=count))


def _filtered_episodes(**filters):
    """Plays episodes of a scenario under an unfiltered recorder and one with `filters`.

    Returns the frames of every episode, the frames that the filtered recorder saved, the
    summaries that a function filter saw, and the filtered recorder.
    """
    batch = cuju.BatchEnvironment(
        'academy_empty_goal_close_l1_r0', environment_count=1, device='cpu', seed=11
    )
    everything = cuju.arena.Recorder(batch)
    seen = []
    keep = filters.pop('keep', None)
    if keep == 'record':

        def keep(summary):
            seen.append(summary)
            return summary['length'] % 2 == 0

    filtered = cuju.arena.Recorder(batch, keep=keep, **filters)
    batch.reset()
    episode = step = 0
    while everything.episodes < 8:
        # Even episodes shoot at the goal and score, odd ones shoot over the touchline.
        action = (12 if step % 3 else 5) if episode % 2 == 0 else (3 if step % 2 else 12)
        _, _, done, _ = batch.step(torch.tensor([[action]], dtype=torch.int32))
        step += 1
        if bool(done[0]):
            episode, step = episode + 1, 0
    batch.close()
    episodes = [list(_recording.frames(path)) for path in everything.saved]
    kept = [list(_recording.frames(path)) for path in filtered.saved]
    return episodes, kept, seen, filtered


def _goals(frames):
    return sum(struct.unpack_from('<2i', frames[-1], 8))


# Attempts of the academy tasks, and groups of recordings.


# The two players of the recordings of attempts, one a side, which stand still.
_ATTEMPT_PLAYERS = [
    {'team': team, 'index': 0, 'goalkeeper': False, 'controlled': team == 0} for team in (0, 1)
]


def _attempt_frame(step, score=(0, 0), flags=0, mode=0):
    """Returns a frame of a recording of two still players, with its step, score and flags."""
    head = struct.pack('<8i', step, mode, score[0], score[1], -1, -1, step * 100, flags)
    frame = head + struct.pack('<7f4i', 0, 0, 0, 0, 0, 0, 1, -1, -1, 0, -1)
    return frame + bytes(_recording.frame_bytes(len(_ATTEMPT_PLAYERS)) - len(frame))


def _attempt_frames(goal_at=None, length=40, finished=True, against_at=None):
    frames = []
    for step in range(length):
        left = int(goal_at is not None and step >= goal_at)
        right = int(against_at is not None and step >= against_at)
        flags = 1 if finished and step == length - 1 else 0
        frames.append(_attempt_frame(step, (left, right), flags))
    return frames


def _short_tasks(monkeypatch, steps):
    """Ends every attempt of an academy task after `steps` steps."""
    from cuju.arena._evaluate import academy as _academy

    real = _academy.BatchEnvironment

    def short(*arguments, **options):
        batch = real(*arguments, **options)
        step, count = batch.step, [0]

        def limited(actions):
            observation, reward, done, information = step(actions)
            count[0] += 1
            return observation, reward, done | (count[0] >= steps), information

        batch.step = limited
        return batch

    monkeypatch.setattr(_academy, 'BatchEnvironment', short)


def _grouped_recordings(count=2, title='Corner, toy'):
    """Saves `count` short recordings as the attempts of one run and returns its group.

    Returns:
        The identifier of the group and the identifiers of its recordings, in the order of
        their attempts.
    """
    matches = []
    for index in range(count):
        frames = _attempt_frames(goal_at=12 if index == 0 else None, length=30)
        head = _recording.header(
            f'{title}, attempt {index + 1}',
            'academy_corner',
            _ATTEMPT_PLAYERS,
            None,
            len(frames),
            {'duration': 400, 'second_half': None, 'full_match': False},
            {'source': 'benchmark', 'task': 'corner', 'attempt': index},
        )
        matches.append(_library.save(head, frames).stem)
    group = _home.new_identifier('g')
    entry = {
        'id': group,
        'kind': 'benchmark',
        'task': 'corner',
        'title': title,
        'competitor': 'toy',
        'result': f'1 of {count}',
        'state': 'done',
        'attempts': count,
        'value': 1,
    }
    _library.set_group(entry, {match: index for index, match in enumerate(matches)})
    return group, matches


# Help.


# The moments of the library, its search and the captions.

_REPOSITORY = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SAMPLE_FOLDER = os.path.join(os.path.dirname(cuju.arena.__file__), 'samples')


class _FeedRequests:
    def __init__(self, entries):
        self.listed = entries

    def entries(self):
        return list(self.listed)


class _FeedTournaments:
    def __init__(self, full):
        self.full = full

    def entries(self):
        return [{key: self.full[key] for key in ('id', 'name')}]

    def get(self, identifier):
        return self.full if identifier == self.full['id'] else None


# The drafts of languages other than English, which load through the language import, each in a
# folder named after its code beside its style guide and glossary.
TRANSLATIONS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'translations'
)


# Names and titles that cuju makes, which travel as labels.


def _names_lineup(count=2):
    return [
        {'team': k % 2, 'index': k // 2, 'goalkeeper': False, 'controlled': False}
        for k in range(count)
    ]
