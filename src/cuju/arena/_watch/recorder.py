# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The recorder, which saves the matches of one environment of a batch into the library.

A recorder is a step observer of its batch. It reads the state of its environment after each reset
and step, keeps the frames of the episode in memory, and hands an ended episode to the writer of
recordings. Beside the frames it keeps the lanes of the players, which the same state gives, the
actions that the batch hands its observers, and the state of the kick off, so a recording of
version 2 shows what each player did and plays again from its start. `record` gives the same
recorder for a `with` block.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import struct
import threading
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cuju.errors import ArgumentError

from .._config import layers as _layers
from .._format import actions as _actions
from .._format import annotations as _annotations
from .._format import keep as _keep
from .._format import overlays as _overlays
from .._format import recording as _recording
from .._format import writer as _writer
from .._store import library as _library
from .._store import runs as _runs
from .._text import messages as _messages
from . import annotate as _annotate
from . import batches as _batches

_LOGGER = logging.getLogger('cuju')


class _Episode:
    """The frames of one episode and what its recording keeps beside them.

    Attributes:
        number: The number of episodes that the recorder ended before this one, or `None` for
            frames that a call by hand saves before their episode ends.
        frames: The frames.
        lineup: The header entry of each player.
        rewards: The return of each agent over the captured steps.
        notes: The annotations, by frame and player.
        overlays: The overlays of a policy, by frame, player and name.
        timing: The timing fields of the scenario.
        seed: The engine seed of the episode, read from the state of its first frame.
        cut: Whether an automatic reset ended the episode, so the frames lack its last step.
        lanes: The lanes of the players, one row for each frame, none for version 1.
        actions: The actions of the agents, one row for each frame.
        last: The actions of the step that ended the episode when an automatic reset replaced
            its frame, or `None`.
        state: The state of the first frame when no step of the episode had counted before it,
            the kick off, or `None` for a capture that began later.
        reverse: Whether the episode plays with GRF's reverse team processing.
        agents: The numbers of agents of the left and the right team.
    """

    __slots__ = (
        'actions',
        'agents',
        'cut',
        'frames',
        'lanes',
        'last',
        'lineup',
        'notes',
        'number',
        'overlays',
        'reverse',
        'rewards',
        'seed',
        'state',
        'timing',
    )

    def __init__(
        self,
        frames: list[bytes],
        lineup: list[dict[str, Any]],
        rewards: list[float],
        notes: dict[tuple[int, int], dict[str, Any]],
        timing: dict[str, Any] | None,
        seed: int | None,
        overlays: _annotate.Overlays | None = None,
    ) -> None:
        self.number: int | None = None
        self.overlays = overlays if overlays is not None else _annotate.Overlays()
        self.cut = False
        self.frames = frames
        self.lineup = lineup
        self.rewards = rewards
        self.notes = notes
        self.timing = timing
        self.seed = seed
        self.lanes: list[bytes] = []
        self.actions: list[bytes] = []
        self.last: bytes | None = None
        self.state: bytes | None = None
        self.reverse: bool | None = None
        self.agents: tuple[int, int] | None = None


