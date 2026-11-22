# Ratings

The **ratings** of Evaluate say how strong each model is on a scenario, on one scale, with how
much evidence stands behind each number. The renderer keeps one table for each scenario. Every
match that it plays on that scenario is evidence for the table, a match of a tournament, a match
of a rating run, a match of an evaluation and a requested match alike. A rating is always shown
with its interval and with the number of matches behind it, so a model that has played little
reads as what it is, a guess with a wide margin.

A researcher gathers evidence for two models in one call, and reads the table afterwards.

```python
run = cuju.arena.rate(['rex', 'ace'], '11_vs_11_kaggle')
```

## Models and checkpoints

A **model** is a team at a checkpoint. A team of the registry is named by its identifier, and its
checkpoint is a digest of the files that it plays with, the first sixteen characters of the
SHA-256 of its folder, without the renderer's own details of the team, such as its name and its
kit. The rating belongs to the two together, written as `rex@3fa9c1d200a1b2c3`.

When the files of a team change, the team plays with another model. The new checkpoint starts
with no evidence, at the rating of the anchor and with the widest interval, and the earlier
checkpoint keeps its rating, its matches and its history under the same team. A table shows the
team as its **latest** checkpoint, the one whose result is the newest, and marks each row with
`latest`. For a team of the registry the row also tells in `current` whether that checkpoint is
the one of the files as they are now, so a page can say that a team has changed since it was
last rated. `GET /api/ratings/teams/KEY` answers every checkpoint of one team with the whole
history across them.

Three more kinds of model have no files to digest.

- **The built in AI** of Google Research Football is a model at each difficulty that a match
  used, such as `builtin:0.60`, and `builtin:scenario` where a match left the difficulty to the
  scenario. It never changes, so it gives every table fixed points to read the others against.
- **A policy given as a function** is rated by the name that `[evaluate] name` gives it, as
  `policy:NAME`. The key `checkpoint` of `[evaluate]` adds the place of the policy in its
  training run, so `policy:ppo@step-2000` and `policy:ppo@step-4000` are two models of one
  policy, and the history of the policy across them is its learning curve.
- **A person** who played a match of a tournament is rated by name, as the entrants of a
  tournament are.

The difficulty that a match gives the built in AI teammates of a team is left out of the name of
the model. A team that controls every player of its side plays the same at any difficulty, and a
team that controls one player is rated together with the teammates that it was given.

## Evidence

The evidence is a list of results and nothing else. Each result is one small row of
`ratings/results.jsonl` in the home folder, which holds the time, the scenario, the key and the
checkpoint of each side, the score, the source, a reference to the match within its source, the
seed of the match, and where they exist the recording of the match and the place of its kept
actions. New rows record the versions of the engine, cuju-arena and NumPy, plus cuju-render
when it is installed. A row takes about 400 bytes, so a table of ten thousand matches stays
near four megabytes. The names of the models lie beside the rows, once for each model, in
`ratings/models.json`.

A table is made from its rows alone, in their order, every time it is read after a change. No
rating is stored, so a rating is always reproducible from the results, and deleting or adding a
row changes the table as if the match had never been played or had been played.

The **source** of a row is one of four.

| Source | The match |
| --- | --- |
| `tournament` | a played match of the draw of a tournament, a forfeit left out |
| `rating` | a match of a rating run, the seeding phase of a tournament included |
| `evaluation` | a match of an evaluation whose policy has a key, unless `[evaluate] rate` is false |
| `match` | a requested match, from the page, the command line or Python |

The key `sources` of `[ratings]` names the sources that count. A table of `["rating"]` alone
reads the matches that were played to rate, and leaves out the rest.

**A result counts once.** A second result with the source and the reference of a row, with the
recording of a row, or between the same two models on the same scenario and seed, is the same
match and is left out. A match on a seed plays the same again, step for step, so an evaluation
run twice on one seed adds its evidence once. A match between two sides of one model, such as a
team against itself, tells nothing and is left out as well.

