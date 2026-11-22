# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The checks of a team under a format, and the verdicts that a team keeps of them.

A check runs when a person asks for it, when a format is pinned, or at the start of a context, and
what it found is kept in `<home>/verdicts/<team>.json`. A verdict is kept under a key of the
checkpoint, the format's identity, every option including those that reach only the check, the
scenario or the size's own, the control, `full`, the renderer's version and whether a GPU is
visible, as `identity.verdict_key` builds it. Any change of those reads as a format that was never
checked. A refusal that belongs to the machine, a missing package, no GPU, a load or timing
problem or a ceiling, and a check that `--max-steps` cut, are never kept, so the next start checks
again.

    subject = verdicts.subject_of_team('t-abc')
    outcome = verdicts.check(subject, side.choose('strict/1'), isolate=True)
    outcome.verdict.accepted, outcome.kept

`subject_of_folder` judges a folder that no team owns, which `cuju arena check` does and which
keeps nothing. Run as `python -m cuju.arena._formats.verdicts`, the module is the process of an
isolated check, which reads one request as JSON on standard input and reports one line of JSON.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .._store import home as _home
from .._text import messages as _messages
from .._text.messages import MessageError
from .._version import __version__
from ..formats import v1 as _v1
from . import checks as _checks
from . import gpu as _gpu
from . import host as _host
from . import identity as _identity
from . import registry as _registry
from . import side as _side
from . import terminals as _terminals

FOLDER = 'verdicts'
VERSION = 1
# The kind of the problem that a cut check reports, and the detail that names its cause.
CUT = 'cut'
# The reasons a verdict is not kept.
ENVIRONMENT = 'environment'
TIMING = 'timing'
CEILING = 'ceiling'
CUT_BY_STEPS = 'cut'
STATES = ('not_checked', 'accepted', 'refused')
# Seconds that an isolated check may run beyond the format's own check time.
_GRACE_SECONDS = 120
_LOCK = threading.RLock()


