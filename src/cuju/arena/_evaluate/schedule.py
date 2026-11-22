# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""The schedule of a phase of matches, played all at once or on demand.

A seeding phase of a tournament and an evaluation run both play a set of matches between sides
that have ratings, and both play it in one of two ways. Batched, the whole plan is known before
the first match and every match starts at once, which suits policies that play in one batch. On
demand, the schedule starts a few matches at a time, picks each next pairing where the order of
the ratings so far is least settled, and stops once the order is settled or the most matches are
played, which suits every kind of team, a program in a process of its own included.

The ratings follow the Elo rule of the tournaments, `update_ratings`, from the rating that each
side starts with. A side whose rating is given stays where it is and anchors the others. Beside
its rating each side keeps how much its matches tell of it, the sum over its matches of
`p * (1 - p)`, where `p` is the share of a win that the ratings expected. The deviation of a
rating is `1 / sqrt(1 / 350**2 + (ln 10 / 400)**2 * sum)`, the deviation of a rating under the
logistic model after those matches, from 350 before the first match, and 0 for a given rating.
A side may start with a deviation of its own, the one that its earlier matches left it with. A
measured side then starts with the sum that gives that deviation, and a given side keeps the
deviation as it is. The interval of a rating is the rating less and plus the deviation times
the two sided quantile of the normal distribution at the confidence, 0.95 by default.

The settling rule compares two sides by the gap of their ratings over the deviation of that gap,
the root of the sum of the squares of their deviations. A pair is settled once that ratio reaches
the two sided quantile of the confidence `settle`, 0.9 by default, and every side of it whose
rating is measured has played as many matches as the phase asks of it and holds an interval
no wider than the phase allows. The order is settled once every pair that may meet is. On demand,
the next pairing is the pair that is least settled, by the ratio plus a quarter for each earlier
meeting of the pair, so two level sides do not hold the schedule to themselves, then by the
matches that its sides have played, the fewest first, then by an order drawn from the seed. A
measured side that has not played yet meets a side with a given rating first, where the phase
has one. A phase with an anchor, one side that every measured side must meet, plays the set
number of matches of each measured side against the anchor before any other match, whatever
the ratings say, so each measured side is tied to the anchor by matches of its own.

The pairing rule is a plugin of the kind `pairing/1`, `next_pairs(state, slots)`, and the rule
above is its standard, `least_settled`. The schedule hands it a `PairingState` for each pick and
holds its answer to the pairs that may meet.

The schedule is deterministic. With several matches at once, results may arrive in any order, so
the schedule applies them in the order of the match numbers, and it picks match `n` from the
results of the matches up to `n - at_once` alone, which are known whenever a slot is free. The
same seed, sides and results therefore give the same pairings, whatever the timing.

