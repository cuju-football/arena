# Requested matches

A **requested match** is a match between two teams of the registry that the arena plays
headless, in a process of its own, and saves as a recording of the library.

`cuju arena play` plays the match in its own process and prints the path of the recording it
saved. With `--json`, it prints one indented JSON object with the recording's identifier
`match` and its `path`. When the match ends, standard error shows its identifier, teams and score
as `played ID  LEFT A-B RIGHT`. In a terminal, progress also shows the share of the match played.
Submissions write to standard error, and `--quiet` keeps only errors on that stream. A
submission that fails ends the command with one line that names the team and the reason, while
`cuju.arena.play` raises the submission's own error. The recording appears under Recordings,
with the source `requested_match` in its provenance. The list of requested matches on the Matches
page holds the requests of the server, those made on the page or through `POST /api/requests`, so
a match of the command line shows under Recordings alone.

`cuju.arena.play` returns the path of the recording, and the path also holds the `summary` that
the library keeps of the match. `path.summary['score']` gives the goals of the left and the right
team. The summary's `length` counts played steps. Its `steps` counts captured frames,
including the initial frame. The summary names the `teams`, `scenario`, `outcome` of the
left team and `provenance` with the seed as well. `cuju arena play --json` answers with the same
`summary` and with the `score` beside the `match` and its `path`, so a script that plays many
matches reads each result from the answer. The `left_checkpoint` and `right_checkpoint`
fields give the checkpoint identifier, label and training step as `id`, `label` and `step`.
A side without a checkpoint has `null` in its field.
The JSON answer also gives these snapshots as `sides[0].checkpoint` and
`sides[1].checkpoint`, in left and right order. The recording keeps the same
snapshots under `provenance.sides`, which `recording show --json` reads.
Later edits to a checkpoint label or step leave the snapshots as they were.

Use `--left` and `--right` to select a team by identifier, a unique prefix of at least four
characters after the type prefix, or an exact name ignoring case. Append `@CHECKPOINT` to
choose the files the team plays. A checkpoint reference can be an identifier, a unique prefix,
a label, `step:N` for a recorded training step, or `latest` for the newest kept checkpoint.
Supply the reference after the at sign. When a label matches a different checkpoint from the
selector of the same spelling, the command prints the exact references you can use to retry.

The `[play]` table supplies the default scenario, seed and right side when the corresponding
options are absent. The shipped scenario is `11_vs_11_kaggle`, the right side is `builtin`
and the seed is drawn when unset. For repeated runs, keep the following in `arena.toml`
or in a file named by `--config`.

```toml
[play]
scenario = "academy_empty_goal_close"
seed = 7
right = "builtin:0.3"
```


