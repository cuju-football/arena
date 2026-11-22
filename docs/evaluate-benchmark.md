# The academy benchmark

The **academy benchmark** plays models headless on the academy scenarios of Google Research
Football and compares them in one table of goal rates. A model is a team of the registry, the
same team that requested matches and tournaments play, GRF's built in AI, or a policy that a
program holds as a function. The benchmark lives in `cuju.arena.benchmark`, on the Evaluate
page, in the command `cuju arena benchmark run`, in the routes under `/api/benchmarks` and in the
script entry `window.cuju.benchmark`, and all five play the same tasks on the same seeds by the
same rules.

## The tasks

Every academy scenario is a **task**, named after its scenario without the start `academy_`. The
benchmark lists the eleven tasks in the order of the table below, which is the order of every
table and file it writes.

| Task | Scenario | What the model has to do |
| --- | --- | --- |
| `empty_goal_close` | `academy_empty_goal_close` | score into an empty goal from close range |
| `empty_goal` | `academy_empty_goal` | run from the middle and score into an empty goal |
| `run_to_score` | `academy_run_to_score` | outrun five defenders and score into an empty goal |
| `run_to_score_with_keeper` | `academy_run_to_score_with_keeper` | outrun five defenders and beat the goalkeeper |
| `pass_and_shoot_with_keeper` | `academy_pass_and_shoot_with_keeper` | free the unmarked attacker with a pass and score |
| `run_pass_and_shoot_with_keeper` | `academy_run_pass_and_shoot_with_keeper` | run with the ball, pass when the defender commits and score |
| `3_vs_1_with_keeper` | `academy_3_vs_1_with_keeper` | combine three attackers against one defender and score |
| `corner` | `academy_corner` | score from a corner |
| `counterattack_easy` | `academy_counterattack_easy` | finish a counterattack of four attackers against one defender |
| `counterattack_hard` | `academy_counterattack_hard` | finish a counterattack of four attackers against two defenders |
| `single_goal_versus_lazy` | `academy_single_goal_versus_lazy` | score against outfield players who stand still |

Each task plays five **attempts**, and attempt `i` always plays the same seed,
`(crc32(task) + 7919 * i) % 2**31` with the identifier of the task in UTF 8. Every model
therefore faces the same five episodes of a task, on every machine and in every benchmark, so two
goal rates of one task compare whenever they were measured. `cuju arena benchmark tasks` and
`GET /api/benchmarks/tasks` give each task with its scenario, its attempts and its seeds.

## A run

A **run** is one model on one task, five attempts one after another. Each attempt plays in a
batch of one environment, with the seed of the attempt, the abilities that the scenario sets and
the action set that a requested match would choose for the team. The run goes through the same
runner that plays a team in a match, which waits for each answer, so a run goes as fast as the
policy answers. A policy has 120 seconds to load and to give its first answer, and 60 seconds for
every later answer.

An attempt **counts** when the model scored before its episode ended. The **value** of a run is
the number of its attempts that count, from 0 to 5, and its **goal rate** is the value over the
attempts, from 0 to 1. The result of each attempt holds its `index`, its `seed`, whether the
episode `finished`, the `goals` for and against, whether the model `scored` and the `seconds` of
play to its first goal, at ten steps a second.

A policy that fails, stops answering or answers with anything but actions ends that attempt
alone. The attempt ends at the step where the policy failed and counts as an attempt that ended
early, its goals against and no goals for, so a failure never helps. Its result says `failed`
with the policy's error in `error`, `error_id` and `error_values`, and the next attempt loads the
policy again, so the run goes on to its end. A policy that cannot load at all fails every attempt
of the run, and the run counts its failed attempts in `failed_attempts`.