The state of a schedule is plain data, which `_store.queue` keeps after every change, with
`mode`, `ratings`, `intervals`, `played`, `playing`, `next`, `count`, `most` and `settled`, so a
phase survives a restart. A schedule made again from its state applies the played matches, starts
the matches that were under way again, and goes on.
"""

from __future__ import annotations

import copy
import hashlib
import math
import random
import statistics
from typing import Any

from .. import plugins as _plugins
from .._tournaments import tournament as _rules

MODES = ('batched', 'on_demand')
# The pairing rule that ships, the name of a plugin of the kind `pairing`.
PAIRING = 'least_settled'
# The deviation of a measured rating before its first match.
PRIOR_DEVIATION = 350.0
# What each earlier meeting of a pair adds to its ratio when the next pairing is picked.
REPEAT = 0.25
# The confidence of the interval of a rating.
CONFIDENCE = 0.95
_SCALE = math.log(10) / 400


def quantile(confidence: float) -> float:
    """Returns the two sided quantile of the normal distribution at a confidence below 1."""
    return statistics.NormalDist().inv_cdf((1 + confidence) / 2)


def fit(at_once: int, threads: Any = None) -> int:
    """Returns the matches a run plays at once, at most those whose CPU sets fit the machine.

    Args:
        at_once: The matches the run asks for.
        threads: The threads of a side whose format plays in a process of its own, one number
            for both sides or one for each, or `None` for sides that take no CPU set.
    """
    wanted = max(1, int(at_once))
    if threads is None:
        return wanted
    from .._formats import cpusets as _cpusets

    return min(wanted, _cpusets.matches_at_once(threads))


def expected(first: float, second: float) -> float:
    """Returns the share of a win that the Elo rule expects of a rating against another."""
    return 1 / (1 + 10 ** ((second - first) / 400))


class _Table:
    """The ratings of the sides after some results, with what the matches tell of each."""

    __slots__ = ('given', 'information', 'matches', 'ratings')

    def __init__(
        self,
        ratings: dict[str, float],
        information: dict[str, float] | None = None,
        given: dict[str, float] | None = None,
    ) -> None:
        self.ratings = dict(ratings)
        self.information = {key: (information or {}).get(key, 0.0) for key in ratings}
        self.matches = dict.fromkeys(ratings, 0)
        # The deviation of each side whose rating is given, which no match changes.
        self.given = given or {}

    def copy(self) -> _Table:
        found = _Table(self.ratings, self.information, self.given)
        found.matches = dict(self.matches)
        return found

    def apply(self, home: str, away: str, score: list[int], fixed: frozenset[str]) -> None:
        """Moves the ratings of two sides by the Elo rule, a given rating staying where it is."""
        share = expected(self.ratings[home], self.ratings[away])
        scratch = {
            key: {'name': key, 'rating': self.ratings[key], 'matches': 0} for key in (home, away)
        }
        _rules.update_ratings(
            scratch, {'key': home, 'name': home}, {'key': away, 'name': away}, list(score)
        )
        for key in (home, away):
            self.information[key] += share * (1 - share)
            self.matches[key] += 1
            if key not in fixed:
                self.ratings[key] = scratch[key]['rating']

    def deviation(self, key: str, fixed: frozenset[str]) -> float:
        if key in fixed:
            return self.given.get(key, 0.0)
        return 1 / math.sqrt(1 / PRIOR_DEVIATION**2 + _SCALE**2 * self.information[key])

    def ratio(self, first: str, second: str, fixed: frozenset[str]) -> float:
        """Returns the gap of two ratings over the deviation of the gap, infinite for two given."""
        spread = math.hypot(self.deviation(first, fixed), self.deviation(second, fixed))
        gap = abs(self.ratings[first] - self.ratings[second])
        return gap / spread if spread > 0 else math.inf


class PairingState:
    """What a pairing rule of the kind `pairing/1` reads to choose the next pairing of a phase.

    The state is a read only view of one pick, made for the match `number` from the results that
    the number may know. A rule never changes it.

    Attributes:
        number: The number of the match to pick.
        sides: The keys of the sides, in the order given.
        fixed: The keys whose rating is given, which stay where they are.
        pairs: The pairs that may meet, each with its home side first, in the order of the phase.
        explicit: Whether the pairs were given, so their home sides are binding. Without it the
            two sides of a pair may swap their places.
        limit: The most meetings of one pair, or `None`.
        anchor: The key of the side that every measured side must meet, or `None`.
        anchor_matches: The matches of each measured side against the anchor.
        ratings: The rating of each side after the results that the number may know.
        matches: The matches of each side among those results.
        games: The matches that each side has started, before this one.
        homes: The matches that each side has started as home side.
        rank: The place of each side in the order that the seed draws, which settles ties.
    """

    def __init__(
        self,
        schedule: Schedule,
        number: int,
        table: _Table,
        started: list[dict[str, Any]],
    ) -> None:
        self._schedule = schedule
        self._table = table
        self.number = number
        self.sides = tuple(schedule.sides)
        self.fixed = schedule.fixed
        self.pairs = tuple(schedule._pairs)
        self.explicit = schedule._explicit
        self.limit = schedule._limit
        self.anchor = schedule.anchor
        self.anchor_matches = schedule.anchor_matches
        self.ratings = dict(table.ratings)
        self.matches = dict(table.matches)
        self.rank = dict(schedule._rank)
        self._meetings: dict[frozenset[str], int] = {}
        self.games = dict.fromkeys(schedule.sides, 0)
        self.homes = dict.fromkeys(schedule.sides, 0)
        for pairing in started:
            pair = frozenset((pairing['home'], pairing['away']))
            self._meetings[pair] = self._meetings.get(pair, 0) + 1
            self.games[pairing['away']] += 1
            self.games[pairing['home']] += 1
            self.homes[pairing['home']] += 1

    def meetings(self, first: str, second: str) -> int:
        """Returns how often two sides have been started against each other."""
        return self._meetings.get(frozenset((first, second)), 0)

    def deviation(self, key: str) -> float:
        """Returns the deviation of the rating of a side, 0 for a side whose rating is given."""
        return self._table.deviation(key, self.fixed)

    def ratio(self, first: str, second: str) -> float:
        """Returns the gap of two ratings over the deviation of the gap, infinite for two given."""
        return self._table.ratio(first, second, self.fixed)

    def unsettled(self, first: str, second: str) -> bool:
        """Returns whether the settling rule of the phase still holds a pair open."""
        return self._schedule._unsettled(self._table, first, second)

    def owed(self, first: str, second: str) -> bool:
        """Returns whether a pair with the anchor has matches against it still to play."""
        return self._schedule._owed(first, second, self._meetings)


class LeastSettled:
    """The pairing rule `least_settled`, as a `pairing/1`.

    A pair that the settling rule holds open and that the limits allow is picked, by order of
    these keys, the pairs that still owe matches against the anchor first, then a measured side
    that has not played with a side whose rating is given, then the pair whose order is least
    settled, which each earlier meeting of the pair makes less urgent, then the pair whose sides
    have played the fewest matches, then the order that the seed drew. The home side of a pair
    that was not given goes to the side that has been home less often.
    """

    def next_pairs(self, state: PairingState, slots: int) -> list[tuple[str, str]]:
        """Returns up to `slots` pairings, the best first, or none once every pair is settled."""
        found = []
        for first, second in state.pairs:
            met = state.meetings(first, second)
            if state.limit is not None and met >= state.limit:
                continue
            owed = state.owed(first, second)
            if not owed and not state.unsettled(first, second):
                continue
            ratio = state.ratio(first, second)
            fresh = any(
                state.games[key] == 0 for key in (first, second) if key not in state.fixed
            ) and any(key in state.fixed for key in (first, second))
            order = (
                0 if owed else 1,
                0 if fresh else 1,
                (0.0 if math.isinf(ratio) else ratio) + REPEAT * met,
                state.games[first] + state.games[second],
                min(state.rank[first], state.rank[second]),
                max(state.rank[first], state.rank[second]),
            )
            found.append((order, first, second))
        found.sort(key=lambda entry: entry[0])
        pairings = []
        for _, first, second in found[: max(0, slots)]:
            if not state.explicit and state.homes[second] < state.homes[first]:
                first, second = second, first
            pairings.append((first, second))
        return pairings


class Schedule:
    """The matches of a phase, their order, their results and the ratings as they stand.

    The owner of a schedule asks `due` for the pairings to start, starts them, and hands each
    result to `finish`. `state` gives what `_store.queue.save_queue` keeps, and `progress` what
    a view shows.

    Attributes:
        mode: `batched` or `on_demand`.
        sides: The keys of the sides, in the order given.
        fixed: The keys whose rating is given.
        most: The most matches that the phase plays.
        at_once: The matches under way at once, on demand.
        settle: The confidence of the settling rule, or `None` to play the most matches.
        anchor: The key of the side that every measured side must meet, or `None`.
        anchor_matches: The matches of each measured side against the anchor.
    """

    def __init__(
        self,
        sides: list[str],
        *,
        mode: str,
        seed: int = 0,
        ratings: dict[str, float] | None = None,
        deviations: dict[str, float] | None = None,
        fixed: Any = (),
        anchor: str | None = None,
        anchor_matches: int = 0,
        needs: dict[str, int] | None = None,
        width: float | None = None,
        pairs: list[tuple[str, str]] | None = None,
        limit: int | None = None,
        each: int | None = None,
        most: int | None = None,
        at_once: int = 1,
        settle: float | None = 0.9,
        confidence: float = CONFIDENCE,
        state: dict[str, Any] | None = None,
        threads: Any = None,
        pairing: str = PAIRING,
    ) -> None:
        """Makes the schedule of a phase, or takes one up again from its saved state.

        Args:
            sides: The key of each side, each once.
            mode: `batched` or `on_demand`.
            seed: The seed, which orders the sides that the rule leaves level.
            ratings: The rating that each side starts with, `BASE_RATING` of the tournaments
                for a side left out.
            deviations: The deviation that a side starts with, from its earlier matches. A
                measured side left out starts from 350, and a given side left out holds 0.
            fixed: The keys whose rating is given, which stay where they are.
            anchor: The key of a given side that every measured side meets `anchor_matches`
                times before any other match, or `None` for a phase without such a side.
            anchor_matches: The matches of each measured side against the anchor.
            needs: The matches that a measured side must play in the phase before a pair of it
                may count as settled, by key, 1 for a side left out.
            width: The widest interval of a measured side, from its lower to its upper bound,
                at which a pair of it may count as settled, or `None` for any width.
            pairs: The pairs that may meet, each with its home side first, or `None` for every
                pair with a side whose rating is measured, whose home side the schedule
                balances.
            limit: The most meetings of one pair, or `None` for no limit.
            each: The matches that each measured side plays in a batched phase without `pairs`.
            most: The most matches of the phase. By default a batched phase plays its whole
                plan, and a phase on demand plays `limit` meetings of each pair, or `each`
                matches for each measured side.
            at_once: The matches under way at once, on demand.
            settle: The confidence of the settling rule, from 0.5 to below 1, or `None`.
            confidence: The confidence of the interval of each rating.
            state: The saved state of the schedule, as `state` returned it, with the matches
                that were under way moved to `next`, as `queue_state(owner, resume=True)` does.
            threads: The threads of a side whose format plays in a process of its own, one
                number for both sides or one for each, or `None`. It holds `at_once` to the
                matches whose CPU sets fit the machine, as `fit` gives them.
            pairing: The name of the pairing rule that picks each next pairing on demand, a
                plugin of the kind `pairing`, by default `least_settled`.

        Raises:
            ArgumentError: If no plugin offers the pairing rule, or it is broken or turned off.
            ValueError: If the mode is unknown, a side comes twice, a pair names an unknown
                side, neither `pairs` with `limit` nor `each` tells what a phase plays, or
                the anchor is no side whose rating is given.
        """
        if mode not in MODES:
            raise ValueError(f'expected a mode of {", ".join(MODES)}, found {mode!r}')
        if len(set(sides)) != len(sides):
            raise ValueError(f'expected each side once, found {sides!r}')
        _plugins.get('pairing', pairing)
        self.pairing = pairing
        self.mode = mode
        self.sides = list(sides)
        self.fixed = frozenset(fixed) & set(sides)
        if anchor is not None and anchor not in self.fixed:
            raise ValueError(f'expected an anchor whose rating is given, found {anchor!r}')
        self.anchor = anchor
        self.anchor_matches = max(0, int(anchor_matches)) if anchor is not None else 0
        self._needs = {key: max(1, int(value)) for key, value in (needs or {}).items()}
        self._width = None if width is None else float(width)
        self.at_once = fit(at_once, threads) if mode == 'on_demand' else 1
        self.settle = settle
        self._threshold = None if settle is None else quantile(settle)
        self._interval = quantile(confidence)
        self._limit = limit
        self._each = each
        self._explicit = pairs is not None
        order = list(sides)
        random.Random(hashlib.sha256(f'{seed}/schedule'.encode()).digest()).shuffle(order)
        self._rank = {key: index for index, key in enumerate(order)}
        measured = [key for key in order if key not in self.fixed]
        anchors = [key for key in order if key in self.fixed]
        if pairs is None:
            found = [
                (first, second)
                for index, first in enumerate(order)
                for second in order[index + 1 :]
            ]
            pairs = [
                pair for pair in found if not (pair[0] in self.fixed and pair[1] in self.fixed)
            ]
        for pair in pairs:
            if len(pair) != 2 or pair[0] == pair[1] or not set(pair) <= set(sides):
                raise ValueError(f'expected a pair of two sides, found {pair!r}')
        self._pairs = [tuple(pair) for pair in pairs]
        self._allowed = {frozenset(pair): pair for pair in self._pairs}
        if (self._explicit and limit is None) or (not self._explicit and each is None):
            raise ValueError('expected pairs with limit, or each, to tell what the phase plays')
        start = {key: float(_rules.BASE_RATING) for key in sides}
        start.update({key: float(value) for key, value in (ratings or {}).items() if key in start})
        spreads = {key: float(value) for key, value in (deviations or {}).items() if key in start}
        information = {
            key: max(0.0, 1 / value**2 - 1 / PRIOR_DEVIATION**2) / _SCALE**2
            for key, value in spreads.items()
            if key not in self.fixed and value > 0
        }
        given = {key: max(0.0, value) for key, value in spreads.items() if key in self.fixed}
        # The table after each number of applied results, the first before any.
        self._tables = [_Table(start, information, given)]
        self._played: list[dict[str, Any]] = []
        self._started: list[dict[str, Any]] = []
        self._results: dict[int, dict[str, Any]] = {}
        self._waiting: list[dict[str, Any]] = []
        self._plan: list[tuple[str, str]] = []
        if mode == 'batched':
            self._plan = self._batched_plan(measured, anchors)
        if most is None:
            if mode == 'batched':
                most = len(self._plan)
            elif self._explicit:
                most = len(self._pairs) * int(limit)
            else:
                most = len(measured) * int(each)
        self.most = int(most)
        if mode == 'batched':
            self._plan = self._plan[: self.most]
        if state:
            self._restore(state)

    # The plan of a batched phase.

    def _batched_plan(self, measured: list[str], anchors: list[str]) -> list[tuple[str, str]]:
        """Returns every pairing of a batched phase, in the order of the match numbers.

        With pairs given, each pair meets `limit` times, pair after pair. Without, each
        measured side plays `each` rounds, first one against each anchor in turn, where there
        are anchors, then rounds among the measured sides in which every side meets another
        than before, as a league draws its days. A side left over in a round of an odd number
        of sides meets the side that it has met least. A phase with an anchor of its own opens
        with the matches of each measured side against it, the home side in turn, and they
        count among the rounds.
        """
        if self._explicit:
            return [pair for pair in self._pairs for _ in range(int(self._limit))]
        plan: list[tuple[str, str]] = []
        meetings: dict[frozenset[str], int] = {}

        def add(first: str, second: str) -> None:
            plan.append((first, second))
            pair = frozenset((first, second))
            meetings[pair] = meetings.get(pair, 0) + 1

        opening = min(self.anchor_matches, int(self._each))
        for number in range(opening):
            for side in measured:
                add(*((side, self.anchor) if number % 2 == 0 else (self.anchor, side)))
        if opening:
            # The anchor has had its matches, so the rounds that follow leave it out.
            anchors = [key for key in anchors if key != self.anchor]
        days = _rules.round_robin(measured) if len(measured) > 1 else []
        for number in range(int(self._each) - opening):
            if anchors and (number < len(anchors) or not days):
                for index, side in enumerate(measured):
                    add(side, anchors[(index + number) % len(anchors)])
                continue
            day = days[(number - len(anchors) if anchors else number) % len(days)]
            busy = set()
            for first, second in day:
                # The circle method names the rest of an odd number of sides as `None`.
                if first is not None and second is not None:
                    add(first, second)
                    busy.update((first, second))
            for side in measured:
                if side not in busy:
                    others = [other for other in [*measured, *anchors] if other != side]
                    add(
                        side,
                        min(
                            others,
                            key=lambda other: (
                                meetings.get(frozenset((side, other)), 0),
                                self._rank[other],
                            ),
                        ),
                    )
        return plan

    # The pick of the next pairing on demand.

    def _meetings(self, count: int) -> dict[frozenset[str], int]:
        found: dict[frozenset[str], int] = {}
        for pairing in self._started[:count]:
            pair = frozenset((pairing['home'], pairing['away']))
            found[pair] = found.get(pair, 0) + 1
        return found

    def _unsettled(self, table: _Table, first: str, second: str) -> bool:
        if self._threshold is None:
            return True
        for key in (first, second):
            if key in self.fixed:
                continue
            if table.matches[key] < self._needs.get(key, 1):
                return True
            if (
                self._width is not None
                and 2 * self._interval * table.deviation(key, self.fixed) > self._width
            ):
                return True
        return table.ratio(first, second, self.fixed) < self._threshold

    def _owed(self, first: str, second: str, meetings: dict[frozenset[str], int]) -> bool:
        """Returns whether a pair with the anchor has matches against it still to play."""
        if self.anchor is None or self.anchor not in (first, second):
            return False
        return meetings.get(frozenset((first, second)), 0) < self.anchor_matches

    def _pick(
        self, number: int, known: int, started: list[dict[str, Any]]
    ) -> tuple[str, str] | None:
        """Returns the pairing of match `number` from the first `known` results, or `None`.

        Args:
            number: The number of the match.
            known: The results that the pick may read.
            started: The pairings of the matches before it.
        """
        if number >= self.most:
            return None
        state = PairingState(self, number, self._tables[known], started)
        picked = _plugins.call(
            'pairing', self.pairing, 'next_pairs', state, 1, check=self._checked(state)
        )
        return picked[0] if picked else None

    def _checked(self, state: PairingState) -> Any:
        """Returns a function that checks the answer of a pairing rule, raising ValueError."""
        allowed = self._allowed

        def check(answer: Any) -> list[tuple[str, str]]:
            if not isinstance(answer, list | tuple) or len(answer) > 1:
                raise ValueError(f'expected at most 1 pairing, found {answer!r}')
            found = []
            for pair in answer:
                if not isinstance(pair, list | tuple) or len(pair) != 2:
                    raise ValueError(f'expected a pair of two sides, found {pair!r}')
                pair = (pair[0], pair[1])
                held = allowed.get(frozenset(pair)) if pair[0] != pair[1] else None
                if held is None or (state.explicit and held != pair):
                    raise ValueError(f'expected a pair that may meet, found {pair!r}')
                found.append(pair)
            return found

        return check

    def _pairing(self, number: int, home: str, away: str) -> dict[str, Any]:
        meeting = sum(
            1
            for earlier in self._started[:number]
            if {earlier['home'], earlier['away']} == {home, away}
        )
        return {'number': number, 'home': home, 'away': away, 'meeting': meeting}

    # What the owner calls.

    def due(self) -> list[dict[str, Any]]:
        """Returns the pairings to start now, each marked as under way.

        A pairing is a mapping of `number`, the number of the match from 0, `home` and `away`,
        the keys of its sides, and `meeting`, the meetings of the two sides before it. The owner
        may add fields of its own to the mapping, such as the seed, which the state keeps.
        """
        found: list[dict[str, Any]] = []
        while self._waiting and (self.mode == 'batched' or len(self.playing) < self.at_once):
            pairing = self._waiting.pop(0)
            self._started.append(pairing)
            found.append(pairing)
        if self._waiting:
            return found
        if self.mode == 'batched':
            for number in range(len(self._started), len(self._plan)):
                pairing = self._pairing(number, *self._plan[number])
                self._started.append(pairing)
                found.append(pairing)
            return found
        while len(self.playing) < self.at_once:
            number = len(self._started)
            known = max(0, number - self.at_once + 1)
            picked = self._pick(number, known, self._started)
            if picked is None:
                break
            pairing = self._pairing(number, *picked)
            self._started.append(pairing)
            found.append(pairing)
        return found

    def finish(self, number: int, score: Any, **fields: Any) -> list[dict[str, Any]]:
        """Takes the result of a match and returns the matches that it lets the ratings count.

        Results count in the order of the match numbers, so a result that arrives before an
        earlier match has ended waits for it, and the call then returns no match.

        Args:
            number: The number of the match.
            score: The goals of the home and of the away side, or `None` for a match that
                ended without a result, which counts as played and moves no rating.
            **fields: What the owner keeps of the match beside its score, such as its recording.

        Raises:
            ValueError: If no match of that number is under way.
        """
        if not any(pairing['number'] == number for pairing in self.playing):
            raise ValueError(f'expected the number of a match under way, found {number!r}')
        self._results[number] = {
            'score': None if score is None else [int(v) for v in score],
            **fields,
        }
        counted = []
        while len(self._played) in self._results:
            pairing = self._started[len(self._played)]
            result = self._results.pop(len(self._played))
            counted.append(self._apply(pairing, result))
        return counted

    def _apply(self, pairing: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        table = self._tables[-1].copy()
        if result.get('score') is not None:
            table.apply(pairing['home'], pairing['away'], result['score'], self.fixed)
        self._tables.append(table)
        entry = {
            **pairing,
            **result,
            'ratings': {
                key: self._rating(table, key) for key in (pairing['home'], pairing['away'])
            },
        }
        self._played.append(entry)
        return copy.deepcopy(entry)

    def _rating(self, table: _Table, key: str) -> dict[str, Any]:
        rating = table.ratings[key]
        spread = self._interval * table.deviation(key, self.fixed)
        return {
            'rating': round(rating, 2),
            'low': round(rating - spread, 2),
            'high': round(rating + spread, 2),
            'matches': table.matches[key],
            'measured': key not in self.fixed,
        }

    def _restore(self, state: dict[str, Any]) -> None:
        """Applies the played matches of a saved state and lines up those that were under way."""
        for entry in state.get('played') or []:
            pairing = {
                key: value for key, value in entry.items() if key not in ('ratings', 'score')
            }
            self._started.append(pairing)
            result = {key: value for key, value in entry.items() if key not in pairing}
            result.pop('ratings', None)
            self._apply(pairing, {'score': entry.get('score'), **result})
        waiting = [*(state.get('playing') or []), *(state.get('next') or [])]
        self._waiting = sorted(
            (dict(entry) for entry in waiting if not entry.get('provisional')),
            key=lambda entry: entry['number'],
        )

    @property
    def playing(self) -> list[dict[str, Any]]:
        """The matches under way, those started whose result does not count yet."""
        return self._started[len(self._played) :]

    @property
    def played(self) -> list[dict[str, Any]]:
        """The matches whose result counts, each with its `score` and the two ratings after."""
        return self._played

    @property
    def settled(self) -> bool:
        """Whether the settling rule holds for every pair that may meet."""
        if self._threshold is None:
            return False
        table = self._tables[-1]
        meetings = self._meetings(len(self._played))
        return not any(
            self._owed(first, second, meetings) or self._unsettled(table, first, second)
            for first, second in self._pairs
        )

    @property
    def done(self) -> bool:
        """Whether the phase has ended, with no match under way and none to start."""
        if self.playing or self._waiting:
            return False
        if self.mode == 'batched':
            return len(self._started) >= len(self._plan)
        number = len(self._started)
        return self._pick(number, len(self._played), self._started) is None

    def ratings(self) -> dict[str, dict[str, Any]]:
        """Returns the rating of each side as it stands, with its interval and its matches."""
        table = self._tables[-1]
        return {key: self._rating(table, key) for key in self.sides}

    def _preview(self) -> list[dict[str, Any]]:
        """Returns the pairings that come next as far as the results so far tell."""
        if self.mode == 'batched' or self._waiting:
            return [dict(pairing) for pairing in self._waiting]
        started = list(self._started)
        found = []
        for _ in range(self.at_once):
            picked = self._pick(len(started), len(self._played), started)
            if picked is None:
                break
            pairing = {'number': len(started), 'home': picked[0], 'away': picked[1]}
            started.append(pairing)
            found.append({**pairing, 'provisional': True})
        return found

    def total(self) -> int:
        """Returns the matches of the phase, those played once it has ended, else the most."""
        if self.mode == 'batched':
            return len(self._plan)
        return len(self._played) if self.done else self.most

    def state(self) -> dict[str, Any]:
        """Returns the state of the schedule as `_store.queue.save_queue` keeps it.

        `next` holds the matches that wait for a slot after a restart, and after them the
        pairings that the results so far point to, each marked `provisional`, which a later
        result may change. `intervals` holds the rating of each side with its bounds and its
        matches, as `ratings` returns them, beside the bare numbers of `ratings`.
        """
        return copy.deepcopy(
            {
                'mode': self.mode,
                'ratings': {key: entry['rating'] for key, entry in self.ratings().items()},
                'intervals': self.ratings(),
                'played': self._played,
                'playing': self.playing,
                'next': self._preview(),
                'count': len(self._played),
                'most': self.most,
                'settled': self.settled,
                'total': self.total(),
                'at_once': self.at_once,
            }
        )

    def progress(self) -> dict[str, Any]:
        """Returns what a view of the phase shows, as plain data.

        The mapping holds `mode`, `played`, the matches whose result counts, `total`, the
        matches of the phase, `most`, `settled`, `done`, `playing` and `next`, the pairings
        under way and to come, `ratings`, the rating of each side with its `low` and `high`
        bound, its `matches` and whether it is `measured`, and `history`, each played match in
        order with its `number`, `home`, `away`, `score` and the `ratings` of its two sides
        right after it.
        """
        return {
            'mode': self.mode,
            'played': len(self._played),
            'total': self.total(),
            'most': self.most,
            'settled': self.settled,
            'done': self.done,
            'playing': copy.deepcopy(self.playing),
            'next': self._preview(),
            'ratings': self.ratings(),
            'history': copy.deepcopy(self._played),
        }
