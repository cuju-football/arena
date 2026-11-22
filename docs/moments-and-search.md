# Moments and captions

The arena names the moments of every match, the goals, shots, saves, blocks, shots off the
woodwork and near misses, the passes, sliding tackles, fouls, cards and offsides, the restarts with
their set pieces, the changes of possession, half time and full time. Each moment carries the step
and time of its event, the team and the player, the score and the clock at that step, and what
later frames settle, such as the end of a shot, the receiver of a pass, whether a tackle won the
ball and the scorer and assist of a goal. The moments come from the event catalog of the
page, `web/js/recording/catalog.js`, version 1 of its rules, and the page and the
server read the same moments from the same frames, with the same identifiers and times, for
recordings of both versions of the format.

A change of possession counts when the ball passes to the other team from the team that last held
it, so a loose ball that its team wins back counts as none. A set piece is a restart other than the
kick off, a corner, a free kick, a penalty, a throw in or a goal kick.

## Searching moments

The server keeps an index of the moments of every recording of the library in the subfolder
`index` of the arena folder, one small file per recording. The index catches up when the
server starts and before each search, so a recording that arrives is read once, a recording whose
file changes is read again, and the file of a recording deleted for good goes with it. A recording
in the Trash keeps its file and leaves the results until it is restored. A new version of the
moments or of the index rebuilds every file. A full match of eleven a side takes about a second to
read on one core of a laptop processor.

## The query language

A query is a line of words and filters, all of which a moment must match. Words that name kinds
choose moments of any of those kinds, and other words match the title, scenario, folder, teams and
tags of the recording and the roster name of the player. A filter is a name, a colon and a value,
and quotes hold a value of several words together, as in `team:"Harbour Rovers"`. A minus sign
before a word or a filter leaves out what it matches. Case and accents never matter.

| Word | Moments |
| --- | --- |
| `goal`, `own-goal` | goals, and own goals alone |
| `shot`, `header` | shots, and headers at goal alone |
| `save`, `block` | saves by a goalkeeper and blocks by a defender |
| `woodwork`, `post`, `bar` | the ball off the frame of a goal, of a post or of the bar |
| `near-miss` | a ball just wide of a post or just over the bar |
| `pass`, `tackle` | passes and sliding tackles |
| `foul`, `card`, `yellow`, `red`, `offside` | fouls, cards of either color or one of them, offsides |
| `restart`, `set-piece` | every restart, or the set pieces alone |
| `corner`, `free-kick`, `penalty`, `throw-in`, `goal-kick`, `kickoff` | one kind of restart |
| `possession` | changes of possession |
| `halftime`, `fulltime` | half time and full time |

| Filter | Matches |
| --- | --- |
| `kind:goal,corner` | any of the kinds listed, by the same words |
| `team:Greens`, `team:left`, `team:right` | moments credited to a team, by reference, by a part of its recorded name or by its side |
| `player:9`, `player:Okafor` | moments of a player, by shirt number or by a part of the roster name |
| `scenario:5_vs_5` | recordings of a scenario, by a part of its name, with spaces or underscores |
| `source:tournament` | recordings by source, `requested`, `game`, `benchmark`, `recorder`, `live`, `import`, `tournament` or `unknown` |
| `tournament:Spring` | recordings of a tournament, by reference or by a part of its name or stage |
| `match:sample-5v5` | one recording by reference, or recordings by a part of the title |
| `date:today`, `date:yesterday`, `date:2026-11-10`, `since:2026-11-01`, `until:2026-11-30` | recordings by the day they were made |
| `after:60`, `before:45` | moments by a finite match clock time, in minutes, as `60:30` or in seconds as `90s` |
| `score:leading`, `score:level`, `score:trailing`, `score:2-1` | the standing of the team of the moment just before it, or the score at the moment, left first |
| `final:2-1` | the final score of the recording, left first |
| `outcome:saved`, `outcome:wide`, `outcome:complete`, `outcome:won` | how a shot, a pass, a save or a tackle ended |
| `limit:20` | the most results, 50 by default and at most 1000 |

Use `goal final:2-1 date:yesterday` to find goals in matches that ended 2 to 1
yesterday. Use `score:2-1` to find moments when the score was 2 to 1. Relative
days use the local calendar.

Use whole numbers for shirt numbers, both sides of a numeric score and the result limit.
A value the search cannot read as the expected number gives a usage error and exits with
status 2. For example, use `player:2` for a shirt number written as `²` elsewhere.
The match clock filters `after:` and `before:` require finite times. NaN and infinity give
the same usage error in minutes, in seconds or in a time such as `60:30`.

For `match:` and `tournament:`, use an exact identifier, a unique prefix of at least four
characters after `m-` or `cup-`, or `last` for the newest recording or tournament. The type
prefix is optional for an abbreviation. When a prefix matches several identifiers, the search
reports the ambiguity and gives up to five candidates. A date must be possible on the calendar.

The `team:` filter selects the team credited with the moment. Use an exact identifier,
a unique prefix of at least four characters after
`t-`, or the exact registered name ignoring case. You can omit `t-` from a prefix. The
filter compares a team reference with the identifier saved for the side credited with each
moment. This finds a team's earlier moments when its registered name changes. You can also
search by part of the name shown in the recording or use `team:left` and `team:right` to
select the credited side. A prefix shared by several teams gives an error with up to five
candidate identifiers.

