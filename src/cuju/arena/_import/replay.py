# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Exact imports, which play a file's match again in cuju and check it step by step.

A GRF dump that starts at its kick off holds the configuration of its match and the actions of
its agents, and GRF's own replay plays such a dump again from them. `dump` does the same through
cuju's public batch: a batch of one environment takes the scenario, the engine seed, the episode
number, the difficulties, the agents and the team order of the dump, plays each step's actions,
the right agents' actions turned back from the frame of the pitch where GRF stores them, and
compares the observation of every step with the dump's, bit for bit. A recorder of the renderer
attached to the batch keeps every joint. A match that plays the same to the dump's last step is
saved as an exact recording, and one that differs is dropped, with the step and the field of the
first difference, for the import of positions to take over.

An action log of cuju, which `cuju.ActionLog` writes, holds the options of its batch, every call
with its actions, and the digest of the outputs after each call. `action_log` plays the calls
again in a batch of those options on the device of the settings, checks the digest after each
call, as `cuju.action_log.replay` does, and records the environments that the settings choose.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .._watch.recorder import Recorder
from . import grf as _grf

# The options of a batch that change no output, which a log leaves to the machine that replays it.
_MACHINE_OPTIONS = (
    'device',
    'library',
    'threads',
    'pin_memory',
    'allocator',
    'pin_threads',
    'numa',
    'debug',
    'check_actions',
)
# The format of `cuju.ActionLog` that this import reads.
LOG_FORMAT = 1


class CancelledError(Exception):
    """Raised when the caller of an import asks it to stop."""


class _ImportRecorder(Recorder):
    """A recorder whose recordings name the file they were imported from.

    `imported` holds the provenance of the import, which each recording keeps as
    `imported_from`, and `accept` tells the filter whether an ended episode may be saved.
    """

    def __init__(self, batch: Any, environment: int, imported: dict[str, Any], **options: Any):
        self.imported = imported
        self.accept = True
        self.limit: int | None = None
        self.kept = 0
        super().__init__(batch, environment, keep=self._keep, **options)

    def _keep(self, summary: dict[str, Any]) -> bool:
        if not self.accept or (self.limit is not None and self.kept >= self.limit):
            return False
        self.kept += 1
        return True

    def _provenance(self, episode: Any) -> dict[str, Any]:
        found = super()._provenance(episode)
        found['source'] = 'import'
        imported = dict(self.imported)
        steps = len(episode.frames) - (1 if episode.state is not None else 0)
        imported['verified_steps'] = steps + (1 if episode.cut else 0)
        found['imported_from'] = imported
        return found


class Result:
    """What an exact import found.

    Attributes:
        paths: The recordings saved.
        verified_steps: The steps found equal to the file.
        first_difference: The `step`, `field` and `detail` of the first difference, or `None`.
        reason: A label of why the import was not exact, or `None`.
        skipped: The fields of the file that the check left out.
        limited: Whether the import stopped at the most recordings that it may keep.
    """

    def __init__(self) -> None:
        self.paths: list[Path] = []
        self.verified_steps = 0
        self.first_difference: dict[str, Any] | None = None
        self.reason: dict[str, Any] | None = None
        self.skipped: set[str] = set()
        self.limited = False


def _label(message_id: str, **values: Any) -> dict[str, Any]:
    from .._text import messages as _messages

    return _messages.label(message_id, **values)


def _difficulty_of(level: str) -> tuple[str, list[tuple[float | None, float | None]] | None]:
    """Returns the scenario of a dump's level and its difficulties, as cuju's dumps name them.

    cuju's `DumpWriter` names a scenario played with difficulties of its own as
    `<scenario>_difficulty_<left>_<right>`, each `none` or a number such as `0p5`.
    """
    base, marker, rest = level.rpartition('_difficulty_')
    if not marker:
        return level, None
    parts = rest.split('_')
    if len(parts) != 2:
        return level, None

    def number(text: str) -> float | None:
        return None if text == 'none' else float(text.replace('p', '.').replace('m', '-'))

    try:
        return base, [(number(parts[0]), number(parts[1]))]
    except ValueError:
        return level, None


