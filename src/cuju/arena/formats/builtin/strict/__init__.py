# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The code of the format `strict/1`, the strict/1 rules of earlier tournaments behind the contract.

Under strict/1 rules each registered team's policy runs in a process of its own, the script
`policy_worker.py`, with its own import path and a limit of threads, so two teams never share a
module and each computes on the same terms. The match times every call of each policy, and a side
that fails to load or to reset in time, fails in a call, answers late, answers an action outside its
set or computes for longer on average than the rules allow, breaks a rule, which ends the match at
once as that side's forfeit. This is isolation for fairness, not a sandbox for code that one cannot
trust, as the renderer's other ways of playing a submission are.

`StrictPolicies` plays a whole match under the rules, and `Player` plays one side of it as the
contract hands the format its steps, over the same `Worker`, limits and rules. The check of a team,
its pre-flight, loads the team and plays a few steps against the built-in AI under the same rules,
once on the left and once on the right, so the turned view of the right side is checked too, and
reports a pass or the rule it broke, with its timings and warnings. `format.py` puts both behind
the contract.

Every match plays exactly from its seed and the actions of each step, so the actions that a match
under strict/1 rules stepped with, kept beside its seed, replay it, which `replay` does.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import queue
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from cuju.arena._playing import play as _play
from cuju.arena._registry import manifest as _manifest
from cuju.arena._registry import teams as _teams
from cuju.arena._store import home as _home
from cuju.arena._store import runs as _runs
from cuju.arena._text import messages as _messages
from cuju.kaggle import action_index, action_indices

from .limits import Guard
from .policy_worker import THREAD_VARIABLES

# The script of a team's process, which runs from its file with nothing of the renderer.
WORKER = Path(__file__).with_name('policy_worker.py')
# The steps that the check of a team plays.
PREFLIGHT_STEPS = 10
# The seconds that a process has to end once the match asks it to.
_END_SECONDS = 2.0
# The variables that hide the GPUs of CUDA and ROCm from a team's process, unless the rules
# allow a GPU.
GPU_VARIABLES = ('CUDA_VISIBLE_DEVICES', 'HIP_VISIBLE_DEVICES', 'ROCR_VISIBLE_DEVICES')
# A team's log starts again once it grows past this many bytes.
_LOG_BYTES = 1024 * 1024
# The bytes of a mebibyte, the unit of the limit `folder_mb`, as of the limit of memory and of an
# upload to the registry.
MEBIBYTE = 1024 * 1024
# The limits and switches of strict/1 rules by default, which `format.toml` states as options.
DEFAULT_LIMITS: dict[str, Any] = {
    'threads': 1,
    'load_seconds': 30.0,
    'first_call_seconds': 10.0,
    'call_seconds': 1.0,
    'reset_seconds': 5.0,
    'mean_call_ms': 20.0,
    'folder_mb': 100.0,
    'memory_mb': 2048.0,
    'match_seconds': 120.0,
    'frozen': True,
    'gpu': False,
    'deterministic': False,
}
# The share of all its actions above which one action draws a warning in the check of a team.
_ONE_ACTION = 0.9


def _label(name: str, /, **values: Any) -> dict[str, Any]:
    """Returns the label of a broken rule, `server.strict.<name>`, which the page translates."""
    return _messages.label(f'server.strict.{name}', **values)


def _seconds(value: float) -> str:
    return f'{value:g}'


def _kept(relative: Path) -> bool:
    """Returns whether a file of a team's folder counts in its digest and its copy."""
    return not (
        relative.parts[0] == _teams.TEAM_FOLDER
        or _runs.tool_part(relative.parts)
        or '__pycache__' in relative.parts
        or relative.suffix == '.pyc'
    )


def submission_folder(team: str) -> Path:
    """Returns the folder whose root holds the submission of a team or of an entrant.

    An entrant `team@checkpoint` that names an earlier checkpoint plays the files kept for it
    inside the team's folder, as `checkpoints.resolve` finds them, and its size, its digest,
    its links and its frozen copy read that folder.
    """
    from cuju.arena._registry import checkpoints as _checkpoints

    if _checkpoints.split(team)[1] is None:
        return _home.folder('teams') / team
    return Path(_checkpoints.resolve(team)['folder'])


def folder_digest(team: str) -> str | None:
    """Returns the SHA-256 of a team's folder, of every file's path and bytes, or `None`.

    The files count in the order of their paths, each its path, its size and its bytes, so the
    digest changes with any file that is added, removed, renamed or changed. The renderer's
    folder of the team's kit and presentation and the caches of Python stay out of it, since the
    Teams page and Python change those while the policy stays the same.
    """
    folder = submission_folder(team)
    if not (folder / 'main.py').is_file():
        return None
    digest = hashlib.sha256()
    for path in sorted(folder.rglob('*'), key=lambda item: item.relative_to(folder).as_posix()):
        relative = path.relative_to(folder)
        if path.is_symlink() or not path.is_file() or not _kept(relative):
            continue
        data = path.read_bytes()
        digest.update(relative.as_posix().encode() + b'\0' + str(len(data)).encode() + b'\0')
        digest.update(data)
    return digest.hexdigest()