class Recorder:
    """Records the matches of one environment of a batch into the renderer's library.

    The recorder is a step observer of the batch, so it captures every `reset` and step by itself.
    A recording ends when its episode ends, and `save` writes it, so a batch with automatic resets
    yields one recording per episode. In a batch without automatic resets an ended environment
    stays done at every step until its reset, and the recorder captures nothing of it in between,
    so an evaluation whose environments end at different steps yields one recording per episode
    too. Code that calls `capture` after each step, as with cuju 0.2.0, still works, since the
    first call of `capture` turns the automatic capture off.

    Filters keep only the episodes worth watching. Once an episode has ended the recorder reads
    its summary, and an episode that a filter drops is discarded without writing anything.
    `annotate` attaches numbers and text of a training loop or a policy to the captured steps.

    A recorder that captures by itself keeps an ended episode until the batch calls it next, at
    the next step, reset or close, so that `annotate(played=True)` after the step that ended the
    episode still reaches it. The filters then read its summary, and one thread of the renderer
    codes and writes the recording while training steps on. `save`, `flush`, `close` and closing
    the batch hand an ended episode over as well and wait for the recordings handed over.

    A recording of version 2 keeps three more things of its episode. The lanes of the players
    hold the direction, tiredness, sticky actions, agent and role of each player on each step,
    read from the state that gives the frame, with the roles that the batch reports. The actions
    are those that the batch hands its step observers, one for each agent and step. The state of
    the kick off is the state of the first frame, kept when the capture began before any step of
    the episode counted. With the state and the actions the episode plays again step for step in
    a batch of the same configuration. A capture by hand that leaves steps out, and a loop of
    steps that run without Python, record the actions that the recorder saw and mark the others
    as unknown.

    Attributes:
        environment: The index of the environment.
        saved: The paths of the recordings written so far, each added once its file is written.
        version: The version of the recording files.
        episodes: The number of episodes that ended, saved, dropped or still held for the next
            call of the batch.
        dropped: The number of episodes that the filters dropped.
    """

    def __init__(
        self,
        batch: Any,
        environment: int = 0,
        title: str | None = None,
        teams: list[dict[str, Any]] | None = None,
        scenario: str | None = None,
        version: Any = _layers.FROM_CONFIG,
        keep: Any = _layers.FROM_CONFIG,
        every: Any = _layers.FROM_CONFIG,
        min_reward: Any = _layers.FROM_CONFIG,
        config: Any = None,
        overrides: Any = (),
        *,
        run: Any = None,
        checkpoint: Any = None,
        home: Any = None,
    ) -> None:
        """Attaches a recorder of `environment` to `batch`, which captures from the next call on.

        Args:
            batch: The batch, or an environment that holds one as `batch` or as
                `unwrapped.batch`, as those of `cuju.env.create_environment` do.
            environment: The environment to record.
            title: The title of each recording, by default the scenario and the environment.
            teams: The name, `color` and `secondary` color of the left and right team, each an
                RGB list of three integers from 0 to 255, each optional.
            scenario: The name of the scenario that the header records, by default the batch's.
            version: The version of the recording files, 2, or 1 for the readers of cuju 0.2.0,
                which takes about two and a half times the room.
            keep: Which episodes to save, `all` or `None` for every one, `goals` for those with a
                goal, a query of the moments language, such as `goal team:left` or `value-drop`,
                which keeps the episodes in which it finds a moment, with the thresholds of the
                `[search]` table of the settings, or a function that takes the summary of the
                episode and returns whether to save it. The summary holds the `episode` number,
                counted from 0 over the episodes that the recorder ended, the `environment`, the
                `score` of the left and right team, the `outcome` of the left team at that score,
                `win`, `draw` or `loss`, the number of `goals`, the `length` in frames, the last
                `steps` number, the `rewards` of each agent summed over the captured steps, their
                sum, `reward`, and the `annotations` of the episode, each with its `frame`,
                `step`, `player`, `values`, `text`, `action` and `probabilities`, the player
                numbered as `annotate` takes it.
            every: Saves the first episode and every `every`-th after it, counting every episode
                that ends, saved or not.
            min_reward: Saves only episodes whose `reward` is at least this.
            config: The settings, a file, a mapping of tables, a `Config` or a list of them, laid
                over the shipped defaults as `cuju.arena.config` describes. The tables
                `[recorder]`, `[annotations]` and `[search]` serve the recorder, and `version`,
                `keep`, `every` and `min_reward` default to their keys of `[recorder]`.
            overrides: Settings such as `annotations.probabilities=all`, laid over `config`. An
                argument given to the call wins over both.
            run: The run that the recordings belong to, such as an evaluation of a checkpoint,
                as its identifier or as a mapping of `id` and, where known, `seed`, the seed
                that the batch was created with, which the batch does not tell its observers.
                Each recording keeps `provenance.run` with the `id` and the `episode`, and
                `provenance.batch_seed` when the seed is given.
            checkpoint: The model that plays, as an identifier, a mapping of `id`, `label` and
                `step`, or the path of its file or folder, whose content digest becomes the
                identifier. Each recording keeps it as `provenance.checkpoint`.
            home: The side at home of every recording, an entrant of the registry, a team, a
                team at a checkpoint, `builtin` or `random-actions`, whose home ground a viewer
                such as cuju render draws, or a mapping of `entrant` with the `ability` that the
                built-in AI played at or the `key` of a policy. Each recording keeps it as
                `provenance.home`. `None` leaves it to the header, which names no team of a
                training run, so such a recording shows the neutral ground.

        Raises:
            ArgumentError: If the batch is not a `BatchEnvironment`, the environment is not one of
                the batch, or a version, filter, run, checkpoint or side at home is invalid.
            ConfigurationError: If a setting is invalid.
        """
        resolved = _layers.resolve(config, overrides)
        settings = resolved.table(
            'recorder', version=version, keep=keep, every=every, min_reward=min_reward
        )
        version, keep = settings['version'], settings['keep']
        every, min_reward = settings['every'], settings['min_reward']
        self._limits = _annotate.Limits(resolved.table('annotations'))
        found = _batches.batch_of(batch)
        if found is None:
            raise ArgumentError(
                'expected a BatchEnvironment or an environment that holds one as batch, found '
                f'{type(batch).__name__}, whose states the recorder cannot read; record one of '
                'the batches of a MultiDeviceBatch instead'
            )
        batch = found
        if not 0 <= environment < batch.environment_count:
            raise ArgumentError(
                f'expected an environment from 0 to {batch.environment_count - 1}, '
                f'found {environment}'
            )
        if version not in _recording.JOINT_VERSIONS or isinstance(version, bool):
            raise ArgumentError(
                f'expected a version from {_recording.JOINT_VERSIONS}, found {version!r}'
            )
        self.version = version
        self._filter = _keep.Filter(keep, every, min_reward, thresholds=resolved.table('search'))
        self._run = _run_of(run)
        self._checkpoint = _runs.checkpoint(checkpoint)
        self._home = _home_of(home)
        # The rewards of the episode, the episodes ended so far and those dropped.
        self._rewards: list[float] = []
        self.episodes = 0
        self.dropped = 0
        # The recordings handed to the writer and not written yet, and the error of a failed
        # write, which the next `save` or `flush` raises.
        self._pending = 0
        self._written = threading.Condition()
        self._failure: Exception | None = None
        # The annotations of the recording, by frame and player.
        self._notes: dict[tuple[int, int], dict[str, Any]] = {}
        self._overlays = _annotate.Overlays()
        # The batch holds the recorder, which holds the batch weakly, so that a dropped batch
        # frees its memory without waiting for the garbage collector.
        self._batch = weakref.ref(batch)
        self.environment = environment
        self._title = title
        self._teams = teams
        self._scenario = scenario or _batches.scenario_name(batch)
        players = int(batch.left_players[environment] + batch.right_players[environment])
        self._reader = _recording.StateReader(players)
        self._action_set = _batches.action_set(batch)
        self._action_count = int(getattr(batch, 'action_count', 0) or 0)
        # What the key of a run names of the batch, its scenarios as it was created with them and
        # the difficulties that play in the environment.
        self._asset = _batches.scenario_name(batch)
        self._difficulty = [float(value) for value in batch.difficulty[environment].tolist()]
        self._stacked = bool(batch.stacked)
        # The columns of the actions of a step that hold the agents of the environment.
        mask = batch.agent_mask[environment].reshape(-1).tolist()
        self._slots = [index for index, kept in enumerate(mask) if kept]
        self._lineup: list[dict[str, Any]] | None = None
        self._frames: list[bytes] = []
        self._timing = None
        # The engine seed of the episode, which the state of its first frame gives.
        self._seed: int | None = None
        # The lanes of the players and the actions of the agents, a row for each frame, the
        # state of the kick off, and the reverse flag and the agents of the episode.
        self._lanes: list[bytes] = []
        self._actions: list[bytes] = []
        self._state: bytes | None = None
        self._reverse: bool | None = None
        self._agents: tuple[int, int] | None = None
        self.saved: list[Path] = []
        # The information of the last step, whose scoring signal completes an ending episode and
        # whose rewards add up to the returns.
        self._information: dict[str, Any] | None = None
        # The actions that the last step played in the environment, copied during the call of
        # the batch since the caller may write its tensor afterwards, and the steps that the
        # batch played since the last capture, which tell whether those actions led to the frame.
        self._played: list[int] | None = None
        self._steps = 0
        # Whether the caller captures by hand, as with cuju 0.2.0, which ends automatic capture.
        self._manual = False
        # Whether the environment ended without an automatic reset and waits for its reset.
        self._idle = False
        # The episode that ended at the last call of the batch, which the next call hands over.
        self._ended: _Episode | None = None
        _RECORDERS.add(self)
        batch.add_observer(self)

    def __repr__(self) -> str:
        batch = self._batch()
        if batch is None:
            state = 'its batch gone'
        elif any(observer is self for observer in batch.observers):
            state = 'by hand' if self._manual else 'capturing'
        else:
            state = 'detached'
        return (
            f'Recorder(environment {self.environment}, {self._scenario!r}, version {self.version}, '
            f'{state}, {len(self._frames)} frames of the current episode, {self.episodes} '
            f'episodes ended, {len(self.saved)} saved, {self.dropped} dropped)'
        )

    def __del__(self) -> None:
        # An ended episode that no later call handed over still reaches the writer.
        with contextlib.suppress(Exception):
            self._settle()

    def __call__(self, batch: Any, done: Any, information: dict[str, Any]) -> None:
        """Captures a reset of the environment or a step, as a step observer of the batch.

        Every call first hands over the episode that ended at the call before. A batch that
        closes waits for the recordings handed to the writer.
        """
        self._settle()
        if done is None:
            if information.get('close'):
                self._wait()
                return
            if 'reset' not in information:
                return
            reset = information['reset']
            if reset is None or bool(reset[self.environment]):
                self._idle = False
                if not self._manual:
                    self._capture(batch, None)
            return
        self._information = information
        actions = information.get('actions')
        self._played = None if actions is None else actions[self.environment].reshape(-1).tolist()
        self._steps += 1
        if not self._manual:
            self._capture(batch, done)

    def capture(self, done: Any = None) -> Path | None:
        """Adds the environment's current step by hand, and saves the recording when its episode
        ended.

        The recorder captures by itself, so a program needs this only to choose its steps. The
        first call turns the automatic capture off for good and drops the frames that it took and
        has not saved, so code written for cuju 0.2.0, which calls this after `reset` and after
        each `step`, records the same frames as before.

        With automatic resets, the step that ends an episode already shows the next one's start,
        so the recording ends at the step before, marked as the end, and the new start opens the
        next recording. Without them, a call for a step of an environment that already ended and
        has not been reset adds nothing and returns `None`.

        A capture by hand that ends an episode hands it over at once, so an annotation of the
        ending step belongs before that call.

        A step that runs without Python calls no observer, as one that a CUDA graph replays or
        that `torch.compile` compiled, so a loop of such steps records by calling this after the
        reset and after each step. The batch hands the recorder neither the rewards nor the
        scoring signal of those steps, so the summaries then count a reward of 0, the recording
        keeps no returns, and with automatic resets the last frame of an episode lacks the goal
        of the step that ended it. A batch without automatic resets keeps that step, goal
        included.

        Args:
            done: The done flags that `step` returned, or `None` after `reset`.

        Returns:
            The path of the recording that this call hands to the writer, which `saved` lists
            once its file is written, or `None`, also for an episode that the filters dropped.

        Raises:
            ArgumentError: If the batch is gone.
        """
        self._settle()
        if not self._manual:
            self._manual = True
            self._take()
        batch = self._batch()
        if batch is None:
            raise ArgumentError('expected the batch of the recorder, found it gone')
        return self._capture(batch, done)

    def _capture(self, batch: Any, done: Any) -> Path | None:
        """Adds the environment's current step, as `capture` describes."""
        ended = done is not None and bool(done[self.environment])
        if self._idle and ended:
            # GRF: an ended environment of a batch without automatic resets stays done at every
            # step until its reset, and those steps play nothing.
            return None
        self._idle = False
        # The actions of the step lead to this frame when the batch played one step since the
        # last capture, and a reset plays none.
        row = self._action_row(done is not None and self._steps == 1)
        self._steps = 0
        if done is not None:
            # The step's rewards belong to the episode that it ends, if it ends one.
            rewards = _keep.step_rewards(batch, self.environment, self._information or {})
            if not rewards:
                rewards = [0.0] * len(self._rewards)
            self._rewards = [
                total + reward
                for total, reward in zip(
                    self._rewards or [0.0] * len(rewards), rewards, strict=True
                )
            ]
        saved = None
        if ended and batch.auto_reset:
            if self._frames:
                last = bytearray(self._frames[-1])
                (flags,) = struct.unpack_from('<i', last, 28)
                struct.pack_into('<i', last, 28, flags | 1)
                # GRF: the scoring signal is 1 when the left team scores and -1 for the right.
                information = self._information or {}
                signal = information.get('score_reward')
                scored = int(signal[self.environment]) if signal is not None else 0
                if scored:
                    offset = 8 if scored > 0 else 12
                    (goals,) = struct.unpack_from('<i', last, offset)
                    struct.pack_into('<i', last, offset, goals + 1)
                    struct.pack_into('<i', last, 28, (flags | 1) & ~(31 << 8))
                self._frames[-1] = bytes(last)
            # The actions of this step ended the episode, whose frame the reset replaced, so
            # they close its actions, and the frame of the reset opens the next episode.
            saved = self._end(cut=True, last=row)
            row = self._action_row(False)
        state = batch.get_state(self.environment, stacked=batch.stacked)
        self._timing = _recording.scenario_timing(state)
        first = self._lineup is None
        if first:
            self._lineup = self._reader.lineup(state)
            self._seed = self._reader.engine_seed
        closing = ended and not batch.auto_reset
        frame = self._reader.frame(state, 1 if closing else 0)
        self._frames.append(frame)
        if self.version != 1:
            self._lanes.append(self._reader.lanes(batch.roles(self.environment)))
            self._actions.append(row)
            if first:
                self._reverse, self._agents = self._reader.reverse, self._reader.agents
                # GRF: the step of a frame counts the steps in play since the reset, so a first
                # frame at 0 is the kick off, and its state starts the episode again.
                self._state = state if struct.unpack_from('<i', frame)[0] == 0 else None
        if closing:
            saved = self._end()
            self._idle = True
        return saved

    def _action_row(self, seen: bool) -> bytes:
        """Returns the actions of the agents of the environment for one frame, as a row of bytes.

        Args:
            seen: Whether the actions of the last step led to the frame. Without them, and for an
                action outside the action set, the row holds 255.
        """
        if not seen or self._played is None:
            return bytes([_actions.NONE]) * len(self._slots)
        count = min(self._action_count, _actions.NONE)
        row = bytearray()
        for slot in self._slots:
            action = self._played[slot]
            # GRF: a negative action counts from the end of the action set.
            action += count if action < 0 else 0
            row.append(action if 0 <= action < count else _actions.NONE)
        return bytes(row)

    def _take(self) -> _Episode | None:
        """Returns the frames captured so far as an episode and starts a new one, `None` without
        frames."""
        episode = None
        if self._frames and self._lineup is not None:
            episode = _Episode(
                self._frames,
                self._lineup,
                self._rewards,
                self._notes,
                self._timing,
                self._seed,
                self._overlays,
            )
            episode.lanes, episode.actions, episode.state = self._lanes, self._actions, self._state
            episode.reverse, episode.agents = self._reverse, self._agents
        self._frames, self._lineup, self._rewards, self._notes = [], None, [], {}
        self._overlays = _annotate.Overlays()
        self._lanes, self._actions, self._state = [], [], None
        return episode

    def _end(self, cut: bool = False, last: bytes | None = None) -> Path | None:
        """Ends the episode. A capture by hand judges it at once and returns the path of a save,
        and the recorder's own capture keeps it for the next call of the batch.

        Args:
            cut: Whether an automatic reset ended the episode, so its frames lack the last step.
            last: The actions of that last step, which close the actions of the episode.
        """
        episode = self._take()
        if episode is None:
            return None
        episode.cut = cut
        episode.last = last
        episode.number = self.episodes
        self.episodes += 1
        if self._manual:
            return self._judge(episode)
        self._ended = episode
        return None

    def _settle(self) -> None:
        """Hands over the episode that ended at the last call of the batch, if one waits."""
        episode, self._ended = getattr(self, '_ended', None), None
        if episode is not None:
            self._judge(episode)

    def _judge(self, episode: _Episode) -> Path | None:
        """Saves or drops an ended episode, as the filters say, and returns the path of a save."""
        notes = [
            _annotations.public(note, episode.frames, episode.lineup)
            for note in episode.notes.values()
        ]
        header = {'scenario': self._scenario, 'players': episode.lineup, **(episode.timing or {})}
        summary = _keep.episode_summary(
            episode.number, episode.frames, episode.rewards, self.environment, notes, header=header
        )
        if self._filter(summary):
            return self._hand_over(episode)
        self.dropped += 1
        return None

    def annotate(
        self,
        values: dict[str, Any] | None = None,
        text: str | None = None,
        player: Any = None,
        step: int | None = None,
        probabilities: Any = None,
        action: Any = None,
        played: bool = False,
        *,
        agent: int | None = None,
        logits: Any = None,
        targets: dict[str, Any] | None = None,
        grids: dict[str, Any] | None = None,
        arrows: dict[str, Any] | None = None,
        extent: Any = None,
    ) -> None:
        """Attaches numbers, text or overlays to a captured step, which the page shows with the
        recording.

        Annotating reads nothing of the batch and changes nothing of it. Several calls for one
        step and player join their numbers, and a later text, action, probabilities or overlay of
        the same name replace the earlier ones. A recording of version 1 has no room for
        annotations and leaves them out. `cuju.arena.annotate` makes the same call for every
        agent of a batch at once.

        Args:
            values: Named numbers, such as `{'value': 0.4, 'reward': 1.0}`, at most as many as
                `[annotations] max_values` allows, 16 by default, each name of at most 40
                characters. Each number is kept to six significant digits.
            text: A short text, cut at 200 characters.
            player: The player, a number that counts the left team's players first and then the
                right team's, or a pair of team, 0 for the left, and the index within the team,
                or `None` for the whole match. The summary that a filter reads names the player
                by the same number.
            step: The step of the episode, as the frame's step counts it, by default the step
                captured last.
            probabilities: The probability of each action, a sequence indexed by action or a
                mapping of action to probability. `[annotations] probabilities` tells how many of
                the most likely actions are kept, three by default, or every one.
            action: The action chosen.
            played: Whether the annotation describes the step that the batch has just played, such
                as its reward. After a step that ended an episode it then goes to that episode, to
                its last frame by default, and joins the summary that a filter reads, where an
                annotation without `played` goes to the kick off of the next episode under
                automatic resets. After any other step the two agree. It serves a recorder that
                captures by itself.
            agent: In place of `player`, the agent, counted as the batch counts its agents, the
                left team's first, whose controlled player in the frame takes the annotation.
            logits: In place of `probabilities`, the logits of each action, which become
                probabilities through the softmax.
            targets: Named values over the players, as `cuju.arena.annotate` takes them for one
                agent.
            grids: Named grids over the pitch, of the player, or of the whole match without one.
            arrows: Named values over the actions.
            extent: The rectangle that every grid of the call covers.

        Raises:
            ArgumentError: If no step is captured yet, the step is not one of the episode, the
                agent controls no player then, or a part is invalid or passes a limit.
        """
        if not isinstance(played, bool):
            raise ArgumentError(f'expected played as True or False, found {played!r}')
        frames, lineup = self._episode_of(played)[:2]
        if not frames or lineup is None:
            if self._ended is not None:
                raise ArgumentError(
                    'expected a captured step of the current episode to annotate, found none '
                    'since the last step ended its episode, which played=True annotates'
                )
            raise ArgumentError('expected a captured step to annotate, found none')
        row = _annotate.one(
            lineup,
            player,
            values=values,
            text=text,
            probabilities=probabilities,
            logits=logits,
            action=action,
            targets=targets,
            grids=grids,
            arrows=arrows,
            extent=extent,
            agent=agent,
        )
        self._plan(row, step, played, strict=True)()

    def _episode_of(self, played: bool) -> tuple[Any, ...]:
        """Returns the frames, lineup, annotations, overlays, lanes and agents of the episode that
        an annotation goes to."""
        ended = self._ended
        if played and ended is not None:
            return (
                ended.frames,
                ended.lineup,
                ended.notes,
                ended.overlays,
                ended.lanes,
                ended.agents,
            )
        return (
            self._frames,
            self._lineup,
            self._notes,
            self._overlays,
            self._lanes,
            self._agents,
        )

    def _plan(
        self, row: _annotate.Row, step: int | None, played: bool, strict: bool = False
    ) -> Callable[[], None] | None:
        """Checks the annotations of a row against the frame they go to and returns the call that
        keeps them, or `None` when the recorder declines them.

        Without `strict`, as `cuju.arena.annotate` calls it, a recorder that holds no frame of
        the step, or one of version 1, declines. With it, those raise.

        Raises:
            ArgumentError: If a part passes a limit, or, with `strict`, the recorder holds no frame
                of the step or no map of its agents.
        """
        frames, lineup, notes, overlays, lanes, agents = self._episode_of(played)
        if not frames or lineup is None or (not strict and self.version == 1):
            return None
        try:
            frame = _annotations.frame_of(frames, step)
        except ArgumentError:
            if strict:
                raise
            return None
        if row.agents:
            if frame >= len(lanes) or agents is None:
                if strict:
                    raise ArgumentError(
                        'expected a recorder of version 2 to annotate by agent, found version 1'
                    )
                return None
            columns = _annotate.agent_players(lanes[frame], lineup, agents)
            players = [columns[agent] if agent < len(columns) else -1 for agent in row.who]
            if strict and players[0] < 0:
                raise ArgumentError(
                    f'expected an agent that controls a player at the step, found {row.who[0]}'
                )
        else:
            players = list(row.who)
        (number,) = struct.unpack_from('<i', frames[frame])
        found, shapes = _annotate.records(row, lineup, players, number, self._limits)
        merged = _annotate.plan_notes(notes, frame, found, self._limits.max_values)
        # The section of the overlays holds its index and the length of the index beside them.
        keep = overlays.plan(frame, shapes, _overlays.MAX_BYTES - 64)

        def commit() -> None:
            notes.update(merged)
            keep()

        return commit

    def save(self) -> Path | None:
        """Writes the frames captured so far as one recording, starts a new one, and waits until
        every recording handed to the writer is written.

        A call by hand saves whatever the filters say, as a training run that stops may wish. An
        episode that ended at the last call of the batch goes through the filters first.

        Returns:
            The path of the recording, or `None` when nothing was captured.

        Raises:
            Exception: The error of a write that failed since the last `save` or `flush`.
        """
        self._settle()
        episode = self._take()
        path = self._hand_over(episode) if episode is not None else None
        self.flush()
        return path

    def flush(self) -> None:
        """Waits until every recording handed to the writer is written, after handing over an
        episode that ended at the last call of the batch.

        Raises:
            Exception: The error of a write that failed since the last `save` or `flush`, which
                the logger `cuju` also reported when it happened.
        """
        self._settle()
        self._wait()
        with self._written:
            failure, self._failure = self._failure, None
        if failure is not None:
            raise failure

    def _wait(self) -> None:
        """Waits for the writes of the recorder."""
        with self._written:
            self._written.wait_for(lambda: self._pending == 0)

    def _hand_over(self, episode: _Episode) -> Path:
        """Hands an episode to the writer, and returns the path that its recording will take.

        The call pays for the header and the name of the file alone, and the writer codes and
        writes the recording. A writer that is busy with several recordings makes this wait for
        room.
        """
        # A title of the recorder's own is a label, which the page shows in its own language.
        title = self._title or _messages.label(
            'server.names.recorder',
            scenario=_messages.scenario(self._scenario),
            environment=self.environment,
        )
        # A batch may have been reset with other seeds before the capture, so the recording
        # names the seed of the batch only as its run gives it, the engine seed of its own
        # episode, which the state holds, and the action set that numbers the actions of its
        # annotations.
        key = None
        if episode.agents is not None:
            key = _recording.run_key(
                self._asset,
                episode.agents,
                self._action_set,
                episode.seed,
                episode.reverse,
                self._difficulty,
                self._stacked,
                _recording.START_RESET if episode.state is not None else _recording.START_MID,
            )
        head = _recording.header(
            title,
            self._scenario,
            episode.lineup,
            self._teams,
            len(episode.frames),
            episode.timing,
            self._provenance(episode),
            episode.agents,
            self._action_set if key is not None else None,
            key,
            home=self._home,
        )
        frames: list[bytes] = episode.frames
        if episode.lanes:
            # The actions go into the file when the recorder saw one, with one more row for the
            # step that an automatic reset cut, and the agents of the state count their columns.
            rows = [*episode.actions, episode.last or self._action_row(False)]
            seen = any(byte != _actions.NONE for row in rows for byte in row)
            fits = episode.agents is not None and sum(episode.agents) == len(self._slots)
            frames = _recording.Frames(
                frames,
                players=episode.lanes,
                actions=[list(row) for row in rows] if seen and fits else None,
                state=episode.state,
                overlays=episode.overlays.written() or None,
            )
        notes = list(episode.notes.values())
        # The summary keeps the return of each agent, which the frames do not hold, when the
        # batch handed the recorder the rewards, and counts the step that an automatic reset left
        # out of the frames.
        results: dict[str, Any] = {}
        if episode.rewards:
            results['returns'] = list(episode.rewards)
        if episode.cut:
            results['length'] = struct.unpack_from('<i', frames[-1])[0] + 1
        path = _library.reserve()
        version = self.version

        def write() -> None:
            try:
                _library.save(head, frames, path.stem, version, notes, results)
            except Exception as error:
                if not path.exists():
                    path.with_suffix('.json').unlink(missing_ok=True)
                _LOGGER.warning(
                    'cuju render: the recorder could not write %s, %s: %s',
                    path.name,
                    type(error).__name__,
                    error,
                )
                with self._written:
                    self._failure = error
            else:
                self.saved.append(path)
            finally:
                with self._written:
                    self._pending -= 1
                    self._written.notify_all()

        with self._written:
            self._pending += 1
        _writer.WRITER.submit(write)
        return path

    def _provenance(self, episode: _Episode) -> dict[str, Any]:
        """Returns the provenance of the recording of an episode, with its run and checkpoint.

        The `episode` of the run counts the episodes that the recorder ended before this one,
        which is the index of the episode in the seed rule of its environment when the recorder
        saw every reset since the batch was created.
        """
        found = _batches.origin('recorder', self.environment, episode.seed, self._action_set)
        if self._run is not None:
            found['run'] = {'id': self._run['id'], 'episode': episode.number}
            if 'seed' in self._run:
                found['batch_seed'] = self._run['seed']
            if 'sides' in self._run:
                found['sides'] = self._run['sides']
        if self._checkpoint is not None:
            found['checkpoint'] = dict(self._checkpoint)
        return found

    def close(self) -> Path | None:
        """Detaches the recorder from its batch, saves the frames captured so far and waits until
        every recording handed to the writer is written.

        Returns:
            The path of the recording that this call saved, or `None` when nothing was captured.

        Raises:
            Exception: The error of a write that failed since the last `save` or `flush`.
        """
        batch = self._batch()
        if batch is not None and any(observer is self for observer in batch.observers):
            batch.remove_observer(self)
        return self.save()

    def __enter__(self) -> Recorder:
        return self

    def __exit__(self, *exception: object) -> None:
        """Closes the recorder, so that a `with` block saves its last episode at its end."""
        self.close()