def dump(
    entries: list[dict[str, Any]],
    imported: dict[str, Any],
    title: Any,
    device: str = 'cpu',
    progress: Callable[[int, int], None] | None = None,
) -> Result:
    """Plays a dump's match again from its configuration and actions, and saves it when equal.

    Args:
        entries: The steps of the dump, as the safe reader reads them, the first holding the
            configuration at the kick off.
        imported: The provenance of the import, which the recording keeps.
        title: The title of the recording, text or a label.
        device: The device of the batch that plays the match.
        progress: Called with the steps played and the steps of the dump.

    Returns:
        The `Result`. Its `paths` hold the recording when every step was equal, and else its
        `reason` and `first_difference` tell why not.
    """
    import torch

    import cuju
    from cuju import raw as _raw

    result = Result()
    config = entries[0]['debug']['config']
    agents = _grf.agent_counts(config.get('players'))
    if agents is None:
        result.reason = _label('server.import.reason.players', players=repr(config.get('players')))
        return result
    left, right = agents
    action_set = config.get('action_set', 'default')
    if action_set not in _grf.ACTION_SETS:
        result.reason = _label('server.import.reason.action_set', action_set=repr(action_set))
        return result
    names = _grf.ACTION_SETS[action_set]
    rows = []
    for index, entry in enumerate(entries):
        actions = (entry.get('debug') or {}).get('action')
        if not isinstance(actions, list) or len(actions) != left + right:
            result.reason = _label('server.import.reason.no_actions', step=index)
            return result
        row = []
        for agent, name in enumerate(actions):
            own = _grf.turned(name) if agent >= left and isinstance(name, str) else name
            if own not in names:
                result.reason = _label(
                    'server.import.reason.action',
                    step=index,
                    action=repr(name),
                    action_set=action_set,
                )
                return result
            row.append(names.index(own))
        rows.append(row)
    level = str(config.get('level', ''))
    scenario, difficulty = _difficulty_of(level)
    try:
        batch = cuju.BatchEnvironment(
            scenario,
            environment_count=1,
            representation='raw',
            action_set=action_set,
            auto_reset=False,
            device=device,
            physics_steps_per_frame=int(config.get('physics_steps_per_frame', 10)),
            game_engine_random_seed=int(config['game_engine_random_seed']),
            agent_counts=[(left, right)],
            difficulty=difficulty,
            # GRF: the first reset of an environment plays the episode after its number, and the
            # parity of that number picks the form of a scenario.
            episode_number=(int(config.get('episode_number', 2)) - 2) % 2,
        )
    except Exception as error:
        result.reason = _label(
            'server.import.reason.scenario', scenario=repr(level), detail=str(error)[:200]
        )
        return result
    recorder = None
    try:
        recorder = _ImportRecorder(batch, 0, imported, title=title, scenario=scenario)
        reverse = config.get('reverse_team_processing')
        batch.reset(reverse=None if reverse is None else int(bool(reverse)))
        cumulative = 0.0
        ended = False
        for index, (entry, row) in enumerate(zip(entries, rows, strict=True)):
            if ended:
                recorder.accept = False
                result.reason = _label(
                    'server.import.reason.ended', step=index - 1, last=len(entries) - 1
                )
                result.first_difference = {
                    'step': index,
                    'field': 'steps',
                    'detail': f'the replay ended its episode at step {index - 1}',
                }
                return result
            actions = torch.tensor([row], dtype=torch.int32)
            _, _, done, information = batch.step(actions.to(batch.torch_device))
            ended = bool(done[0])
            views = _raw.grf_observations(batch, 0)
            reward = float(information['score_reward'][0])
            cumulative += reward
            mine = {
                'observation': _grf.observation(views, left, right),
                'reward': reward,
                'cumulative_reward': cumulative,
                'debug': {'frame_cnt': index},
            }
            stored = {key: entry[key] for key in mine if key in entry}
            if 'debug' in stored:
                stored['debug'] = {
                    key: value for key, value in stored['debug'].items() if key == 'frame_cnt'
                }
            found = _grf.difference(mine, stored, f'step {index}', result.skipped)
            if found is not None:
                recorder.accept = False
                field, detail = found
                field = field.split('.', 1)[1] if '.' in field else field
                result.first_difference = {'step': index, 'field': field, 'detail': detail}
                result.reason = _label('server.import.reason.difference', step=index, field=field)
                return result
            result.verified_steps = index + 1
            if progress is not None:
                progress(index + 1, len(entries))
        if result.verified_steps == 0:
            recorder.accept = False
            return result
        path = recorder.close()
        recorder.flush()
        result.paths = list(recorder.saved) or ([path] if path is not None else [])
        return result
    finally:
        if recorder is not None and not result.paths:
            recorder.accept = False
            _detach(batch, recorder)
        batch.close()


def _detach(batch: Any, recorder: Recorder) -> None:
    """Detaches a recorder that must save nothing more, dropping what it holds."""
    if any(observer is recorder for observer in batch.observers):
        batch.remove_observer(recorder)
    # An ended episode that waits is judged by the filter, which refuses it now.
    recorder._settle()
    recorder._take()


def _log_batch_options(header: dict[str, Any]) -> dict[str, Any]:
    """Returns the options of a log's batch, as `BatchEnvironment` takes them."""
    options = {
        key: value
        for key, value in (header.get('options') or {}).items()
        if key not in _MACHINE_OPTIONS
    }
    if 'channel_dimensions' in options:
        options['channel_dimensions'] = tuple(options['channel_dimensions'])
    for key in ('difficulty', 'agent_counts'):
        if options.get(key) is not None:
            options[key] = [tuple(pair) for pair in options[key]]
    return options


