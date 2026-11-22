# Submission formats

A submission format tells cuju-render how to check and how to play a kind of submission. A
competition, a course or a person decides what a submission looks like, which files it holds,
how it is called, how long it may think and what ends a match early, and writes those rules once
as a format. Every submission that follows the same rules then plays under that format, on any
machine, and the submissions themselves stay exactly as their authors made them.

A format is a folder of scripts under one contract of cuju-arena, contract 1. The arena keeps
the engine and its lockstep, every recording, the ratings and a few safety ceilings for the
machine. The format keeps everything that belongs to a competition, its checks, its limits and
what counts as a forfeit. A small library, `cuju.arena.formats.v1`, gives a format easy access
to the engine in exactly the shape that gfootball gives it, so the code of a submission that was
written against gfootball runs unchanged.

The context of a match chooses the format, so a tournament, a rating run, a requested match, an
evaluation or a game in Play each says which format its sides play under. Two sides of one
match may play under two formats, and the engine still steps them together. A side that no
context gave a format plays as cuju-render played teams before formats, which is Kaggle open,
the built-in Kaggle format under its preset `open`. [Choosing a format](#choosing-a-format) tells
how each place chooses.

This guide is for a person who writes a format. It describes the contract first, then the
library, how to write a format with four complete examples, the built-in formats, and how a format
is added, exported and removed.

## The contract

### The folder

A format is a folder that holds a file `format.toml`, the script that it names and any other
file the format needs, such as a child script or a table of its own.

```
formats/arena/
  format.toml   the contract, the name, the script, the process, the results, the options and presets
  format.py     accept and start, imported as a package of its own
  inner.py      any other file of the format, such as the script of a child process
```

The script is imported as a package of its own, under a name that the arena draws from the
folder, `cuju_format_` and twelve digits of a digest. A format therefore imports its other files
with `from . import inner`, and no file of a format ever shadows a module of a submission, since
both live under names of their own.

### The file `format.toml`

Every key but `contract`, `name` and `script` is optional.

| Key | What it says |
|---|---|
| `contract = 1` | the contract that the folder follows |
| `name` | the name of the format in the menu, at most 64 characters |
| `script` | the file of the folder that holds `accept` and `start` |
| `version` | the version of a built-in format, which joins its identity |
| `summary` | one line that the menu shows |
| `requires` | the versions of cuju-arena the format needs, such as `">=0.1.0"` or `">=0.1,<0.4"` |
| `[process] host` | `format`, a process of its own for each side, the default, or `match`, which only the built-in formats may ask for |
| `[process] scope` | `match`, a fresh process for every match, the default, or `run`, one for the consecutive matches of a run |
| `[process] threads` | the threads that the format's process may use, from 1 to 64, by default 1 |
| `[process] gpu` | `never`, every GPU hidden, the default, or `allowed` |
| `[process] memory_mib` | the memory of the format's process tree, at most 65,536 MiB |
| `[process] match_minutes` | the time that one side's answers may take over a match, by default 60 and at most 360 |
| `[process] check_minutes` | the time that a pre-check may take, by default 15 and at most 120 |
| `[process] env` | the names of variables that the format's process receives, such as a key for a remote policy |
| `[results] forfeit_score` | the score that a forfeit records, such as `[0, 3]`, the forfeiting side first |
| `[results] evidence` | whether the format's matches may count as evidence for the ratings, by default true |
| `[options.<key>]` | a setting that a context may change, described below |
| `[presets.<key>]` | a named set of option values, which the menu lists as a format of its own |

A file that does not read, that names a key the contract lacks, or that asks for more than the
ceilings allow is refused when the format is added, with one line that says what does not fit.
A format whose `requires` the arena does not meet is refused the same way, with a line such
as "needs cuju-arena 0.3 or later".

### Options and presets

Each option is a table under `[options]`, whose key is lowercase letters, digits and
underscores. The menu draws a toggle, a stepper or a list for each one.

| Key | What it says |
|---|---|
| `type` | `bool`, `int`, `float`, `choices` or `text` |
| `default` | the value where a context sets none |
| `label` | what the menu calls it, by default the key |
| `range` | the lowest and the highest number, for `int` and `float` |
| `choices` | the values of a `choices` option |
| `unit` | the unit that the menu shows beside a number, such as `s` or `MiB` |
| `bounds` | `step` for a limit on each step, at most 300 seconds, or `match` for a limit over the match |
| `reaches` | `play`, the default, or `check` for an option that only the pre-check reads |
| `per_entrant` | whether the option may differ for each entrant of a tournament or a run |
| `max_length` | the longest text of a `text` option, at most 200 characters |

An option that reaches play is handed to the format in `Match.options` and joins the format's
identity, so two matches played with two values of it are two models in the ratings. An option
that reaches only the check, such as the number of check matches, is handed to `accept` alone and
never to play, and the arena holds to that, so it can never change how a match is played.

A preset names values for some options, as Kaggle open sets `players = "control"` and
`timing = false`. A context that chooses a preset takes those values over the defaults, and any
value the context sets itself comes last.

### The pre-check

```python
def accept(folder: Path, team: Team) -> Verdict
```

`accept` decides whether a team plays under the format. It runs before a team plays, after an
upload when a person asks for a check, at the start of a tournament or a rating run for every
entrant, and before a match where no verdict is kept. It runs in a checker process of its own on
the submission's folder, with the format's process settings, and it may take up to the format's
`check_minutes`.

`Team` holds the team's `name`, its `size`, `5v5` or `11v11`, the context's `scenario` or `None`
where the context names none yet, `size_default_scenario`, which is `5_vs_5` or `11_vs_11_kaggle`,
the team's `control`, its `checkpoint`, the format's `options` resolved, those that reach only the
check among them, and `full`, which is true when the context asks for the full check, such as a
tournament's start.

`accept` returns `formats.accept(...)` or `formats.refuse(...)`. A verdict holds `players`, what
the submission states it controls, `name`, the name it states, `deterministic`, which is true,
false or unknown, and `problems`. Each problem has a `kind`, a `message` of plain text, a `level`
and an optional `detail`. A problem of the level `error` refuses the team, a `warning` is shown
beside the team, and a `note` reports a figure, such as the score of a check match. A refusal
that belongs to the machine rather than the team, such as a missing package or no GPU, says so
with `environment=True`, and the arena then never keeps it and checks again at the next start.

An exception from `accept` refuses the team as `crash` when it arose in the submission's own code,
and is a failure of the format when it arose in the format's code, as the section on blame
describes.

### Play

```python
def start(folder: Path, match: Match) -> Player

class Player:
    def reset(self) -> None                          # optional, once per match, before the first act
    def act(self, step: Step) -> list[int] | None    # every step of the engine
    def end(self, result: Result) -> Forfeit | None  # optional, a forfeit over the whole match
    def close(self) -> None                          # optional
```

`start` returns the side's player for one match. The arena then calls `reset` once, `act` for
every step of the engine, `end` with the result, and `close`.

`Match` holds the `scenario`, its `steps`, the engine's `seed`, a `policy_seed` drawn from the
seed and the side for the submission's own randomness, the `side`, `left` or `right`, the number
of controlled `players`, the engine `indices` of the controlled players at kick off, the `roles`
of every player of the side by engine index, the `assignment`, `dynamic` or `fixed`, the
`team_action_set` that the team's control chose, the `kind` of the context, `match`, `rating`,
`tournament`, `evaluation`, `check` or `game`, the `options` that reach play, whether a `gpu` is
allowed, the `threads`, the submission's `folder` and a writable `cache` folder.

`Step` holds the engine's step `number`, whether its answer is `used`, whether the previous
answer was applied as `previous_applied`, the `raw` observations, one GRF raw observation for
each controlled player that plays the step, the engine `indices` of those players, the side's
`action_set`, the `renderer_seconds` of the previous step's round trip as the arena measured
it, and the `score`, the side's goals first.

Every step of the engine is sent, even one whose answer is not used. At a set piece of a fixed
assignment GRF hands the players over, and the step arrives with `used` false. A player may still
update its state then, as a stack of frames does, and may answer `None`. The raw observations
come from the side's own point of view, its own team as `left_team`, exactly as gfootball gives a
right agent with `number_of_right_players_agent_controls`.

The answer holds one action for each entry of `step.raw`, in that order, in the side's action
set. `formats.as_actions` turns any common answer into that list or raises `formats.Invalid`.
The arena checks every answer again, and an answer that does not fit is a failure of the
format, since `as_actions` should have caught it.

### Lockstep and the processes

The engine does lockstep. Each step the match process sends a step to every format process at
once, then plays the sides that think in the match process, then reads the answers, applies them
together and steps the engine once. Two sides that play under two formats therefore think at the
same time, and the recording of the match stays exact whatever the formats do, since it keeps
every side's actions.

A format plays in a process of its own for each side, started with `subprocess` as
`python -I -X utf8`, never by fork, so CUDA that the match process initialised cannot break it.
`-I` leaves out the variables of Python, the user's site packages and the working folder, and the
renderer passes the folders that import cuju and the arena in a variable of its own. The
process gets a minimal environment, the thread variables set to `[process] threads`, and the
variables that `[process] env` names. The submission plays from a read-only copy of its folder,
without the team's own folders, `cuju-team` and the folders of tools such as `cuju-look`.

Messages between the processes are frames, two lengths, a header of JSON with the contract
number, and the raw bytes of every array. GRF's raw observations travel as arrays of one fixed
table, so a format process reads Python integers for single values such as `active` and
`game_mode`, a list for `score`, and numpy arrays of GRF's own dtypes and shapes elsewhere,
exactly what `grf_observation` of cuju gives. Nothing is ever pickled, in either direction.

The built-in formats may declare `[process] host = "match"`, and then play in the match process
itself, as cuju-render played teams before formats, with no process and no frames of their own.

A frame of one side's step costs about half a millisecond on a laptop for one
controlled player, and about one millisecond for eleven. The start of a format process with numpy
costs about 0.2 seconds on Linux, and importing torch adds about 2.5 seconds. `scope = "run"`
and the cache folder let a format pay a long warm up once.

### Processes across a run

A format of `[process] scope = "run"` keeps each side's process across the consecutive matches
of a run, such as a rating run, an evaluation or a round of a tournament. The arena holds the
processes of a run in a pool, each under the format's identity, the team's checkpoint and its
effective control, and hands a kept process to the next match of the same side. The format's
`start`, `reset`, `end` and `close` still come once for every match, so a player starts clean
each time, while the format's module, and whatever it loads at module level such as a model or
a framework, loads once for the whole run.

A process that met a forfeit, a ceiling or a failure of the format, or that ended, is never kept,
and the next match starts a fresh one. Two sides of one team in one match each get a process of
their own. At the end of the run every process of the pool closes, and so does the read-only copy
of its submission, whether the run ended as planned, by an error, by a stop with Ctrl-C or with
the end of the server. A process whose parent ends reads the end of its input pipe and exits by
itself. Each process has an answer pipe of its own. Its reader reaches the end when that process
exits, even if a child of the process is still running. A format of `scope = "match"`, the default,
starts a fresh process for every match.

A rating run, an evaluation and a tournament play each match of teams in a child process of the
renderer, so a failing submission ends that process alone. Where a match plays a side of a format
of `scope = "run"`, the child is a match worker instead, one for each match the context plays at
once. A worker plays one match after another as its slot of the schedule hands them over, and it
holds the pool of the run for its whole life, so a side keeps its process across every match of
that slot. A context that plays two matches at once therefore loads a model twice, once in each
worker, and never more. Each match keeps a terminal of its own, as the terminal of a match of its
own process would. At the end of a match, the arena gives the terminal reader time to save any
pending output while the kept process can continue running. As the next match starts, the kept
process sends its output to that match's terminal.

A worker whose process ends, because a ceiling ended it, a side crashed it, its match passed the
renderer's 30 minutes or the context was cancelled, is closed, and the next match of its slot starts
a fresh worker. The arena reads the last words of a worker that a ceiling ended, and the side
that passed the ceiling forfeits the match, as in a match of its own process. When the context
ends, as planned, by an error, by a cancel or by Ctrl-C, its workers end and close their pools, and
a worker whose parent goes away finds it at its next report and ends as well. Kaggle open, the
built-in formats that play in the match process and every format of `scope = "match"` keep a fresh
child for every match, as before.

On a laptop, ten matches of sixty steps under a format that imports torch took 42.4 seconds with a
process for every match and 10.5 seconds with one process for the run, since each later start
took 2 milliseconds in place of 2.7 seconds. The same ten matches of a format without a framework
took 10.3 and 7.8 seconds.
A rating run of ten full matches of 5 vs 5, two at a time, under a format of `scope = "run"`
that imports torch took 183.0 seconds when each match played in a fresh child, and 66.2 seconds
with a match worker for each of its two slots, since the match process and the side's process
each loaded torch once for each slot in place of once for each match.

### Forfeits

The format decides what counts as a forfeit. `start`, `reset` and `act` raise `formats.Forfeit`
to end the match at that step, and `end` may return one, such as a mean time over the match. The
renderer records the forfeit against the format's team, with the score at that moment and the
format's `forfeit_score`, and a forfeit is never evidence for the ratings.

A forfeit names one of five kinds, with a free `detail` of the format's own.

| Kind | What it says |
|---|---|
| `load` | the submission did not load |
| `timeout` | an answer, a load or a budget took too long, such as Kaggle's overage bank with detail `overage` |
| `crash` | the submission's code raised or ended |
| `invalid` | the submission answered something that is no action |
| `rule` | the submission broke a rule of the format, such as a mean time with detail `mean` |

### The arena's stops

The arena keeps four stops of its own for the machine. Each is a forfeit of the side whose
answer is missing, recorded with the format's forfeit score, so no stall can ever void a lost
match.

| Kind | When |
|---|---|
| `ceiling.answer` | a step gets no answer within 300 seconds |
| `ceiling.time` | the side's own round trips over the match pass the format's `match_minutes` |
| `ceiling.load` | `start` takes longer than 15 minutes |
| `ceiling.crash` | the side's format process ends, or its submission exits in the match process |

Beside these four, the ceilings of a side's processes forfeit it as `ceiling.memory`,
`ceiling.process` or `ceiling.threads`, as [Safety and sharing a machine](#safety-and-sharing-a-machine)
describes. A failure of the engine or of the arena itself is the one thing that ends a match
without a result.

### Blame

When a side raises an exception that is no `Forfeit`, the arena reads its traceback from the
innermost frame outwards, and the first frame whose file lies in the submission's folder or in
the format's folder decides. Frames of the standard library, the site packages, cuju and the
renderer are passed over.

| Where the error arose | What it is |
|---|---|
| the submission's folder | the team's forfeit, `load` during `start` and `crash` later |
| the format's folder, a built-in format's code, or no owned frame at all | a failure of the format, which gives no result and marks the format failing, never a forfeit or a refusal of the team |
| a `formats.Forfeit` | always the team's |

A format whose `format.toml` does not read, or whose script fails to import, is broken. It refuses
nobody, has no verdicts, and no context can choose it.

The arena remembers the most recent failure for each format in the home folder. Match and
benchmark workers update this record one at a time, so a failure in one format leaves the
record of another format intact. Each benchmark cell reports its own failure reason.

### Identity and ratings

A format's identity is what decides how a side played under it. It joins the format's name, its
version for a built-in format or the SHA-256 of its folder for a person's own, the contract, the
library's `BEHAVIOUR`, every option that reaches play with its value, the per-entrant options,
and the revision a remote player reports. A model of the ratings is a team at a checkpoint under
a format's identity and its effective control. A model under Kaggle open keeps the identifier it
always had, and a model under any other format gains a suffix of its own, so one folder under two
formats is two models of one table.

Every row of the ratings records the format each team side played under, the versions of the
frameworks its process imported, and the versions of cuju, cuju-arena and numpy. Rows from before
formats read as they were played, a match of a strict tournament as `strict/1` with the
tournament's stored limits as its options, and every other match as Kaggle open. Nothing is fitted
again, since a table is made from the rows on every read. A format with `[results] evidence =
false` never enters a table.

### Versioning

A format imports the library of its contract by its major number.

```python
from cuju.arena.formats import v1 as formats
```

The arena keeps the current major and the one before it. A new major comes only for a removal
or a change of meaning. Within a major, objects and frames only gain fields, and a format reads
the fields it knows and leaves the others alone, and helpers only gain arguments with defaults.
`formats.BEHAVIOUR` is raised whenever a helper gives another output for the same input, and the
change is named in the changelog.

### Exactness

A recording replays exactly whatever the format, since it keeps every side's actions. A match
played again from its seed gives the same actions when the submission is deterministic and the
format's answers depend only on the submission's files, the format's folder and its cache, the
fields of `Match` and its seeds, and each step's `raw`, `indices` and `used`. Time decides only
forfeits, and never an action.

## Safety and sharing a machine

cuju-render protects the machine it runs on. It keeps every side of a match inside a few safety
ceilings, bounds what the sides can take from each other, and says plainly what two sides on one
machine still share. The ceilings are constants in the arena's code, the same on every
machine and in every context, and no setting changes them. A format may tighten a ceiling
through its own `[process]` table, and it can never loosen one. Rules of a competition, such as
a time for each step or a bank of extra seconds, belong to the format and its options, and the
ceilings sit behind them only to keep a broken or runaway side from harming the machine.

### The ceilings of a side

Each side whose format plays in a process of its own runs as a process tree, the format process
and every process it starts, such as a `Child`. The whole tree is held to these ceilings. A
built-in format that plays in the match process and starts a `Child`, as timed Kaggle does,
holds that child to the same ceilings in a tree of its own.

The process that launches a guarded submission measures that submission's tree. In a mixed
match, Kaggle open can run one submission in the match process while that process measures
the other submission in a separate tree. A guarded checker asks its own host to take the
snapshots for its nested matches. On macOS, the host runs `ps` outside the checker's spawn guard
when it samples a nested match tree. Each check match runs its single engine environment on the
thread that called it.


| Ceiling | Value | Forfeit |
|---|---|---|
| Memory of the tree | 65,536 MiB, or the format's own `memory_mib` where it is lower | `ceiling.memory` |
| Processes of the tree | 8 | `ceiling.process` |
| Threads of the tree | the format's `threads` plus 10 for each process of the tree | `ceiling.threads` |
| A new process | refused, apart from the library's own `Child` | `ceiling.process` |
| An answer | 300 seconds | `ceiling.answer` |
| A load | 15 minutes | `ceiling.load` |
| A match | the format's `match_minutes`, at most 360 | `ceiling.time` |
| A pre-check | the format's `check_minutes`, at most 120 | a refusal that is never kept |

A side that passes a ceiling forfeits the match at that step, with the format's forfeit score,
exactly as a side forfeits at the arena's stops above. The forfeit always counts against the
side that passed the ceiling, never against the other side and never as a match without a
result. A side whose process a run keeps across its matches is held to the same ceilings for the
whole life of that process, and a side that passes one leaves the run's pool, so the next match
starts a fresh process. A pre-check that passes a ceiling refuses the team for that check alone,
since the machine shares the blame, and the arena checks again at the next start.

The allowance of ten threads for each process covers the interpreter, the reader of the pipe and
the threads that the system and the usual libraries keep whatever their limit. A format process
that loads torch and runs a small network with `threads = 1` held 3 threads and about 507 MiB on
this project's laptop, against a ceiling of 21 threads for its two processes, and the same
format with `threads = 4` held 9 threads.

### Time that a format may ask for

A format declares how long a side may take, through options of `bounds = "step"` and
`bounds = "match"`, and through its `match_minutes`. The arena checks the two against each
other when the format is added, so the format's own timing always decides a slow side before
the arena's ceiling of the match would.

- An option of `bounds = "step"` allows at most 300 seconds, and each option with bounds needs
  a range.
- The time a side may take within the options must fit in `match_minutes`, over the longest
  scenario, 3000 steps. Every option of `bounds = "step"` but the one with the smallest maximum
  counts once, as a first call or a reset does, and the option with the smallest maximum counts
  at every other step. Every option of `bounds = "match"` counts once with its maximum.

Kaggle allows 1.1 seconds for each step and a bank of 600 seconds, which comes to 3900 seconds
inside its 90 minutes. The League example allows a first call of 300 seconds and 1.1 seconds for
each later call, which comes to 3599 seconds inside its 60 minutes. A format whose options allow
more than its `match_minutes` holds is refused when it is added, with one line such as "the
options allow 100.0 minutes over 3000 steps, past match_minutes = 60". A context that sets a
longer match than 3000 steps should choose a format whose options still fit that length.

### How each system holds a tree

Each system enforces what it can, and the arena watches the rest four times a second.

| System | Held by the system | Watched by the arena |
|---|---|---|
| Linux with cgroups v2 delegated to the arena | the memory, the CPU set and the tasks of the tree, by a cgroup of its own | threads and processes |
| Linux | the CPU set, by affinity | memory, threads and processes, summed over the tree from `/proc` |
| Windows | the memory of the whole tree, its processes and its CPU set, by a job object | threads |
| macOS | nothing | memory, threads and processes, from `ps` |

On Linux the format process starts in a session of its own and makes itself a child subreaper,
so a process that its tree starts and leaves behind stays in the tree, and at the end the
renderer ends the whole session and every process it saw in the tree. Where the person who runs
the arena has delegated cgroups v2 to it, each side gets a cgroup of its own with
`memory.max`, `pids.max` and `cpuset.cpus`, and the system itself refuses what goes beyond.
Without that, a tree that grows faster than the watch looks may pass a ceiling for a quarter of
a second before it is stopped.

On Windows the format process starts suspended, joins a job object of its own and only then
runs. Arena starts the interpreter directly and keeps the virtual environment active. The host
can then use the checker's process handle to watch nested matches, and the answer pipe closes
when its interpreter exits. The job object limits the active processes to 8, limits the memory
that the whole tree commits, pins the tree to its CPU set and ends every process of the tree
when it closes. The system refuses a process or memory beyond the limits at once, and tells
the arena, which records the forfeit.

On macOS the arena reads the tree with `ps` and ends its process group at the end. The system
offers no limit of its own for a tree and no CPU affinity, so a process that leaves the group
and its parent can outlive the match. The watch on macOS is therefore weaker than on Linux and
Windows, and a competition that runs on macOS relies on the watch alone.

### Processes, threads and the environment

Every format process and every `Child` runs an audit hook of Python from its first line. The hook
refuses every new process except the library's own `Child`, and the short helper that Python's
`ctypes` runs on Linux to find a library, as torch does when it loads. A submission that starts
any other process ends its format process at once, and its side forfeits as `ceiling.process`.
The refusal names the arena as the source of the process limit.
The arena counts every process of the tree as well, so a process that native code starts past
the hook still counts against the ceiling of 8.

On Windows, format processes reuse the host's platform details. The host queries Windows
for those details before it starts a guarded process, so the query and its helper threads
stay outside the process tree being measured.

When a built-in format blocks and the watchdog cannot wake it, an isolated match process
writes the side and the ceiling it passed to standard error before it exits. The process
that started the match reads those details to record the forfeit.

The thread variables of the usual libraries are set to the format's `[process] threads` in the
format process and in each of its children. They are `OMP_NUM_THREADS`, `MKL_NUM_THREADS`,
`OPENBLAS_NUM_THREADS`, `NUMEXPR_NUM_THREADS`, `NUMEXPR_MAX_THREADS`, `VECLIB_MAXIMUM_THREADS`,
`BLIS_NUM_THREADS`, `TF_NUM_INTRAOP_THREADS`, `TF_NUM_INTEROP_THREADS`, and `XLA_FLAGS` with
`--xla_cpu_multi_thread_eigen=false` and `intra_op_parallelism_threads`. Once torch loads, a hook
on the imports sets `torch.set_num_threads` and `torch.set_num_interop_threads` to the same count.
onnxruntime reads no variable, so a format that uses it sets the threads of its session itself.

The environment of a format process is a minimal one. It holds the variables that the system,
the interpreter and the libraries of numbers and GPUs need to run, such as `PATH`, the folders of
temporary files and the locale, the variables of the arena, the thread counts and the
variables of the GPUs. It also holds each variable that the format names in `[process] env`,
which is the way to hand a format a key, and each child of the format keeps those as well. Every
other variable of the person's environment stays with the arena, and so does every variable
of Python, such as `PYTHONPATH`.

### CPU sets

Each side whose format plays in a process of its own takes a CPU set for its whole tree, as many
logical CPUs as its `[process] threads`, apart from the sets of every other side. The first CPU
stays with the arena and the engine. On Linux the sets spread over the physical cores first,
so two sides of one thread each sit on cores of their own before any two share the halves of one
core. The sets hold through affinity on Linux and through the job object on Windows. A side of a
built-in format that plays in the match process is never pinned, as cuju-render played teams
before formats.

A run plays no more matches at once than the CPU sets fit. With one thread for each side, the
laptop of this project, with 12 logical CPUs, plays 5 matches at once, with two threads 2, and
with four threads 1. A side that finds every CPU taken, such as a match started by hand beside a
run, takes the CPUs least in use, and its set then overlaps another's.

### GPUs

A format of `[process] gpu = "never"` sees no GPU at all. CUDA, ROCm and JAX are each told so by
their own variables. A format of `gpu = "allowed"` sees a GPU of its own where the machine has
several, chosen through `CUDA_VISIBLE_DEVICES`. On a machine with one GPU, each of the two sides
of a match gets an equal share of its memory, 45 percent, which leaves room for the drivers and
the engine.

| Framework | How the share holds |
|---|---|
| JAX | `XLA_PYTHON_CLIENT_PREALLOCATE=false` and `XLA_PYTHON_CLIENT_MEM_FRACTION` |
| TensorFlow | `TF_FORCE_GPU_ALLOW_GROWTH=true`, so it takes memory as it needs it |
| torch | `torch.cuda.set_per_process_memory_fraction`, applied when torch starts CUDA |

Other frameworks see the GPU unbounded, and a format that uses one should bound its memory
itself. The arena counts the GPUs from its own `CUDA_VISIBLE_DEVICES`, or else from
`nvidia-smi`.

### What two sides on one machine still share

Two sides of one match run as the same person on the same machine, and the arena isolates
them only as far as this chapter says. One side can still slow the other through the processor,
the memory bandwidth, the disk and the GPU. It can signal or suspend the other side's processes,
which every system allows between processes of one person. It can read the other side's files,
the read-only copy of the other team's weights among them, and it can reach the network.

The CPU sets, the GPU shares and the ceilings make these effects small for submissions that
behave, and they stop the accidents that a runaway submission causes. They do not stand against
code written to harm the other side. A competition that must stop malice, rather than accidents,
runs cuju-render in a container or on a machine for each team, as the arena of the course does.
Within cuju-render itself, tournaments and rating runs play every side under one format and one
set of options, and a forfeit is never evidence for the ratings, so contention between two
formats cannot move a rating through a forfeit.

## The library

The library is `cuju.arena.formats.v1`. Each helper serves at least two formats, and each one
gives exactly what gfootball or Kaggle gives for the same input.

### Verdicts and forfeits

`formats.accept(players=None, name=None, deterministic=None, problems=())` returns a verdict that
accepts the team unless one of its problems is an error. `formats.refuse(kind, message,
detail=None, environment=False)` returns a verdict that refuses it with one error.
`formats.problem(kind, message, level='error', detail=None, environment=False)` makes one problem.
A kind and a detail are letters, digits, dots and underscores, at most 40, and a message is cut to
300 characters.

`formats.Forfeit(kind, message, detail=None)` is raised from `start`, `reset` and `act`, or
returned from `end`. Its kind is one of `formats.KINDS`, and only the arena records a `ceiling.`
kind.

### Actions

`formats.as_actions(value, count, action_set='default', negative=False)` returns a list of `count`
actions of the action set, `default` with 19 actions, `v2` with 20 or `full` with 33. It takes a
whole number, a list or a tuple, a numpy array, a tensor or an enum whose value is the action,
such as `Action` of Kaggle's football helpers. It refuses booleans, since GRF would read `True` as
action 1, and with `negative=True` it reads `-1` as the last action, as Kaggle's agents may answer.
Anything else raises `formats.Invalid`, which forfeits the team as `invalid`.

### Kaggle

`formats.kaggle_agent(folder)` returns a submission's agent as Kaggle finds it, the last callable of
all the values its `main.py` defines. The file runs from its bytes, so its own encoding line
decides how it reads, with the folder first on `sys.path` and as the working folder, and every
call of the agent runs from the folder again. The modules the file imports from its folder leave
`sys.modules` afterwards, so a second submission with modules of the same names gets its own.
This is the body of cuju-render's loading of a team before formats.

`formats.kaggle_observation(raw, step=0, overage=60.0)` returns Kaggle's observation of the players
of a step, `players_raw` with GRF's arrays as lists, `controlled_players`, `step` and
`remainingOverageTime`, in a dictionary whose keys are also attributes and whose missing
attributes raise `AttributeError`, as Kaggle's own does.

### Folders and modules

`formats.from_folder(folder)` is a context that runs a block from a submission's folder, first on
`sys.path` and as the working folder. `formats.load_module(folder, file)` returns a file of the
submission as a module under a name of its own, `cuju_submission_` and twelve digits.

`formats.read_manifest(folder, file)` returns a manifest as a dictionary, read by its extension,
TOML and JSON with the standard library and YAML with PyYAML's safe loader. A manifest that is
missing, larger than 1 MiB, does not read, needs PyYAML where it is missing, or holds no mapping
at its top raises `formats.ManifestError` with one line that says so.

`formats.require(*modules, hint=None)` returns the modules a format needs, one alone or a tuple,
or raises `formats.Missing`, which `accept` turns into an environment refusal with the hint, such
as `install cuju[gfootball]`.

`formats.imports_of(folder)` returns the top level modules that the Python files of a folder import,
parsed and never run, so a format can ask for a package only when a submission needs it.
`formats.folder_size(folder)` returns the size of a folder in bytes, without the caches of Python.

`formats.seed_globals(seed)` seeds `random`, `numpy.random` and torch where it is imported
already, for a submission that rolls dice of its own.

### Observations as gfootball gives them

`formats.Observer(representation='raw', stacked=False, channel_dimensions=(96, 72))` gives exactly
what `cuju.gfootball.env.create_environment` gives a side with those arguments, through cuju's own
copies of GRF's wrappers. It makes `raw`, `simple115`, `simple115v2` and `extracted`, and with
`stacked` it joins the last four observations along the last axis, the first one repeated after
a reset, as gfootball's frame stack does. `observe(step)` gives gfootball's observation, a single
player's own alone, and `per_player(step)` gives one observation for each controlled player
whatever their number. `reset()` clears the stack. Call one of them once for every step, whether
or not its answer is used, since gfootball stacks every step.

### A submission in a process of its own

`formats.Child(folder, script, factory, *arguments, threads=None, load_timeout=None,
forfeits=False)` starts a process of its own that builds `factory(folder, *arguments)` from one of
the format's files, with the submission's folder first on its path and as its working folder.
`child.call(method, *arguments, timeout=None, forfeits=None)` runs one method of that object and
returns its value and the seconds it took, measured from the format's side of the pipe.
Arguments and answers cross as frames, and an answer may be at most 1 MiB. When several matches
call formats in the same process, each call resolves child scripts and ceilings through its own
format context.

With `forfeits=True` the standard mapping applies. A call past its timeout kills the child and
raises `Forfeit('timeout')`, an error of the submission raises `Forfeit('crash')`, and an answer
that `as_actions` refused raises `Forfeit('invalid')`. Without it the same failures raise
`formats.ChildTimeout`, `formats.ChildCrash` and `formats.Invalid`. A `Forfeit` that the child
raises itself always crosses as one, and an error of the format's own code in the child is a
failure of the format. `child.close()` ends the child.

### Check matches

`formats.test_match(opponent='builtin', side='left', steps=None, seed=0, repeat=False)` plays a
check match of the team inside `accept`, the team in a fresh format process under the same format
and the options of the check, on the context's scenario or the size's own. The opponent is the
built-in AI or `random`, an entrant that answers at random. It returns `goals`, the team's first,
`steps`, `forfeit`, the round trip `seconds` of each step and a count of the `actions`, and with
`repeat` it plays the same steps twice from the seed and says in `same_actions` whether the
actions were the same. Outside `accept` it raises `RuntimeError`.

### Constants

`formats.CONTRACT` is 1, `formats.BEHAVIOUR` the behavior of the helpers, `formats.KINDS` the five
kinds of forfeit, `formats.CEILINGS` the arena's own, `formats.ACTION_COUNTS` the actions of
each set, `formats.ROLES` the names of GRF's roles and `formats.REPRESENTATIONS` what `Observer`
makes.

## Writing a format

A format is the part of a competition that is not the submission. A submission is whatever its
author trained or wrote, a network, a table of rules or a program that calls a service, and it
stays exactly that. The format reads the folder, decides whether the team may play, calls the
submission at each step and decides what counts as a forfeit. Writing one is writing down the
rules of a competition in Python, once, so that every team that follows them plays under them in
the same way.

### The method and the bookkeeping

Every example below keeps two things apart, and a format of your own should too. The method is
what the submission does when it is called, the network that runs on an observation, the rules
that choose an action, the class that an author wrote against gfootball. The bookkeeping is
everything around that call, which file to read, which keys a manifest may hold, how large a
folder may be, how long a call may take, which random generators to seed and what a forfeit is
worth.

Keeping them apart is what makes a submission portable. The author writes the method against
gfootball and never learns that a format exists. The format holds the bookkeeping in its own
folder, in `format.toml` and one script, and reaches the engine only through the library, which
gives the method exactly what gfootball would give it. A competition that changes a limit changes
a number of its format and leaves every submission alone.

### The smallest format

A format needs a `format.toml` that names its script and a script with two functions. This pair
plays a Kaggle `main.py` as Kaggle open does and accepts every folder that holds one.

```toml
contract = 1
name = "Plain"
script = "format.py"
```

```python
from cuju.arena.formats import v1 as formats


def accept(folder, team):
    if not (folder / 'main.py').is_file():
        return formats.refuse('load', 'main.py is missing')
    formats.kaggle_agent(folder)
    return formats.accept()


def start(folder, match):
    return Player(folder)


class Player:
    def __init__(self, folder):
        self.agent = formats.kaggle_agent(folder)

    def act(self, step):
        if not step.used:
            return None
        observation = formats.kaggle_observation(step.raw, step=step.number)
        return formats.as_actions(self.agent(observation), len(step.raw), step.action_set)
```

Everything else a format does is a refinement of these twenty lines. `accept` says yes or no
before a team plays, `start` makes the side's player for one match, and `act` answers each step.
The sections below add the refinements one at a time, in the order that people usually need them.

### What the pre-check judges

The pre-check is the format's chance to refuse a team for a reason that a person can fix, with
one line that says what is wrong. Each refusal is a problem with a kind, a message and a detail,
and the detail is a short name that tests and tools can match on, such as `layout` or `manifest.players`.

A useful pre-check goes in the order of the cost of its checks. It looks at the layout first,
since a missing file is the commonest mistake. It reads the manifest next, then measures the
folder, then loads the submission, and plays a match only when everything else holds. The arena
example below follows that order, and so does each of the others.

Three kinds of answer cover almost everything.

- A refusal, `formats.refuse(...)` or a problem of the level `error`, stops the team from playing.
- A warning is a problem of the level `warning`. The team plays, and the menu shows the warning beside it.
- A note is a problem of the level `note`, which reports a figure such as the score of a check match.

A refusal that comes from the machine and not from the team passes `environment=True`, which is
what `formats.require` does when a package is missing. The arena never keeps such a verdict, so
the same team is checked again once the package is installed.

`formats.test_match` plays a short match of the team inside `accept`, through the same format and
the options of the check, against the built-in AI or a random entrant. It is the best check of
all, since it exercises the format and the submission together, and the examples call it with a
small number of steps so a pre-check stays quick. An option that only the pre-check reads, such
as the number of check matches of the arena example, is declared with `reaches = "check"`, and
the arena never hands it to play, so it can never change how a match goes.

### Limits and forfeits that belong to the format

The arena provides no limit such as half a second a step or a mean of twenty milliseconds. A
format declares the limits of its competition as options, reads them in `start` and applies them.
There are two ways to apply a time limit, and the examples show both.

When the submission runs in a `Child`, the format passes a `timeout` to each call and sets
`forfeits=True`. The child is killed past its time, the call raises `Forfeit('timeout')`, and the
format never has to measure anything itself. The arena example works this way, and it also keeps
the seconds that each call took, which is how it enforces a mean over the match in `end`.

When the submission runs in the format's own process, as the Policy example's network does, the
format cannot interrupt a call, so it reads `step.renderer_seconds`, the round trip of the
previous step as the arena measured it from outside, and raises a forfeit when it was too long.
This is also the only time that a format reads a clock, and the time decides a forfeit and never
an action.

A forfeit is one of five kinds, `load`, `timeout`, `crash`, `invalid` and `rule`, with a free
detail. A format raises `formats.Forfeit` from `start`, `reset` or `act`, or returns one from `end`
for a rule that only the whole match can break, such as a mean. The arena records it against the
team with the format's `forfeit_score`, and keeps four ceilings of its own behind the format, which
the earlier sections describe.

A format that declares step limits must keep them inside its `match_minutes`. The arena checks
this when the format is added. The arena example caps its limit for each later call at 1.1 seconds
and allows 300 seconds for a first call, which together come to 3599 seconds over 3000 steps and
fit inside its 60 minutes. A team that is slow therefore meets the format's own forfeit and never
the arena's ceiling.

### Where the submission runs

A format may run the submission in its own process, in a `Child`, or in a mixture. The choice
decides what the format can promise.

- In the format's own process the submission shares memory with the format, a call costs nothing
  beyond the call, and a model loads once for the whole run when the format has `scope = "run"`.
  The format cannot interrupt a call, so it cannot enforce a time on one that never returns. The
  renderer's ceiling of 300 seconds for an answer then ends the side. Policy, Squad and Bot work
  this way.
- In a `Child` the submission has a process and a working folder of its own, and the format
  times and stops it from outside. A call costs a round trip through a pipe, about half a
  millisecond. The arena example works this way, since its rules are limits on the submission
  and a submission that hangs must be stopped by the format.

A child imports the format's own script by its path, with the submission's folder first on its
path, and builds one object that the format then calls by method name. The arena example's
`inner.py` is that script. Arguments and answers cross as JSON or as arrays, up to 1 MiB, and
nothing is pickled in either direction, so a submission cannot send the format code to run.

### Observations as gfootball gives them

A submission written against gfootball expects what gfootball gives it, and `formats.Observer`
gives exactly that. It makes the `raw`, `simple115`, `simple115v2` and `extracted` representations
through cuju's copies of gfootball's own wrappers, stacks four steps when asked, and gives one
observation for each controlled player with `per_player`.

Two details matter in a format of your own. The observer stacks every step that it reads, so a
format that stacks reads each step with it, whether or not the answer will be used, which is why
the Policy and Squad examples call `per_player` before they look at `step.used`. And each
step arrives from the side's own point of view, with its own team as `left_team`, the way
gfootball gives a right agent, so a format never mirrors a side itself.

### Several players and roles

A step holds one raw observation for each controlled player that plays it, and `step.indices` names
the engine index of each. The answer holds one action for each entry, in that order. A team whose
players change during a match, through a red card or an academy task with players out, therefore
sends a shorter list, and a format that keeps a state for each player keeps it by the index and
never by the position in the list. `match.roles` maps each controlled player's index to its role,
which the Squad example uses to pick the network of each player.

### Seeds and exactness

A recording of a match replays exactly whatever the format does, since it keeps every side's
actions. A match played again from its seed gives the same actions only when the submission is
deterministic and the format's answers depend on nothing but the submission's files, the format's
folder and its cache, the fields of `match`, and each step's `raw`, `indices` and `used`.

`match.policy_seed` is a seed that the arena draws from the match's seed and the side, made for
a submission's own randomness. The Bot example passes it to `formats.seed_globals`, which seeds
`random`, `numpy.random` and torch where it is imported, so a bot that rolls dice replays. The
arena example offers the same as an option that is off by default, since an arena plays its agents
unseeded, and the Policy example draws its samples from a generator seeded with it.

A format declares whether a submission is deterministic in its verdict, and the declaration is
trusted and never verified. `formats.test_match` with `repeat=True` plays the same steps twice from
one seed and reports in `same_actions` whether the actions agreed, which is the usual way to back
the declaration with a warning. A GPU makes the declaration weaker, since the same network can give
slightly different numbers on two runs, and the Policy example reports a team that asks for `cuda`
as not deterministic for that reason.

A format that batches the inference of several matches on one GPU, to use the card better, needs
the same care. The numbers a network gives can change with the size of the batch, so the actions
are replayable from the seed only when the makeup of every batch is fixed. A format that cannot
promise that should say so in its verdicts. Its matches still play and still record, and every
recording replays exactly, since it keeps the actions that were taken.

### Submissions that import gfootball

Submissions that were written for gfootball import it by name, for its wrappers or its observation
helpers. cuju 0.3 holds gfootball's code as `cuju.gfootball` and offers the extra `cuju[gfootball]`,
which makes `import gfootball` work through a file of paths, and the format processes of the
renderer read it. A format whose submissions import gfootball should check for it in `accept`,
through `formats.imports_of`, with `formats.require('gfootball', hint='install cuju[gfootball]')`, so a team on a machine that
lacks the extra is refused with a line that says what to install and is checked again once it
is there. The arena example makes that check only for a folder that imports gfootball, since its
sample does not.

### Remote players

A format may send each step to a service, as a policy that runs on another machine needs. Three
details matter.

- The keys of the service reach the format process through `[process] env`, which names the
  variables the process receives. They never reach a terminal, a file or an export.
- A model that a service changes under the same address is no longer the model that was rated.
  A player that sets a `revision` text of at most 64 characters after `start`, such as a version
  of the weights, adds it to the identity of its side, so a rating row belongs to one revision. A
  format whose service reports none should declare `[results] evidence = false`, which keeps its
  matches out of the ratings tables.
- A format builds its own wire format from JSON and arrays. It never builds on the remote
  environment of gfootball, whose client unpickles what the server answers, so a server can run
  code in the client.

A replay format is the extreme of this case. Its `act` returns the logged action of `step.number`,
checks `match.seed` in `start`, and declares `evidence = false`.

### Trying a format

A format is added from its folder, and a pre-check runs on any folder that holds a submission.

```sh
cuju arena format add docs/formats/arena
cuju arena check docs/formats/samples/arena --format arena
```

The second command registers nothing and exits with 0 when the team is accepted, 1 when it is
refused, 2 when the format itself fails and 3 for a usage or environment problem. A failure of the
format shows its traceback in the terminal of the check, which the viewer of terminals draws. The
tests of this repository add each example as a person does and play a short match under it.

## Examples

The folder `docs/formats/` of the repository holds four complete formats and a sample
submission for each, under `docs/formats/samples/`. They are examples and never built-ins, so a
person adds them as any other format. Each is small enough to read in one sitting, and each shows
one way to divide the method from the bookkeeping.

| Example | The submission | Where the submission runs | Process |
|---|---|---|---|
| Arena 5 vs 5 | an `agent.py` with a class `Agent` and a `manifest.yml` | a `Child`, timed from outside | one for each match |
| Policy | a `policy.toml` and one TorchScript or ONNX file | the format's process | one for the run |
| Squad | a `squad.toml` and one recurrent policy for each role | the format's process | one for the run |
| Bot | a `bot.py` that defines `decide` | the format's process | one for the run |

### Arena 5 vs 5

The arena is the example to read first, since it holds the most bookkeeping. It describes a
competition's rules for 5 vs 5, the shape that courses and leagues commonly use, and every number
of it is an option that a copy of the folder changes.

The submission is a folder with an `agent.py` that defines `Agent(path)`, with an optional
`reset()` once for each match and `act(observations)` that gets the list of raw observations of the
controlled players and answers one action for each. A `manifest.yml` states the team's name, how
many players it controls, from 1 to 4, and whether it is deterministic. Keys that the format does
not know are reported as a warning and never refused.

The format keeps five time limits as options, with the defaults of a typical arena.

| Option | Default | What it limits |
|---|---|---|
| `load_seconds` | 30 s | importing the agent and building it |
| `first_call_seconds` | 10 s | the first call of a match, which may warm a model up |
| `call_seconds` | 1 s | each later call, at most 1.1 s |
| `reset_seconds` | 5 s | the reset before a match |
| `mean_ms` | 20 ms | the mean of every call after the first over a match |

Beside them it declares a folder size in MiB, `seed_globals` that is off, and `check_matches`
that reaches only the pre-check. A match is capped at 60 minutes with a one thread process and no
GPU, and a forfeit records a score of 0 to 3 against the side.

The pre-check runs in the order of its cost. It confirms that numpy is installed and, for a
folder that imports gfootball, that gfootball is, then looks for `agent.py` and reads the manifest,
confirms that the scenario is `5_vs_5`, measures the folder and loads the agent in a `Child` under
the load limit. On a full check, which a tournament asks for at its start, it also plays full
matches against the random entrant and the built-in AI, as many as `check_matches` says for each
and on alternating sides. From those matches it reports the score of each, the mean call time
after the first call against `mean_ms`, a warning when the same ten steps played twice give
different actions for a team that declares itself deterministic, and a warning when one action is
more than nine tenths of all the actions.

During a match every call goes through the child with the limit that fits it, a first call, a
later call or a reset, and a call past its limit forfeits the side as `timeout`. The mean is a rule of
the whole match, so it lives in `end`, which returns a forfeit of the kind `rule` and the detail
`mean` when the mean over the match passes `mean_ms`.

### Policy

Policy is the format for the commonest way to train, one network that plays the active player over
a representation that gfootball gives. The submission is a TorchScript file, or an ONNX file read
through onnxruntime, and a small `policy.toml` that names it.

```toml
model = "model.pt"
representation = "simple115v2"
stacked = true
sample = false
device = "cpu"
```

The format builds the observer for that representation and stack, runs the network on the batch of
observations at each step, and takes the largest logit, or draws from the softmax with the
match's policy seed when `sample` is on. It reads `step.renderer_seconds` against its one option,
`step_seconds`, and forfeits a side that was slow.

The process of Policy has `scope = "run"`, so a model loads and warms up once for a run of
matches, and `gpu = "allowed"` so a team that asks for `device = "cuda"` can use the card. A team
that asks for `cuda` where no GPU is visible is refused with an environment refusal, which is
never kept, and a team that runs on `cuda` is reported as not deterministic. The sample `policy-gpu.toml`
is the same sample on a GPU, renamed to `policy.toml`.

The sample ships without weights. `docs/formats/samples/policy/make_model.py` writes `model.pt`, an
untrained network with 460 inputs and 19 outputs, the size of `simple115v2` over four stacked
steps, so a sample can be tried at once.

### Squad

Squad is the format for multi-agent training, where every role has a network of its own and every
player carries a recurrent state. The submission names one policy file for each role in a
`squad.toml`, with the representation and the size of the state.

```toml
representation = "extracted"
hidden = 128

[roles]
CB = "policies/defender.pt"
CM = "policies/midfielder.pt"
CF = "policies/forward.pt"
```

A team plays it under a fixed control that selects its players by role, which the pre-check
requires, and the pre-check refuses a role with no policy. At the start of a match the format
loads every network, and at each step it feeds each controlled player's observation and state to
the network of that player's role, using `match.roles` for the role and `step.indices` for the
player. The states are kept by the engine index of the player, cleared in `reset`, and updated at
every step, including a step whose answer is not used, so each state follows the match as it did in
training.

`docs/formats/samples/squad/make_policies.py` writes three small untrained actors over the minimap
observation, so the sample is complete.

For the academy benchmark, register the sample with `--size 11v11`,
`--selection CB,CM,CF` and `--goalkeeper builtin`. This selection fits
`corner`, `counterattack_easy`, `counterattack_hard` and
`single_goal_versus_lazy`. Those tasks have all three controllable roles
and a controllable goalkeeper, which the built-in AI plays. The seven other
academy tasks have too few controllable players or lack a selected role.
Squad checks the full selection before the benchmark can adapt it.

### Bot

Bot has no weights and no learning. The submission is a `bot.py` with `decide(observation)`, which
gets one player's raw observation and answers the name of a gfootball action such as `right` or
`shot`. The format turns the name into the number of the action in the team's action set, and a
name that is no action of the match is a forfeit of the kind `invalid`.

It is the shortest of the four and shows what a format owes a plain script. It loads `bot.py`,
calls it from its folder as Kaggle does so that the bot's own imports resolve, and seeds the usual
random generators from the match's policy seed in `reset`, so a bot that rolls dice plays the same
match from the same seed.

### Copying an example

To make a format of your own, copy the folder of the example that is closest, change the name in
`format.toml`, and change its options and its pre-check to the rules of your competition. Add it,
check the sample of the example that you copied, and then check a submission of your own. The tests
of the repository, in `tests/arena/test_format_examples.py`, show how to drive the checks and a short
match from Python.

## Built-in formats

Two formats ship with cuju-render, `kaggle/1` and `strict/1`. They are folders of the same kind as
a person's own, under `formats/builtin/` of the package, with a `format.toml` and a script, and the
renderer reads them as it reads any format. A built-in format is read only and keeps its version in
its identifier, so a context may name it as `kaggle`, which means the version that ships, or as
`kaggle/1`. Both play in the match process, which a person's format cannot ask for, so a match
under either runs the code that cuju-render always ran, in the same process, with the same
environment and at the same speed, and nothing is framed or sent through a pipe.

### Kaggle

The format `kaggle/1` is Kaggle's football environment, generalized to any scenario with its own
length. A submission is a folder with a `main.py` at its top. The last callable that the file
defines is the agent, found as Kaggle finds it, and the agent is called as `agent(obs)` or
`agent(obs, configuration)`, whichever its signature takes. The observation holds `players_raw`,
one entry of GRF's raw observation for each controlled player with the arrays as lists,
`controlled_players`, `step`, which counts the calls so far, and `remainingOverageTime`. The agent
answers an action of the default set from 0 to 18 for one player, as `[action]` or the action
alone, and one action for each controlled player, in the order of `players_raw`, for several. A
team of the v2 set answers from 0 to 19, where 19 hands the player to the built-in AI for the
step. No call is made at a step whose answer would not be used, such as a set piece of a fixed
assignment, where GRF hands the players over, and Kaggle's `step` counts calls alone.

The default action set comes from GRF. The engine's
[Rewards and actions](https://github.com/cuju-football/engine/blob/main/docs/rewards-and-actions.md#actions)
guide gives the same indices and explains how directions and sticky actions work.

| Index | Action | Index | Action |
| --- | --- | --- | --- |
| 0 | `idle` | 10 | `high_pass` |
| 1 | `left` | 11 | `short_pass` |
| 2 | `top_left` | 12 | `shot` |
| 3 | `top` | 13 | `sprint` |
| 4 | `top_right` | 14 | `release_direction` |
| 5 | `right` | 15 | `release_sprint` |
| 6 | `bottom_right` | 16 | `sliding` |
| 7 | `bottom` | 17 | `dribble` |
| 8 | `bottom_left` | 18 | `release_dribble` |
| 9 | `long_pass` | | |

The format has five options.

| Option | Default | What it sets |
|---|---|---|
| `players` | `one` | `one` plays Kaggle's own rule that a team controls one player, and `control` lets a team play as many players as its control section says |
| `timing` | on | Kaggle's own timing, below |
| `step_seconds` | 0.5 s | the time of each step, from 0.05 to 1.1 s |
| `overage_seconds` | 60 s | the bank of overage, from 0 to 600 s |
| `load_seconds` | 60 s | the time to load the agent, from 1 to 600 s |

With `timing` on, which is the default when a context chooses Kaggle itself, the agent runs in a
child process that the format starts. The format measures each step from before sending the
request until it receives the answer, so communication and scheduling time count too. A step that
takes longer than `step_seconds` draws the excess from the bank, and the agent sees the bank as it
stands as `remainingOverageTime` in its next observation. A step that outlasts its time and the
whole bank forfeits the side as a `timeout` with the detail `overage`, and an agent that does not
load within `load_seconds` forfeits as a `load` with the detail `timeout`. Kaggle's own football
environment gives each step 0.5 seconds and each agent a bank of 60 seconds, which is where the
defaults come from. The process of the child imports the submission's folder first, as Kaggle
does, and what it prints goes to the terminal of the side.

The preset `open`, **Kaggle open**, plays every team as cuju-render played teams before formats,
with `timing` off and `players` set to `control`. A context that chooses no format plays Kaggle
open, so a match, a tournament or a rating run that never named a format behaves as it always did,
and its ratings keep their keys. Kaggle open calls the agent in the match process with no limit of
time. The conformance suite of the format plays every team of the samples and the test
submissions through the earlier code and through `kaggle/1` under Kaggle open, on `5_vs_5`,
`11_vs_11_kaggle`, an academy task with players out, `benchmark_full_game_11_vs_11_easy` for a
fixed assignment with set pieces and a team of the v2 set, for several seeds, and the two agree on
every action, call, score and recording.

The pre-check of the format asks for a `main.py` at the top of the folder, loads it, and under the
rule `one` refuses a team whose control section says more than one player. The same load check
runs with `--full`. Play a match to exercise the agent and check the actions it returns.

### Strict

The format `strict/1` holds the strict limits that tournaments could set before formats, one
process for each team under limits of time, memory and threads, as a format of its own. Its code
is that of those tournaments, moved behind the contract, so a tournament that earlier versions
began under limits resumes under `strict/1` with the limits it stored as the options of the format.
A submission is a `main.py` as for Kaggle, on any scenario.

Each team plays in a process of its own, with its own import path, a limit of threads and a private
working folder, and from a read only copy of its folder. The host that starts the team measures
its processes from outside the guarded team process. A side that fails to load or to reset in
time, fails in a call, answers late, answers an action outside its set, starts another process,
uses more memory than the format allows or computes for longer on average than it allows forfeits
at once. A forfeit names one of the five kinds of the contract, and the rule of the limit that
broke is its detail, as the section on the format in [Tournaments](evaluate-tournaments.md) lists.
The two sides of a match think at the same time, since each is sent its step before either answer
is read, and each answer counts its time from its own sending, so a match under `strict/1` plays
the same actions as one that asks the sides in turn, in about the time of the slower side alone.
The options are the limits and switches that the tournaments set.

| Option | Default | Range |
|---|---|---|
| `threads` | 1 | 1 to 64 |
| `load_seconds` | 30 s | 1 to 600 s |
| `first_call_seconds` | 10 s | 0.1 to 600 s |
| `call_seconds` | 1 s | 0.01 to 60 s |
| `reset_seconds` | 5 s | 0.1 to 600 s |
| `mean_call_ms` | 20 ms | 0.1 to 10,000 ms |
| `folder_mb` | 100 MiB | 1 to 10,000 MiB |
| `memory_mb` | 2048 MiB | 64 to 65,536 MiB |
| `match_seconds` | 120 s | 1 to 36,000 s |
| `frozen` | on | a read only copy of the folder, and a team whose files changed since its pre-flight is refused at its next match |
| `gpu` | off | whether the team's process sees a GPU |
| `deterministic` | off | whether the pre-check refuses a team whose answers differ when the same steps play again |

The pre-check plays ten steps against the built-in AI on each side and the same ten again on the
left, under the options, and refuses a team that breaks a limit, holds a link, is larger than
`folder_mb` or, with `deterministic` on, does not repeat itself. A team that merely answers
differently on a replay, or answers one action in more than nine of ten calls, gets a warning.
A context records its own forfeit score, as tournaments always did.

The conformance suite of this format plays the strict test submissions, one that plays, one that
crashes, one that sleeps, one that answers invalid actions, one that fails to load and one whose
module clashes with another's, through the earlier code and through `strict/1`, and holds the
forfeit's kind, its step, the score at that point and the pre-flight's report to agree.

## Choosing a format

Every place that plays matches chooses the format its sides play under, and that choice decides
what counts as a forfeit. A tournament, a rating run and Collect evidence, a requested match on
Kick off, a game in Play, an evaluation and the academy benchmark each hold one choice. A choice
names a format by its identifier, such as `kaggle/1` or `strict/1`, and may name one of its
presets and set any of its options, and every team that plays must pass that format's pre-check
before its first match.

A place that chooses nothing plays Kaggle open, the built-in Kaggle format under its preset
`open`. Kaggle open plays every team exactly as cuju-render played teams before formats, with the
same calls, the same observations and the same actions, and it runs no pre-check of its own
beyond the load that every upload already passes. Choosing Kaggle itself, `kaggle/1` without a
preset, turns Kaggle's own timing on, 0.5 seconds for each step and a bank of 60 seconds of
overage, with the agent in a child process as Kaggle runs it.

Under any format, Kaggle open included, a side that breaks the format's rules forfeits. A
submission that crashes, answers an action outside its set or fails to load ends the match at
that step, and the match records the forfeit against that side, with the format's reason. A
forfeit is never evidence for the ratings, so a crash costs a side its match and never moves a
rating. A failure of a format's own code blames no team, gives no result and marks the format
failing in the menu.

### The Format choice on the page

Each place on the page that plays matches shows the same Format choice. A stepper runs over the
formats that load, Kaggle open first, then Kaggle, Strict and the other built-in formats, each
with a lock, then the formats a person added. Beside it stand the presets of the chosen format as
tiles and its options as toggles, tiles and steppers, and a choice of the device a side plays on
shows a processor for `cpu` and a graphics card for `gpu`. The entrants stand on the right, each
with its verdict under the choice and a Check button where it has none.

A broken format is never a step of the stepper, so no context can choose it. Its warning, with
the reason it does not load, shows once, in the Formats list of the Teams page, where a person can
mend or remove it, and the Format choice of each context stays clear of it.

### The value of a choice

A choice is a format's identifier alone, or a mapping of `id` with an optional `preset` and
`options`.

```json
{"id": "strict/1", "options": {"step_seconds": 0.2}}
```

A context also takes `per_entrant` beside them, a mapping from the text that names an entrant to
the options of that entrant alone. Only an option that its format declares with `per_entrant =
true` may differ between entrants, and those options join that entrant's identity in the ratings,
so two entrants under different options are two models. The text of an entrant is the one the
context names it by, such as `rex` or `rex@3fa9c1d200a1b2c3` for a team at an earlier checkpoint,
and a rating run also reads `team:rex`.

### Each place and its default

- **Tournaments.** The configuration holds `submission_format`, one format for every entrant,
  with `per_entrant` keyed by each entrant's team. The pre-flight checks every team under it from
  the kept verdicts where they exist, leaves out each team the format refuses with its reasons,
  and keeps the terminal of each refusal until the tournament ends. A tournament plays every
  match under the one format, the seeding phase included. With no format it plays Kaggle open.
- **Rating runs and Collect evidence.** The body of `POST /api/ratings/runs` and `rate` take
  `submission_format` beside the models, with `per_entrant` keyed by each model's text. The run
  checks each team it measures as it starts, and a team the format refuses leaves the run under
  `excluded`, with the problems the format named. With no format the run plays Kaggle open.
- **Kick off.** A requested match takes `submission_format` as a pair, one choice for the left
  side and one for the right, so two teams of one match may play under two formats. A side
  without a choice plays Kaggle open.
- **The command line.** `cuju arena play --format NAME` plays both sides of a match
  under one format, with `--option KEY=VALUE`, which may repeat, and `--preset NAME` for its
  options. Without it both sides play Kaggle open.
- **Games in Play.** A game takes the same pair for the sides its teams play. A format's forfeit
  ends the game, which is never evidence, and a late answer that the game replaced with the
  built-in AI reaches the format as `Step.previous_applied`.
- **Evaluations.** The table `[evaluate]` of the settings names the format of every team of an
  evaluation, its policy and its opponents, with `format`, `format_preset` and `format_options`.
  The default `format = "open"` plays Kaggle open.
- **The academy benchmark.** The table `[benchmark]` holds the same three keys, `format`,
  `format_preset` and `format_options`, and the body of `POST /api/benchmarks` takes
  `submission_format`. Under a format other than Kaggle open each attempt starts the team afresh,
  so a forfeit ends that attempt alone and the next attempt plays on.

A tournament and a rating run play every side under one format and one set of options, so a
format's limits weigh the same on everyone who is compared, and only per-entrant options may
differ. Kick off and Play may mix formats, one for each side, since their results stand alone.

### Formats that need a machine of their own

A format whose sides play in processes of their own pins each side to a CPU set, so a context
under it plays no more matches at once than the sets of the machine fit. A tournament, a rating
run and an evaluation lower their matches at once to that number by themselves. A tournament
with a stage of a set length longer than 3000 steps also checks that its format's options fit
that length, and refuses the format when a side could pass the arena's time ceiling before the
format's own forfeit.

### Adding a team under a format

A team that no format judges needs Kaggle's `main.py` at the top of its folder, as before. A team
added under a format leaves its entry to that format's pre-check, so a folder whose entry is an
`agent.py`, a `policy.toml` or a `squad.toml` registers as it is.
`cuju arena team add --each DIR --format NAME` adds folders accepted by their format.
Each new team needs a name without `@`, which separates a team from its checkpoint.
Built-in team identifiers such as `builtin` and `random` are reserved in any case.
The route `POST /api/teams?submission_format=ID` accepts one upload under the same rules.
`cuju arena team add FOLDER --format NAME` adds one folder under a format, with
`--option KEY=VALUE` and `--preset NAME` for the format's options.

### Ratings by format

Each scenario keeps one table of ratings, which holds every format, and each row keeps the format
its teams played under. The Ratings page of Evaluate filters the table by format with
`GET /api/ratings?scenario=S&format=kaggle/1`, and without a filter it groups the rows by the
name and version of their format. A row of Kaggle open names no format, as every row before
formats, and reads as `kaggle/1`. The built-in AI plays under no format and stands in every group.

## Terminals

Every format process has a terminal, and a person can open it on demand. A terminal holds what the
process printed, its standard output and its standard error, from Python and from C code alike,
with the output of every child process that the format started through the library's `Child`
joined in. All of it belongs to the submission, whether the submission or a library it loads
printed it, so a warning from a framework, a progress line of a model or a traceback shows
exactly as it was written. A side that plays inside the match process, as the built-in formats
may, keeps what its Python code prints in the same way.

A terminal is kept for each side of a match, one for every pre-check, one for every rating or
tournament match and one for each match that is played on request. A match that is saved gives its
terminals the identifier of its recording, so Watch finds them from the recording in view. A match
of a tournament keeps its terminals under the run `<tournament id>-<match key>`, which the
Tournaments page reads. A
pre-check keeps one terminal for each team, checkpoint and format, and a new check replaces the old
one. The files live in the folder `terminals` of the arena's home, a folder for each run, and
they leave with the run that owns them. A format of `scope = "run"` keeps one process for the
matches of a run, and each match still gets a terminal of its own. The output of the process moves
to the next match's terminal when the next match starts, so a terminal holds the output of its
own match alone.

```text
terminals/
    <run>/
        left.log
        left.meta.jsonl
        left.json
```

The file `left.log` is the submission's stream and nothing else. The lines of the arena itself
are kept apart in `left.meta.jsonl`, one JSON object for each line, with the byte of the stream at
which the arena wrote it. They are the start of the process with its format, the load time, the
time of the answers, the exit code, a refusal or a forfeit with the name of the team and its
reason, and the note that a cut happened. Because the lines travel apart from the stream, a
submission cannot print a line that looks like one of them. The viewer draws them as rows of their
own with the icon of the arena. The file `left.json` holds the state of the terminal, its size,
what was cut, whether it ended and who pinned it. A live terminal retries an update when a reader
holds one of its files open on Windows. Its output becomes visible after the reader releases the
file, even if the submission has finished printing.

### Size

A terminal keeps at most 1 MiB. When a submission prints more, the first 256 KiB and the last
768 KiB stay, and the arena notes how many bytes were cut between them, which the viewer shows
as a row where the cut lies. All terminals together keep at most the setting `max_mib` of the
table `[terminals]`, 512 by default, and the oldest runs leave first. A run whose side still
prints never leaves.

The terminals of a refusal or a forfeit are pinned. A pinned terminal stays whatever the size
limit says, until the tournament that holds it is sealed. A pin that no tournament ever claims,
such as that of a pre-check, expires after the setting `pin_days` of the table `[terminals]`, 30 by
default, and the terminal then leaves like any other run. Sealing unpins it, and the last 16 KiB of
each such terminal are copied into the tournament's own file, so the evidence of a forfeit outlives
the terminal.

### Viewing

The viewer shows plain text. The colors that a submission sets with the usual escape sequences
keep their color, taken from the page's own palette, and so do bold, dim, italic and underline.
Every other control character shows as an escape, such as `\x1b` or `\r`, and so do the characters
that could mislead the eye, such as a right to left override, which shows as `\u202e`. Nothing a
submission prints is ever read as HTML.

The viewer is hidden until a person presses its button. Watch has it as the Terminal tab of the
side panel, with a switch for each team, and it follows a match that is still playing. The
Submission section shows it under the verdict of a check, and the review of a tournament shows it
on the row of a refused or forfeited team. The Copy button puts the visible text on the clipboard
and Save writes it to a text file.

A terminal can hold whatever a submission prints about its machine, such as paths, user names or
library versions. For that reason a team's export never holds a terminal. A recording export or a
tournament export holds the terminals only when the option Terminals is on, which is off by
default. The Terminals tile of the Export tray of Watch saves the terminals of the recording in
view as a zip archive, and the command line adds them with `--terminals`.

```sh
cuju render export match-20261118-101500-3fa9 --output tables --terminals
```

The server answers the same under `/api/terminals`, which `docs/http.md` lists.

## Checking a team

A check asks a format whether it accepts a team, and what the check found stays with the team. A
team is checked only when a person asks. Nothing runs in the background when a team is added,
with one exception that the next paragraph names, and a team that was never checked under a
format reads "not checked" until somebody presses Check.

The exception is an upload. Uploading a team, or a new checkpoint of a team, starts one check of
it after the upload has already answered, under the format the team was added with. A team added
with `cuju arena team add --each DIR --format NAME`, with `--format` or with
`POST /api/teams?submission_format=ID` keeps that format, and each later checkpoint of the team is
checked under it. A team added under no format is checked under Kaggle open, because that check is
only the load that an upload implies, the same load that has always told a person whether their
`main.py` holds an agent. Every other format is checked when a person presses Check in the
Submission section of the team, when the format is pinned there, or at the start of a context that
plays under it, such as a tournament or a rating run. The format a team was added with stays in
`<home>/verdicts/<team>.json` beside its verdicts.

### What a verdict keeps

A verdict is the answer of a format's `accept`, kept together with the terminal of the check. A
team keeps one verdict for each key, and the key holds everything that could change the answer.

- The checkpoint, the identifier of the files that were checked.
- The identity of the format, its name and version or the digest of its folder, with the contract
  and the behavior version of the library.
- Every option of the format, including the ones that reach only the check, such as the number
  of check matches.
- The scenario, or the scenario of the team's size where none was named.
- The control that the check ran with, `full`, the version of the arena and whether a GPU
  was visible to it.

When any of these differs, the team reads as never checked under that format, and the older
verdict stays in the file until newer ones push it out. A team keeps at most the number of
verdicts that the setting `kept_verdicts` of the table `[formats]` gives, 64 by default, and the
oldest go first. Removing a team removes its verdicts.

A verdict that belongs to the machine or to the moment is never kept. A missing package, no GPU,
a load that ran out of time, a check that ran out of time and a safety ceiling all say more about
the computer than about the team, so the arena shows the answer once and checks again the next
time a context starts. A new check that cannot be kept also drops the verdict kept earlier under
the same key, so the Submission section never shows an answer that the last check contradicted. A
refusal that belongs to the team, such as a missing `main.py`, a manifest that does not read or a
team that controls more players than the format allows, is kept like an acceptance.

The Submission section lists every format, and every preset of a format, with its verdict for the
checkpoint that plays. The list comes from `GET /api/teams/ID/verdicts`, which answers the rows
and the number of formats that accept the team, the figure that the Formats tile shows. A row
holds the format, its preset and options, its state, which is `not_checked`, `accepted` or
`refused`, the time of the check, whether the verdict is kept and why it is not, each problem
with its name, level and message, and the run of its terminal. `POST /api/teams/ID/check` runs one
check and answers its row. Its body names the format as `submission_format`, with `options`,
`preset`, `scenario`, `full`, `checkpoint` and `max_steps` beside it. A format that fails answers
its row with the message in `failed`, so the page can mark the format as failing and offer its
terminal.

### Checking a folder from the command line

A student can check a folder before submitting it, without a server and without registering
anything.

```sh
cuju arena check ./my-team --format kaggle
cuju arena check ./my-team --format league --option check_matches=2 --full
cuju arena check ./my-team --format strict --scenario 5_vs_5 --output report.json
```

The command runs the same pre-check as the Submission section, in a process of its own, and takes
the path to a submission folder. To find the folder of a registered submission, run
`cuju arena team show ID`. The size of the team comes from the scenario,
else from the `cuju-team` folder of the submission, else it is 11 against 11, and its name and
control come from that folder too. `--option KEY=VALUE` sets an option of the format and may
repeat, `--preset NAME` starts from one of its presets, `--scenario` names the scenario of the
check, and `--full` asks for the full check of the format, as the start of a tournament does.
The format decides whether its full check plays any steps. The built-in Kaggle check loads the
agent without calling it, even with `--full` or `--max-steps`. The command reports coverage
as agent load in text and as `coverage: "load_only"` in JSON. Use `cuju arena play` to exercise
the agent's actions.
`--output FILE` writes the verdict as a JSON file and prints its path. Use `--json` by
itself to print the verdict as JSON on standard output.
The report holds the folder, the checkpoint, the format and its options, the exit status, the
problems and, under `--verbose`, the last lines that the submission printed.

The exit status tells a script what happened.

| Status | Meaning |
|---|---|
| 0 | The format accepts the submission. |
| 1 | The format refuses it, or the check was cut by `--max-steps`. |
| 2 | The arguments do not fit, for example an unknown format or a bad format option. |
| 3 | The format failed to run, or the computer cannot run it, for example when a package is missing. |

Use `--verbose` after a refusal to see the submission output on standard error. The
verdict explains the refusal on standard output. A check that stops at a missing
`gfootball` is answered by installing `cuju[gfootball]`, which cuju 0.3 provides for
the imports that exported course submissions make.

### A full check that was cut

`--max-steps N` plays at most N steps of every check match. It gives a quick look at a format
that normally plays whole matches, and the report says so. A check in which any match met the
limit before its end refuses with the problem `cut`, whatever the format said, and its verdict is
never kept and never enters a table. A limit as long as every match cuts nothing, and the check
then reads as it would without the option.

### Registering a folder of submissions

A course's organizer registers the submissions of a whole class at once.

```sh
cuju arena team add --each ./submissions --format league --size 5v5
```

Every folder inside the directory is checked under the format, one after another, each in a
process of its own. Hidden folders and loose files are no entrants. A folder that the format
refuses is left out, with the first error of the format as the reason. A folder that the format
accepts becomes a team named by what the manifest states to the format, else by the name in its
`cuju-team` folder, else by the name of the folder, and the verdict of its check is kept for the
team.

Two accepted folders that state the same name are both left out as "duplicate team name". The
renderer never numbers a repeated name, since a number would hide the repeat from the person who
has to fix it, and each folder left out names the other ones that share its name. Names compare
without regard to case or spacing.

Running the command again reuses the team that each folder made before. A folder is found by its
path, and a folder that moved is found by the digest of its files. Files that did not change
leave the team as it is, files that changed become a new checkpoint of the same team, and files
that match an earlier checkpoint make that checkpoint play again. A tournament over the folder
therefore resumes with the same teams and keeps their ratings, and a new checkpoint is a new
model under the same team.

The exit status is 0 when every folder became a team or kept its team, 1 when the format left
some folder out, 2 when the arguments do not fit, and 3 when the format or the computer could
not run the check. `--json` prints the report with the folders registered, their
actions `added`, `same` or `checkpoint`, and the folders left out with their reasons. The Add team
tile of the page posts the same to `POST /api/team-folders`, which `docs/http.md` lists.

## Adding a format

A format is Python that runs with your permissions. Add only formats you trust.

The command line keeps formats with `cuju arena format`.

```sh
cuju arena format list
cuju arena format show kaggle/1
cuju arena format add ./arena
cuju arena format add arena.zip --id arena
cuju arena format export arena --output arena.zip
cuju arena format remove arena
```

A person's formats live in the folder `formats` of the arena's home, one folder each. A format
is added from a folder or a zip archive of at most 16 MiB and 1,000 files, without links, and its
`format.toml` must read under a contract this renderer serves before it is kept. A built-in format
stays as it ships, and a person's format of the same identifier is refused. A team may carry
suggested formats in its own `cuju-team/formats/` folder, which play only once a person adds them.

The server answers the same under `/api/formats`, which `docs/http.md` lists, and a pre-check of
a team of the registry under a format is `POST /api/formats/ID/check`.

The cache folders of formats live in `format-cache` of the home. The table `[formats]` of the
settings bounds them, `cache_gib` for every folder together, 20 by default, and
`format_cache_gib` for one format identity and checkpoint, 2 by default. The folders used least
recently leave first, and a cache never goes into an export.

The table `[terminals]` bounds the terminals with `max_mib`, the MiB that every terminal together
may hold, 512 by default, and `pin_days`, the days that an unclaimed pin stays, 30 by default.