The recording of a requested match, and of each match of a tournament, keeps the sections beside
its frames that a `Recorder` writes, the lanes of the players, the action of every agent of a
team on every step and the state of the kick off, with the agents, the action set and the run key
in its header. A batch of the same configuration that loads the state and plays the actions plays
the match again bit for bit, so a match of interest can be studied step by step, as
[Recording format](recording-format.md#sections-beside-the-frames) describes. A side of the built
in AI has no agents, so its players appear in the lanes and none of its actions in the recording.

A match requested on the page plays in a process of its own on the CPU and becomes a recording in
the library. The play command also takes a device. Each submission runs from its own folder,
with its own modules, as on Kaggle. Every Python process that the arena starts, for a match, a
game, a tournament, a benchmark, an export or the server, runs with `-P` on Python 3.11 and later,
which keeps the arena's folder off its path of modules, so folders such as `teams` and
`requests` never import as packages, and on Python 3.10 it starts in the empty folder `run` of the
arena's folder instead. Full matches follow the team size, five or eleven a side.
When a registered team's size differs from the full-match scenario, `play` prints
its usage and exits with status 2.
Academy scenarios accept either team size when the requested players exist on that side.
The default scenario is the Kaggle competition scenario. The built in AI plays any scenario.
When a match reaches the time limit, it plays one more step than the scenario's GRF duration.
See the [GRF duration rule](evaluate-tournaments.md#match-length).

Each side of a requested match takes a **difficulty**, a number from 0 to 1 that sets how well
the built in AI plays the players it controls on that side, which is GRF's team difficulty under
the engine's own name. It steers the whole team of the built in AI and, in a scenario where a
submission controls one player of its side, that player's teammates as well. A side left alone
plays the scenario's own difficulty, and the recording keeps the difficulty that each team played
with under `provenance.ability` in both the header and the library summary.

| Where | How it is set |
| --- | --- |
| `cuju arena play` | the options `--left-difficulty` and `--right-difficulty` |
| `cuju.arena.play` | the argument `difficulty`, a pair of the left and the right side, such as `(0.6, None)` |
| `cuju arena tournament` | the option `--difficulty`, before every entrant for the standard, after one for its own |
| A tournament file | the key `difficulty` of the tournament, a stage, an entrant or a preset |
| The Matches page | the Difficulty control of each side, Auto until it is set |

The tournament file calls the difficulty `ability`, so a
script written before stays as it is. A call or a mapping that names both with two different
values is refused. The page offers a slider for each side in steps of 0.05, and Auto, which is
where it starts, leaves the scenario's own value. The same controls on Play set the difficulty
of the teammates and opponents there.

The arena includes six training scenarios from GRF MARL by Light MALib. Each format has easy,
medium and hard variants, with team difficulties of 0.05, 0.6 and 1.0. They take 3001 calls to
`step`, with the second half at step 1500, swap the formations by episode parity and leave both
goalkeepers uncontrollable. Their names are shown below.

```text
benchmark_full_game_5_vs_5_easy
benchmark_full_game_5_vs_5_medium
benchmark_full_game_5_vs_5_hard
benchmark_full_game_11_vs_11_easy
benchmark_full_game_11_vs_11_medium
benchmark_full_game_11_vs_11_hard
```

Pass their names to `cuju.arena.play` or to the server's match and game requests. They are
available to the renderer through scenario files and the bundle cache. The scenario lists on Matches
and Play hold the full matches, `1_vs_1_easy` and the academies, and leave out these six and
GRF's example scenario `test_example_multiagent`, which stay playable from Python and the API.

## The Matches page

The Matches page is where a match is set up and kicked off, and where the queue shows every
requested match from its wait to its recording. The New match tile of Home opens it. The page works
with the keyboard and with the pointer, and the controller belongs to Play alone.

![Matches in the dark set, with home and away teams and their difficulty controls above the scenario, seed, batch and Kick off](https://github.com/cuju-football/render/blob/main/tests/support/shots/pages-matches-desktop-dark.png)

### A new match

The top of the page shows the home team on the left, VS and the away team on the right, each as a
team tile. A new match starts with the built in AI on both sides, so a first match needs no team of
your own. Choosing a tile opens the list of every team, with Random team first, and each team shows
its small shirt in its kit and its format. The tile itself shows the team's name, its shirt, and for
a submission its format and the number of players it controls, and resting on the tile shows the
whole control setting of the submission. Home plays on the left of the pitch and Away on the right.

A submission that has more than one checkpoint shows a stepper under its tile. The current files
come first, labeled Current, and each press of the arrows moves to an earlier checkpoint, shown by
the label that you gave it or the first characters of its identifier. The request then names the
side as the team and the checkpoint, written `team@checkpoint`, so two checkpoints of one team can
meet in a single match, or a checkpoint can play the team that came after it. Choosing another team
puts the stepper back on the current files. [Team files](team-files.md#checkpoints) describes the
checkpoints of a team.

Each side takes a Difficulty, the control of the built in AI that the section above describes. A
stepper moves through Auto, where each side starts and which leaves the scenario's own value, then
the levels of the built in AI, and last Custom. Each level shows its difficulty and its rating on
the table of the chosen scenario, and the level that anchors the table is marked Anchor, so you can
pick an opponent by how strong it is. Custom opens a slider in steps of 0.05 for any number from 0
to 1, so every difficulty stays reachable. Resting on the label shows what the number steers.
The stepper is the one that the team menu uses, a single stop of Tab. The left and down arrows
step to an easier stop, the right and up arrows to a harder one, Home and End reach Auto and
Custom, and Enter opens a field where typing the start of a stop's name, or a level's number,
picks it.

Every side that has files has a Format choice, so two formats can meet in one match. A side that
plays the built in AI or is drawn at random has no files to judge, so it shows no choice and plays
Kaggle open. The choice starts on Kaggle
open, which plays a match as Kaggle's environment does, and a stepper moves through Kaggle, Strict
and the formats that you added. Under the choice the verdict of the side's team shows, accepted,
refused or not checked yet, with a button that checks the team under the chosen format and opens its
terminal. The chapter
[Submission formats](formats.md) describes what each format asks of a team. When both sides stay on
Kaggle open the request is the one that Matches always made, with no format in it, so scripts that
read the requests see no change.

The scenario, the seed and the batch stand under the two sides. The scenario field opens the list of
the scenarios that both sides can play, with Random scenario first when two or more fit, and resting
on the field shows the description of the scenario. The seed field takes a whole number, and leaving
it empty lets the server draw one. The dice button draws a seed into the field now, which lets you
read it before the match plays. The batch is the number of matches that one Kick off queues, from 1
to 64. A batch of more than one counts up from the seed, so a batch of three from the seed 10 queues
the seeds 10, 11 and 12, and with an empty seed it counts up from one that the page draws. A batch
is the quick way to play the same pairing on a run of seeds, and the matches of an evaluation run
over those seeds are read from Recordings.

A random side adds the Pool of the draw under the tiles, one box for each team and a box that lets a
team meet itself, as [Matches drawn at random](#matches-drawn-at-random) describes. A team that
cannot play the chosen scenario shows faded with the reason on hover and for a screen reader.

Kick off queues the match. The toast names the two teams, and a batch names its seeds. A page that
cannot reach the server shows how to start it, and a refused request shows the server's reason.

### The queue

![The queue of Matches in the dark set, a playing match with its bar, two finished matches with Watch, Compare and Repeat, and a forfeit that shows the side and the kind of its break](https://github.com/cuju-football/render/blob/main/tests/support/shots/pages-matches-queue-desktop-dark.png)

Below the new match the queue lists every request of the server, the newest first, each as a card.
A card shows the two teams with their shirts, a badge for the state, and a chip for each fact of the
request. Those are the scenario, the seed, the difficulty of a side that was set, the format of a
side that did not play Kaggle open, the date, and the time played. A waiting match shows a bar that
fills as the match plays. A part that was drawn at random shows as Random while the match waits,
and as what was drawn, with a dice mark, once it starts.

| Button | Where | What it does |
| --- | --- | --- |
| Watch | a finished match | opens the recording in Watch |
| Compare | a finished match | lists the other finished matches, the ones of the same scenario first, and opens Watch on the pair with the linked timeline, as [Compare](https://github.com/cuju-football/render/blob/main/docs/compare.md#compare-in-watch) describes |
| Repeat | a finished match | queues it again with the same seed, difficulty and formats, and a match drawn at random draws anew |
| Retry | a match that did not finish | queues it again with the same seed, difficulty and formats |
| Cancel | a waiting or playing match | asks the server to stop it |

A match that a side forfeited shows as a forfeit, with the flag in its badge, the side that
forfeited, the kind of the break and the reason that its format gave, such as an answer that came too
late. The section on [forfeits](formats.md#forfeits) lists the kinds. A forfeit leaves no recording,
and Retry plays it again. A match that failed for another reason shows the message of the server, and
a match that a restart of the server interrupted says so.

The filters above the cards narrow the queue by state, team, scenario and date, and the last chip
sorts it. The State filter lists a forfeit apart from a failure, and the Team and Scenario filters
list what the cards show. A team drawn at random for a match that has not started stays out of the
filters until the match starts, since the card still shows Random. Show more at the foot of a long
queue adds the next 50 cards.

The queue asks the server for its list again only when the last answer has come, every one and a
half seconds while a match plays or the page is in view, and every few seconds while the page is
elsewhere. An answer that fails leaves the list as it was and says so in a line above it, with Try
again when the whole list did not load. A good answer from the server, to this page or any other,
clears the offline notice.

## Matches drawn at random

A side of a requested match may be drawn at random. On the Matches page the list of each side
starts with Random team, and on the command line `--left random` or `--right random` stands in for
a team identifier. When the request is made, the server draws a team for each random side from
the **pool**, the teams that the person ticks under Pool of the draw on the page or names with
`--pool` as identifiers separated by commas, and every team of the registry, the built in AI
among them, when no pool is given. A team takes part in the draw of a side only when it can play
that side of the scenario, so its format must match the scenario's and its control settings must
fit the players of that side. In the drill `academy_3_vs_1_with_keeper`, for example, the left
side holds four controllable players and the right side two, so a team that controls three
players can be drawn for the left side alone. A random side that no team of the pool can play is
refused with the message `server.draw.left` or `server.draw.right`, which names the scenario.

Two sides that are drawn, or a drawn side that meets a named team, take different teams whenever
another team of the pool fits. A pool with a single team that fits plays that team on both sides.
Let a team meet itself on the page, or `--same-team` on the command line, lets the draw pick one
team for both sides however many fit, which suits a team that should play against its own copy
now and then.

The scenario can be drawn too. The scenario list of the page offers Random scenario first
whenever two or more scenarios fit, and `--scenario random` asks for it on the command line. The
draw then picks one of the scenarios that the page lists, the full matches, `1_vs_1_easy` and the
academies, among those where each side has a team that can play it. The six training scenarios and
GRF's example scenario stay out of the draw. When both the scenario and a side are random, the
scenario comes first, and the teams then fit it. A request where no listed scenario has a team for
each side is refused with the message `server.draw.scenario`.

The seed of the request decides the draw, the same seed that the match plays with. A request
without a seed takes one that the server draws, as always, and the draw then follows it. The same
seed and the same pool always draw the same teams and scenario, on every computer, so the draw
of a match is repeatable from its seed. A team added to the registry or removed from it changes the
draw of a request without a pool, since the pool is then the whole registry at the time of the
request. `cuju arena play` prints the teams, scenario and seed that it drew before the match
plays, and with `--json` adds `left`, `right`, `scenario`, `seed` and `draw` to its answer. The
recording keeps the draw in its provenance, beside the teams that played.

## Teams and matches over HTTP

Scripts and agents reach the registry and the requested matches through the routes of the
server, which [HTTP routes](https://github.com/cuju-football/render/blob/main/docs/http.md#teams-and-matches-over-http) lists with their bodies and
answers.
