# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Match workers, a process for each scheduling slot of a run, which keep a pool across matches.

A rating run, an evaluation and a tournament play each match of teams in a child process. Under
a format of `[process] scope = "run"` a side's process should live across the matches of the run,
so its module, and a model it loads at module level, loads once. A child that plays one match
cannot keep it, so such a match goes to a match worker instead: a child process that plays one
match after another, as its parent hands them over, under one `pool.Pool` for its whole life.

The parent keeps a `Workers` for the run. Each scheduling slot, the thread that plays one match
at a time, leases a worker for a match and gives it back after it, so a run never holds more
workers than it plays matches at once. A worker whose process ended, by a ceiling, a crash, a kill
or the end of its match time, is closed and the next match of that slot starts a fresh one.

    workers = worker.Workers(command, environment, folder)
    with workers.lease() as found:
        reports, code = found.play(spec, seconds=MATCH_SECONDS)
    workers.close()

The protocol is one line of JSON for each match on the worker's standard input, the request,
and on its channel of reports the same lines that a child of one match writes, followed by a
line `{"end": true, "pids": [...]}` that names the processes of its pool. The worker ends when
its standard input ends or its parent goes away, which it finds as its next report fails, and
its pool closes every process it holds on the way out. A format process whose parent ends reads
the end of its pipe and exits by itself, so a worker that was killed leaves nothing behind.

Every other match, Kaggle open and every format of `scope = "match"`, keeps a fresh child of
its own, as `wanted` decides.
"""

from __future__ import annotations

import collections
import contextlib
import json
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TextIO

from . import ceilings as _ceilings

# The key of the line that ends the reports of one match.
END = 'end'
# The lines of standard error that a worker keeps, for the last words and the tail of a failure.
_TAIL = 40
# Seconds a worker may take to end once its standard input has ended.
_CLOSE_SECONDS = 10.0


def wanted(choices: Any) -> bool:
    """Returns whether a match plays a side whose format keeps its process for the run.

    Args:
        choices: The format of the match, one choice for both sides or a pair, as a context
            hands it to `play.chosen_formats`. A side without one plays Kaggle open, which plays
            in the match process and keeps nothing.
    """
    from . import side as _side

    pair = list(choices) if isinstance(choices, list | tuple) else [choices, choices]
    for value in pair:
        if value is None or value == '':
            continue
        try:
            process = _side.choose(value).format.spec.process
        except Exception:
            # A choice that does not read fails in the match itself, with its own message.
            continue
        if process.scope == 'run' and process.host != 'match':
            return True
    return False


class Worker:
    """One match worker, the child process that plays the matches of one slot in turn.

    Attributes:
        process: The `subprocess.Popen` of the worker.
        stderr: The last lines of its standard error.
        pids: The processes of its pool after its last match, as it reported them.
        matches: The matches it has played.
    """

    def __init__(self, command: list[str], environment: dict[str, str] | None, folder: Any):
        # A ceiling of a side in the worker ends it with last words that name the side.
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            cwd=None if folder is None else str(folder),
            env=_ceilings.match_environment(environment),
        )
        self.stderr: collections.deque[str] = collections.deque(maxlen=_TAIL)
        self.pids: list[int] = []
        self.matches = 0
        self._broken = False
        self._reader = threading.Thread(target=self._read_errors, daemon=True)
        self._reader.start()

    def _read_errors(self) -> None:
        with contextlib.suppress(OSError, ValueError):
            for line in self.process.stderr:
                self.stderr.append(line)

    def alive(self) -> bool:
        """Returns whether the worker runs and may play another match."""
        return not self._broken and self.process.poll() is None

    def play(
        self,
        request: Any,
        on_report: Callable[[dict[str, Any]], None] | None = None,
        seconds: float | None = None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Hands the worker one match and reads its reports until the match ends.

        Args:
            request: The match, a value of plain JSON as the worker's entry reads it.
            on_report: A function called with each report as it comes, such as its progress.
            seconds: The seconds the match may take, after which the worker is killed.

        Returns:
            The reports of the match, without the line that ends them, and `None` while the
            worker lives on, or the exit code of a worker that ended during the match, after
            which the tail of its standard error is complete.
        """
        timer = None
        if seconds is not None:
            timer = threading.Timer(seconds, self.kill)
            timer.daemon = True
            timer.start()
        reports: list[dict[str, Any]] = []
        try:
            try:
                self.process.stdin.write(json.dumps(request) + '\n')
                self.process.stdin.flush()
            except (OSError, ValueError):
                self._broken = True
            if not self._broken:
                for line in self.process.stdout:
                    try:
                        report = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(report, dict):
                        continue
                    if report.get(END) is True:
                        pids = report.get('pids')
                        self.pids = (
                            [p for p in pids if isinstance(p, int)]
                            if isinstance(pids, list)
                            else []
                        )
                        self.matches += 1
                        return reports, None
                    reports.append(report)
                    if on_report is not None:
                        on_report(report)
            self._broken = True
            code = self.process.wait()
            self._reader.join(5)
            return reports, code
        finally:
            if timer is not None:
                timer.cancel()

    def kill(self) -> None:
        """Ends the worker at once, and with it every process of its pool."""
        self._broken = True
        with contextlib.suppress(OSError):
            self.process.kill()

    def close(self) -> None:
        """Ends the worker's input, so it closes its pool and exits, and kills it if it lingers."""
        with contextlib.suppress(OSError, ValueError):
            self.process.stdin.close()
        try:
            self.process.wait(_CLOSE_SECONDS)
        except subprocess.TimeoutExpired:
            self.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(_CLOSE_SECONDS)
        with contextlib.suppress(OSError, ValueError):
            self.process.stdout.close()