**A row points to what shows the match.** A match of a tournament and a requested match are
recordings, and their rows name the recording. A match of a rating run or of an evaluation keeps
its seed and its actions, and its row names the run and the number of the match there. The kept
actions of a full match take from half a kilobyte, where the built in AI plays alone, to a few
kilobytes for a policy that controls every player, while its recording takes from a quarter of a
megabyte on `5_vs_5` to several megabytes on `11_vs_11_kaggle`. A match of a rating run can be
drawn as a recording at any later time, bit for bit as it was played, and its row then names the
recording too.

**Tables stay apart.** A result belongs to the exact scenario it was played on, so `5_vs_5` and
`11_vs_11_kaggle` never mix. A match of a set length is another game than its scenario, so it
joins a table of its own, named by the scenario and the length, such as `5_vs_5@400`. A result
whose scenario is unknown stays readable in a table of its own, under the scenario null.

**The earlier ratings** are those that tournaments kept before the tables, one number for each
team key whatever the scenario, by the Elo rule from 1500. They are kept as they stand and
readable through `GET /api/ratings/earlier`, on their own scale. They name no scenario and no
checkpoint, so they enter no table, and no result joins them any more.

## The anchor

One model is the fixed point of every table, the **anchor**. It is the built in AI at the
difficulty that the key `anchor` of `[ratings]` names, 0.6 by default, the medium opponent of
GRF and one of the three default opponents of an evaluation. The anchor stands at a rating of
**1000** in every table and by both methods, with an interval of no width, and every other
rating reads against it. A model at 1100 is expected to take 64 percent of the points of a match
against the anchor, and one at 1400 takes 91 percent.

Evidence against the anchor ties a model to the scale by matches of its own, where it would
otherwise be tied only through the models that it met. Each row therefore counts its
`anchor_matches` beside its `matches`, and a rating run always plays each model against the
anchor first.

Changing `anchor` reads every table against another difficulty from then on. The rows stay as
they are, and the tables are made anew from them.

## The two methods

Every row of a table holds its rating by two methods, on the same scale of 400 points for each
tenfold of strength, both anchored at 1000. The key `method` of `[ratings]` says which of the
two ranks the table and seeds the draw of a tournament, `points` by default, and each answer
names the method of its numbers.

**Points** is the classic Elo rating. Each match gives its points, 1 for a win, a half for a
draw and 0 for a loss. Each model starts at 1000, and after a match its rating moves by 20 times
the gap between the points it took and the points that the two ratings expected of it. The
anchor is held where it is, so a match against the anchor moves the other side alone, by the
whole step. The rating by points is the running score of a model, and it is the number that a
rating run shows moving after every match.

The value by points depends on the order of the matches. A model that loses five matches and
then wins five ends higher than one that wins five and then loses five, since each win of the
first came as more of a surprise. This is why the rows keep their order, and the same rows in
the same order always give the same ratings.

Beside the rating by points stands a **deviation**, which tells how much the matches of the
model could tell. It starts at 350 points and falls as the matches add up, as
`1 / sqrt(1 / 350**2 + (ln 10 / 400)**2 * sum)`, where each match adds `p * (1 - p)` at the share
`p` of the points that the ratings expected. A match between level sides tells the most, and one
whose result was almost certain tells little. The interval is the rating less and plus the
deviation times the quantile of the confidence, 1.96 at 95 percent. It is an approximation, the
deviation of a rating under the logistic model after those matches, and it says how wide the
doubt is rather than where the rating would lie under another order.

**Bradley and Terry** fits every result of the table at once. A model of strength p beats one
of strength q with the chance of p over the sum of p and q, a draw counts as half a win for each
side, and the fit finds the strengths under which the results are the most likely, with the
anchor held at 1000. As its prior each model also plays one draw against a virtual model of the
strength of the anchor, which keeps the rating of a model that won or lost every match finite
and gives a model without a match a rating of 1000 with a deviation near 350, as by points. The
order of the results changes nothing. The deviation comes from the curvature of the fit at its
ratings, the inverse of the matrix of its second derivatives, and it grows for a model that is
tied to the anchor through few matches, however many it played against others.

