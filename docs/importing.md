# Importing matches

The import brings matches that were played elsewhere into the arena library, where they
play, search and export as any recording of cuju does. It takes five kinds of file. A GRF dump is
what Google Research Football writes with `write_full_episode_dumps` or `write_goal_dumps`, and
what `cuju.dumps.DumpWriter` writes for a batch. A Kaggle episode is the JSON that Kaggle keeps of
a match of its GRF competition. An action log is the ZIP file of `cuju.ActionLog`. A recording is
a `.cjr` file of cuju, and a bundle is a `.cjz` file that carries a recording with its
attachments, its annotations, overlays and look. The import tells them apart by their content, so
the name of a file and its suffix may be anything.

Every import says how faithful its recording is. A file that holds everything needed to play its
match again is played again in cuju and checked against the file step by step, and when every step
is equal the recording is exact, with every joint of every player. A file that cannot play again,
or whose match plays differently, keeps the positions that its observations give, as a recording
of positions that says so in its header, in its provenance and on the page. The import never makes
up joints, and it never calls a recording exact without the check.

## Importing from Python

`cuju.arena.add_recordings` imports files into the library and returns one entry for each
recording it saved.

```python
import cuju.arena

entries = cuju.arena.add_recordings('episode_done_20261026-120000000000.dump')
entries = cuju.arena.add_recordings('dumps/score_0001.dump', 'kaggle/12345.json', 'run.zip')
entries = cuju.arena.add_recordings('match.dump', overrides=['import.fidelity=positions'])
```

The call takes any number of files of any of the five kinds, and `config` and `overrides` take
settings as elsewhere in the arena, which [Configuration](configuration.md) describes. The
table `[import]`, at the end of this guide, serves the import. Each entry holds these fields.

| Field | Content |
| --- | --- |
| `id` | the identifier of the recording in the library |
| `path` | the path of the recording file |
| `source` | `grf_dump`, `kaggle`, `action_log`, `cjr` or `cjz` |
| `fidelity` | `exact` for a recording with every joint, `positions` for a recording of positions |
| `verified_steps` | the steps that the check found equal to the file, `None` for a recording file |
| `first_difference` | the `step`, `field` and `detail` of the first step that differs, or `None` |
| `reason` | why the recording keeps positions, a label with its English `text`, or `None` |
| `warnings` | labels of what the import noticed, such as a file that ends inside a step |
| `file` | the name of the file |
| `sha256` | the SHA-256 of the file in hexadecimal |
| `run` | the import in the runs manifest |

A label is a mapping of the message `id`, its English `text` and its `values`, so a program shows
the text and the page shows it in its own language. A file that is missing, larger than the
settings allow, of none of the four kinds, or that holds what a dump does not, stops the call with
an `ArgumentError` whose message names the file and the reason.

## Importing from the command line

`cuju arena recording add` imports files and prints the file path of each recording. It reports the identifier, fidelity and any reason
for a recording of positions on standard error.

```sh
cuju arena recording add episode_done_20261026-120000000000.dump
cuju arena recording add dumps/*.dump --set import.fidelity=exact
cuju arena recording add 12345.json --config import.toml --json
```

`--config` names a file of settings and `--set` an override, each of them repeatable, and `--json`
prints the entries that the Python call returns. `cuju render open` takes a dump, a Kaggle episode
or an action log as well as a recording, imports it first and opens the recording that the import
saved.

```sh
cuju render open episode_done_20261026-120000000000.dump
```

## GRF dumps

A GRF dump holds the steps of one episode, one pickle of Python for each step. Each step holds
the observation of the match as a left agent sees it, the positions, movements, tiredness, cards
and roles of every player, the ball with its height, its movement and its spin, the owner of the
ball, the score, the game mode and the steps left, with the sticky actions and the controlled
player of each agent. Each step also holds the actions that the agents took and the reward. The
first step holds the configuration of the match, its scenario as `level`, the engine seed as
`game_engine_random_seed`, the players, the episode number, which picks the form of a scenario,
the order in which the engine processes the teams, and the number of physics steps in a step.
GRF stores the actions of the right agents turned to the frame of the pitch, a direction to the
left as one to the right, and the import turns them back. The players that the built-in AI
controls take no actions of the file, since the engine decides them again as it plays.

### Exact imports