# The reparse tags of a symbolic link and of a junction on Windows, whose `stat` alone names them.
_LINK_TAGS = (0xA000000C, 0xA0000003)


def _is_link(path: Path) -> bool:
    """Returns whether a path is a link, a symbolic link or, on Windows, a junction."""
    try:
        found = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(found.st_mode) or getattr(found, 'st_reparse_tag', 0) in _LINK_TAGS


def links_in(folder: Path) -> list[str]:
    """Returns the paths of the links in a submission's folder, in order, which strict/1 refuses.

    A link may point anywhere, so its target would play in the copy of a match while the size and
    the digest of the folder leave it out. The renderer's folder `cuju-team` and the caches of
    Python stay out, as they stay out of the copy.
    """
    found = []
    for root, folders, files in os.walk(folder):
        here = Path(root)
        for name in [*folders, *files]:
            relative = (here / name).relative_to(folder)
            if _kept(relative) and _is_link(here / name):
                found.append(relative.as_posix())
        folders[:] = [
            name
            for name in folders
            if _kept((here / name).relative_to(folder)) and not _is_link(here / name)
        ]
    return sorted(found)


def links(team: str) -> list[str]:
    """Returns the paths of the links in a team's submission, which strict/1 refuses."""
    return links_in(submission_folder(team))


class LinkError(ValueError):
    """Raised when a team's submission holds a link, which strict/1 refuses.

    Attributes:
        paths: The paths of the links inside the team's folder.
    """

    def __init__(self, paths: list[str]) -> None:
        super().__init__(f'the submission holds a link, {paths[0]}')
        self.paths = paths


def frozen_copy_of(source: Path, target: Path) -> Path:
    """Copies a submission's folder into `target` with every file read only, and returns the copy.

    A match plays the team from the copy, inside its private working folder, so whatever the team
    writes stays in that folder and goes with it at the end of the match. The copy holds the same
    files as the size and the digest of the folder count, so a submission with a link is refused.

    Raises:
        LinkError: If the submission holds a link.
    """
    found = links_in(source)
    if found:
        raise LinkError(found)
    shutil.copytree(
        source,
        target,
        symlinks=False,
        ignore=lambda folder, names: [
            name
            for name in names
            if name == '__pycache__'
            or name.endswith('.pyc')
            or (
                (name == _teams.TEAM_FOLDER or name.startswith(_runs.TOOL_PREFIX))
                and Path(folder) == source
                and (Path(folder) / name).is_dir()
            )
        ],
    )
    for path in target.rglob('*'):
        if path.is_file():
            path.chmod(stat.S_IREAD)
    return target


def frozen_copy(team: str, target: Path) -> Path:
    """Copies a team's folder into `target` with every file read only, and returns the copy.

    Raises:
        LinkError: If the team's submission holds a link.
    """
    return frozen_copy_of(submission_folder(team), target)


def _writable(function: Any, path: str, info: Any) -> None:
    """Makes a read only file writable and removes it, as removing a frozen copy needs."""
    del info
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        function(path)


def log_path(team: str) -> Path:
    """Returns the log of a team's processes under strict/1 rules, what its policy printed."""
    folder = _home.folder('logs') / 'policies'
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{team}.log'


def _log(team: str) -> Any:
    """Opens the log of a team to add to, started again once it grows too long."""
    path = log_path(team)
    with contextlib.suppress(OSError):
        if path.stat().st_size > _LOG_BYTES:
            path.unlink()
    stream = path.open('a', encoding='utf-8', errors='replace')
    stream.write(f'--- a process of the team started at {time.strftime("%Y-%m-%d %H:%M:%S")}\n')
    stream.flush()
    return stream