A team trained for full games may control more players than the side of a task holds, such as a
five a side team of four agents on the empty goal, whose side holds two controllable players. The
team then plays with its extra players out, as a selected player who leaves the pitch after a red
card is out of a match, and the built in AI plays the rest of the side. A team of dynamic
assignment plays as many agents as the side has controllable players. A team of fixed assignment
plays the players of its selection that the side holds, by role or by index as in a match, and
the others of its selection are out, while the built in AI plays the players of the side outside
the selection, its goalkeeper among them. The policy is still called in its own form, Kaggle's
single form for one player and its multi agent form for several, with one observation for each
player it controls, and a player out sees the observation of the team's first player that plays,
whose answer for the player out counts for nothing. So that every goal rate stays honest, the
result of such a run says `playing`, the players that played and the players the team controls,
such as `[2, 4]`, and the cell of the benchmark says Played with 2 of its 4 players, the others
out. A run in which every player plays says `playing` as null. The size rule of matches stays,
so the academy tasks stay open to five a side and eleven a side teams alike, and a fixed selection
of which the side holds no player cannot play the task, which the benchmark then skips with the
reason.

A benchmark leaves the library as it is by default. With its recordings kept, each attempt becomes
a recording whose provenance names the source `benchmark`, the task, the attempt and the seed, as
[Recording format](recording-format.md) describes, and the recordings of one run form one group,
which the Recordings page shows as one row, as [Recordings](recording.md) describes. A failed
attempt keeps the frames played so far, with `failed` in its provenance.

## From Python

`cuju.arena.benchmark(policy, config=None, overrides=(), *, progress=None)` plays one model on
the tasks and returns an `Evaluation`, the object that [Evaluation runs](evaluation.md)
describes. The policy takes the forms that `cuju.arena.evaluate` takes, a function
`policy(observation, batch)`, the text `module:function`, `team:ID` or `builtin`.

```python
import cuju.arena

result = cuju.arena.benchmark(policy, overrides=['evaluate.name=ppo', 'evaluate.agents=1'])
print(result.summary['score'])
for task in result.summary['tasks']:
    print(task['task'], task['value'], task['rate'])
result.log(writer, step=update)
```

A policy that plays in a batch, a function or the built in AI, plays the five attempts of a task
at once in one batch, one batch for each task. Each environment of that batch starts from the
seed of its attempt, so attempt `i` is the episode that a team's run and a person's attempt play
on that seed, bit for bit. The runner keeps the actions of every attempt and captures no frame,
as an evaluation does, so `cuju arena evaluation check ID` plays a benchmark again and confirms
every result. A team of the registry plays its runs in child processes, as on the server.

The table `[benchmark]` of the settings names the tasks, and the table `[evaluate]` names the
model, with its `name`, `run`, `checkpoint`, `policy`, `policy_options`, `interface`, `agents`,
`difficulty`, `device`, `threads` and the options of its batches in `batch`.

```toml
[benchmark]
tasks = "all"
keep_recordings = false
workers = "auto"
```

| Key | Meaning |
| --- | --- |
| `tasks` | the academy tasks, `"all"` or a list of their identifiers, such as `["corner", "empty_goal"]` |
| `keep_recordings` | whether the library keeps the attempts as recordings. The attempts of a batch are drawn after play from their seeds and actions, as the kept matches of an evaluation are |
| `workers` | the runs of a team of the registry played at once, from 1 to 8, or `"auto"` for one for every four processors |

The `summary` of the returned object holds the `score` of the model, the attempts played in
`attempts`, those that count in `value`, and `tasks`, each task with its `task`, `scenario`,
`state`, `attempts`, `value`, `rate` and `failed_attempts`. Its `episodes` hold every attempt
with its `task`, its `index`, the seed of the catalogue in `own_seed`, its `goals_for`,
`goals_against` and `length`, and `to_dict()` adds `benchmark`, the benchmark as the section
The benchmark as data describes it. `log` writes `eval/score` and the goal rate of each task as
`eval/TASK/rate`. The run goes to the runs manifest as it plays, in the form that the list of
benchmarks reads, so the Evaluate page lists a benchmark from Python beside its own.

## On the Evaluate page

The Evaluate tab of the page bar opens the Evaluate page, whose Benchmark section holds the whole
benchmark. The section reads as a grid, models down the side and academy tasks across the top,
much as a results board of a football competition sets clubs against rounds.

