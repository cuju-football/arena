# Arena configuration

The arena resolves its settings from defaults, the home file, optional files and overrides,
in that order. A run records the values it used, so another run can use the same settings.
Each arena settings file describes the match, submission or recording work of the arena.
The [renderer configuration](https://github.com/cuju-football/render/blob/main/docs/configuration.md#renderer-settings) describes the separate
settings of the page.

## The layers

The settings are resolved from layers, and each layer lies over the ones before it. A layer names
only what it changes, so a key that a layer leaves out keeps the value of the layer below.

| Order | Layer | Where it comes from |
| ----- | ----- | ------------------- |
| 1 | The shipped defaults | the folder of defaults of the package, one file for each table |
| 2 | The home file | `arena.toml` in the home folder of the arena, or `render.toml` in that of the renderer, when it exists |
| 3 | The files of the environment | the files that the variable `CUJU_ARENA_CONFIG` or `CUJU_RENDER_CONFIG` names |
| 4 | The files of the call | the files that a function or a command names, in their order |
| 5 | The overrides | texts such as `recorder.every=4`, and the keyword arguments of a call |

The first layer holds every key, each with a comment that says what the key does. The files are
plain text and worth reading, and they live in
[the folder of the defaults of the arena](../src/cuju/arena/defaults/annotations.toml) and
[that of the renderer](https://github.com/cuju-football/render/blob/main/src/cuju/render/defaults/clip.toml), whose first files explain
the rules in their opening lines.

The second layer suits the choices of one person on one computer, such as a live source that keeps
a backlog. The file is `arena.toml` in the home folder of the arena and `render.toml` in the home
folder of the renderer, as the next section describes.

The third layer reaches a script that runs unchanged, which is how `CUJU_RENDER` turns live
watching on for a training run. The variable holds one path or several, separated as the paths of
`PATH` are, with a colon on Linux and macOS and a semicolon on Windows. `CUJU_ARENA_CONFIG` holds
files of arena tables and `CUJU_RENDER_CONFIG` files of renderer tables.

```sh
CUJU_RENDER=on CUJU_RENDER_CONFIG=watching.toml python train.py
```

The fourth layer belongs to one call or one command, and the fifth to a single value that is
tried once or that a script computes.

## The two home folders

The arena keeps its records in `~/.cuju/arena`, or the folder that the variable `CUJU_ARENA_HOME`
or the option `--home` names. That folder holds the matches, teams, tournaments, ratings,
runs, formats, the format cache, terminals, scenarios, statistics, logs, plugins and the list of
sample teams. The renderer keeps its own state in `~/.cuju/render`, or the folder that
`CUJU_RENDER_HOME` or the option `--home` names. That folder holds the languages of the page, live
sources, requested matches, videos, import jobs and the files of the server and of the launchers.

Version 0.1.0 kept all of it in `~/.cuju/render`. The first start of the arena with no folder of
its own, and with `CUJU_ARENA_HOME` unset, moves the folders and files that the arena owns out of
the renderer's folder into `~/.cuju/arena`, once. The move renames each entry in place, so every
byte stays and nothing is deleted, and the arena prints one line that names what moved and where.
A file `render.toml` that holds tables of the arena is split in the same step. Its arena tables
go to a new `arena.toml` in `~/.cuju/arena`, the original stays beside it as `render.toml.bak`,
and a new `render.toml` keeps the tables that belong to the renderer, when it has any. The one
line that the arena prints says so.

## Files

A file of settings is TOML when its name ends in `.toml` and JSON when its name ends in `.json`.
Both formats follow one schema and resolve to the same settings, so a team may keep its files in
the format it prefers, and a program that writes settings may write JSON while a person edits
TOML. The settings are grouped in tables, and a file holds the tables and keys that it changes.

```toml
# evaluation.toml
[recorder]
keep = "goals"
every = 4

[search]
window = 3.0
```

The same file as JSON reads as follows.

```json
{
  "recorder": {"keep": "goals", "every": 4},
  "search": {"window": 3.0}
}
```

Four rules hold for every file.

A table changes the keys that it names. The file above leaves `version` of `[recorder]` as the
layer below set it.

The text `"none"` stands for a key that is unset. TOML has no empty value, so the word takes its
place, and a JSON file may write either `"none"` or `null`. A key accepts the word only when it
can be unset, such as `min_reward` of `[recorder]`, and the resolved settings then hold `None`
for it in Python.

A list of tables replaces the list of the layer below as a whole. Each entry of the new list
takes the keys that it leaves out from the first entry of the shipped defaults, so an entry names
only what makes it differ.

An unknown table or key is refused, and so is a value that its key does not accept. The message
names the key, lists the keys of its table and names the file, so a slip of the keyboard is found
at once.

```text
expected a key of [recorder], one of capture, every, inspector, keep, min_reward and version, found kep in evaluation.toml
expected recorder.every as a whole number of at least 1, found 0 in evaluation.toml
```

## Overrides

An override is a text of the form `key=value`. The key names its table and then the key, joined
by periods. The value is read as TOML, so `4` is a whole number, `0.25` a number, `true` a truth
value and `["a", "b"]` a list, and a value that is no TOML value is taken as text, so
`recorder.keep=goals` needs no quotation marks. A whole table takes an inline table, and an entry
of a list of tables is named by its index, counted from 0.

```sh
cuju arena config show --set recorder.every=4 --set recorder.keep=goals
cuju arena config show --set 'recorder={ every = 4, keep = "goals" }'
```

Overrides are checked as the files are, with the same messages, and they are laid after every
file in the order they are given.

## Python

`cuju.arena.config` resolves the layers of the arena and returns the settings. The renderer
resolves the renderer tables the same way.

```python
import cuju.arena

settings = cuju.arena.config('evaluation.toml', overrides=['recorder.every=2'])
settings['recorder']['keep']  # 'goals'
settings['recorder.every']  # 2
settings.source('recorder.keep')  # 'evaluation.toml'
settings.source('recorder.every')  # 'overrides'
settings.source('recorder.version')  # 'defaults'
```

The function takes any number of paths, laid in their order over the standing layers, and
`overrides` as a list of texts or as one text. The result is a `Config`, a read-only mapping of
the tables by name. A table reads as a mapping and a list as a tuple, and both refuse changes, so
settings that are passed around stay what they were. A key may also name its table and its key in
one text joined by periods, as `settings['recorder.every']` does.

| Member of `Config` | What it gives |
| ------------------ | ------------- |
| `settings[key]` | a table, or one key named with its table, such as `recorder.keep` |
| `settings.source(key)` | the layer that set a key, or for a table the highest layer that set one of its keys |
| `settings.sources()` | every key with its layer |
| `settings.layers` | the layers in order, the lowest first |
| `settings.to_dict()` | the settings as plain dictionaries and lists, a copy that may change |
| `settings.to_toml()` | the settings as TOML text, which reads back to the same settings |
| `settings.table(name)` | one table as a plain dictionary |

A layer is named `defaults` for the shipped defaults, by its path for a file, `mapping` for a
mapping and `overrides` for the overrides.

The functions and classes of the renderer that read settings take the same two arguments,
`config` and `overrides`. `config` may be a path, a list of paths, a `Config` that was resolved
earlier, or a plain mapping of tables, which suits a notebook where a file would be one thing
more to keep.

```python
recorder = cuju.arena.Recorder(batch, config='evaluation.toml')
recorder = cuju.arena.Recorder(batch, config={'recorder': {'keep': 'goals'}})
recorder = cuju.arena.Recorder(batch, config='evaluation.toml', every=2)
```

The keyword arguments that a function had before the settings existed keep their meaning. An
argument that is given wins over every file, as the last line shows, and an argument that is left
out takes the value of the settings. Runtime objects such as a batch, a policy or a function that
chooses episodes stay arguments, since a file holds only plain values.

## The command line

`cuju arena config show` prints the resolved settings of the arena as TOML, each key followed by
the layer that set it. The output can be saved, edited and used as a settings file.

```sh
cuju arena config show
cuju arena config show --config evaluation.toml --set recorder.every=2
cuju arena config show --config evaluation.toml --config machine.json --json
```

```toml
# The layers, the lowest first, defaults, evaluation.toml, overrides.

[annotations]
probabilities = 3  # defaults
max_values = 16  # defaults
perspective = "agent"  # defaults
grid_cells = 6912  # defaults
grid_every = 1  # defaults

[recorder]
keep = "goals"  # evaluation.toml
every = 2  # overrides
min_reward = "none"  # defaults
version = 2  # defaults
inspector = "auto"  # defaults
capture = "state"  # defaults

[search]
value_drop = 0.3  # defaults
window = 3.0  # evaluation.toml
surprise = 0.1  # defaults
```

`cuju arena config check` resolves the same layers and prints nothing but the layers it checked,
or the first refusal with exit status 1. It suits a hook that runs before a long job starts, so a
mistyped key stops the job in the first second.

```sh
cuju arena config check evaluation.toml
```

```text
checked the defaults
checked evaluation.toml
the settings fit
```

Both actions take files as plain arguments and through `--config`, which may repeat, and
overrides through `--set`, which may repeat as well. Both read the home file and the
variable of the package as every call does, so `cuju arena config check` alone checks the
standing files of the computer. With `--json`, `show` prints the settings under `config`, the layer of
every key under `sources` and the layers under `layers`, and `check` prints `ok` and `layers`. A
refusal under `--json` is a JSON object with the message under `error`, as for every command of
`cuju render`.

## The shipped tables

### Recordings, `[recorder]`

The table holds what a `Recorder` saves of the episodes that it captures.

| Key | Accepts | Default | Meaning |
| --- | ------- | ------- | ------- |
| `keep` | text | `"all"` | the episodes to save, `all`, `goals` for those with a goal, or a query of the moments language |
| `every` | a whole number from 1 | `1` | saves the first episode and every so many after it, counting every episode that ends |
| `min_reward` | a number or `"none"` | `"none"` | saves only the episodes whose summed reward is at least this |
| `version` | `1` or `2` | `2` | the version of the recording files, 1 for the readers of cuju 0.2.0 |
| `inspector` | `"auto"`, `true` or `false` | `"auto"` | GRF's raw fields of every player for the inspector, kept under `auto` when the batch has raw observations |
| `capture` | `"state"` or `"actions"` | `"state"` | whether a capture reads the state at every step, or keeps the actions alone and plays the episode again from its seed once it ends |

### Annotations, `[annotations]`

The table holds the limits of the annotations that a recorder and a live source keep of a step.

| Key | Accepts | Default | Meaning |
| --- | ------- | ------- | ------- |
| `probabilities` | a whole number from 1, or `"all"` | `3` | the likely actions kept of the probabilities of a step |
| `max_values` | a whole number from 1 to 16 | `16` | the named values kept of one player at one step, at most |
| `perspective` | `"agent"` or `"pitch"` | `"agent"` | the view of grids and targets, as the observation of the agent shows the pitch or as the recording shows it |
| `grid_cells` | a whole number from 1 to 6912 | `6912` | the cells of one grid at most, 96 by 72 |
| `grid_every` | a whole number from 1 | `1` | the steps from one kept grid to the next |

### Search, `[search]`

The table holds the thresholds of the policy signals that a search of moments finds.

| Key | Accepts | Default | Meaning |
| --- | ------- | ------- | ------- |
| `value_drop` | a number from 0 | `0.3` | a value drop counts when a named value falls by this much within the window |
| `window` | a number from 0 | `2.0` | the window of a value drop, in seconds |
| `surprise` | a number from 0 to 1 | `0.1` | a surprise counts when the chosen action had at most this probability |

### The plugins, `[plugins]`

The table holds which plugins of the arena stay off. A plugin that the list names is found and
shown as disabled, and no context can choose it. [Plugins](plugins.md#choosing-a-plugin) tells how
a plugin is written and chosen.

| Key | Accepts | Default | Meaning |
| --- | ------- | ------- | ------- |
| `disabled` | a list of texts | `[]` | the identifiers of the plugins to leave off |

### The tables of other guides

Each of the other shipped tables belongs to one feature, and the guide of that feature gives its
keys, what each accepts and its default. The arena's tables come first and the renderer's follow.

| Table | What it holds | Guide |
| --- | --- | --- |
| `[library]` | whether the moments, signals and statistics of a recording are found as it is saved | [Folder and server](https://github.com/cuju-football/render/blob/main/docs/server.md) |
| `[import]` | the fidelity, the device, the folder, the title and the limits of imports | [Importing matches](importing.md#the-settings) |
| `[play]` | the scenario, seed and right side of requested matches | [Requested matches](matches.md) |
| `[evaluate]` | the policy, its opponents, the matches, what to keep and the statistics of an evaluation | [Evaluation runs](evaluation.md#settings) |
| `[benchmark]` | the tasks, the recordings and the workers of a benchmark from Python | [The academy benchmark](evaluate-benchmark.md#from-python) |
| `[tournament]` | what a tournament seeded by rating does when an entrant lacks evidence | [Tournaments](evaluate-tournaments.md#the-seeding-phase) |
| `[ratings]` | the method, the anchor, the sources and the evidence of the ratings, and the rating runs | [Ratings](evaluate-ratings.md#settings) |
| `[teams]` | the checkpoints that a team keeps, their number and their size | [Team files](team-files.md#keeping-checkpoints) |
| `[formats]` | the folder that submission formats keep between runs and the verdicts of checks | [Submission formats](formats.md) |
| `[terminals]` | the size that the terminals of the matches may take and how long a pinned one stays | [Submission formats](formats.md) |
| `[export]` | the tables and the options of the analytics export, in the renderer | [Analytics export](https://github.com/cuju-football/render/blob/main/docs/analytics-export.md#the-settings) |
| `[clip]` | the renderer and the options of a clip or a video, in the renderer | [Video](https://github.com/cuju-football/render/blob/main/docs/video.md) |
| `[compare]` | how Compare orders the matches of two evaluations, in the renderer | [Compare](https://github.com/cuju-football/render/blob/main/docs/compare.md) |
| `[ground]` | the look of the ground that a recording draws, in the renderer | [Look and settings](https://github.com/cuju-football/render/blob/main/docs/look-and-settings.md) |
| `[server]` | the quiet time of a kept connection and the size from which a JSON answer goes out in gzip, in the renderer | [Folder and server](https://github.com/cuju-football/render/blob/main/docs/server.md#the-settings-of-the-server) |

## Tournament files

The file argument of `cuju arena tournament new` and `tournament check` supplies the configuration of
a tournament, which is a document of its own with its own keys and stays apart from the layers
above. It reads a TOML file as well as a JSON file, by the end of the name. In a TOML file the
text `"none"` stands for the `null` of JSON, such as an entrant whose difficulty follows the
standard. A file names the level of the built in AI as `difficulty`, or by its earlier name
`ability`, which reads the same.

```toml
name = "Spring league"
seed = 7

[[entrants]]
kind = "builtin"
difficulty = 0.3

[[entrants]]
kind = "builtin"
difficulty = "none"

[defaults]
scenario = "5_vs_5"

[[stages]]
format = "league"
```

The page, `POST /api/tournaments` and Edit as JSON keep to JSON.

## The folder of the defaults

The shipped defaults are the schema of the settings. Each package has a folder of them, `defaults`
for each package. Each file of a folder is named after the one
table that it holds, and a new file adds its table with nothing more to register.
A key accepts values of the kind of its default. Where the default leaves open what a key
accepts, the file adds a rule under `[accepts.<table>]`, with `type`, `choices`, `minimum`,
`maximum` or `items`, and the key `"*"` opens a table to keys that the defaults leave out. Every
key carries a comment, and `tests/arena/test_config.py` and `tests/render/test_settings.py` hold the folders to
these rules.
