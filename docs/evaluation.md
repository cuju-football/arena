# Evaluation runs

An evaluation plays a policy against declared opponents and summarizes its results. Use it to
measure a checkpoint under the same scenarios and seeds as earlier checkpoints, then use
the saved actions to check the results.

## The policy and its opponents

The **policy** is what plays. `cuju.arena.evaluate` takes it in one of four forms.

- A function `policy(observation, batch)`. It receives the observation of every match at once
  and the batch, and returns the actions as a tensor of the shape `[matches, agents]`, or the
  actions and a mapping of extras. This is the form that a training loop already holds.
- The text `module:function`, such as `agents.ppo:make`. The function is called with the keyword
  arguments of `policy_options` and returns a policy of the first form. The module is one that
  Python imports, with the working folder among its places, or a file that ends in `.py`.
- The text `team:ID`, a team of the registry, as [Teams](teams.md) describes.
- The text `builtin`, the built in AI of Google Research Football on the whole side.

With `interface = "kaggle"` the function is a Kaggle agent instead, `agent(obs)` or
`agent(obs, configuration)`, which the renderer calls once a step for each match through
`cuju.kaggle.KaggleAgent`, in one batch all the same.

The **extras** are what a policy knows beside its actions. The mapping takes the names that
`cuju.arena.annotate` takes as keyword arguments, `values`, `probabilities`, `logits`,
`targets`, `grids` and `arrows`, each with the shapes that [Annotations](annotations.md)
describes for a whole batch. The kept recordings carry them as annotations, so the inspector
shows the value and the likely actions of each agent on each step.

An **opponent** is the built in AI at a difficulty or a team of the registry. The shipped
settings name three, the built in AI at the difficulties 0.05, 0.6 and 1.0, which are the easy,
medium and hard opponents of the scenarios of GRF.

## How an evaluation plays

The arena records the evaluation in three steps. The renderer can display it later.

1. **Play.** Every match is one environment of a batch that plays each environment once, without
   automatic resets. The runner keeps the actions of every step, one byte for each agent, with
   the goals and the length of each match. It reads no state and captures no frame, so the batch
   plays at its own speed on the CPU and on the GPU alike.
2. **Keep.** After the last match the rule `keep` of `[evaluate.record]` picks the matches worth
   watching, such as the losses.
3. **Draw.** Each kept match plays again in a batch of one environment on the CPU, from the same
   seed and with the kept actions, under a recorder. The match plays as it did, bit for bit, on
   either backend, and the recorder writes a full recording with the lanes of the players, the
   actions, the state of the kick off and the extras as annotations. This step runs in a process
   of its own, so `evaluate` returns as soon as the results stand.

Match `k` against any opponent is index `k` of the seed rule of cuju for the seed of the
evaluation. Two policies evaluated with the same settings therefore meet the same matches, and a
difference between their results comes from the policies alone. With `sides = "both"` each seed
plays twice, once with the policy on the left and once on the right.

The recordings of one evaluation form one group of the library, which the Recordings page shows
as one row, and each recording names the run in its provenance. A replay that gives another
score or length than the match had is marked in the manifest and leaves no recording, so a
recording always shows what the evaluation counted.

## Batched and on demand

The matches play in one of two ways, which `mode` chooses.

**Batched** plays every match against an opponent at once in one batch. It needs a policy and
opponents that play in a batch, a function or the built in AI against the built in AI.

**On demand** starts a few matches at a time, as `at_once` says, and picks each next match where
the order of the ratings so far is least settled. Each side of the evaluation holds an Elo rating,
which every result moves by the rule of the tournaments, and beside it a deviation that shrinks
with every match. A policy and an opponent are settled once the gap of their ratings reaches the
quantile of the confidence `settle` times the deviation of that gap. The next match goes to the
opponent with the least such ratio, with a quarter added for each earlier meeting so that two
level sides leave room for the others. An opponent that is settled plays no more, and the run
ends once every opponent is settled or `most` matches are played. These ratings start at 1000
for every side, where the anchor of the tables stands, and serve the schedule of the evaluation
alone, while the ratings of a scenario are made from the results of every run, as
[Ratings](evaluate-ratings.md) describes. On demand serves every kind of team, and a team of the
registry plays each match in a process of its own.