@dataclasses.dataclass(frozen=True)
class Subject:
    """What a check judges, a team at a checkpoint or a folder.

    Attributes:
        name: The name that the check gives the team.
        size: `5v5` or `11v11`.
        control: The control the check runs with, as the registry checks it.
        checkpoint: The identifier of the files, the front of their digest.
        folder: The folder whose root holds the submission.
        team: The team of the registry that keeps the verdicts, or `None` for a folder.
    """

    name: str
    size: str
    control: dict[str, Any]
    checkpoint: str | None
    folder: Path
    team: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Returns the subject as JSON."""
        return {**dataclasses.asdict(self), 'folder': str(self.folder)}

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> Subject:
        """Returns the subject that `as_dict` gave."""
        return cls(**{**document, 'folder': Path(document['folder'])})


def subject_of_team(entrant: str) -> Subject:
    """Returns the subject of a team of the registry, or of an entrant `team@checkpoint`.

    Raises:
        ArgumentError: If the team or the checkpoint is unknown, or the team has no files.
    """
    from .._registry import checkpoints as _checkpoints
    from .._registry import teams as _teams

    found = _checkpoints.resolve(entrant)
    entry = _teams.entry(found['team'])
    if entry['kind'] != 'kaggle' or found['folder'] is None:
        raise MessageError('server.verdicts.no_files', team=repr(entrant))
    size = entry['format']
    control = _teams.check_control(found['control'] or entry.get('control'), size)
    return Subject(
        name=str(entry.get('name') or found['team']),
        size=size,
        control=control,
        checkpoint=found['checkpoint'],
        folder=Path(found['folder']),
        team=found['team'],
    )


def subject_of_folder(
    path: str | Path, scenario: str | None = None, size: str | None = None
) -> Subject:
    """Returns the subject of a folder that no team owns, nothing of which is registered.

    The size is the one given, else the scenario's, else the `cuju-team` folder's team file's,
    else `11v11`, and the control and name come from that file too.

    Raises:
        ArgumentError: If the path is no folder, or the control of its team file does not fit.
    """
    from .._registry import checkpoints as _checkpoints
    from .._registry import teams as _teams
    from .._store import runs as _runs

    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        raise MessageError('server.verdicts.folder', folder=repr(str(path)))
    reading = _teams._read_team(folder / _teams.TEAM_FOLDER / 'team.json')
    written = {} if reading is None or reading.get('unreadable') else reading['team']
    held = written.get('format') if written.get('format') in _teams.FORMATS else '11v11'
    players = None if scenario is None else _teams.scenario_size(scenario)
    size = size or (held if players is None else f'{players}v{players}')
    if size not in _teams.FORMATS:
        raise MessageError('server.verdicts.size', size=repr(size))
    control = _teams.check_control(written.get('control'), size)
    return Subject(
        name=str(written.get('name') or folder.name),
        size=size,
        control=control,
        checkpoint=_runs.files_digest(folder, (_teams.TEAM_FOLDER,))[: _checkpoints.ID_LENGTH],
        folder=folder,
    )


def team_of(subject: Subject, choice: _side.Choice, scenario: str | None, full: bool) -> _v1.Team:
    """Returns the `v1.Team` that a pre-check of a subject under a choice judges."""
    return _v1.Team(
        name=subject.name,
        size=subject.size,
        scenario=scenario,
        size_default_scenario=_checks.SIZE_SCENARIOS.get(subject.size, '11_vs_11_kaggle'),
        control=_v1.Control.from_dict(subject.control),
        checkpoint=subject.checkpoint,
        options=choice.values,
        full=bool(full),
    )


def key_of(subject: Subject, choice: _side.Choice, scenario: str | None, full: bool) -> str:
    """Returns the key that a verdict of a subject under a choice is kept under."""
    return _identity.verdict_key(
        subject.checkpoint,
        choice.identity(),
        choice.values,
        scenario,
        subject.size,
        subject.control,
        full,
        __version__,
        bool(_gpu.devices()),
    )


def unkept(verdict: _v1.Verdict | None, cut: bool = False) -> str | None:
    """Returns why a verdict is not kept, or `None` when it is.

    A refusal of the machine, a missing package, no GPU, a ceiling or a check that ran out of
    time, and a check that a ceiling on its steps cut, give a different verdict on another day.
    """
    if verdict is None:
        return ENVIRONMENT
    if cut:
        return CUT_BY_STEPS
    if verdict.environment:
        return ENVIRONMENT
    for found in verdict.problems:
        if found.level != 'error':
            continue
        if (found.detail or '').startswith('ceiling'):
            return CEILING
        if found.kind == 'timeout':
            return TIMING
    return None


@dataclasses.dataclass
class Outcome:
    """What one check found.

    Attributes:
        format: The format's label, such as `kaggle/1`.
        key: The key the verdict is kept under.
        options: Every option the check ran with.
        preset: The preset the choice named, or `None`.
        scenario: The scenario of the check, or `None` for the size's own.
        full: Whether it was the full check.
        max_steps: The ceiling on the steps of its check matches, or `None`.
        checked: When it ran, as local time to the second.
        verdict: The verdict, or `None` where the format failed.
        failed: What the format's own code failed with, or `None`.
        cut: Whether the ceiling on the steps cut a check match.
        terminal: The terminal of the check, `{run, side}`, or `None`.
        subject: What was judged.
        stored: Whether the verdict was read from the store, not run.
    """

    format: str
    key: str
    options: dict[str, Any]
    preset: str | None
    scenario: str | None
    full: bool
    max_steps: int | None
    checked: str
    verdict: _v1.Verdict | None
    failed: str | None
    cut: bool
    terminal: dict[str, str] | None
    subject: Subject
    stored: bool = False

    @property
    def unkept(self) -> str | None:
        """Why the verdict is not kept, or `None` when it is."""
        return unkept(self.verdict, self.cut)

    def as_dict(self) -> dict[str, Any]:
        """Returns the outcome as JSON, for the isolated process and the store."""
        return {
            'format': self.format,
            'key': self.key,
            'options': self.options,
            'preset': self.preset,
            'scenario': self.scenario,
            'full': self.full,
            'max_steps': self.max_steps,
            'checked': self.checked,
            'verdict': None if self.verdict is None else self.verdict.as_dict(),
            'failed': self.failed,
            'cut': self.cut,
            'terminal': self.terminal,
            'subject': self.subject.as_dict(),
        }

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> Outcome:
        """Returns the outcome that `as_dict` gave."""
        found = dict(document)
        verdict = found.pop('verdict')
        subject = Subject.from_dict(found.pop('subject'))
        return cls(
            verdict=None if verdict is None else _v1.Verdict.from_dict(verdict),
            subject=subject,
            **found,
        )

    def row(self) -> dict[str, Any]:
        """Returns the outcome as a row of the Submission section, see `row_of`."""
        return row_of(
            _registry.get(self.format),
            self.preset,
            self.options,
            self.key,
            self.scenario,
            self.full,
            self,
        )


def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%S')


def check_scenario(scenario: Any) -> str | None:
    """Returns a scenario of the check, `None` for the size's own.

    Raises:
        ArgumentError: If the scenario is no shipped scenario.
    """
    if scenario in (None, ''):
        return None
    from cuju import bundled_scenarios

    from .._registry import teams as _teams

    if not isinstance(scenario, str) or scenario not in {
        *bundled_scenarios(),
        *_teams.renderer_scenarios(),
    }:
        raise MessageError('server.scenario', scenario=repr(scenario))
    return scenario


def limit_steps(steps: Any) -> int | None:
    """Returns the ceiling on the steps of a check, `None` for none.

    Raises:
        ArgumentError: If it is not a whole number from 1.
    """
    if steps in (None, ''):
        return None
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
        raise MessageError('server.verdicts.max_steps', steps=repr(steps))
    return steps


def run(
    subject: Subject,
    choice: _side.Choice,
    scenario: str | None = None,
    full: bool = False,
    max_steps: int | None = None,
    terminal: str | None = None,
) -> Outcome:
    """Runs a format's pre-check of a subject in this process and returns what it found.

    A built-in format that plays in the match process loads the submission here, so a server
    calls `check` with `isolate=True` instead.

    Args:
        subject: What to judge.
        choice: The format and its options, as `side.choose` gives them.
        scenario: The scenario, or `None` for the size's own.
        full: Whether to ask for the full check.
        max_steps: The most steps of any check match, or `None`. A match that this cuts before
            its end makes the check refuse as cut.
        terminal: The name of the run that keeps the checker's output, or `None` for none.
    """
    format = choice.format
    team = team_of(subject, choice, scenario, full)
    key = key_of(subject, choice, scenario, full)
    verdict = None
    failed = None
    cap = None
    manager = _terminals.run(terminal) if terminal else contextlib.nullcontext()
    try:
        with manager, _checks.capped(max_steps) as cap:
            verdict = _checks.check(format, choice.values, subject.folder, team)
    except _host.FormatFailure as error:
        failed = str(error)
        _registry.mark_failing(format.id, failed)
    cut = bool(cap is not None and cap.cut)
    if verdict is not None and cut:
        found = _v1.problem(
            CUT,
            f'the check was cut at {max_steps} steps, so it is not a pass',
            'error',
            'max_steps',
        )
        verdict = _v1.Verdict(
            verdict.players,
            verdict.name,
            verdict.deterministic,
            (*verdict.problems, found),
        )
    return Outcome(
        format=format.label,
        key=key,
        options=dict(choice.values),
        preset=choice.preset,
        scenario=scenario,
        full=bool(full),
        max_steps=max_steps,
        checked=_now(),
        verdict=verdict,
        failed=failed,
        cut=cut,
        terminal=None if not terminal else {'run': terminal, 'side': 'check'},
        subject=subject,
    )


def terminal_name(subject: Subject, choice: _side.Choice) -> str:
    """Returns the name of the run that holds the terminal of a check."""
    return _terminals.check_run(subject.team or 'folder', subject.checkpoint, choice.format.label)


def check(
    subject: Subject,
    choice: _side.Choice,
    scenario: str | None = None,
    full: bool = False,
    max_steps: int | None = None,
    isolate: bool = False,
    keep: bool = True,
) -> Outcome:
    """Checks a subject, keeps the verdict of a team where it may be kept, and returns the outcome.

    Each isolated check keeps its own terminal, so concurrent checks can report independently.

    Args:
        subject: What to judge.
        choice: The format and its options.
        scenario: The scenario, or `None` for the size's own.
        full: Whether to ask for the full check.
        max_steps: The most steps of any check match, or `None`.
        isolate: Whether to run the check in a process of its own, as a server does.
        keep: Whether to keep the verdict, which a folder's check never does.

    Raises:
        ArgumentError: If an option or a scenario does not fit, or an isolated check fails.
    """
    if isolate:
        name = _home.new_identifier('check')
        outcome = _isolated(subject, choice, scenario, full, max_steps, name)
    else:
        name = terminal_name(subject, choice)
        _terminals.remove(name)
        outcome = run(subject, choice, scenario, full, max_steps, name)
    if keep and subject.team is not None and outcome.failed is None:
        if outcome.unkept is None:
            store(subject.team, outcome)
        else:
            forget(subject.team, outcome.key)
    return outcome


def ensure(
    subject: Subject,
    choice: _side.Choice,
    scenario: str | None = None,
    full: bool = False,
    isolate: bool = True,
) -> Outcome:
    """Returns the kept verdict of a team where there is one, else checks, as a context's start.

    A verdict that was never kept, a refusal of the machine or of time, is checked again here.
    """
    if subject.team is not None:
        key = key_of(subject, choice, scenario, full)
        found = lookup(subject.team, key, subject)
        if found is not None:
            return found
    return check(subject, choice, scenario, full, isolate=isolate)


def _isolated(
    subject: Subject,
    choice: _side.Choice,
    scenario: str | None,
    full: bool,
    max_steps: int | None,
    terminal: str,
) -> Outcome:
    """Runs a check in a process of its own, as the pre-flight of a tournament does."""
    request = {
        'subject': subject.as_dict(),
        'choice': choice.as_dict(),
        'scenario': scenario,
        'full': bool(full),
        'max_steps': max_steps,
        'terminal': terminal,
    }
    seconds = float(choice.format.spec.process.check_minutes) * 60.0 + _GRACE_SECONDS
    command = [*_home.python_command(), '-m', 'cuju.arena._formats.verdicts']
    try:
        done = subprocess.run(
            command,
            input=json.dumps(request),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            cwd=_home.process_folder(),
            env=_home.process_environment(),
            timeout=seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise MessageError('server.preflight.timeout', count=int(seconds // 60)) from error
    report = None
    for line in done.stdout.splitlines():
        with contextlib.suppress(ValueError):
            found = json.loads(line)
            report = found if isinstance(found, dict) else report
    if report is None:
        found = _messages.exit_fields('server.requests.exit', done.returncode, done.stderr.strip())
        raise MessageError('server.verdicts.process', reason=found['error'])
    if 'error' in report and 'format' not in report:
        reported = _messages.report_fields(report)
        if reported['error_id'] is not None:
            raise MessageError(reported['error_id'], **reported['error_values'])
        raise MessageError('server.verdicts.process', reason=reported['error'])
    return Outcome.from_dict(report)


# The store, one file for each team.


def _path(team: str) -> Path:
    return _home.folder(FOLDER) / f'{team}.json'


def _document(team: str) -> dict[str, Any]:
    try:
        document = json.loads(_path(team).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(document, dict) or document.get('version') != VERSION:
        return {}
    return document


def _read(team: str) -> list[dict[str, Any]]:
    found = _document(team).get('verdicts')
    if not isinstance(found, list):
        return []
    return [item for item in found if isinstance(item, dict) and isinstance(item.get('key'), str)]


def _write(team: str, entries: list[dict[str, Any]], added: Any = None) -> None:
    """Writes the verdicts of a team, keeping the format it was added under."""
    path = _path(team)
    if added is None:
        added = _document(team).get('added_under')
    if not entries and added is None:
        path.unlink(missing_ok=True)
        return
    document: dict[str, Any] = {'version': VERSION, 'verdicts': entries}
    if added is not None:
        document['added_under'] = added
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.part')
    temporary.write_text(json.dumps(document, indent=1), encoding='utf-8')
    os.replace(temporary, path)


def remember_upload(team: str, submission_format: Any) -> None:
    """Keeps the format a team was added under, which the check of each later upload uses.

    A team added under no format keeps nothing, and its uploads are checked under Kaggle open.

    Raises:
        ArgumentError: If the format is unknown or broken, or an option does not fit.
    """
    if submission_format is None or submission_format == '':
        return
    found = _side.choose(submission_format).as_dict()
    with _LOCK:
        _write(team, _read(team), found)


def added_under(team: str) -> dict[str, Any] | None:
    """Returns the format a team was added under, as `remember_upload` kept it, or `None`."""
    with _LOCK:
        found = _document(team).get('added_under')
    return found if isinstance(found, dict) and isinstance(found.get('id'), str) else None


def _most() -> int:
    from .._config import layers as _layers

    return max(1, int(_layers.table('formats')['kept_verdicts']))


def store(team: str, outcome: Outcome) -> None:
    """Keeps the verdict of an outcome for a team, the newest of the most that the setting keeps.

    A verdict of the same key is replaced. Past `[formats] kept_verdicts` the oldest go.
    """
    entry = outcome.as_dict()
    entry.pop('subject')
    entry['team'] = team
    with _LOCK:
        entries = [item for item in _read(team) if item['key'] != outcome.key]
        entries.append(entry)
        entries.sort(key=lambda item: str(item.get('checked')))
        _write(team, entries[-_most() :])


def forget(team: str, key: str | None = None) -> None:
    """Drops the verdict of one key of a team, or every verdict of the team."""
    with _LOCK:
        if key is None:
            _path(team).unlink(missing_ok=True)
            return
        entries = _read(team)
        kept = [item for item in entries if item['key'] != key]
        if len(kept) != len(entries):
            _write(team, kept)


def lookup(team: str, key: str, subject: Subject | None = None) -> Outcome | None:
    """Returns the verdict that a team kept under a key, or `None` for none."""
    with _LOCK:
        entries = _read(team)
    for item in entries:
        if item['key'] == key:
            return _from_entry(item, subject)
    return None


def _from_entry(item: dict[str, Any], subject: Subject | None) -> Outcome:
    verdict = item.get('verdict')
    return Outcome(
        format=str(item.get('format')),
        key=item['key'],
        options=dict(item.get('options') or {}),
        preset=item.get('preset'),
        scenario=item.get('scenario'),
        full=bool(item.get('full')),
        max_steps=item.get('max_steps'),
        checked=str(item.get('checked')),
        verdict=None if verdict is None else _v1.Verdict.from_dict(verdict),
        failed=None,
        cut=False,
        terminal=item.get('terminal'),
        subject=subject
        or Subject('', '11v11', {}, None, Path('.'), item.get('team')),  # a row needs no more
        stored=True,
    )


# The rows of the Submission section.


def row_of(
    format: _registry.Format,
    preset: str | None,
    options: dict[str, Any],
    key: str,
    scenario: str | None,
    full: bool,
    outcome: Outcome | None,
) -> dict[str, Any]:
    """Returns one format, as the Submission section lists it, with its verdict if it has one.

    `state` is `not_checked` before the first check, then `accepted` or `refused`. `kept` says
    whether the verdict stays for the next visit, and `unkept` names why it does not, one of
    `environment`, `timing`, `ceiling` and `cut`. `failed` holds the message of a format whose own
    code failed, and `failing` what the registry knows of a format that failed before.
    """
    verdict = None if outcome is None else outcome.verdict
    reason = None if outcome is None else outcome.unkept
    if verdict is None:
        state = 'not_checked'
    else:
        state = 'accepted' if verdict.accepted else 'refused'
    return {
        'format': format.label,
        'id': format.id,
        'name': format.name,
        'builtin': format.builtin,
        'preset': preset,
        'state': state,
        'accepted': None if verdict is None else verdict.accepted,
        'checked': None if outcome is None else outcome.checked,
        'kept': outcome is not None and verdict is not None and reason is None,
        'unkept': reason if verdict is not None else None,
        'key': key,
        'options': options,
        'scenario': scenario,
        'full': bool(full),
        'players': None if verdict is None else verdict.players,
        'stated_name': None if verdict is None else verdict.name,
        'deterministic': None if verdict is None else verdict.deterministic,
        'problems': [] if verdict is None else [found.as_dict() for found in verdict.problems],
        'terminal': None if outcome is None else outcome.terminal,
        'failed': None if outcome is None else outcome.failed,
        'failing': _registry.failing(format.id),
        'cut': bool(outcome is not None and outcome.cut),
        'max_steps': None if outcome is None else outcome.max_steps,
    }


def _choices(format: _registry.Format) -> list[_side.Choice]:
    """Returns the choices a format shows, its own options and one for each preset."""
    found = [_side.choose({'id': format.label})]
    for preset in format.spec.presets:
        found.append(_side.choose({'id': format.label, 'preset': preset}))
    return found


def listing(
    team: str,
    checkpoint: str | None = None,
    scenario: Any = None,
    full: bool = False,
) -> dict[str, Any]:
    """Returns every format with the verdict a team kept under it, for the playing checkpoint.

    Each format shows its own options and one row for each of its presets, `kaggle/1` and Kaggle
    open for one. A row that was never checked, or whose key changed since, reads `not_checked`.

    Args:
        team: The team of the registry.
        checkpoint: The checkpoint, by default the one that plays.
        scenario: The scenario of the checks, by default the size's own.
        full: Whether to read the full check.

    Returns:
        `{team, checkpoint, scenario, full, formats, accepting, checked, total}`, where
        `accepting` counts the formats with an accepted row, which the Formats tile shows.
    """
    from .._registry import checkpoints as _checkpoints

    scenario = check_scenario(scenario)
    subject = subject_of_team(_checkpoints.entrant(team, checkpoint) if checkpoint else team)
    with _LOCK:
        entries = {item['key']: item for item in _read(team)}
    rows = []
    for format in _registry.formats():
        if format.spec is None:
            continue
        for choice in _choices(format):
            key = key_of(subject, choice, scenario, full)
            item = entries.get(key)
            found = None if item is None else _from_entry(item, subject)
            rows.append(row_of(format, choice.preset, choice.values, key, scenario, full, found))
    accepting = {row['id'] for row in rows if row['state'] == 'accepted'}
    return {
        'team': team,
        'checkpoint': subject.checkpoint,
        'scenario': scenario,
        'full': bool(full),
        'formats': rows,
        'accepting': len(accepting),
        'checked': sum(1 for row in rows if row['state'] != 'not_checked'),
        'total': len(rows),
    }


def choice_of(document: dict[str, Any]) -> _side.Choice:
    """Returns the format that a request names, Kaggle open when it names none.

    The request holds `submission_format`, a format's identifier or `{id, preset, options}`, and
    may hold `options` and `preset` beside an identifier.

    Raises:
        ArgumentError: If the format is unknown or broken, or an option or preset does not fit.
    """
    value = document.get('submission_format') or {
        'id': _identity.DEFAULT['format'],
        'preset': _identity.DEFAULT['preset'],
    }
    if isinstance(value, str):
        value = {'id': value}
    if not isinstance(value, dict):
        raise MessageError('server.formats.choice', choice=repr(value))
    found = {**value, **{key: document[key] for key in ('options', 'preset') if document.get(key)}}
    return _side.choose(found)


def at_upload(team: str, submission_format: Any = None) -> threading.Thread:
    """Starts the check of an upload, a new team or a new checkpoint of one.

    The upload is checked under the format the team was added with, which `submission_format`
    names for a new team and which the team keeps for its later checkpoints. A team added under no
    format is checked under Kaggle open, the cheap load that an upload implies. The check runs in
    a thread of its own, in a process of its own, so an upload answers at once, and a failure of
    the check leaves the team as it is. The team reads `not checked` until it ends.

    Args:
        team: The team of the registry.
        submission_format: The format the team is added under, which it then keeps, or `None`
            for the format it was added under before, if any.
    """
    if submission_format is not None and submission_format != '':
        with contextlib.suppress(Exception):
            remember_upload(team, submission_format)

    def work() -> None:
        with contextlib.suppress(Exception):
            chosen = added_under(team)
            check(subject_of_team(team), choice_of({'submission_format': chosen}), isolate=True)

    thread = threading.Thread(target=work, name=f'verdict-{team}', daemon=True)
    thread.start()
    return thread


# The process of an isolated check.


def main() -> None:
    """Runs the check that one JSON request on standard input asks and reports one line of JSON."""
    sys.stdout.flush()
    reports = os.fdopen(os.dup(1), 'w', encoding='utf-8')
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    try:
        request = json.loads(sys.stdin.read())
        subject = Subject.from_dict(request['subject'])
        choice = _side.choose(request['choice'])
        outcome = run(
            subject,
            choice,
            request.get('scenario'),
            bool(request.get('full')),
            request.get('max_steps'),
            request.get('terminal'),
        )
        report = outcome.as_dict()
    except Exception as error:
        report = {'error': _messages.described(error), **_messages.id_fields(error)}
    reports.write(json.dumps(report) + '\n')
    reports.flush()


if __name__ == '__main__':
    # The check runs from the module as the package imports it, as a match does.
    from cuju.arena._formats import verdicts as _module

    _module.main()