class Workers:
    """The match workers of one run, at most one for each match it plays at once."""

    def __init__(
        self,
        command: list[str],
        environment: dict[str, str] | None = None,
        folder: Path | str | None = None,
    ) -> None:
        """Holds the workers of a run, started on their first lease.

        Args:
            command: The command of a worker.
            environment: Its environment, to which the mark of a match process is added.
            folder: Its working folder.
        """
        self._command = list(command)
        self._environment = environment
        self._folder = folder
        self._lock = threading.Lock()
        self._idle: list[Worker] = []
        self._all: set[Worker] = set()
        self._closed = False

    @contextlib.contextmanager
    def lease(self) -> Iterator[Worker]:
        """Lends a worker of the run for one match, kept for the next where it lived through it.

        Raises:
            RuntimeError: If the run has ended.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError('the run of these workers has ended')
            found = None
            while self._idle and found is None:
                held = self._idle.pop()
                if held.alive():
                    found = held
                else:
                    self._all.discard(held)
            if found is None:
                found = Worker(self._command, self._environment, self._folder)
                self._all.add(found)
        try:
            yield found
        finally:
            with self._lock:
                keep = found.alive() and not self._closed
                if keep:
                    self._idle.append(found)
                else:
                    self._all.discard(found)
            if not keep:
                found.close()

    def processes(self) -> list[subprocess.Popen]:
        """Returns the process of every worker of the run, in use or kept."""
        with self._lock:
            return [found.process for found in self._all]

    def __len__(self) -> int:
        with self._lock:
            return len(self._all)

    def kill(self) -> None:
        """Ends every worker at once, as a cancel or a stop of the run does."""
        with self._lock:
            self._closed = True
            found = list(self._all)
            self._idle.clear()
        for worker in found:
            worker.kill()
        for worker in found:
            worker.close()

    def close(self) -> None:
        """Ends the run. A kept worker ends now and one in use as its match ends."""
        with self._lock:
            self._closed = True
            idle, self._idle = self._idle, []
            for worker in idle:
                self._all.discard(worker)
        for worker in idle:
            worker.close()


# The worker's own side.


def serve(
    play: Callable[[Any, Any], None], reports: TextIO, requests: TextIO | None = None
) -> None:
    """Plays the matches that arrive on standard input, one at a time, under one pool.

    Args:
        play: Plays one match, given its request and the pool, and writes its reports. It
            reports a failure of its match itself and raises only to end the worker.
        reports: The channel of the reports.
        requests: Where the requests arrive, standard input by default.
    """
    from . import pool as _pool

    stream = sys.stdin if requests is None else requests
    with _pool.Pool() as run:
        for line in stream:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
            except ValueError:
                request = None
            try:
                if request is not None:
                    play(request, run)
                reports.write(json.dumps({END: True, 'pids': run.pids()}) + '\n')
                reports.flush()
            except OSError:
                # The parent has gone, so the worker ends, and its pool with it.
                return