The two agree where the order is clear, and they part on few matches and on results whose order
matters. Points answers how a model has done match after match. Bradley and Terry answers which
strengths explain all the results best.

This ranking by points is the Elo rating, where a match gives a share of one point. It is
another thing than the table of a league stage of a tournament, which ranks its entrants by
three points for a win and one for a draw, as [Tournaments](evaluate-tournaments.md) describes.

## A worked example

Three models play six matches on one scenario, in this order. `rex` and `ace` are teams, and
`builtin:0.60` is the anchor.

<!-- example results -->
```json
[
  {"home": "rex", "away": "builtin:0.60", "score": [2, 1]},
  {"home": "builtin:0.60", "away": "ace", "score": [0, 0]},
  {"home": "rex", "away": "ace", "score": [1, 0]},
  {"home": "ace", "away": "builtin:0.60", "score": [3, 1]},
  {"home": "builtin:0.60", "away": "rex", "score": [1, 1]},
  {"home": "ace", "away": "rex", "score": [0, 2]}
]
```

By points, `rex` beats the anchor first. Both stand at 1000, so each is expected to take half a
point. `rex` took one, and moves by 20 times a half to 1010, while the anchor stays at 1000.
`ace` then draws with the anchor, as expected, and stays at 1000. In the third match `rex` at
1010 is expected to take 0.514 of a point from `ace` at 1000, wins, and moves by 20 times 0.486
to 1019.71, and `ace` falls by the same to 990.29. The other three matches go the same way.

The table then holds these ratings, each as the rating, the lower bound and the upper bound of
its interval at 95 percent.

<!-- example ratings -->
```json
{
  "rex": {"matches": 4, "points": [1028.61, 723.42, 1333.8], "bradley_terry": [1248.34, 840.67, 1656.01]},
  "builtin:0.60": {"matches": 4, "points": [1000.0, 1000.0, 1000.0], "bradley_terry": [1000.0, 1000.0, 1000.0]},
  "ace": {"matches": 4, "points": [991.1, 685.98, 1296.22], "bradley_terry": [1018.43, 667.64, 1369.23]}
}
```

`rex` leads by both methods, with four matches, three wins and a draw. The fit places it higher
than its points do, since 20 points a match is a slow step for so few matches, and both give it
an interval of several hundred points, which is the honest reading of four matches. `ace` took a
point and a half of two from the anchor and lost twice to `rex`. By points it ends just below
the anchor, since its last match was a loss that cost it nine points, and the fit, which sets
those two losses against the strength of `rex`, places it just above. The two methods part here
as they do on few matches, and the intervals say that neither order is settled. The table
`[ratings.evidence]` would count `rex` and `ace` as rated, with four matches each and two
against the anchor.

The same in a program, with two difficulties of the built in AI on a short scenario.

<!-- example program -->
```python
import cuju.arena

run = cuju.arena.rate(
    ['builtin:0.1', 'builtin:1'],
    'academy_run_to_score_with_keeper',
    overrides=['ratings.run.matches=2'],
)
print(run['state'], run['progress']['played'], 'matches')
for key in run['models']:
    row = run['after'][key]
    print(key, row['method'], row['rating'], row['low'], row['high'], row['matches'])
```

## What counts as rated

The table `[ratings.evidence]` names the evidence that a model needs before it counts as rated.

| Key | What a model needs | Default |
| --- | --- | --- |
| `matches` | this many matches on the scenario | 4 |
| `anchor_matches` | this many of them against the anchor | 2 |
| `width` | an interval of at most this many points from its lower to its upper bound, by the method of the table | unset, any width |