def _run_of(run: Any) -> dict[str, Any] | None:
    """Returns the run of a recorder as a mapping of `id` and, where given, `seed`, or `None`.

    Raises:
        ArgumentError: If the run is no identifier or mapping of one, or its seed is no whole
            number from 0 to 2**64 - 1.
    """
    if run is None:
        return None
    if isinstance(run, str):
        run = {'id': run}
    identifier = run.get('id') if isinstance(run, dict) else None
    if not isinstance(identifier, str) or not 0 < len(identifier) <= 200:
        raise ArgumentError(f'expected a run as an identifier or a mapping of id, found {run!r}')
    found: dict[str, Any] = {'id': identifier}
    if run.get('seed') is not None:
        seed = run['seed']
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**64:
            raise ArgumentError(f'expected the seed of a run from 0 to 2**64 - 1, found {seed!r}')
        found['seed'] = seed
    snapshots = _runs.side_snapshots(run.get('sides'))
    if snapshots is not None:
        found['sides'] = snapshots
    return found


# The recorders alive, whose ended episodes a process that exits hands over before the writer,
# registered at the import above, waits for its queue.
_RECORDERS: weakref.WeakSet[Recorder] = weakref.WeakSet()


def _settle_all() -> None:
    """Hands over the ended episode of every recorder that still holds one."""
    for recorder in list(_RECORDERS):
        with contextlib.suppress(Exception):
            recorder._settle()


