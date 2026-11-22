# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""`format.toml`, the file that puts a folder under a contract, read and checked.

The file names the contract, the format's name and its script, and optionally its version, a
summary, the versions of cuju-render it needs, its process, its results, its options and their
presets. `read(folder)` returns a `Spec` or raises `ContractError` with one line that says what
does not fit, and `options(spec, values, preset)` resolves a context's choice of options.

The renderer's own ceilings bound what a file may declare: a match of at most 360 minutes, a
check of at most 120, at most 64 threads and 65,536 MiB, and a text option of at most 200
characters. A format may tighten them and never loosen them.
"""

from __future__ import annotations

import dataclasses
import math
import re
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover, Python 3.10
    import tomli as tomllib

# The file of a format's folder, and the contracts this renderer reads.
FILE = 'format.toml'
CONTRACTS = (1,)
# The ceilings of what a format declares, design v6 11.
MATCH_MINUTES = (60, 360)
CHECK_MINUTES = (15, 120)
THREADS = 64
MEMORY_MIB = 65536
TEXT_LENGTH = 200
STEP_SECONDS = 300
# The keys of each table of the file.
_TOP = {
    'contract',
    'name',
    'script',
    'version',
    'summary',
    'requires',
    'process',
    'results',
    'options',
    'presets',
}
_PROCESS = {
    'host',
    'scope',
    'threads',
    'gpu',
    'memory_mib',
    'match_minutes',
    'check_minutes',
    'env',
}
_RESULTS = {'forfeit_score', 'evidence'}
_OPTION = {
    'label',
    'type',
    'default',
    'range',
    'choices',
    'unit',
    'bounds',
    'reaches',
    'per_entrant',
    'max_length',
}
TYPES = ('bool', 'int', 'float', 'choices', 'text')
_KEY = re.compile(r'[a-z][a-z0-9_]{0,39}')
_VARIABLE = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}')
_REQUIREMENT = re.compile(r'\s*(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)\s*')


class ContractError(ValueError):
    """Raised for a `format.toml` that is missing, does not read, or holds what does not fit."""


@dataclasses.dataclass(frozen=True)
class Option:
    """One option of a format, which a context may set.

    Attributes:
        key: Its name in the file.
        type: `bool`, `int`, `float`, `choices` or `text`.
        default: Its value where a context sets none.
        label: What the menu calls it.
        range: The lowest and highest number, for `int` and `float`, or `None`.
        choices: The values of `choices`.
        unit: The unit the menu shows beside a number, or `None`.
        bounds: `step` for a limit on each step, `match` for one over the match, or `None`.
        reaches: `play`, which reaches `Match.options` and joins the format's identity, or
            `check`, which only `accept` reads.
        per_entrant: Whether the option may differ for each entrant of a tournament or a run.
        max_length: The longest text of `text`.
    """

    key: str
    type: str
    default: Any
    label: str
    range: tuple[float, float] | None = None
    choices: tuple[Any, ...] = ()
    unit: str | None = None
    bounds: str | None = None
    reaches: str = 'play'
    per_entrant: bool = False
    max_length: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """Returns the option as JSON."""
        found = dataclasses.asdict(self)
        found['range'] = None if self.range is None else list(self.range)
        found['choices'] = list(self.choices)
        return found


@dataclasses.dataclass(frozen=True)
class Process:
    """What the renderer sets when it starts a format's process, and where it plays.

    Attributes:
        host: `format`, a process of its own for each side, or `match`, the match process, which
            only a built-in format may ask for.
        scope: `match`, a fresh process for every match, or `run`, one for the consecutive
            matches of a run with the same identity.
        threads: The threads it may use.
        gpu: `never`, every GPU hidden, or `allowed`.
        memory_mib: The memory of its process tree, or `None` for the renderer's ceiling.
        match_minutes: The time a side's answers may take over a match.
        check_minutes: The time a pre-check may take.
        env: The variables of the environment it receives beyond a minimal one, such as a key.
    """

    host: str = 'format'
    scope: str = 'match'
    threads: int = 1
    gpu: str = 'never'
    memory_mib: int | None = None
    match_minutes: float = MATCH_MINUTES[0]
    check_minutes: float = CHECK_MINUTES[0]
    env: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Spec:
    """A format's `format.toml`, read and checked.

    Attributes:
        contract: The contract it follows.
        name: Its name in the menu.
        script: The file of its script, inside its folder.
        version: Its version, which a built-in format states.
        summary: One line for the menu.
        requires: The versions of cuju-arena it needs, such as `>=0.1.0`.
        process: Its `Process`.
        forfeit_score: The score a forfeit records, as `[forfeiting side, other side]`, or `None`
            for the context's own.
        evidence: Whether its matches may be evidence for the ratings.
        options: Its options by key, in the file's order.
        presets: Its presets by key, each `{label, values}`.
    """

    contract: int
    name: str
    script: str
    version: str | None
    summary: str | None
    requires: str | None
    process: Process
    forfeit_score: tuple[int, int] | None
    evidence: bool
    options: dict[str, Option]
    presets: dict[str, dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        """Returns the spec as JSON."""
        return {
            'contract': self.contract,
            'name': self.name,
            'script': self.script,
            'version': self.version,
            'summary': self.summary,
            'requires': self.requires,
            'process': {
                **dataclasses.asdict(self.process),
                'env': list(self.process.env),
            },
            'results': {
                'forfeit_score': None if self.forfeit_score is None else list(self.forfeit_score),
                'evidence': self.evidence,
            },
            'options': {key: option.as_dict() for key, option in self.options.items()},
            'presets': self.presets,
        }


def _text(value: Any, where: str, length: int = TEXT_LENGTH, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > length:
        raise ContractError(f'{where} is text of at most {length} characters, found {value!r}')
    return value.strip()


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _unknown(table: dict[str, Any], known: set[str], where: str) -> None:
    extra = sorted(set(table) - known)
    if extra:
        raise ContractError(f'{where} holds unknown keys, {", ".join(extra)}')


def check(option: Option, value: Any) -> Any:
    """Returns a value of an option, checked against its type and its bounds.

    Raises:
        ContractError: If the value does not fit.
    """
    where = f'the option {option.key}'
    if option.type == 'bool':
        if not isinstance(value, bool):
            raise ContractError(f'{where} is true or false, found {value!r}')
        return value
    if option.type == 'text':
        limit = option.max_length or TEXT_LENGTH
        if not isinstance(value, str) or len(value) > limit:
            raise ContractError(f'{where} is text of at most {limit} characters, found {value!r}')
        return value
    if option.type == 'choices':
        if value not in option.choices:
            choices = ', '.join(map(str, option.choices))
            raise ContractError(f'{where} is one of {choices}, found {value!r}')
        return value
    if option.type == 'int' and not (isinstance(value, int) and not isinstance(value, bool)):
        raise ContractError(f'{where} is a whole number, found {value!r}')
    if not _number(value):
        raise ContractError(f'{where} is a number, found {value!r}')
    if option.range is not None and not option.range[0] <= value <= option.range[1]:
        low, high = option.range
        raise ContractError(f'{where} lies from {low:g} to {high:g}, found {value!r}')
    return float(value) if option.type == 'float' else int(value)


def _option(key: str, table: Any) -> Option:
    where = f'[options.{key}]'
    if not _KEY.fullmatch(key):
        raise ContractError(f'{where} needs a key of lowercase letters, digits and underscores')
    if not isinstance(table, dict):
        raise ContractError(f'{where} is a table')
    _unknown(table, _OPTION, where)
    kind = table.get('type')
    if kind not in TYPES:
        raise ContractError(f'{where} needs a type, one of {", ".join(TYPES)}')
    label = _text(table.get('label', key), f'{where} label', 64)
    span = table.get('range')
    if span is not None:
        if kind not in ('int', 'float') or not (
            isinstance(span, list) and len(span) == 2 and all(map(_number, span))
        ):
            raise ContractError(f'{where} range is two numbers, for int and float alone')
        if span[0] > span[1]:
            raise ContractError(f'{where} range runs from low to high')
        span = (span[0], span[1])
    choices = table.get('choices', ())
    if kind == 'choices':
        if not (isinstance(choices, list) and choices):
            raise ContractError(f'{where} needs choices, a list of values')
        if len({repr(choice) for choice in choices}) != len(choices):
            raise ContractError(f'{where} lists a choice twice')
    elif 'choices' in table:
        raise ContractError(f'{where} has choices only for the type choices')
    bounds = table.get('bounds')
    if bounds not in (None, 'step', 'match') or (bounds and kind not in ('int', 'float')):
        raise ContractError(f'{where} bounds is step or match, for a number of seconds')
    if bounds == 'step' and span is not None and span[1] > STEP_SECONDS:
        raise ContractError(f'{where} allows {span[1]:g} s a step, the ceiling is {STEP_SECONDS} s')
    reaches = table.get('reaches', 'play')
    if reaches not in ('play', 'check'):
        raise ContractError(f'{where} reaches play or check')
    per_entrant = table.get('per_entrant', False)
    if not isinstance(per_entrant, bool):
        raise ContractError(f'{where} per_entrant is true or false')
    length = table.get('max_length')
    if kind == 'text':
        length = TEXT_LENGTH if length is None else length
        if not (isinstance(length, int) and not isinstance(length, bool)) or not (
            1 <= length <= TEXT_LENGTH
        ):
            raise ContractError(f'{where} max_length is a whole number from 1 to {TEXT_LENGTH}')
    elif length is not None:
        raise ContractError(f'{where} has max_length only for the type text')
    unit = table.get('unit')
    if unit is not None:
        unit = _text(unit, f'{where} unit', 16)
    option = Option(
        key,
        kind,
        None,
        label,
        span,
        tuple(choices),
        unit,
        bounds,
        reaches,
        per_entrant,
        length,
    )
    if 'default' not in table:
        raise ContractError(f'{where} needs a default')
    return dataclasses.replace(option, default=check(option, table['default']))


def _process(table: Any, builtin: bool) -> Process:
    if table is None:
        return Process()
    if not isinstance(table, dict):
        raise ContractError('[process] is a table')
    _unknown(table, _PROCESS, '[process]')
    host = table.get('host', 'format')
    if host not in ('format', 'match'):
        raise ContractError('[process] host is format or match')
    if host == 'match' and not builtin:
        raise ContractError('[process] host = "match" belongs to the built-in formats alone')
    scope = table.get('scope', 'match')
    if scope not in ('match', 'run'):
        raise ContractError('[process] scope is match or run')
    threads = table.get('threads', 1)
    if not (isinstance(threads, int) and not isinstance(threads, bool)) or not (
        1 <= threads <= THREADS
    ):
        raise ContractError(f'[process] threads is a whole number from 1 to {THREADS}')
    gpu = table.get('gpu', 'never')
    if gpu not in ('never', 'allowed'):
        raise ContractError('[process] gpu is never or allowed')
    memory = table.get('memory_mib')
    if memory is not None and (
        not (isinstance(memory, int) and not isinstance(memory, bool))
        or not 64 <= memory <= MEMORY_MIB
    ):
        raise ContractError(f'[process] memory_mib is a whole number from 64 to {MEMORY_MIB}')
    minutes = {}
    for name, (default, ceiling) in (
        ('match_minutes', MATCH_MINUTES),
        ('check_minutes', CHECK_MINUTES),
    ):
        value = table.get(name, default)
        if not _number(value) or not 0 < value <= ceiling:
            raise ContractError(f'[process] {name} is more than 0 and at most {ceiling}')
        minutes[name] = float(value)
    variables = table.get('env', [])
    if not isinstance(variables, list) or not all(
        isinstance(name, str) and _VARIABLE.fullmatch(name) and not name.startswith('PYTHON')
        for name in variables
    ):
        raise ContractError('[process] env is a list of variable names, none of them PYTHON')
    return Process(
        host,
        scope,
        threads,
        gpu,
        memory,
        minutes['match_minutes'],
        minutes['check_minutes'],
        tuple(variables),
    )


def satisfies(version: str, requirement: str | None) -> bool:
    """Returns whether a version of cuju-arena meets a requirement such as `>=0.2,<0.4`.

    Raises:
        ContractError: If the requirement does not read.
    """
    if requirement is None:
        return True

    def parts(text: str) -> tuple[int, ...]:
        numbers = [int(part) for part in re.findall(r'\d+', text.split('+')[0])[:3]]
        return tuple(numbers + [0] * (3 - len(numbers)))

    have = parts(version)
    for clause in requirement.split(','):
        found = _REQUIREMENT.fullmatch(clause)
        if not found:
            raise ContractError(f'requires is a range such as ">=0.1.0", found {requirement!r}')
        operator, wanted = found.group(1), parts(found.group(2))
        ok = {
            '>=': have >= wanted,
            '<=': have <= wanted,
            '>': have > wanted,
            '<': have < wanted,
            '==': have == wanted,
            '!=': have != wanted,
        }[operator]
        if not ok:
            return False
    return True


def needs(requirement: str) -> str:
    """Returns what a requirement asks for in words, such as `cuju-arena 0.3 or later`."""
    found = _REQUIREMENT.fullmatch(requirement) if ',' not in requirement else None
    if found and found.group(1) == '>=':
        version = found.group(2)
        while version.endswith('.0') and version.count('.') > 1:
            version = version[:-2]
        return f'cuju-arena {version} or later'
    return f'cuju-arena {requirement.strip()}'


def parse(text: str, builtin: bool = False) -> Spec:
    """Returns the spec of a `format.toml`'s text.

    Args:
        text: The file's text.
        builtin: Whether the format is one of the renderer's own, which alone may ask for the
            match process.

    Raises:
        ContractError: If the file does not read or holds what does not fit.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ContractError(f'{FILE} does not read, {error}') from None
    _unknown(data, _TOP, FILE)
    contract = data.get('contract')
    if contract not in CONTRACTS:
        known = ', '.join(map(str, CONTRACTS))
        raise ContractError(f'{FILE} needs contract = {known}, found {contract!r}')
    name = _text(data.get('name'), 'name', 64)
    script = data.get('script')
    if not isinstance(script, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\.py', script):
        raise ContractError(f'script is the name of a .py file of the folder, found {script!r}')
    version = data.get('version')
    if version is not None:
        version = _text(str(version) if isinstance(version, int) else version, 'version', 32)
    summary = data.get('summary')
    if summary is not None:
        summary = _text(summary, 'summary')
    requires = data.get('requires')
    if requires is not None:
        requires = _text(requires, 'requires', 64)
        satisfies('0.0.0', requires)
    results = data.get('results') or {}
    if not isinstance(results, dict):
        raise ContractError('[results] is a table')
    _unknown(results, _RESULTS, '[results]')
    score = results.get('forfeit_score')
    if score is not None:
        if not (
            isinstance(score, list)
            and len(score) == 2
            and all(isinstance(goals, int) and not isinstance(goals, bool) for goals in score)
            and all(0 <= goals <= 99 for goals in score)
        ):
            raise ContractError('[results] forfeit_score is two whole numbers from 0 to 99')
        score = (score[0], score[1])
    evidence = results.get('evidence', True)
    if not isinstance(evidence, bool):
        raise ContractError('[results] evidence is true or false')
    tables = data.get('options') or {}
    if not isinstance(tables, dict):
        raise ContractError('[options] holds a table for each option')
    options = {key: _option(key, table) for key, table in tables.items()}
    presets = {}
    for key, table in (data.get('presets') or {}).items():
        where = f'[presets.{key}]'
        if not _KEY.fullmatch(key) or not isinstance(table, dict):
            raise ContractError(f'{where} is a table with a key of lowercase letters')
        label = _text(table.get('label', key), f'{where} label', 64)
        values = {option: value for option, value in table.items() if option != 'label'}
        for option, value in values.items():
            if option not in options:
                raise ContractError(f'{where} sets {option}, which is no option')
            check(options[option], value)
        presets[key] = {'label': label, 'values': values}
    return Spec(
        contract,
        name,
        script,
        version,
        summary,
        requires,
        _process(data.get('process'), builtin),
        score,
        evidence,
        options,
        presets,
    )


def read(folder: Path, builtin: bool = False) -> Spec:
    """Returns the spec of a format's folder, its script checked to be there.

    Raises:
        ContractError: If `format.toml` or the script is missing, or the file does not fit.
    """
    path = Path(folder) / FILE
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as error:
        raise ContractError(f'{FILE} does not read, {error}') from None
    spec = parse(text, builtin)
    if not (Path(folder) / spec.script).is_file():
        raise ContractError(f'the script {spec.script} is missing')
    return spec


def options(
    spec: Spec, values: dict[str, Any] | None = None, preset: str | None = None
) -> dict[str, Any]:
    """Returns every option of a format resolved, its defaults, then a preset, then values.

    Raises:
        ContractError: For an unknown preset, an unknown option or a value that does not fit.
    """
    found = {key: option.default for key, option in spec.options.items()}
    if preset is not None:
        if preset not in spec.presets:
            raise ContractError(f'{spec.name} has no preset {preset!r}')
        found.update(spec.presets[preset]['values'])
    for key, value in (values or {}).items():
        if key not in spec.options:
            raise ContractError(f'{spec.name} has no option {key!r}')
        found[key] = check(spec.options[key], value)
    return found


def playing(spec: Spec, values: dict[str, Any]) -> dict[str, Any]:
    """Returns the options that reach play, which `Match.options` holds and the identity joins."""
    return {
        key: value
        for key, value in values.items()
        if key in spec.options and spec.options[key].reaches == 'play'
    }