Each row of a table tells whether it is `rated` and, where it is short, what it is `lacking`, a
mapping of the `matches` and the `anchor_matches` that it misses and of its `width` where that
is too wide. `POST /api/ratings/evidence` answers the same for any list of models, those without
a result included, and a tournament seeded by rating asks it of each entrant. A team that plays
under a format is a model of its own in the table, so the report takes `submission_format`, the
format the models would play under, and reads each team's rows under that format. Collect evidence
sends the format of its Format choice with the report and with the run, and a tournament sends its
own format with the report of its entrants.

In text, a model row reports its match count as `1 match` or `N matches`. The count is followed
by the number of matches against the anchor.

## Rating runs

Pass several checkpoints of one team as separate models, such as `Greens@baseline` and
`Greens@step:3000`. The run and its rating table track them separately. The table gives the
checkpoint identifier and shows the label and training step when available. A tournament
result uses the label and step of the checkpoint that played. For older tournament results that
stored a team reference as the label, the table can recover these details from the registry
while it still holds that checkpoint. Submitting the same team at the same checkpoint twice
gives a usage error with exit status 2.

Each saved match in `rating show ID --json` has a `sides` array in home and
away order. Each side holds a `checkpoint` object with the identifier,
label and step captured for the run, or `null` for a side without a
checkpoint. A recording drawn from that match uses the saved snapshots.
Later registry edits leave those values unchanged.

A **rating run** gathers evidence for chosen models on one scenario.

```python
run = cuju.arena.rate(['rex', 'ace', 'builtin:0.3'], '5_vs_5', config='ratings.toml')
```

```
cuju arena rating run rex ace builtin:0.3 --scenario 5_vs_5
```

A model is the identifier of a team of the registry, alone or as `team:ID`, or the built in AI
as `builtin`, a colon and a difficulty. The team is rated at the checkpoint of its files as they
are when the run begins. A run whose team changes its files meanwhile ends with an error that
says so, and a new run rates the new checkpoint.