atexit.register(_settle_all)
# A recorder takes the rows of `cuju.arena.annotate`.
_annotate.SINKS.append(Recorder)


def _home_of(home: Any) -> Any:
    """Returns the side at home that a recorder was given, an entrant checked against the registry.

    A mapping passes on as it is, so a team that leaves the registry later shows the neutral
    ground, as the replay of an evaluation does.

    Raises:
        ArgumentError: If the side is neither an entrant nor a mapping, or names a team or a
            checkpoint that the registry does not hold.
    """
    from .._registry import teams as _teams

    if home is None:
        return None
    if isinstance(home, dict):
        return dict(home)
    if not isinstance(home, str) or not home:
        raise ArgumentError(f'expected the side at home as an entrant, found {home!r}')
    _teams.side_entry(home)
    return home


def record(batch: Any, environment: int = 0, **options: Any) -> Recorder:
    """Records the matches of one environment of a batch, as a `Recorder` that also serves as a
    context manager.

    Leaving the `with` block detaches the recorder and saves the episode that it was capturing,
    and every episode that ended inside the block is saved already.

    Args:
        batch: The batch, or an environment that holds one, as for `Recorder`.
        environment: The environment to record.
        **options: The other arguments of `Recorder`, such as `title` and `teams`.

    Returns:
        The recorder, whose list `saved` holds the paths of its recordings.
    """
    return Recorder(batch, environment, **options)