With `mode = "auto"`, the default, an evaluation plays batched where the policy and every
opponent can, and on demand otherwise. A team of the registry always plays on demand, since its
policy is called as Kaggle calls it, one match at a time. A function meets the built in AI in
either way, and a policy that is to meet a team enters as a team of the registry. On demand,
match `k` against an opponent is still index `k` of the seed rule, so it is the match that the
batch would have played, and an evaluation on demand plays a part of the matches of the batched
one.

The state of the queue goes to the manifest after every change, so a view shows at any moment
what was played, what plays and what comes next, and an evaluation of the server goes on after a
restart from where it stopped.

## From Python

`cuju.arena.evaluate(policy, config=None, overrides=(), *, progress=None)` plays an evaluation
and returns an `Evaluation`. `config` and `overrides` take the settings as
[Configuration](configuration.md) describes, and `progress` is a function called after every
change with the progress of the run.

```python
import cuju.arena


def policy(observation, batch):
    logits, value = model(observation)
    actions = logits.argmax(-1).int()
    return actions, {'values': {'value': value}, 'logits': logits}


result = cuju.arena.evaluate(
    policy,
    config='eval.toml',
    overrides=['evaluate.episodes=200', 'evaluate.checkpoint=120000'],
)
print(result.summary['overall']['win_rate'])
print(result.recordings)
```

An `Evaluation` reads its run from the manifest and reports its current state. Open it
with `cuju.render.show(result)` in the renderer, or use `cuju render open ID`.

| Member | What it holds |
| --- | --- |
| `id` | the identifier of the run, such as `e-20261117-101500-3fa9` |
| `name`, `run`, `checkpoint` | the name of the model, the series that the run belongs to and its place in it |
| `kind`, `state` | `evaluation` or `benchmark`, and `queued`, `running`, `done`, `failed` or `cancelled` |
| `config` | the resolved settings stored with the results, with the engine version under `cuju` and the versions of cuju-arena and cuju-render |
| `summary` | the results overall and against each opponent |
| `episodes` | the matches, each with its `number`, `opponent`, `index`, `side`, `seed`, `goals_for`, `goals_against`, `outcome`, `length` and `recording` |
| `recordings` | the identifiers of the kept recordings. Reading it waits for the process that draws them |
| `progress`, `queue` | where the run stands, as the next section describes |
| `wait(timeout=None)` | waits until the run has ended and its kept matches are drawn |
| `log(target, step=None, prefix='eval')` | writes the results as scalars to TensorBoard or W&B |
| `scalars(prefix='eval')` | the numbers that `log` writes, by tag |
| `to_dict()` | the evaluation as plain data, as the server answers it |

The **summary** holds `confidence`, `overall` and `opponents`, the results over every match and
against each opponent. Each set of results holds these fields.

| Field | Meaning |
| --- | --- |
| `episodes`, `failed` | the matches with a result, and those that ended without one |
| `wins`, `draws`, `losses` | the outcomes from the side of the policy |
| `win_rate` | the wins over the matches |
| `win_rate_interval` | the lower and the upper bound of the Wilson score interval of the win rate at the confidence of `[evaluate.stats]`, which stays between 0 and 1 for any number of matches |
| `goals_for`, `goals_against` | the goals of the policy and of its opponents |
| `goal_difference` | the `mean` goal difference of a match, with the `low` and `high` bound of a bootstrap, the matches drawn again with replacement as many times as `resamples` says, by a generator seeded from the seed of the evaluation, so the same results always give the same interval |
| `length` | the mean length of a match in steps |
| `stats_episodes` | the matches that hold football statistics, those that were drawn |
| `possession`, `pass_accuracy`, `shots`, `shots_on_target` | the means of the policy's side over those matches, from the statistics of their recordings |
| `actions` | the share of each action of the policy over those matches, by action number |

The football statistics come from the statistics that the library keeps of each recording, so
they cover the kept matches. With `episodes = "all"` in `[evaluate.stats]` every match is drawn
for its statistics, and only the kept ones stay in the library.