Above the grid stand the choices. The Models list takes one or several teams of the registry,
GRF's built in AI among them, each shown beside the small shirt of its kit. The Academy tasks list
takes several or all tasks, with All tasks to choose or clear them at once. One model and one task
give a single run, and one model on every task gives the profile of that model. Beside the lists,
the Seeds figure gives the number of fixed seeds that each task plays, which is the number of
attempts behind every cell, and Keep the attempts of the benchmark as recordings, off by default,
keeps every attempt in the library as the group of its run. How the benchmark works, under the
heading, opens the short account of the rules that this guide gives in full.

Run benchmark starts the job on the server, which plays one benchmark at a time. A bar and a line
follow the runs done and the share of the attempts played, and Cancel benchmark ends the job, so
the runs that were left never start. The grid fills in as each run ends. Each cell shows the goal
rate of its run as a bold percentage and the attempts that count beneath it, such as 4 of 5, with
the count of failed attempts and the players out when there are any, or the state of the cell and
its reason. A played cell is tinted with the accent of the page in steps of a tenth, from the
plain surface for no goal to the strongest tint for a goal in every attempt, so the strong and
the weak tasks of a model stand out at a glance, and the strip under the grid shows the scale. The
tint never carries the number alone, since every cell keeps its figure. A cell whose attempts the
library keeps opens that group of recordings on the Recordings page. Each row ends with the score
and the place of its model. Export JSON saves the benchmark as the server answers it, and Export
CSV saves the table.