class Worker:
    """One team's process, the requests it is sent and the lines it answers with.

    Attributes:
        process: The process.
        started: When the process started, once the copy of the team's folder was made, on the
            clock of `time.perf_counter`.
        arrived: When the last answer that `receive` returned arrived, on the same clock.
        calls: The calls that the policy has answered.
        translate: Whether the policy answers the default set in a batch of the v2 set.
    """

    def __init__(
        self,
        team: dict[str, Any],
        limits: dict[str, Any],
        translate: bool,
        folder: Path | None = None,
    ) -> None:
        """Starts the process of a team, from its folder or, by default, the registry's.

        Args:
            team: The team's registry entry, whose `id` names its log and whose `entrant` names
                the checkpoint it plays.
            limits: The limits and switches of strict/1 rules.
            translate: Whether the policy answers the default set in a batch of the v2 set.
            folder: The folder of the submission to play, in place of the team's own.
        """
        threads = int(limits['threads'])
        # The variables of Python that a renderer's process may carry, such as PYTHONPATH, stay
        # out, as `-I` leaves them out too, and the threads of each library follow the rules.
        environment = {
            key: value
            for key, value in _home.process_environment().items()
            if not key.upper().startswith('PYTHON')
        }
        environment.update({name: str(threads) for name in THREAD_VARIABLES})
        if not limits.get('gpu', False):
            environment.update({name: '' for name in GPU_VARIABLES})
        self.calls = 0
        self.total = 0.0
        self.translate = translate
        # Each line comes with the moment it arrived, so a side's answer counts from then, however
        # late the match reads it.
        self._lines: queue.Queue[tuple[float, str] | None] = queue.Queue()
        # A private working folder, which the match removes once the process ends, with a copy
        # of the team's folder whose files read only, so nothing the team writes outlasts it.
        self.folder = tempfile.mkdtemp(prefix='cuju-policy-')
        if folder is None:
            folder = submission_folder(team.get('entrant') or team['id'])
        if limits.get('frozen', True):
            folder = frozen_copy_of(folder, Path(self.folder) / 'team')
        self._log = _log(team['id'])
        self.guard = Guard(float(limits.get('memory_mb', 2048)), threads)
        # The clock of loading starts once the copy is made, so the team's limit holds its own
        # loading alone.
        self.started = time.perf_counter()
        self.arrived = self.started
        # The team's prints go to its log, and its answers come on a pipe of their own, which no
        # other team reaches.
        self.process = subprocess.Popen(
            [sys.executable, '-I', '-X', 'utf8', str(WORKER), str(folder), str(threads)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log,
            text=True,
            encoding='utf-8',
            errors='replace',
            cwd=self.folder,
            env=environment,
            preexec_fn=self.guard.preexec(),
        )
        self.guard.attach(self.process)
        threading.Thread(target=self._read, daemon=True, name='cuju-strict-worker').start()
        # The team's code runs only once the process stands under its limits.
        self.send({'start': True})

    def _read(self) -> None:
        try:
            for line in self.process.stdout:
                self._lines.put((time.perf_counter(), line))
        except (OSError, ValueError):
            pass
        self._lines.put(None)

    def send(self, request: dict[str, Any]) -> None:
        """Sends one request, and leaves a process that has ended to answer that it has."""
        with contextlib.suppress(OSError, ValueError):
            self.process.stdin.write(json.dumps(request) + '\n')
            self.process.stdin.flush()

    def receive(self, timeout: float) -> dict[str, Any] | None:
        """Returns the next answer within `timeout` seconds, `{'closed': True}` once the process
        has ended, or `None` when no answer came in time. `arrived` then holds the moment the
        answer arrived, on the clock of `time.perf_counter`."""
        deadline = time.perf_counter() + max(0.0, timeout)
        while True:
            try:
                item = self._lines.get(timeout=max(0.0, deadline - time.perf_counter()))
            except queue.Empty:
                return None
            if item is None:
                # The process ended, so its last lines have all been read.
                self._lines.put(None)
                return {'closed': True}
            arrived, line = item
            try:
                found = json.loads(line)
            except ValueError:
                continue
            if isinstance(found, dict):
                self.arrived = arrived
                return found

    def exit_text(self) -> str:
        """Returns how the process ended, with its exit code once it has one."""
        with contextlib.suppress(subprocess.TimeoutExpired):
            code = self.process.wait(1.0)
            return f'its process ended with exit code {code}'
        return 'its process stopped answering'

    def close(self) -> None:
        """Asks the process to end and ends it when it does not in time."""
        self.send({'end': True})
        with contextlib.suppress(OSError, ValueError):
            self.process.stdin.close()
        try:
            self.process.wait(_END_SECONDS)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                self.process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(_END_SECONDS)
        self.guard.close()
        with contextlib.suppress(OSError):
            self._log.close()
        shutil.rmtree(self.folder, onerror=_writable)
        shutil.rmtree(self.folder, ignore_errors=True)

    def failure(self, found: dict[str, Any], kind: str, name: Any, label: dict[str, Any]) -> Any:
        """Returns the rule that a failed request broke, the guard's limit where it saw one.

        Args:
            found: What the process answered, its error or that it ended.
            kind: The kind of rule that a plain failure breaks, such as `crash` or `load`.
            name: The name of the side.
            label: The reason of a plain failure.

        Returns:
            The kind, the reason and the detail of the break.
        """
        broken = self.guard.broken
        if broken is not None:
            limit, values = broken
            return limit, _label(limit, player=name, **values), None
        stage = found.get('stage')
        memory = self.guard.memory_mb
        if stage == 'memory' or (
            stage is None and self.guard.peak() >= 0.9 * memory and found.get('closed')
        ):
            return 'memory', _label('memory_out', player=name, limit=_seconds(memory)), None
        if stage == 'process':
            return 'process', _label('process', player=name), None
        return kind, label, None


class StrictPolicies(_play.Policies):
    """The policies of a match under strict/1 rules, each submission in a process of its own.

    The processes start together and load at once, so the limit of loading counts from each
    process's start, after the copy of its team's folder, to the moment its answer arrived, and the
    time that the match spends copying the other team's folder counts for neither. The random
    entrant plays in the match's own process, as it is no team.

    The mean compute time of a call, which the rules limit over the whole match, leaves out the
    first call, which may well load a model or compile, and the first call has a limit of its own.

    Args:
        limits: The limits and switches, the options of `strict/1`.
        mean_after_first: Whether the mean of a call leaves out the first, true unless a test asks
            for the plain mean.
    """

    def __init__(
        self,
        *arguments: Any,
        seed: int = 0,
        limits: dict[str, Any],
        mean_after_first: bool = True,
    ) -> None:
        self.limits = limits
        self.mean_after_first = mean_after_first
        self.workers: list[Worker] = []
        # The sides whose submission holds a link, refused before any process of theirs starts.
        self._refused: list[tuple[Any, ...]] = []
        try:
            super().__init__(*arguments, seed=seed)
            if self._refused:
                raise _play.RuleError(self._refused)
            self._start()
        except _play.RuleError as broken:
            broken.stats = self._safe_stats()
            self.close()
            raise
        except BaseException:
            self.close()
            raise

    def _safe_stats(self) -> list[Any]:
        try:
            return self.stats()
        except AttributeError:
            return [None, None]

    def _runner(
        self,
        team: dict[str, Any],
        side: int,
        extended: bool,
        control: dict[str, Any],
        seed: int,
        name: Any,
    ) -> Any:
        if team['kind'] == 'random':
            return super()._runner(team, side, extended, control, seed, name)
        found = links(team['id'])
        if found:
            label = _label('link', player=name, path=found[0])
            self._refused.append((side, 'load', label, 'link'))
            return None
        worker = Worker(team, self.limits, extended and control['action_set'] == 'default')
        self.workers.append(worker)
        return worker

    def _players(self) -> list[tuple[dict[str, Any], Worker]]:
        return [
            (player, player['runner'])
            for player in self.players
            if isinstance(player['runner'], Worker)
        ]

    def _start(self) -> None:
        """Waits for every process to load and then to reset, each within its limit.

        Raises:
            RuleError: If a side fails or runs out of time in either.
        """
        breaks: list[tuple[int, str, dict[str, Any]]] = []
        load = self.limits['load_seconds']
        for player, worker in self._players():
            found = worker.receive(worker.started + load - time.perf_counter())
            name = player['name']
            if found is not None and found.get('ready') and worker.arrived - worker.started > load:
                # An answer that arrived after the limit, while the match was busy with the other
                # side, came too late all the same.
                found = None
            if found is None:
                breaks.append(
                    (player['side'], 'load', _label('load_time', player=name, count=_seconds(load)))
                )
            elif found.get('closed') or 'error' in found:
                reason = str(found.get('error') or worker.exit_text())
                label = _label('load', player=name, reason=reason)
                breaks.append((player['side'], *worker.failure(found, 'load', name, label)))
            else:
                player['stats'].load = worker.arrived - worker.started
                worker.guard.loading = False
        if breaks:
            raise _play.RuleError(breaks)
        reset = self.limits['reset_seconds']
        asked = {}
        for _, worker in self._players():
            worker.send({'reset': True})
            asked[id(worker)] = time.perf_counter()
        for player, worker in self._players():
            found = worker.receive(asked[id(worker)] + reset - time.perf_counter())
            name = player['name']
            if found is None:
                breaks.append(
                    (
                        player['side'],
                        'reset',
                        _label('reset_time', player=name, count=_seconds(reset)),
                    )
                )
            elif found.get('closed') or 'error' in found:
                reason = str(found.get('error') or worker.exit_text())
                label = _label('reset', player=name, reason=reason)
                breaks.append((player['side'], *worker.failure(found, 'reset', name, label)))
            else:
                player['stats'].reset = time.perf_counter() - asked[id(worker)]
        if breaks:
            raise _play.RuleError(breaks)

    def _actions(self, worker: Worker, answer: Any, players: int) -> list[int]:
        """Returns the actions of an answer, checked as Kaggle checks them, or raises."""
        count = _play.DEFAULT_ACTIONS if worker.translate else self.batch.action_count
        values = (
            [action_index(answer, count)]
            if players == 1
            else action_indices(answer, count, players)
        )
        return [value % _play.DEFAULT_ACTIONS for value in values] if worker.translate else values

    def decide(self, parts: list[Any]) -> list[Any]:
        """Sends each process its observation at once, and takes each answer within its limit.

        The time of an answer runs from its sending to the moment it arrived, which the thread
        that reads the process stamps, so the order in which the match reads the sides changes
        nothing.

        Raises:
            RuleError: If a side fails in its call, answers late or answers an invalid action.
        """
        sent: dict[int, float] = {}
        for index, (player, part) in enumerate(zip(self.players, parts, strict=True)):
            if part is not None and isinstance(player['runner'], Worker):
                player['runner'].send({'obs': part[2]})
                sent[index] = time.perf_counter()
        answers: list[Any] = []
        breaks: list[tuple[int, str, dict[str, Any]]] = []
        for index, (player, part) in enumerate(zip(self.players, parts, strict=True)):
            if part is None:
                answers.append(None)
                continue
            runner, name, side = player['runner'], player['name'], player['side']
            if not isinstance(runner, Worker):
                started = time.perf_counter()
                answer = runner.act(part[2], self.batch.action_count)
                player['stats'].add(time.perf_counter() - started, answer)
                answers.append(answer)
                continue
            first = runner.calls == 0
            limit = self.limits['first_call_seconds' if first else 'call_seconds']
            found = runner.receive(sent[index] + limit - time.perf_counter())
            # Each answer counts from its own sending to its own arrival, so a side is judged on
            # its own time, however long the match spent reading the other side first.
            wall = runner.arrived - sent[index]
            if found is not None and 'answer' in found and wall > limit:
                found = None
            answers.append(None)
            if found is None:
                reason = (
                    _label('first_time', player=name, count=_seconds(limit))
                    if first
                    else _label(
                        'call_time', player=name, call=runner.calls + 1, count=_seconds(limit)
                    )
                )
                breaks.append((side, 'timeout', reason))
                continue
            if found.get('closed') or 'error' in found:
                reason = str(found.get('error') or runner.exit_text())
                if found.get('stage') == 'answer':
                    breaks.append(
                        (
                            side,
                            'invalid',
                            _label('invalid_type', player=name, reason=reason),
                            'type',
                        )
                    )
                else:
                    label = _label('crash', player=name, reason=reason)
                    breaks.append((side, *runner.failure(found, 'crash', name, label)))
                continue
            try:
                actions = self._actions(runner, found.get('answer'), len(part[2]['players_raw']))
            except (TypeError, ValueError, IndexError) as error:
                # A wrong count of actions, an action of another type or one outside the set.
                detail = {TypeError: 'type', ValueError: 'count', IndexError: 'range'}[type(error)]
                reason = _label(f'invalid_{detail}', player=name, reason=_play._reason(error))
                breaks.append((side, 'invalid', reason, detail))
                continue
            compute = found.get('compute')
            valid = isinstance(compute, (int, float)) and not isinstance(compute, bool)
            # The time that the process reports for its call counts, within the time it took.
            compute = min(float(compute), wall) if valid and compute >= 0 else wall
            runner.calls += 1
            runner.total += compute
            player['stats'].add(compute, actions)
            answers[-1] = actions
            most = self.limits.get('match_seconds')
            if most is not None and runner.total > most:
                # The time of all the team's calls of the match together has its own limit.
                reason = _label(
                    'total_time', player=name, total=f'{runner.total:.2f}', limit=_seconds(most)
                )
                breaks.append((side, 'total_time', reason))
        if breaks:
            raise _play.RuleError(breaks)
        return answers

    def finish(self) -> None:
        """Checks the mean compute time of a call of each side against its limit.

        Raises:
            RuleError: If a side computed for longer on average than the rules allow.
        """
        limit = self.limits['mean_call_ms']
        breaks = []
        for player, _ in self._players():
            mean = player['stats'].mean(self.mean_after_first) * 1000
            if player['stats'].calls and mean > limit:
                reason = _label(
                    'mean', player=player['name'], mean=f'{mean:.1f}', limit=_seconds(limit)
                )
                breaks.append((player['side'], 'mean', reason))
        if breaks:
            raise _play.RuleError(breaks)

    def close(self) -> None:
        """Ends every team's process, and keeps the most memory that each used."""
        for worker in self.workers:
            worker.close()
        for player, worker in self._players():
            player['stats'].memory = worker.guard.peak()


# The five kinds of forfeit of the contract that each of the twelve rules of strict/1 gives, so a
# record names the contract's kind and keeps the rule as its detail.
KINDS = {
    'load': 'load',
    'reset': 'timeout',
    'timeout': 'timeout',
    'crash': 'crash',
    'invalid': 'invalid',
    'mean': 'rule',
    'total_time': 'rule',
    'memory': 'rule',
    'threads': 'rule',
    'process': 'rule',
    'changed': 'rule',
    'deterministic': 'rule',
}


def _team_of(folder: Path) -> str:
    """Returns the identifier of the registry team whose folder holds a submission, or a name."""
    try:
        parts = folder.resolve().relative_to(_home.folder('teams').resolve()).parts
    except ValueError:
        return 'submission'
    return parts[0] if parts else 'submission'


class Player:
    """One side of a match under strict/1 rules, as the contract hands the format its steps.

    It holds one `Worker` and the rules of `StrictPolicies` for its side, so a side plays under
    the same limits, in the same process and with the same forfeits, each a `formats.Forfeit` of
    the contract's kind with the rule of strict/1 rules as its detail.

    Args:
        folder: The submission's folder.
        match: The side's `v1.Match`, whose `options` are the limits and switches.
        mean_after_first: Whether the mean of a call leaves out the first, true unless a test asks
            for the plain mean.

    Raises:
        Forfeit: `load`, when the submission holds a link, fails to load or loads too slowly.
    """

    def __init__(self, folder: Path, match: Any, mean_after_first: bool = True) -> None:
        from cuju.arena.formats import v1 as formats

        self._formats = formats
        self.limits = dict(match.options)
        self.mean_after_first = mean_after_first
        self.stats = _play.CallStats()
        self.observed = 0
        self.worker: Worker | None = None
        self.load_seconds: float | None = None
        # The step that `send` gave the team and that `receive` reads the answer of.
        self._pending: tuple[Any, bool, float, float] | None = None
        self._team = _team_of(Path(folder))
        self._action_set = match.team_action_set
        linked = links_in(Path(folder))
        if linked:
            label = _label('link', player=self._team, path=linked[0])
            raise self._forfeit('load', label, 'link')
        self.worker = Worker({'id': self._team}, self.limits, False, Path(folder))
        try:
            self._ready()
        except BaseException:
            self.close()
            raise

    def _forfeit(self, kind: str, label: dict[str, Any], detail: str | None = None) -> Any:
        """Returns the forfeit of a rule, the contract's kind with the rule named."""
        found = KINDS[kind]
        if detail is None and kind != found:
            detail = kind
        return self._formats.Forfeit(found, label['text'], detail)

    def _broken(self, found: dict[str, Any], kind: str, label: dict[str, Any]) -> Any:
        rule, reason, detail = self.worker.failure(found, kind, self._team, label)
        return self._forfeit(rule, reason, detail)

    def _ready(self) -> None:
        worker = self.worker
        load = self.limits['load_seconds']
        found = worker.receive(worker.started + load - time.perf_counter())
        if found is not None and found.get('ready') and worker.arrived - worker.started > load:
            # An answer that arrived after the limit came too late all the same.
            found = None
        if found is None:
            label = _label('load_time', player=self._team, count=_seconds(load))
            raise self._forfeit('load', label)
        if found.get('closed') or 'error' in found:
            reason = str(found.get('error') or worker.exit_text())
            raise self._broken(found, 'load', _label('load', player=self._team, reason=reason))
        self.load_seconds = worker.arrived - worker.started
        worker.guard.loading = False

    def reset(self) -> None:
        """Calls the team's reset within its limit."""
        worker = self.worker
        reset = self.limits['reset_seconds']
        asked = time.perf_counter()
        worker.send({'reset': True})
        found = worker.receive(asked + reset - time.perf_counter())
        if found is None:
            label = _label('reset_time', player=self._team, count=_seconds(reset))
            raise self._forfeit('reset', label)
        if found.get('closed') or 'error' in found:
            reason = str(found.get('error') or worker.exit_text())
            raise self._broken(found, 'reset', _label('reset', player=self._team, reason=reason))

    @staticmethod
    def _actions(answer: Any, players: int, translate: bool, size: int) -> list[int]:
        """Returns the actions of an answer, checked as Kaggle checks them, or raises."""
        count = _play.DEFAULT_ACTIONS if translate else size
        values = (
            [action_index(answer, count)]
            if players == 1
            else action_indices(answer, count, players)
        )
        return [value % _play.DEFAULT_ACTIONS for value in values] if translate else values

    def act(self, step: Any) -> list[int] | None:
        """Sends the team its observation and returns its answer, within its limit.

        Raises:
            Forfeit: If the team fails in its call, answers late or answers an invalid action.
        """
        self.send(step)
        return self.receive()

    def send(self, step: Any) -> None:
        """Sends the team its observation, which its process thinks over until `receive`.

        The match host calls `send` for every side before it calls `receive` for any, so the
        two sides of a match under strict/1 think at the same time, each in its own process, and
        each answer counts its time from its own sending.
        """
        self._pending = None
        if not step.used:
            return
        worker = self.worker
        obs = self._formats.kaggle_observation(step.raw, step=self.observed, overage=60.0)
        self.observed += 1
        first = worker.calls == 0
        limit = self.limits['first_call_seconds' if first else 'call_seconds']
        sent = time.perf_counter()
        worker.send({'obs': obs})
        self._pending = (step, first, limit, sent)

    def receive(self) -> list[int] | None:
        """Returns the answer to the step that `send` gave, within its limit, or `None`.

        Raises:
            Forfeit: If the team fails in its call, answers late or answers an invalid action.
        """
        pending, self._pending = self._pending, None
        if pending is None:
            return None
        step, first, limit, sent = pending
        worker, name = self.worker, self._team
        found = worker.receive(sent + limit - time.perf_counter())
        # The answer counts from its sending to its arrival, however long the match took to read it.
        wall = worker.arrived - sent
        if found is not None and 'answer' in found and wall > limit:
            found = None
        if found is None:
            if first:
                label = _label('first_time', player=name, count=_seconds(limit))
            else:
                label = _label(
                    'call_time', player=name, call=worker.calls + 1, count=_seconds(limit)
                )
            raise self._forfeit('timeout', label)
        if found.get('closed') or 'error' in found:
            reason = str(found.get('error') or worker.exit_text())
            if found.get('stage') == 'answer':
                label = _label('invalid_type', player=name, reason=reason)
                raise self._forfeit('invalid', label, 'type')
            raise self._broken(found, 'crash', _label('crash', player=name, reason=reason))
        # A default set answer in a batch of the v2 set is checked against 19 and translated.
        worker.translate = step.action_set != self._action_set
        size = self._formats.ACTION_COUNTS[step.action_set]
        try:
            actions = self._actions(found.get('answer'), len(step.raw), worker.translate, size)
        except (TypeError, ValueError, IndexError) as error:
            # A wrong count of actions, an action of another type or one outside the set.
            detail = {TypeError: 'type', ValueError: 'count', IndexError: 'range'}[type(error)]
            label = _label(f'invalid_{detail}', player=name, reason=_play._reason(error))
            raise self._forfeit('invalid', label, detail) from None
        compute = found.get('compute')
        valid = isinstance(compute, (int, float)) and not isinstance(compute, bool)
        # The time that the process reports for its call counts, within the time it took.
        compute = min(float(compute), wall) if valid and compute >= 0 else wall
        worker.calls += 1
        worker.total += compute
        self.stats.add(compute, actions)
        most = self.limits.get('match_seconds')
        if most is not None and worker.total > most:
            label = _label(
                'total_time', player=name, total=f'{worker.total:.2f}', limit=_seconds(most)
            )
            raise self._forfeit('total_time', label)
        return actions

    def end(self, result: Any) -> Any:
        """Returns the forfeit of a mean compute time of a call above its limit, or `None`."""
        del result
        limit = self.limits['mean_call_ms']
        mean = self.stats.mean(self.mean_after_first) * 1000
        if self.stats.calls and mean > limit:
            label = _label('mean', player=self._team, mean=f'{mean:.1f}', limit=_seconds(limit))
            return self._forfeit('mean', label)
        return None

    def close(self) -> None:
        """Ends the team's process."""
        if self.worker is not None:
            self.worker.close()
            self.stats.memory = self.worker.guard.peak()
            self.worker = None


def folder_bytes(folder: Path) -> int:
    """Returns the bytes of a team's submission, the files that its digest and its copy keep.

    The renderer's folder of the team, `cuju-team`, which holds its kit, its presentation and its
    kept checkpoints, stays out, and so do the caches of Python and links, so the limit holds the
    submission alone, as the competition counts it.
    """
    total = 0
    for root, folders, files in os.walk(folder):
        here = Path(root)
        folders[:] = [name for name in folders if _kept((here / name).relative_to(folder))]
        for name in files:
            path = here / name
            with contextlib.suppress(OSError):
                if not path.is_symlink() and path.is_file() and _kept(path.relative_to(folder)):
                    total += path.stat().st_size
    return total


def _rule(kind: str, reason: dict[str, Any], detail: str | None = None) -> dict[str, Any]:
    return {
        'side': 'home',
        'kind': kind,
        'reason': reason['text'],
        'reason_id': reason.get('id'),
        'reason_values': reason.get('values'),
        'detail': detail,
        'step': 0,
        'at': [0, 0],
    }


def setting_warnings(folder: Path) -> list[dict[str, Any]]:
    """Returns a warning for each setting of a team's file that the renderer does not read.

    The keys inside the manifest's block `kept` are left out, since the file keeps them on purpose.
    """
    path = folder / _teams.TEAM_FOLDER / 'team.json'
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    unknown = _manifest.unread_keys(saved)
    return [_label('warning_setting', setting=key, file='team.json') for key in unknown]


def action_warning(actions: dict[str, int], name: str) -> dict[str, Any] | None:
    """Returns the warning for one action that makes up more than nine in ten of the answers."""
    total = sum(actions.values())
    if not total:
        return None
    action, count = max(actions.items(), key=lambda item: item[1])
    if count > _ONE_ACTION * total:
        share = f'{100 * count / total:.0f}'
        return _label('warning_action', player=name, action=action, share=share)
    return None


def _side_check(
    team: str,
    scenario: str,
    limits: dict[str, Any],
    left: bool,
    replay: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Plays a few steps with the team on one side and returns the rule it broke and its stats.

    `replay`, when given, takes the actions of every step, which the check of determinism
    compares.
    """
    stats: dict[str, Any] = {}
    sides = (team, _teams.BUILTIN) if left else (_teams.BUILTIN, team)
    try:
        _play.play(
            *sides,
            scenario,
            seed=0,
            strict=limits,
            stats=stats,
            steps=PREFLIGHT_STEPS,
            save=False,
            replay=replay,
        )
    except _play.ForfeitError as forfeit:
        rule = dict(forfeit.rule)
        rule['side'] = 'home'
        return rule, forfeit.stats
    except Exception as error:
        ids = _messages.id_fields(error)
        found = {'text': str(error), 'id': ids.get('error_id'), 'values': ids.get('error_values')}
        return _rule('load', found), stats
    return None, stats


def _timings(side: dict[str, Any]) -> dict[str, Any]:
    return {
        'load_seconds': side.get('load_seconds'),
        'reset_seconds': side.get('reset_seconds'),
        'calls': side.get('calls', 0),
        'first_ms': side.get('first_ms'),
        'mean_ms': side.get('mean_ms'),
        'max_ms': side.get('max_ms'),
        'mean_after_first_ms': side.get('mean_after_first_ms'),
    }


def preflight(
    team: str, scenario: str | None = None, limits: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Loads a team and plays a few steps against the built-in AI on each side, under strict/1.

    The team plays once on the left and once on the right, where it sees the match turned, so a
    policy that reads the turned view wrong fails before its tournament. Its submission may hold at
    most the limit `folder_mb` in mebibytes, counted without the renderer's folder `cuju-team` and
    the caches of Python, and no link, which a match would refuse. Warnings, which never fail the
    check, name the
    settings of the team's file that the renderer does not know, and an action that makes up more
    than nine in ten of all the actions the team answered.

    Args:
        team: The team of the registry.
        scenario: The scenario, by default five against five for a team of five a side and the
            Kaggle match for one of eleven.
        limits: The limits of strict/1 rules, or `None` for the defaults of `DEFAULT_LIMITS`.

    Returns:
        Whether the team passed, the rule it broke otherwise with its reason, its timings on the
        left, the seconds to load and to reset, its calls and the first, mean and largest compute
        time of a call in milliseconds with the mean after the first call, the same for each
        side under `sides`, its actions and its warnings.
    """
    entry = _teams.entry(team)
    if scenario is None:
        scenario = '5_vs_5' if entry.get('format') == '5v5' else '11_vs_11_kaggle'
    limits = limits if limits is not None else dict(DEFAULT_LIMITS)
    name = _teams.shown_name(entry)
    rule = None
    sides: dict[str, Any] = {}
    actions: dict[str, int] = {}
    notes: list[dict[str, Any]] = []
    folder = submission_folder(team)
    size = folder_bytes(folder) / MEBIBYTE if entry.get('kind') != 'builtin' else 0.0
    most = limits['folder_mb']
    linked = links(team) if entry.get('kind') != 'builtin' else []
    if linked:
        rule = _rule('load', _label('link', player=name, path=linked[0]), 'link')
    elif size > most:
        reason = _label('folder_size', player=name, size=f'{size:.1f}', limit=_seconds(most))
        rule = _rule('load', reason, 'size')
    else:
        first: dict[str, Any] = {}
        for left in (True, False):
            broken, stats = _side_check(team, scenario, limits, left, first if left else None)
            side = (stats.get('sides') or [None, None])[0 if left else 1] or {}
            sides['left' if left else 'right'] = {
                'ok': broken is None,
                'rule': broken,
                'timings': _timings(side),
            }
            for action, count in (side.get('actions') or {}).items():
                actions[action] = actions.get(action, 0) + count
            if broken is not None:
                rule = broken
                break
        if rule is None:
            # The same steps from the same seed again, whose answers a deterministic team repeats.
            again: dict[str, Any] = {}
            broken, _ = _side_check(team, scenario, limits, True, again)
            if broken is not None:
                rule = broken
            elif again.get('actions') != first.get('actions'):
                reason = _label('deterministic', player=name)
                if limits.get('deterministic'):
                    rule = _rule('deterministic', reason)
                else:
                    notes.append(_label('warning_deterministic', player=name))
    warnings = [*notes, *setting_warnings(_home.folder('teams') / entry['id'])]
    warned = action_warning(actions, name)
    if warned is not None:
        warnings.append(warned)
    return {
        'team': team,
        **_messages.named('name', name),
        'scenario': scenario,
        'ok': rule is None,
        'rule': rule,
        'timings': (sides.get('left') or {}).get('timings') or _timings({}),
        'sides': sides,
        'actions': dict(sorted(actions.items(), key=lambda item: int(item[0]))),
        'folder_mb': round(size, 3),
        'digest': folder_digest(team) if entry.get('kind') != 'builtin' else None,
        'warnings': warnings,
        'limits': dict(limits),
    }


def replay(found: dict[str, Any], device: str = 'cpu') -> dict[str, Any]:
    """Plays a match again from its seed and the actions of each step, as a match kept them.

    Args:
        found: What `play` filled under `replay`, the seed, the scenario file, the agents, the
            action set, the ability of each side and the actions of every step.
        device: `cpu` or `cuda`.

    Returns:
        The steps played and the score of the left and the right side at the end.
    """
    import torch

    from cuju import BatchEnvironment
    from cuju.arena._format import recording as _recording

    ability = tuple(found.get('ability') or (None, None))
    batch = BatchEnvironment(
        found['asset'],
        agent_counts=[tuple(found['agents'])],
        environment_count=1,
        representation='raw',
        seed=int(found['seed']),
        auto_reset=False,
        device=device,
        difficulty=None if ability == (None, None) else [ability],
        action_set=found['action_set'],
    )
    try:
        reader = _recording.StateReader(int(batch.left_players[0] + batch.right_players[0]))
        batch.reset()
        steps = 0
        for row in found['actions']:
            batch.step(torch.tensor([row], dtype=torch.int32))
            steps += 1
        score = _play.frame_score(reader.frame(batch.get_state(0)))
    finally:
        batch.close()
    return {'steps': steps, 'score': score}