`cuju.arena.runs(kind='evaluation')` lists the evaluations of the manifest, newest first, each
as the whole manifest of its run, which [Folder and server](https://github.com/cuju-football/render/blob/main/docs/server.md) describes. With
`run='SERIES'` it lists the evaluations of one series, such as the checkpoints of one training
run, which the dashboard draws as one curve, and `cuju.arena.Evaluation(id)` opens any of them
by its `id`.

## Progress

A run reports where it stands as one plain mapping, the same for an evaluation and a benchmark,
and a rating run reports the same fields with a few more, as [Ratings](evaluate-ratings.md#progress)
describes. `Evaluation.progress` returns it, the function `progress` of
`evaluate` receives it after every change, and `GET /api/evaluations/ID` answers it as
`progress`. It is read from the manifest alone, so it holds after a restart as well.

| Field | Meaning |
| --- | --- |
| `phase` | `evaluation` or `benchmark` |
| `state` | the state of the run |
| `mode` | `batched` or `on_demand` |
| `played`, `total` | the matches whose result counts, and the matches of the run, which on demand is `most` until the run has ended and the matches played after it |
| `most`, `settled`, `done` | the most matches, whether the settling rule holds for every opponent, and whether the run has ended |
| `playing` | the matches under way, each with its `number`, its `home` and `away` key, the `meeting` of the two before it and its `seed` |
| `next` | the matches to come. On demand each is marked `provisional`, since a later result may change it |
| `ratings` | the rating of each side by key, with its `rating`, the `low` and `high` bound of its interval, its `matches` and whether it is `measured` |
| `history` | each played match in order, with its `number`, `home`, `away`, `score` and the `ratings` of its two sides right after it, so a view draws each rating as it moved |
| `entrants` | the name, the kind and the index of the side of each key |

`queue` holds `playing`, `next`, `played`, `most` and `settled` of the same mapping.

## Checking an evaluation

An evaluation stores what replays it. The folder `runs/ID.actions` beside the manifest holds the
actions of every match, and the manifest holds a digest of the actions of each. A check first
holds the kept actions to their digests, so a file that changed is found even where the change
leaves the score as it was. It then plays every match again at the speed of a batch, with the
kept actions in place of the policy, and holds each score and length to those of the manifest.
The check needs neither the policy nor the device of the first run, since the same seed and
actions give the same match on the CPU and on the GPU.

`cuju arena evaluation check ID` runs the check and exits with 1 when a match differs.

## On the command line

```sh
cuju arena evaluation run [TEAM] [--series NAME] [--config FILE]... [--set KEY=VALUE]... [--json]
cuju arena evaluation list [--series NAME] [--json]
cuju arena evaluation show ID [--json]
cuju arena evaluation check ID [--device DEVICE] [--json]
cuju arena evaluation remove ID
```

Given alone, `cuju arena evaluation run` plays the policy that the settings name in `policy`, or
the team named as an argument, and prints the results, a line for each opponent and one
overall, with the match count, win rate, its interval and the goal difference. A report with
one match against an opponent reads `1 match` on that opponent line. Each change prints a line of
progress on standard error, prefixed by the evaluation identifier. `--json` prints the evaluation as the server answers it, and `cuju render open ID` opens it on the Evaluate page.

```sh
cuju arena evaluation run t-20261106-101500-3fa9 --series baseline --episodes 200
cuju arena evaluation run --config eval.toml --set evaluate.policy=agents.ppo:make
```

The run option `--episodes N` sets the number of episodes per opponent. It sets
`evaluate.episodes` and takes precedence over the value from files and `--set`.

The run option `--series NAME` sets `evaluate.run` and reads the name as text. For example,
`--series 42` names the series `42`, as does `--set 'evaluate.run="42"'`. Use `--series none`
to leave the evaluation without a series. Use `list --series NAME` in a new shell to find
the runs of that series, for example `cuju arena evaluation list --series 42`. If the series matches
nothing, the command lists the known names on one line on standard error, using quotes and
escapes for the names. A line break in a name appears as `\n`. The `show`, `check` and
`remove` commands accept an exact identifier, a unique prefix of at least four characters
after the type prefix, or `last` for the newest evaluation. The remove command deletes the
manifest and actions of a finished evaluation and leaves its recordings in the library.
Show reads the summary saved by a completed run. An active run or an older manifest
without a summary calculates its statistics from the available matches. Both paths read
the manifest without loading NumPy or the match engine.

The evaluation captures the checkpoint identifier, label and step of each registered
submission when it starts. Each episode in JSON holds a `sides` array in left and
right order. A side's `checkpoint` object is a snapshot, and a side without a
checkpoint has `null` there. Recordings drawn later use the same snapshots
under `provenance.sides`. Editing a checkpoint after the run leaves the episode
and recording snapshots as they were.

Set `evaluate.checkpoint` with an identifier, label and step to describe the run
in its series. For a registered policy, this run descriptor defaults to the
checkpoint digest and the team identifier. The team argument selects the files
that play and supplies the per-side checkpoint snapshot.

## Settings

The table `[evaluate]` holds the settings of an evaluation, with these defaults.

```toml
[evaluate]
name = "none"
run = "none"
checkpoint = "none"
policy = "none"
policy_options = {}
interface = "batch"
scenario = "11_vs_11_kaggle"
agents = 1
difficulty = "none"
episodes = 100
seed = 1000003
device = "cpu"
threads = 0
sides = "left"
mode = "auto"
at_once = 1
most = "none"
settle = 0.9
pairing = "least_settled"
rate = true

[evaluate.batch]
representation = "simple115v2"

[[evaluate.opponents]]
kind = "builtin"
difficulty = 0.05

[[evaluate.opponents]]
kind = "builtin"
difficulty = 0.6

[[evaluate.opponents]]
kind = "builtin"
difficulty = 1.0

[evaluate.record]
keep = "losses"
limit = 32
annotations = true
extras_mb = 256
background = true

[evaluate.stats]
episodes = "recorded"
confidence = 0.95
resamples = 1000
```

| Key | Meaning |
| --- | --- |
| `name` | the name of the model on the dashboard and in the ratings, the policy's own name when unset |
| `run` | the series that the dashboard joins, such as a training run |
| `checkpoint` | the place of the evaluation in its series, an identifier, a whole number that is its step, or a table of `id`, `label` and `step`. A team of the registry takes the digest of its files when unset |
| `policy` | the policy for the command line and the server, `builtin`, `team:ID` or `module:function` |
| `policy_options` | the keyword arguments of the function that `policy` names |
| `interface` | `batch` for a function that plays every match at once, or `kaggle` for a Kaggle agent |
| `scenario` | the scenario, without agent counts |
| `agents` | the players that a function controls, from 0 to 11. A team and the built in AI bring their own |
| `difficulty` | the difficulty of the built in AI on the side of the policy, which plays its other players, from 0 to 1, the scenario's own when unset |
| `episodes` | the matches against each opponent |
| `seed` | the seed of the batches |
| `device`, `threads` | the device of the batches, `cpu` or `cuda`, and the CPU threads, 0 for one for each processor |
| `sides` | `left`, or `both` to play each seed from each side |
| `mode` | `auto`, `batched` or `on_demand` |
| `at_once` | the matches at once on demand, from 1 to 8. A function plays one at a time |
| `most` | the most matches on demand, the episodes against every opponent when unset |
| `settle` | the confidence of the settling rule, from 0.5 to below 1, or `"none"` to play the most matches |
| `pairing` | the pairing rule on demand, `least_settled` or the name of one of a [plugin](plugins.md#pairing-rules) |
| `rate` | whether the matches are evidence for the ratings of the scenario, for a team, the built in AI and a function with a `name`, as [Ratings](evaluate-ratings.md) describes |
| `batch` | any option of `BatchEnvironment`, so the evaluation sees what training saw |
| `opponents` | the opponents, each with its `kind`, `builtin` or `team`, its `difficulty` and, for a team, its `team` |
| `record` | what to keep, as the next table describes |
| `stats` | the statistics, with `episodes`, `recorded` or `all`, the `confidence` of the two intervals and the `resamples` of the bootstrap |

| Key of `[evaluate.record]` | Meaning |
| --- | --- |
| `keep` | the matches kept as recordings, `all`, `nothing`, `losses`, `draws`, `wins`, `goals` for those with a goal, `first:8` for the first eight, or `worst:8` for the eight of the least goal difference |
| `limit` | the recordings kept at most |
| `annotations` | whether a kept recording carries the extras of the policy |
| `extras_mb` | the megabytes of extras held during play at most, in half precision. Extras beyond are left out, counted in the manifest and reported once through the logger `cuju` |
| `background` | whether the kept matches are drawn in a process of their own while `evaluate` returns |

The word for keeping no match is `nothing`, since `"none"` stands for an unset key in every
table of the settings.