![The Benchmark section in the dark set, with the Kaggle open format and the built in AI results on three academy tasks](https://github.com/cuju-football/render/blob/main/tests/support/shots/evaluate-benchmark-desktop-dark.png)

Groups kept for explicitly selected checkpoints use the same checkpoint name as the benchmark
table. A group title uses the checkpoint label where one is present and the abbreviated
identifier otherwise.

## The score

The **score** of a model is the mean of its goal rates over the tasks of the benchmark, times 100
and rounded to one decimal. A cell that skipped, failed or was cancelled counts as a goal rate of
0, so a score of 100 means a goal in every attempt of every task, and a score of 50 on two tasks
may mean five goals on one and none on the other. Models place by score, and models level on it
share a place. A zero score can come from completed attempts without a goal, or from cells
that never played. Use the cell's state, played count and error to distinguish these cases.
The text progress names failures, and the JSON answer keeps those fields for each cell.

In JSON, `playable` counts team and task pairs that fit both the scenario and the
submission format. An eligible pair can still fail during play. When no pair fits,
the command exits with status 0 and prints one line on standard error saying that
nothing was played. Use `playable` to distinguish this outcome from a played score of zero.

The same model on the same seeds gives the same goal rates on every machine, as long as its policy
answers within its time limits and plays the same way on the same observations. Each pair of a
model and a task plays in a process of its own with a separate pre-check terminal, so no model's run sees the state of another, such
as a module or a global variable that a submission leaves behind. A run that ends without a
result, such as one whose process stops, leaves its cell failed with the reason, so the job
always goes on to the next pair.

A completed attempt gives its format worker time to shut down and return its CPU allocation
before the next attempt starts.

The server plays several runs of a benchmark at once, one for every four logical processors of the
machine by default, from 1 to 4, which `workers` sets from 1 to 8. Each run waits for its policy's
answers and plays as fast as they come, so a benchmark goes as fast as the models and the
processors allow. Each benchmark is a run of the runs manifest, written as it goes, so it
survives a restart of the server, which lists every waiting or running one and the newest 20
that ended. The server saves a completed benchmark and its results before reporting it as done.

## From the command line

`cuju arena benchmark run` plays a benchmark from the command line, with each team as a positional argument and `--task` once for each task, every task when none is named, and `--workers`, `--device`,
`--keep-recordings` and `--json` or `--csv`. `--config FILE` and `--set KEY=VALUE` lay settings
as every command does, so `[benchmark]` may name the tasks, the recordings and the workers in a
file, and an option of the command line wins over it. With no team argument, the command plays the
policy that the settings name in `policy` of `[evaluate]`, as `cuju.arena.benchmark` does, and
prints the score with a line for each task. A command that names neither a team nor a policy is
refused. Each run plays in a process of its own, as on the
server, and its end prints a line on standard error. The table goes to standard output, each model
with its place and score and each of its tasks with the attempts that count, or the whole
benchmark as JSON under `--json`, or the CSV under `--csv`. Ctrl+C ends the runs and exits with
130. A missing team argument or an invalid task exits with status 2. An unknown registered team
exits with status 1.
`benchmark tasks` prints the tasks, as JSON under `--json`.

```sh
cuju arena benchmark tasks
cuju arena benchmark run t-20261106-101500-3fa9 builtin
cuju arena benchmark run t-20261106-101500-3fa9 --task corner --task empty_goal --json
cuju arena benchmark run builtin --task empty_goal_close --csv > table.csv
cuju arena benchmark run builtin:0.3 random --task corner --keep-recordings
cuju arena benchmark run --set evaluate.policy=agents.ppo:make --task corner --json
```

The CSV has one row for each model, in the order chosen, with the columns `model`, the team's
name, `team`, its identifier, then one column for each task, named after the task, with the goal
rate in percent and one decimal, empty for a cell without a result, and `score` with one decimal
and `place`. Python's `csv` module and the page write it alike, in UTF 8 with a comma between
fields and quotes around a field that holds a comma, a quote or a line break.

## Over HTTP

| Request | Answer |
| --- | --- |
| `GET /api/benchmarks/tasks` | the tasks, each with its `id`, `scenario`, `attempts` and `seeds` |
| `GET /api/benchmarks` | the benchmarks of the server, newest first |
| `POST /api/benchmarks` | queues a benchmark and answers its entry with status 201 |
| `GET /api/benchmarks/ID` | one benchmark |
| `GET /api/benchmarks/ID/table.csv` | the table of one benchmark as CSV |
| `DELETE /api/benchmarks/ID` | cancels a benchmark that waits or runs |

The body of `POST /api/benchmarks` is a JSON object with `teams`, a list of distinct team
identifiers, and where wanted `tasks`, a list of distinct tasks, every task when absent,
`keep_recordings`, false by default, and `workers`, from 1 to 8. A field of another name, an
unknown team or task, or a value of the wrong kind answers 400 with the message in `error`,
`error_id` and `error_values`, and an unknown benchmark answers 404.

```sh
curl -X POST http://127.0.0.1:8780/api/benchmarks \
     -H 'Content-Type: application/json' \
     -d '{"teams": ["builtin"], "tasks": ["corner"]}'
```

## Scripts

Scripts and agents that drive the page reach the benchmark through `window.cuju.benchmark`, with
no mouse.

| Call | Does |
| --- | --- |
| `cuju.benchmark.show()` | shows the Evaluate page and waits for its choices |
| `cuju.benchmark.tasks()` | answers the tasks of the server |
| `cuju.benchmark.start(teams, { tasks, keepRecordings, workers })` | starts a benchmark and follows it on the page, with every task when `tasks` is absent |
| `cuju.benchmark.job()` | the benchmark that the page follows, or null |
| `cuju.benchmark.cancel()` | cancels the benchmark that the page follows |
| `cuju.benchmark.list()` | answers the benchmarks of the server |
| `cuju.benchmark.table(job)` | the columns and rows of a benchmark, by default the one that the page follows |
| `cuju.benchmark.csv(job)` | the table of a benchmark as CSV |

```js
await cuju.benchmark.show();
const job = await cuju.benchmark.start(['builtin'], { tasks: ['corner', 'empty_goal'] });
console.log(job.id, cuju.benchmark.table().columns);
```

## The benchmark as data

A benchmark of the server or the command line holds `teams`, `tasks` in the order of the academy,
`models` with each model's `team` and `name`, `cells` with one entry for each pair, `rows` with
each model's `score`, `done`, the number of its cells with a result, and `place`, `progress` from
0 to 1, `keep_recordings`, `workers` and `device`, and on the server its `id`, `state`, `created`,
`ended` and error fields. A cell holds its `team`, `task`, `state`, one of `queued`, `running`,
`done`, `failed`, `skipped` and `cancelled`, its `attempts` and the attempts `played`, its `value`
and `rate`, its `failed_attempts`, `playing` and `group`, the `results` of its attempts as a run
returns them, and `error`, `error_id` and `error_values` for a cell that failed or skipped.