def read_log(path: Path) -> tuple[dict[str, Any], bytes, bytes, list[bytes]]:
    """Returns the header, actions, digests and states of an action log.

    Raises:
        ValueError: If the file holds no action log of the format that this import reads.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            header = json.loads(archive.read('log.json'))
            if not isinstance(header, dict) or header.get('format') != LOG_FORMAT:
                found = header.get('format') if isinstance(header, dict) else None
                raise ValueError(f'expected an action log of format {LOG_FORMAT}, found {found}')
            calls = header.get('calls')
            if not isinstance(calls, list) or not all(isinstance(call, dict) for call in calls):
                raise ValueError('expected the calls of an action log')
            actions = archive.read('actions.bin')
            digests = archive.read('digests.bin')
            count = sum(call.get('call') == 'set_state' for call in calls)
            states = [archive.read(f'states/{number}.bin') for number in range(count)]
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as error:
        raise ValueError(str(error)[:200]) from None
    return header, actions, digests, states


def action_log(
    path: Path,
    imported: dict[str, Any],
    title: Any,
    environments: Any,
    device: str = 'cpu',
    limit: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Result:
    """Plays an action log again, checks it call by call and saves the episodes it records.

    The recordings of the chosen environments are saved as their episodes end, each checked
    through the digest of every call up to its end. A call whose digest differs stops the replay,
    the episodes that it reached are dropped, and the result names the call.

    Args:
        path: The log.
        imported: The provenance of the import, which each recording keeps.
        title: The title of the recordings, text or a label.
        environments: `all`, or the indices of the environments to record.
        device: The device of the batch that plays the log.
        limit: The recordings to keep at most, or `None` for every one.
        progress: Called with the calls played and the calls of the log.

    Raises:
        ValueError: If the file holds no action log, or its options do not make a batch.
    """
    import torch

    import cuju
    from cuju.action_log import Digest

    header, actions, digests, states = read_log(path)
    calls = header['calls']
    try:
        batch = cuju.BatchEnvironment(header['assets'], device=device, **_log_batch_options(header))
    except Exception as error:
        raise ValueError(f'the options of the log make no batch, {error}'[:300]) from None
    result = Result()
    recorders: list[_ImportRecorder] = []
    try:
        count = batch.environment_count
        chosen = range(count) if environments in (None, 'all') else environments
        chosen = [index for index in chosen if 0 <= int(index) < count]
        for environment in chosen:
            recorder = _ImportRecorder(batch, int(environment), imported, title=title)
            recorder.limit = limit
            recorders.append(recorder)
        value = Digest()
        offset = state = 0
        for number, call in enumerate(calls):
            kind = call.get('call')
            if kind == 'reset':
                value.update(batch.reset(seeds=call.get('seeds'), reverse=call.get('reverse')))
            elif kind == 'step':
                size = 1
                for part in call['shape']:
                    size *= int(part)
                step_actions = torch.frombuffer(
                    bytearray(actions[offset : offset + 4 * size]), dtype=torch.int32
                ).reshape(call['shape'])
                offset += 4 * size
                observation, reward, done, _ = batch.step(step_actions.to(batch.torch_device))
                value.update((observation, reward, done))
            elif kind == 'reset_done':
                batch.reset_done()
                value.update(batch.observation())
            elif kind == 'set_state':
                batch.set_state(call['index'], states[state])
                state += 1
                value.update(batch.observation())
            else:
                raise ValueError(f'expected the calls of an action log, found {kind!r}'[:200])
            expected = digests[32 * number : 32 * number + 32].hex()
            if value.hexdigest() != expected:
                result.first_difference = {
                    'step': number,
                    'field': f'call {number}, {kind}',
                    'detail': f'the digest is {value.hexdigest()}, the log holds {expected}',
                }
                result.reason = _label('server.import.reason.log', call=number, kind=str(kind))
                break
            result.verified_steps = number + 1
            if progress is not None:
                progress(number + 1, len(calls))
            if limit is not None and sum(recorder.kept for recorder in recorders) >= limit:
                break
        if result.first_difference is None and result.verified_steps == len(calls):
            if not batch.stacked:
                for index in range(batch.environment_count):
                    value.update(batch.get_state(index))
            if value.hexdigest() != header.get('digest'):
                result.first_difference = {
                    'step': len(calls),
                    'field': 'states after the last call',
                    'detail': f'the digest is {value.hexdigest()}, the log holds '
                    f'{header.get("digest")}',
                }
                result.reason = _label('server.import.reason.log', call=len(calls), kind='states')
        result.limited = limit is not None and sum(recorder.kept for recorder in recorders) >= limit
        for recorder in recorders:
            if result.first_difference is not None:
                # The episodes that the last calls reached hold steps the check did not pass.
                recorder.accept = False
                _detach(batch, recorder)
            elif result.limited:
                # An episode that ended waits for the filter, which counts the limit, and the one
                # under way is dropped.
                _detach(batch, recorder)
            else:
                recorder.close()
            recorder.flush()
            result.paths.extend(recorder.saved)
    finally:
        for recorder in recorders:
            if any(observer is recorder for observer in batch.observers):
                batch.remove_observer(recorder)
        batch.close()
    return result