Every team of a run plays under one submission format, Kaggle open unless the run chooses
another with `submission_format`, such as `rate(['rex', 'ace'], '5_vs_5',
submission_format='strict/1')`. The run checks each team under that format as it starts, and a
team that the format refuses leaves the run, listed under `excluded` with its problems. A model
under a format other than Kaggle open is a model of its own in the table, and a match that a
format forfeits is never evidence. [Choosing a format](formats.md#choosing-a-format) tells the
whole of it.

The run plays in two parts.

1. **Against the anchor.** Each model plays the anchor `anchor_matches` times, the home side in
   turn, before any other match and whatever the ratings say.
2. **Where the order is least settled.** The schedule then picks each next pairing among the
   models and the anchor. Each side holds its rating by points with its deviation, which the
   run moves after every match, starting from what the table held. Two sides are settled once
   the gap of their ratings reaches the quantile of the confidence `settle` times the deviation
   of that gap, each of them has played what `[ratings.evidence]` asks, and its interval is as
   narrow as `width` asks. The next match goes to the pair that is least settled, with a quarter
   added for each earlier meeting, so two level sides leave room for the others.

The run ends once every pair is settled or the most matches are played, the models times
`matches` unless `most` says otherwise. With the step of 20 points the ratings by points move
slowly, so a run of eight matches for each model usually ends at its most matches, and a longer
run settles a clear order in about fifteen matches for each pair.

A run plays as an evaluation does. Matches of the built in AI alone play in a batch, all at once
with `mode` as `batched`, which `auto` picks where every model of the run is the built in AI. A
team plays each match in a process of its own, a few at a time as `at_once` says. Either way the
run keeps the seed and the actions of every match in `runs/ID.actions`, one byte for each agent
and step, and writes no frame. The matches that the rule `keep` names are drawn as recordings
once the run has ended, by the same rule and the same drawing as the kept matches of an
evaluation, `nothing` by default. Any other match is drawn on request.

```
cuju arena rating list
cuju arena rating show rate-20261117-101500-3fa9
cuju arena rating draw rate-20261117-101500-3fa9 4
cuju arena rating check rate-20261117-101500-3fa9
```

The list command gives the identifiers of stored runs, and the show command prints one
run. The draw command prints the path of the resulting recording. Give
`rating table` a scenario when choosing a rating method with `--method`. The built-in methods
calculate the ratings from stored match results without loading NumPy or the match engine.

`--draw` writes the recording of one match and names it in the row of the match. `--check` plays
every match of a run again from its kept actions and confirms each score and length, after
holding the actions to the digest that the run keeps.

The seed of a run is drawn anew unless `seed` sets it, so two runs never play the same match.
Within a run the pairings follow from the seed and the results alone, and the document of the
run, `ratings/runs/ID.json`, is written after every change. A run that a stop cut goes on from
there with the same pairings, the matches that were under way play again, and no kept result
plays twice. `cuju.arena.rate` and the command play the run in their own process, and the
server plays the runs that a page asks for one at a time, each in a child process, and takes up
those that a stop left when it starts again.

### Progress

A run tells where it stands as one plain mapping, the same that the seeding phase of a
tournament shows. The function that `rate` takes as `progress` receives it after every change,
and `GET /api/ratings/runs/ID` answers it under `progress`.
The arena coordinates reads and updates of the saved progress within each process.

| Field | Meaning |
| --- | --- |
| `run` | the identifier of the run |
| `phase` | `rating`, or `seeding` for the run of a tournament |
| `state` | `queued`, `running`, `done`, `failed` or `cancelled` |
| `scenario`, `anchor` | the scenario of the table and the key of the anchor |
| `method` | `points`, the method of the ratings that the run moves |
| `mode` | `batched` or `on_demand` |
| `played`, `total` | the matches whose result counts, and the matches of the run, which on demand is `most` until the run has ended and the matches played after it |
| `most`, `settled`, `done` | the most matches, whether every pair is settled, and whether the run has ended |
| `playing` | the matches under way, each with its `number`, its `home` and `away` key, the `meeting` of the two before it, its `index` and its `seed` |
| `next` | the matches to come. On demand each is marked `provisional`, since a later result may change it |
| `ratings` | the rating by points of each side by key, with its `rating`, the `low` and `high` bound of its interval, its `matches` in the run and whether it is `measured` |
| `history` | each counted match in order, with its `number`, `home`, `away`, `score`, `time` and the `ratings` of its two sides right after it, so a view draws each rating as it moved |
| `entrants` | for each key the name, the `kind`, the `index`, the `checkpoint` and the `model` of the side, and whether it is `measured` |

A side that is not measured is the anchor or another model that the measured ones meet. It keeps
its rating through the run, with the interval that the table held for it.

The answer of `rate` and of the route holds more beside `progress`.

| Field | Meaning |
| --- | --- |
| `id`, `state`, `created`, `ended` | the run and its times in seconds since the epoch |
| `scenario`, `seed`, `anchor`, `models` | what the run plays, and the keys of the models that it measures |
| `owner`, `job` | `tournament:ID` for the seeding phase of a tournament, and who plays the run |
| `settings` | the table `[ratings]` that the run was made with |
| `matches` | each match with its `number`, `home`, `away`, `index`, `seed`, `score`, `length`, the `digest` and the file of its `actions`, whether the rule keeps it in `keep`, and its `recording` once it is drawn |
| `record` | the `state` of the drawing, `none`, `waiting` or `done`, and the matches `drawn` and `failed` |
| `before`, `after` | what the table held of each side before the run and holds now, with `rating`, `low` and `high` by the method of the table, the same under `points` and `bradley_terry`, the `matches`, the `anchor_matches`, and whether the side is `rated` and what it is `lacking` |
| `error`, `error_id`, `error_values` | the failure of a run that failed |

## Tournaments and the table

A tournament whose draw the ratings seed reads the table of the scenario of its first stage.
Each entrant is a model there, a team at the checkpoint of its files or the built in AI at the
difficulty that the matches give it, and its rating by the method of the settings seeds the
draw. A rating that the configuration states for an entrant still wins, and is marked as given.
It reads on the scale of the tables in a configuration that names `evidence`, and a
configuration of 0.1.0, which names no `evidence`, reads its ratings on the earlier scale from
1500 and takes 500 off each, as [Tournaments](evaluate-tournaments.md#settings) describes.

When the tournament is set up it checks whether each entrant holds the evidence that
`[ratings.evidence]` asks, and reports who lacks what. The key `evidence` of the tournament, or
of the table `[tournament]`, then decides among three.

| `evidence` | What the tournament does |
| --- | --- |
| `refuse` | It is not made, and the refusal names each entrant that lacks and what it misses. This is the default. |
| `collect` | It opens with a seeding phase, a rating run for the entrants that lack, and draws once the run has ended. |
| `proceed` | It draws with the ratings as they stand, and its file marks the entrants that lack. |

The default refuses, so that a command or a program never plays matches that nobody asked for
and never seeds a draw from too little without saying so. The page shows the same report and
offers to collect, and [Tournaments](evaluate-tournaments.md#the-seeding-phase) describes the
phase and what the file of a tournament keeps of the ratings that seeded it.

## On the Evaluate page

The Ratings section of the Evaluate page shows the tables as a standings board, the way a
broadcast shows a league table between matches. The address `#page=evaluate&section=ratings`
opens it, and the address may add `scenario=<scenario>` for a table, `model=<key>` for the page
of a model and `run=<id>` for a rating run to follow.

### The table

A switch of scenarios stands at the head of the section, one choice for each scenario that holds
a table, the one with the most results first. A table of matches of a set length is named with
its length, such as 5 vs 5, 400 steps. The line under the switch names the anchor and the rating
it holds, 1000, and the row of the anchor carries the tag Anchor, so the fixed point of the scale
is always in sight.

Each row of the table is one model, its place, its small shirt in the kit of its team, its name
and the checkpoint it stands for. The rating by the method in view shows large, with the half
width of its interval after a plus and minus sign. The rating by the other method stands beside
it in small, so the two can be compared on every row. Matches counts the results behind the
rating and, beneath, those against the anchor. W D L gives the wins, draws and losses. Evidence
lists where the results came from, tournaments, rating runs, evaluations and requested matches,
each with its count. Status says Rated for a model that holds the evidence the settings ask for,
or what it still needs, such as Needs 3 more matches, 2 more matches vs anchor. A model that
lacks evidence shows its rating in a softer ink, since it is still a guess.

### The two methods

A second switch picks the method that ranks the table, Points Elo, the default, or Bradley-Terry,
and a line of help beneath it says what each one is. Points Elo moves a rating a few points after
each result, in the order the results came. Bradley-Terry fits all the results at once, whatever
their order. Every row carries both ratings, so the switch changes only which one ranks the table
and shows large, and the rows slide to their new places as the order changes. With reduced
motion they take their places at once. The method of the settings, `[ratings] method`, is the one
the section opens with.

### A model's page

A row's name opens the page of that model, and Table at its head goes back to the row. The head
of the page gives the model's place, its rating by the method in view and its rating by the
other. Below it stand four cards.

- **Checkpoints** lists every checkpoint of the team as a row of its own, with its rating by both
  methods, its matches and those against the anchor, its record and whether it is rated. The
  checkpoint of the team's files as they are now carries the tag Current.
- **Rating over time** draws the rating by points of each checkpoint after each of its results,
  one line for each checkpoint, so the climb of a model through its training shows as lines that
  rise from one checkpoint to the next.
- **Opponents** lists the models that it met, the most met first, with its record against each.
- **Results** lists every result, the newest first, with its score, the opponent, where it came
  from, the points it moved, and what it offers. Watch opens the recording of a result that has
  one. Draw draws a match of a rating run from the actions it kept, the seed and the scenario, as
  a recording of the library, and then offers Watch. A result with neither says Result only.

### Collecting evidence

Collect evidence opens a panel above the table. It takes a scenario, chosen on a switch of
buttons like the one above the table, and the models to rate, the teams of the registry, the
models of the table and the built in AI at a few difficulties, each beside its small shirt. The
scenario and the models of the table in view come chosen.

Check evidence asks the server what the table holds of each chosen model and lists what each one
lacks, such as 3 more matches, or that it has enough. Start rating run queues a rating run of the
chosen models on the scenario, which always plays matches against the anchor first, and the panel
then follows the run live.

The live view of the run has four parts.

- A **progress bar** of the matches played of the total, with the count beside it, and a tag of
  the state of the run, Waiting, Live, Settled once the order of the models is clear, or Full time
  once the run has played its last match. On demand the total is the most matches the run may
  play.
- **Queue** opens the matches playing now and those up next. On demand the next matches are
  provisional, since each one is chosen from the ratings so far.
- The **live table** ranks every side of the run by its rating in the run, the anchor marked, each
  with the matches it played in the run and its rating with its interval. A row that climbs moves
  up to its new place, and its place carries a small mark of the climb.
- A **chart** draws the rating of every side over the matches of the run, from where each side
  started.

The table of the section reads its rows again as each result lands, so the standings move with
the run. A screen reader hears each new result in one short sentence, such as Rex 2, Built-in AI
0.60 1. Rex now 1,012, and the table is left alone. The view reads the run's own document, so it
holds after a reload of the page, and a run that still waits or plays shows again whenever the
section opens.

### The earlier ratings

Earlier ratings, 1500 scale, under the tables, opens the ratings of before the tables, one for
each team key, each with its rating and its matches. They name no scenario and no checkpoint, so
they stand apart from the tables and never mix with them.

![The Ratings section in the dark set, the table of 5 vs 5 with the anchor marked and a model that needs more evidence](https://github.com/cuju-football/render/blob/main/tests/support/shots/evaluate-ratings-desktop-dark.png)

## Over HTTP

| Route | Answer |
| --- | --- |
| `GET /api/ratings` | the scenarios that have a table, each with its `results`, its `models` and the time of its `last` result, with the `anchor`, the `method`, `methods`, the `confidence`, the `sources`, the `evidence` asked and the number of `earlier` ratings |
| `GET /api/ratings?scenario=NAME` | the table of one scenario, `&method=` ranks it by the other method, and `&format=` keeps the teams of one format, such as `kaggle/1`, beside the built-in AI |
| `GET /api/ratings/teams/KEY?scenario=NAME` | one team, with its `latest` row, its `checkpoints`, its `history` and its `opponents` |
| `GET /api/ratings/earlier` | the earlier ratings, with their `scale` and the rating they `start` from |
| `POST /api/ratings/evidence` | what the table holds of the `models` of the body on its `scenario`, and what each lacks, under the `submission_format` of the body where it names one |
| `GET /api/ratings/runs` | the rating runs, newest first |
| `POST /api/ratings/runs` | queues the run of the body, a mapping of `models`, `scenario`, optionally `ratings`, keys of `[ratings]` laid over the settings, and optionally `submission_format`, the format of every team |
| `GET /api/ratings/runs/ID` | one run, as the section on progress describes |
| `POST /api/ratings/runs/ID/check` | plays the matches of an ended run again and answers whether each is the `same` |
| `POST /api/ratings/runs/ID/draw/NUMBER` | draws one match as a recording |
| `DELETE /api/ratings/runs/ID` | cancels a run that waits or plays, and removes one that has ended, whose results stay in the table |

A table holds its `scenario`, its `anchor`, the `method` that ranks it, `results`, the rows that
count, and `models`, best first. Each model holds these fields.

| Field | Meaning |
| --- | --- |
| `id`, `key`, `checkpoint` | the model, its team key and its checkpoint, null for a model without files |
| `name`, `kind`, `label`, `step` | the name and the kind of the model, and the label and the step of its checkpoint where a result gave them |
| `rank`, `rating`, `low`, `high`, `deviation`, `method` | the place of the model, and its rating with its interval and its deviation by the method of the table |
| `points`, `bradley_terry` | the `rating`, `low`, `high` and `deviation` by each method |
| `matches`, `anchor_matches`, `wins`, `draws`, `losses` | the evidence of the model |
| `sources` | its matches by source |
| `first`, `last` | the time of its first and of its newest result |
| `width`, `rated`, `lacking` | the width of its interval, and whether it holds the evidence asked |
| `anchor`, `latest`, `current` | whether it is the anchor, whether it stands for its team, and whether its checkpoint is that of the files of the team now |

Each entry of the `history` of a team holds the `time`, the `checkpoint` that played, the
`opponent`, the `score` with the goals of the team first, the `source`, the `phase` and the
`ref` of the result, its `recording` and its `actions` where it has them, and `points`, the
rating by points of the checkpoint `before` and `after` the match.

## Settings

The table `[ratings]` holds the settings, with these defaults.

```toml
[ratings]
method = "points"
anchor = 0.6
sources = ["tournament", "rating", "evaluation", "match"]
rule = "default"
confidence = 0.95

[ratings.evidence]
matches = 4
anchor_matches = 2
width = "none"

[ratings.run]
mode = "auto"
matches = 8
anchor_matches = 2
at_once = 2
most = "none"
settle = 0.9
seed = "none"
pairing = "least_settled"
device = "cpu"
threads = 0
keep = "nothing"
limit = 32
```

| Key | Meaning |
| --- | --- |
| `method` | `points`, `bradley_terry` or the name of a rating method of a [plugin](plugins.md#rating-methods), the method that ranks a table and seeds a draw |
| `anchor` | the difficulty of the built in AI that stands at 1000, from 0 to 1 |
| `sources` | the sources whose results count |
| `rule` | the evidence rule, `default` or the name of one of a [plugin](plugins.md#evidence-rules), which keeps the results of the sources that count |
| `confidence` | the confidence of the intervals, from 0.5 to below 1 |
| `evidence` | the table of what a model needs to count as rated, as its section describes |
| `run` | the table of a rating run |

The keys of `[ratings.run]` are these.

| Key | Meaning |
| --- | --- |
| `mode` | `auto`, `batched` or `on_demand` |
| `matches` | the matches that each measured model plays, which also sets the most matches of a run on demand |
| `anchor_matches` | the matches of each measured model against the anchor, which come first |
| `at_once` | the matches at once on demand, from 1 to 8 |
| `most` | the most matches on demand, the measured models times `matches` when unset |
| `settle` | the confidence at which two models count as settled, from 0.5 to below 1, or unset to play the most matches |
| `seed` | the seed of the run, drawn anew when unset |
| `pairing` | the pairing rule of a run on demand, `least_settled` or the name of one of a [plugin](plugins.md#pairing-rules) |
| `device`, `threads` | the device of the batches and their CPU threads, 0 for one for each processor |
| `keep` | the matches drawn as recordings, by the rule of `[evaluate.record]`, where `wins`, `draws` and `losses` are those of the home side |
| `limit` | the recordings drawn at most |

## Files

| File of the home folder | Content |
| --- | --- |
| `ratings/results.jsonl` | the rows of evidence, one result on each line, in the order they were kept |
| `ratings/models.json` | the name, the kind and the checkpoint's label of each model |
| `ratings/runs/ID.json` | the document of a rating run, with its schedule and its matches |
| `runs/ID.actions` | the seed and the actions of the matches of a rating run, as an evaluation keeps them |
| `ratings/ratings.json`, `ratings/history.jsonl` | the earlier ratings and what moved them, kept as they stand |

## See also

- [Evaluation runs](evaluation.md), whose matches are evidence and whose way of playing a rating run shares
- [Tournaments](evaluate-tournaments.md), which read the table and gather what lacks
- [Configuration](configuration.md), the layers of the settings
- [Folder and server](https://github.com/cuju-football/render/blob/main/docs/server.md), the home folder