A full episode dump, whose first step is the first step of its episode, holds everything that
playing the match again needs, which is what GRF's own replay relies on. The import plays it in a
batch of cuju of one environment on the CPU, with the scenario, the engine seed, the episode
number, the difficulties, the agents and the order of the teams of the dump, and gives each step
the actions of the file. After each step it builds the observation of the match from the batch,
as GRF builds it, and compares it with the step of the file bit for bit, every position,
direction and flag, the sticky actions and controlled player of each agent, the score and the
reward. A recorder of the arena keeps the frames of the match as it plays, with every joint,
the lanes of the players, the actions and the state of the kick off.

When every step of the file is equal, the recording is exact and the entry says how many steps
the check covered. Such a recording equals the one that a recorder would have kept of the same
match, frame for frame, so it plays in 3D, searches with every kind of moment and plays again from
its own state and actions.

When a step differs, the import names the step and the field, such as `observation.ball` at step
12, keeps the steps before it as checked, and keeps the positions of the whole file instead. A
dump of GRF played with rendering on can differ from the same match played without, and a dump of
a version of GRF whose rules differ from cuju's in some detail differs where the detail first
matters. The check finds both, so the label of the recording is always what the check found. A
part of the observation that the batch does not give, such as the designated player of a team
without agents, is left out of the check and named in a warning.

### Imports of positions

A goal dump holds the steps before a goal, up to a hundred, and the step that scored, and a dump
that a running environment wrote holds its last steps, so neither starts at the kick off and
neither can play again. Such a dump becomes a recording of positions. So does a dump without its
configuration, one whose file ends inside a step, one whose players include definitions other than
agents, and one whose match plays differently from the file.

A recording of positions keeps, for every step of the file, where each player and the ball stood,
the score, the owner of the ball, the game mode and the cards. It keeps the lanes of the players
that the observation gives, the movement, tiredness, role, sticky actions and agent of each player
and the designated player of each team, and the actions of the agents. Its steps keep the numbers
that the file gave them, so a goal dump that starts at step 1,900 shows the clock of that part of
the match.

## Kaggle episodes

A Kaggle episode keeps, for each step, an entry of each agent with its action, its status and its
observation, and the observation holds `players_raw`, GRF's raw observation of each player that the
agent controls. The left agent sees the match in the frame of the pitch. The right agent sees it
turned by half a turn, with its own team as `left_team`, so the import turns its view back,
swapping the teams, the owner of the ball and the score, negating the positions and movements and
turning its sticky actions. The frame of each step comes from the left agent's view, or from the
right agent's turned back where the left one is missing, and a warning counts those steps. The
scenario comes from `scenario_name` of `configuration`, and the names of the teams from
`TeamNames` of `info` when the file has them.

The import reads the layout that Kaggle's episode files use for the GRF competition, as its
environment for Kaggle writes them. That layout holds no engine seed of the match that cuju could
confirm, so the 20 players of the built-in AI cannot be played again, and every Kaggle episode
becomes a recording of positions whose reason says so. An action that lies outside GRF's default
action set leaves the actions out of the recording, with a warning.

## Action logs

An action log of cuju holds the options of its batch, every call to the batch with its actions,
and the digest of the outputs after each call. The import plays the calls again in a batch of
those options on the device of the settings and checks the digest after every call, as
`cuju.action_log.replay` does, while a recorder keeps the episodes of each environment that the
settings choose. Every episode that ends within the checked calls is an exact recording. A call
whose digest differs stops the import, and the episodes that it reached are dropped, so every
recording that an action log gives is exact.

## Recording files