The signals of a policy find moments too, in the recordings whose model annotated its values and
the probabilities of its actions. Each signal term adds its moments beside those of the words of
kinds, and the filters above narrow them as they narrow every moment, so `value-drop team:left
after:45` lists the value drops of the left team in the second half. The thresholds come from the
`[search]` table of the settings when a search runs, so a change of them needs no new index.
[Searching by policy signals](policy-search.md) describes every term, its thresholds and a worked
example, and the same language serves the `keep` setting of a recorder.

| Signal term | Moments |
| --- | --- |
| `value-drop`, `value-drop:value` | a named value fell by at least `value_drop` within `window` seconds |
| `surprise`, `surprise:0.05` | the chosen action had at most that probability, `surprise` by default |
| `entropy:>1.5`, `entropy:<0.2` | the highest or lowest entropy of a player's actions past the bound |
| `reward:>0`, `ann:value<-0.5` | a named value went past the bound |

The outcome of a shot is `goal`, `saved`, `woodwork`, `blocked`, `redirected`, `keeper`, `wide`,
`over`, `stopped` or `none`, of a pass `complete`, `intercepted`, `out` or `stopped`, of a tackle
`won` or `lost`, and of a save `saved` or `scored`. An unknown filter or a value out of place stops
the search with a message that names what the query expects, such as `Unknown filter zz:. The
filters are kind:, team:, player:` followed by the rest of the filters. The name of a filter may
hold hyphens, as `value-drop:` does, so a word such as `free-kick:x` reads as the filter
`free-kick:` and stops the search as an unknown one. Quotes keep such a word as plain text, as in
`"free-kick:x"`, which matches the title, teams and players like any other word.

```text
goal team:Greens after:60
shot outcome:saved player:9
corner source:tournament since:2026-11-01
card -yellow
possession team:right score:trailing before:15
```

## Captions

A cue describes a match event in a short line at the time of the event. The arena supplies
cues for goals, shots, saves, tackles, restarts and other changes in play. Each cue holds a
start and end time, a message identifier and the values used to render its text. Use
`cuju arena captions REC` to print WebVTT. Give the command `-o FILE` to save it, or use
`--json` to inspect the cues.

## Agents

Agents reach the same index and captions without the page, and the server answers the same queries
with the same results.

In a shell, quote comparison filters so that the shell passes the comparison characters to Arena.
For example, run `cuju arena search 'ann:value<0.5' 'entropy:>1.5' 'reward:>0'`.

When a query excludes terms, quote the whole query as in `cuju arena search 'card -yellow'`.
You can also put all options first, then `--` and the query terms, as in
`cuju arena search --json -- pass -team:left`. The separator tells Arena that the remaining
arguments are query terms.

A multiword filter value needs quotes that reach the query parser. Put double quotes around
the value and single quotes around the filter, as in
`cuju arena search 'team:"Harbour Renamed"' date:yesterday --json`. The shell removes the outer
quotes and passes the inner quotes to Arena. The team filter then uses Harbour Renamed as one
name, including when it finds moments recorded under the team's old name.

| Route or command | Returns |
| --- | --- |
| `cuju arena search QUERY [--limit N] [--json]` | the results under the identifier and title of each recording, or as JSON |
| `cuju arena captions RECORDING [-o FILE] [--json]` | the captions of a recording or a `.cjr` file, as WebVTT or JSON |
| `cuju.arena.search(query, limit=None, config=None, overrides=())` | the results as a dictionary, with the thresholds of the policy signals read from the `[search]` table of `config` and `overrides` |
| `cuju.arena.captions(recording, path=None)` | the cues, written as WebVTT to `path` when given |

Each result holds the recording as `match` with its `title`, `scenario`, `created` date and
`source`, the moment's `id`, `kind`, `step` and time `t` in seconds of the recording, the `seek`
time four seconds before it, the `clock` in milliseconds and the `minute` as minutes and seconds,
the `team` and `team_name`, the `player` with its `number` and `name`, the `score`, a plain line of
`text`, the `data` of the moment and the `url` of the page that opens Watch at the seek time. The
search also returns the `total` number of moments, the number of `matched_recordings` that contain
them and the number of `recordings` searched. These counts cover the whole search before the
result limit. In text, the first value is the match clock, and `t` is the recording time in seconds.
The address seeks a few seconds earlier so that the moment has a lead-in. The footer reports them as `N moments in M of T recordings`. A cue holds its
`start` and `end` in seconds, its message `id`, its `params` and its `text`.

The arena prints a page fragment beside each result. To open a result in the renderer, pass
that fragment in quotes to `cuju render open`. The renderer uses its current server or starts one.

```console
$ cuju arena search goal team:left
sample-5v5  Sample match, 5 vs 5
    2:54  t=9.7  Goal, number 3 of Lakeside United, 1 to 0
          #match=sample-5v5&t=5.7
sample-11v11  Sample match, 11 vs 11
   12:46  t=42.6  Goal, number 10 of Harbour Rovers, 1 to 0
          #match=sample-11v11&t=38.6
2 moments in 2 of 2 recordings
Use cuju render open with an address to start the server.
$ cuju arena captions sample-5v5 -o sample-5v5.vtt
sample-5v5.vtt
```