A `.cjr` file joins the library as it is, as `cuju render open` adds one. A file that the library
already holds keeps its identifier, so a file imported twice is added once. Its entry is `exact`
for a recording with joints and `positions` for one of version 3, and holds no checked steps,
since cuju wrote the file itself. The attachment files beside it whose key fits it come along, as
[Recording format](recording-format.md#attachments) describes.

## Bundles

A `.cjz` file is a bundle, a recording with its attachments in one file, as the Export tray of
Watch saves it with the tile Bundle. It joins the library as a recording file does, and its
attachments, the annotations, the overlays and the look of the match, lie beside the recording
again, so the match shows exactly as it did where it was saved. Each attachment of the bundle is
checked against the digest of its recording before anything is written, and a bundle that holds
an attachment of another recording is refused whole, with a message that names the file. A
bundle whose recording the library holds already keeps that recording, adds the attachments that
it lacks and opens it. Its entry is that of a recording file, with `cjz` as its source.

To save the same bundle from a terminal, run
`cuju arena recording export ID -o match.cjz`, using an identifier from the library.

## Exact recordings and recordings of positions

An exact recording is a recording of version 2 with every joint of every player, so the Watch page
shows it in 3D with its animations, touches and calls of the referee. A recording of positions is
a recording of version 3, which [Recording format](recording-format.md) describes, with one
position for each player and none of the joints. The page opens it in the 2D view, where every
player stands at its place, and the 3D view says that the recording keeps positions alone. Its
moments are those that the positions, the owner of the ball and the game mode give, such as goals,
shots and passes, and the moments that need the kind of a touch or the calls of the referee are
listed as unavailable for it. The analytics export writes the same tables of both kinds.

The header of a recording of positions says `fidelity` `positions` and leaves out the scorer of
the engine, which no observation tells, so the page and the moments find the scorer from the
positions. Its frames keep the rotation of the ball at rest, no animation, no touch and no call of
the referee, and the clock runs 1.8 seconds a step, as 90 minutes over 3000 steps.

## Provenance and the runs manifest

Every imported recording names its source in its provenance. The field `source` is `import`, and
`imported_from` holds the name of the file, its SHA-256, its kind as `source`, the `fidelity`
and, for an exact recording, `verified_steps`, the steps that the check found equal. A recording
of positions also holds its `reason`. The provenance travels inside the recording file and its
summary, and the SHA-256 tells whether two recordings came from the same file.

Each import is also a run of kind `import` in the runs manifest, which
[Recordings](recording.md) describes, with one episode for each recording, its file, digest,
source, fidelity, checked steps, scenario, score, outcome and length, and with the counts of
exact recordings and recordings of positions among its aggregates. A failed or cancelled import
keeps its run, with the recordings that it saved before it stopped.

## Limits

An import reads one file at a time and keeps its limits in the settings. `max_bytes` is the
largest file that it reads, `max_files` the files of one call or command and the imports that the
server keeps waiting or running at once, `max_recordings` the recordings that one file may give,
as the episodes of an action log, and `timeout` the seconds that one import of the server may run
before it is stopped. The server reads a request of at most 110 MiB, so a file for the server
stays within that. An import of the server runs in a process of its own, one at a time, and while
the setting `index_on_save` of `[library]` is on, the moments of each recording it saved are
indexed before that process ends, so the recording searches at once.

## Reading a dump safely

A GRF dump is a pickle of Python, and a pickle may name any function of any module for the reader
to call, so loading an untrusted pickle with Python's own reader can run any code. The import
reads dumps with a reader of its own that admits plain data, numpy's arrays and GRF's actions,
each built by a small class of the renderer, and stops a file that names anything else before a
call happens, with a message that names what the file holds. It also counts the values that a file
makes and the depth of their nesting, so a small file that repeats one value into a very large one
stops as well. cuju's own `cuju.dumps.read_dump` follows the same rule.

A Kaggle episode is read as JSON, an action log as a ZIP archive of JSON and bytes, and a
recording through the reader of recordings, none of which runs code. The size of every file is
checked before it is read.

## The settings

The table `[import]` of the settings, which [Configuration](configuration.md) describes, serves
the import. Each key may be set in a file of settings, by `--set` on the command line, by
`overrides` in Python, and the fidelity also by the query of `POST /api/imports`.

```toml
[import]
fidelity = "auto"
device = "cpu"
folder = "Imported"
title = "none"
environments = "all"
max_bytes = 104857600
max_files = 16
max_recordings = 64
timeout = 1800
```

| Key | Accepts | Default | Effect |
| --- | --- | --- | --- |
| `fidelity` | `auto`, `exact` or `positions` | `auto` | `auto` keeps a file exact when it plays again equal and else its positions, `exact` refuses a file that does not play again exactly, and `positions` keeps the positions of every file without playing it again |
| `device` | `cpu` or `cuda` | `cpu` | the device of the batch that plays a match again, which gives the same steps on both |
| `folder` | a folder of the library, empty for the top | `Imported` | the folder of the Recordings page that takes the imported recordings |
| `title` | text, or `none` | `none` | the title of every imported recording, by default the name of its file followed by imported |
| `environments` | `all`, or a list of indices | `all` | the environments of an action log whose episodes become recordings |
| `max_bytes` | a whole number from 1 | `104857600` | the largest file that an import reads, in bytes |
| `max_files` | a whole number from 1 | `16` | the files of one import, and the imports that the server keeps waiting or running |
| `max_recordings` | a whole number from 1 | `64` | the recordings that one file may give |
| `timeout` | a whole number from 1 | `1800` | the seconds that one import of the server may run |

Testing is internal, and reports of files that import differently from what this guide says are
welcome as issues.
