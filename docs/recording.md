# Recordings

A recording holds the captured steps of one episode, each as the world position and rotation of
every joint
of every player, the position and rotation of the ball, the score, the clock, the game mode, the
owner of the ball, and the kind of animation and the touch of the ball of every player. `cuju.arena.Recorder` captures one
environment of a batch and saves one recording per episode into the library. The recorder is a
[step observer][cuju-api-step-observers] of its batch, so from its creation on it captures every
reset of its environment and every step by itself, and saves a recording when an episode ends.
The training loop stays as it was.

```python
import cuju
import cuju.arena

batch = cuju.BatchEnvironment('11_vs_11_stochastic_l1_r0', environment_count=8, device='cuda')
recorder = cuju.arena.Recorder(
    batch,
    environment=3,
    title='Training run 12, environment 3',
    teams=[{'name': 'Agent', 'color': [214, 48, 49]}, {'name': 'Built-in AI'}],
)
observation = batch.reset()
for step in range(3001):
    actions = policy(observation)
    observation, reward, done, information = batch.step(actions)
print(recorder.saved)
```

`Recorder` and `record` also take an environment of `cuju.env.create_environment` or of one of
cuju's gym ids, and a wrapper around one. Such an environment keeps the batch that plays its
matches as `batch`, and a wrapper reaches it as `unwrapped.batch`, so `cuju.arena.record(env)`
records what `env.step` plays, with `environment` counting the environments of that batch.

Programs written for cuju 0.2.0 call `recorder.capture()` after `reset` and
`recorder.capture(done)` after each step, and they keep working. The first call of `capture` turns
the automatic capture off for good and drops the frames that it took and has not saved, so such a
program records the same frames as before. `capture` returns the path of a recording when its call
ends an episode, and it also suits a program that records chosen steps only.

Each capture reads the state of one environment and keeps about 4 KB of it, at a cost of about
5 milliseconds on one core of a laptop processor, more than two steps of an eleven a side match on
the CPU. A batch with automatic resets already shows the start of the next episode on the step that
ends one, so the recorder closes the recording at the step before, which it marks as the end, adds
the goal of the ending step to that frame's score from the scoring signal, and opens the next
recording with the new kick off. The positions of the ending step itself are gone, so a goal shows
the step before the ball crosses the line. A batch with `auto_reset=False` keeps the last step,
and the recording ends on it.

A batch with `auto_reset=False` leaves an ended environment as it is until the program resets it,
and such an environment reports done at every step in between, while the others of the batch play
on. The recorder captures nothing of it between the end of its episode and its reset, so an
evaluation that steps a whole batch until every environment has ended, as cuju's guide teaches it,
yields one recording per episode. The reset of the environment, through `reset`, `reset_done` or a
`reset` with a mask that names it, opens the next recording.

A recording of version 2 keeps more than the picture of its episode. Beside the frames the
recorder writes the lanes of the players, the direction, the tiredness, the sticky actions, the
controlling agent and the role of each player on each step, which it reads from the same state
as the frame and from `batch.roles`. It writes the action of each agent on each step, which the
batch hands to its step observers, and the state of the kick off. The lanes feed the inspector
and the arrows of the page, and the state with the actions plays the episode again step for step
in a batch of the same configuration, which gives every observation of the episode exactly. The
three sections add about 5 percent to a file and less than a tenth to the time of a capture, and
[Recording format](recording-format.md#sections-beside-the-frames) describes them. A recorder
that is attached in the middle of an episode keeps the lanes and the actions of what it sees,
and its first recording says that it started after the kick off and keeps no state. A recording
of version 1 has room for the frames alone.

Call `recorder.save()` to keep an episode before it ends, for example when a training run stops.
It returns the saved path, or `None` when there are no captured frames, and starts a fresh
recording on the next capture. The list `recorder.saved` holds the paths written so far.

A recorder that captures by itself keeps an ended episode until the batch calls it next, at the
next step, reset or close, so that an annotation of the ending step still reaches it, as
[Annotations](annotations.md) describes. The filters then read its summary, and one thread of the
arena encodes and writes each recording while training steps on, in the order the episodes ended.
The thread holds up to four recordings, so a recorder that saves faster than the thread writes
waits for room. `recorder.episodes` counts an episode on the step that ends it, and the list
`recorder.saved` gains a recording once its file is written. `recorder.save()`, `recorder.flush()`
and `recorder.close()` hand an ended episode over at once and wait until every recording handed
over is written, as closing the batch does, and `flush` leaves the frames of the current episode
where they are. A write that fails, from a full disk for example, logs one warning through the
logger `cuju`, and the next `save` or `flush` raises its error. A process that exits hands over
the ended episodes of its recorders and waits for the recordings handed over. A recorder that
captures by hand hands an episode over inside the call of `capture` that ends it, and the path
that `capture` returns names the file before it exists.

A recorder can keep only the episodes worth watching. `keep='goals'` saves the episodes with at
least one goal, `every=10` saves the first episode and every tenth after it, and `min_reward=1.0`
saves the episodes whose reward reaches 1. `keep` also takes a query of the moments language,
such as `keep='value-drop surprise:0.02'`, as
[Searching by policy signals](policy-search.md#in-a-recorder) describes. `keep` also takes a
function, which receives the summary of each episode as it ends and returns whether to save it.

```python
recorder = cuju.arena.Recorder(
    batch, environment=3, keep=lambda summary: summary['goals'] >= 2 or summary['reward'] > 0.5
)
```

The summary holds the `episode` number, counted from 0 over the episodes that the recorder ended,
the `environment`, the `score` of the left and the right team, the `outcome` of the left team at
that score, `win`, `draw` or `loss`, the number of `goals`, the `length`
in frames, the `steps` number of the last frame, the `rewards` of each agent of the environment
summed over the captured steps, their sum as `reward`, and the episode's
[annotations](annotations.md), each with its `frame`, `step`, `player`, `values`, `text`, `action` and
`probabilities`. The agents count in the batch's order, the left team's first, and an annotation's
`player` is the number that `annotate` took, which counts the left team's players first as well,
whatever order the file lists the teams in. An episode is saved when every condition holds, and
`every` counts every episode that ends, saved or not. The recorder decides when the episode ends,
and discards the frames of an episode that it drops without writing anything, which
`recorder.dropped` counts. A filter reads only what the recorder captured and the rewards that the
step returned, so filters leave every output of the batch as it was. A call of `recorder.save()`
writes the frames captured so far whatever the filters say, and a function that raises an exception
removes the recorder from its batch as any failure of capture does. A recorder that captures by hand
does so after every step to preserve consecutive motion. Each recorder takes one batch of a
`MultiDeviceBatch`, as a live source does. A recorder that fails while it captures by itself, from a
full disk for example, leaves its batch with one warning through the logger `cuju`, and the batch
steps on.

A full match of eleven a side takes 3001 frames with automatic resets and 3002 without, about 4.4 MB
on disk, and one of five a side about 1.1 MB with the built in AI on both sides and more when agents
keep the players moving. A full match of
eleven a side plays and records in about half a minute on one core of a laptop processor, and the
recorder's writing thread codes its frames in a few seconds. The recorder writes the
[recording format](recording-format.md) of version 2 by default, and `Recorder(batch, version=1)`
writes version 1, about two and a half times larger, for the page of cuju 0.2.0. The
library keeps each recording as a `.cjr` file beside a small summary that the page lists. The
summary also holds the favorite mark, tags and notes that the page edits, and the time a recording
went to the Trash, so every one of these changes rewrites the small file and leaves the frames as
they are. Deleting a recording on the page moves it to the Trash, which keeps both files on disk
until a recording is deleted from there for good.

The summary beside a recording also keeps the results of its episode, so a list of thousands of
recordings shows them without opening a file. `score` holds the goals of the left and the right
team at the last frame, and `outcome` says how the left team stands at that score, `win`, `draw`
or `loss`. `length` holds the steps that the episode had played at its last frame, and it counts
the last step that an automatic reset leaves out of the frames, so an episode has the same length
with automatic resets and without them. `returns` holds the sum of the rewards of each agent over
the captured steps, in the batch's order of agents, the left team's first. Only a recorder is
handed the rewards, so `returns` comes with the recordings of a recorder, while every recording
that the renderer saves names its `outcome` and `length`. The `length` of the summary that a
filter reads counts frames, as it did before, and the `length` of the library counts steps. A
recording saved before its episode ends keeps the standing and the returns so far.
`cuju.arena.recordings()` returns these fields with each summary. A summary written by an earlier
version gives its `outcome` from its score and leaves `length` and `returns` out.

Each recording of a recorder names the engine seed of its episode in its provenance, as
`engine_seed`, and `cuju.arena.recordings()` returns it with the provenance of each summary. A
batch draws a fresh seed for every episode, and the recorder reads the one that the state holds at
the first frame, so two recordings of one environment name two seeds.
`batch.reset(seeds=[engine_seed])` plays an episode again from its seed when the scenario, the
options of the batch, the order of the teams and the actions are the same, which makes the
recording of an interesting episode a starting point for a closer look at it.

The recordings of one run that keeps them form a group, which the Recordings page shows as one
tile, as [the Recordings page](#the-recordings-page) describes. A model's run of a task of
[the academy benchmark](evaluate-benchmark.md) is such a run when the benchmark keeps its
recordings. The summary of each such recording holds its `group`, with the run's identifier `id`,
its `kind`, which is `benchmark` for a run of the benchmark, its `title`, `competitor` and
`result`, each an English text with its message beside it in `title_id` and `title_values` and so
on, the `attempt` of the recording counted from zero, and the run's `task`, `attempts`, `state`
and `value`, the number of attempts in which the model scored. The state is `playing` while the
run plays, `done` once the run is scored and `stopped` for a run that ended on a failure. A group
that an earlier release wrote keeps its title, competitor and result as the English text it was
written with. `GET /api/matches` lists the summaries of the library and `GET /api/trash` those of
the Trash, both with `?group=ID` for the recordings of one group in the order of their attempts.
`DELETE /api/groups/ID` moves every recording of a group to the Trash at once and answers with
their identifiers in `trashed`, and `POST /api/groups/ID/restore` brings them all back and answers
with `restored`. Each recording of the group also restores on its own, like any other.

The command `cuju arena recording list` prints one row per recording. Each row starts with its
identifier, then the local date, teams, final score and title, with two spaces between columns.
Use `--date today`, `--date yesterday` or `--date 2026-11-20` to filter by local day.
The `--group ID` option lists the attempts of one group in order, and `--trash` lists the
trash. Use `recording list --json` or add `--trash` to find a group identifier under `group.id`.
An empty listing exits with status 0 and gives a next command on standard error.
Use an exact identifier, a unique prefix of at least four characters after the type prefix,
or `last` for the newest recording when you show, export or request captions for a recording.
The show command gives the identifier, title, scenario, teams, score, played length in steps,
date and provenance.
Add `--json` to read the same fields under the schema `arena.recording.show/1`. The
`date` field comes from the library summary's `created` field.

`recording remove --group ID` and `recording restore --group ID` move a group. From Python,
`cuju.arena.recordings` returns the same summaries, and `cuju.arena.delete_group` and
`cuju.arena.restore_group` move a group and return the identifiers of its recordings. The example
below reads the newest group of the library, such as the one that
`cuju arena benchmark run builtin --task corner --keep-recordings` leaves, prints its
recordings and moves the group to the Trash.

```python
import cuju.arena

kept = [summary for summary in cuju.arena.recordings() if 'group' in summary]
group = kept[0]['group']['id']
for summary in cuju.arena.recordings(group=group):
    print(summary['group']['attempt'], summary['id'], summary['score'])
cuju.arena.delete_group(group)
```

Two sample matches ship with the arena, a full match of eleven a side and one of five a side,
both played by the built-in AI. The first start of the server copies each into the library as
`sample-11v11` and `sample-5v5`, where they appear on Home and in Recordings and open in Watch like
any other recording, dated from its shipped file, so the recordings of the user list above it.
The folder notes each sample it has received in `samples.json`, so a deleted
sample stays deleted, and a recording of the same name stays as it is. A sample in the Trash stays
there when the server starts, and Restore brings it back. The command `cuju arena sample add` adds
both samples again, and `cuju arena sample add sample-11v11` or `cuju arena sample add sample-5v5` adds one. A sample
that sits in the Trash comes back from there with its folder, tags and notes, and the command says
which samples it restored and which it added.

The page opens a recording in stages and shows each on a bar over the view, the download with its
share, the reading of the frames, and the players and the stadium while their files still load. A
background worker of the browser unpacks the recording and reads its frames, so the page keeps
drawing and loading meanwhile, and where a worker cannot start the page reads the recording itself
into the same frames. A worker that starts and then fails on a recording leaves it unopened and
shows why. The art of the players, the ball, the goals and the stands loads all at once
beside the recording, and the players of a recording are built once, with their models. The rest
loads when the page first needs it, the sounds when the sound is turned on, and the models and animations of other characters when a team wears them.

The page opens a recording that decompresses to at most 128 MB, holds at most 50,000 frames and
at most 1,000,000 frames times players, and lists at most 32 players, which leaves room for fifteen
full matches of eleven a side in one file. It refuses a larger recording with a message before it
reads the frames. It also refuses a header that is no JSON object and a header whose player
entries lack a team of 0 or 1 or a whole index within the team. A header whose teams are no
objects reads with the teams Left and Right in their place, and a rate of steps that is no number
from 1 to 100 reads as ten steps a second.

## The Recordings page

The Recordings page is the library of every match that the renderer keeps. Each recording stands
as a tile, with the codes of its two teams, each beside a small shirt in its kit, the score in large
figures between them, and the date and its marks along the foot. A mark tells where the recording
came from when that is worth knowing at a glance, an upload arrow for an imported match, a
controller for a game that a person played, the gauge of Evaluate for a run of the benchmark and
the live dot for a live source, and a line of text for a recording with tags. A star at the corner
of a tile keeps the recording among the favorites, and pointing at a tile or giving it the focus
brings up three buttons in its middle, Watch, Compare and Details.

![Recordings with the Import tray open beside the tiles, two of them selected with the bar of the selection below](https://github.com/cuju-football/render/blob/main/tests/support/shots/recordings-import-desktop-dark.png)

### The tree and the grid

A tree of folders runs down the left of the page. All holds every recording of the library and
Favorites the starred ones, each with its count. The folders follow, made with the button beside
their heading, and a folder holds the recordings of the folders below it. A folder renames from its
pencil or with F2 and deletes from its waste bin or with Delete, and deleting a folder moves its
recordings to the folder above, since a folder is only a label in the summary of each recording.
Tiles dragged onto a folder move there. The Trash closes the tree at its foot. The tree is one stop
of the Tab key, and the arrows walk through it while Enter or Space opens the folder in focus.

The grid of tiles is one stop of the Tab key as well. Inside it the arrows move from tile to tile,
the up and down arrows by a whole row, Home and End to the ends of the row, Ctrl with Home or End to
the first and last tile, and Page Up and Page Down by a screen. Enter opens the recording on Watch,
Space selects it or leaves it, Shift with the arrows selects a range, Ctrl A selects every tile,
and Esc clears the selection. F stars the recording, I opens its details, C compares it, F2
renames it and Delete moves it to the Trash. A press on a tile opens Watch as well. Once some
tiles are selected, a press selects instead, as Ctrl and Shift always do, and the small round check
at the top of each tile selects with the pointer alone. Import in the command palette opens the
Import tray from the page.

The page draws only the rows that are in view, a few more above and below, and moves them as the
grid scrolls, so a library of a thousand recordings opens at once. On a phone the tree becomes a
strip across the top, the grid sets two tiles to a row, and the bar of the selection keeps its
symbols.

### Search, chips and order

The search field above the grid finds recordings by their title, teams, scenario, folder, tags
and notes in every folder at once, whatever the case and the accents of the words, and it waits for
a pause in typing before it narrows the grid. The chips beside it narrow the grid further, by
scenario, team, date, source and result. A chip shows its symbol alone while it is unset and lights
with its value once a value is chosen, which is the one rule of every chip of the page, and a
button to clear the filters appears while any chip is lit. The Source chip offers requested
matches, played games, benchmarks, recorders, live sources, imported recordings and those of
unknown source, and the Result chip the matches that the left team won, those that the right team
won, draws and recordings whose score is unknown. The Sort list orders the grid by date, the
newest or the oldest first, by title, by the most goals, by teams, by length or by size. The count
at the right gives the recordings in view and the size of the library on disk.

The switch beside the search reads Matches and Moments. Moments turns the field into the search of
the moments of every recording, with the chips of events and of policy signals, as
[Searching moments](moments-and-search.md#searching-moments) describes.

While the server builds the index of moments, a line under the search says how far it has come,
such as indexing moments, 12 of 40, and the line leaves once every recording is indexed.

The address `#page=recordings&q=Greens` opens the page on a search of the recordings, and
`#page=recordings&moments=goal` on a search of moments, which other pages use to show the
recordings behind a figure. Evaluate links to three more. `#page=recordings&evaluation=ID` shows
the recordings that the evaluation of that identifier kept, `#page=recordings&group=ID` the
attempts of one group of the benchmark, and `#page=recordings&search=QUERY` opens the search of
moments on that query, such as the value drops of one recording. A choice in the tree, or the
address of the page alone, shows the whole library again.

### The bar of the selection

A selection brings up a bar along the foot of the grid. It counts the selected recordings and
offers what applies to them. Watch opens one recording, Compare opens two side by side on Watch,
and Evaluate opens several in the Dashboard of Evaluate. Favorite stars them all, or takes the star
away when every one of them has it. Move to puts them in a folder, Tag adds the tags typed in its
question to each of them, Rename gives one recording a new title, and Details opens the panel of
one recording. Data opens the panel of [tracking data](#tracking-data) for the selection, and the
waste bin moves them to the Trash. The arrows move along the bar, and Esc or the last button clears
the selection.

### Details

Details opens a panel above the grid for one recording, with its Favorite mark, its tags,
separated by commas, up to twenty of at most forty characters each, and its notes, up to ten
thousand characters, which Save details keeps and Close or Esc leaves. The panel lists the
provenance of the recording, the source, the version of cuju and, where they are known, the seed
and the engine seed, the scenario, the teams, who played each side, the controls, the agent
counts, the action set, the ability and the environment, as described under
[Recording format](recording-format.md). A field that the panel leaves out is unknown. An imported
recording also shows where it came from, the file, its fidelity, an exact replay or positions, the
steps that the import checked against the file, and, for a recording of positions, why the import
kept the positions alone, as [Importing matches](importing.md) explains.

### The Trash

Delete moves recordings to the Trash at once, and a message says where they went. The Trash in the
tree lists the recordings deleted from the library with the same search, chips and order, and its
bar offers Restore, which returns the recordings to the library with their folder, details and
frames as they were, and Delete permanently, which removes their files from the disk after a
question and cannot be undone. A recording is deleted for good from the Trash alone, it opens on
Watch once it is restored, and the Trash keeps using disk space until its recordings are deleted.

### Groups

The recordings of one run of the benchmark show as one tile, which names the task and the
competitor, such as Academy, corner, GRF's built-in AI, with the run's result, such as 3 of 5, the
count of its recordings and the date of the newest. A press, Enter, or Alt with the right arrow
opens the group in place, its attempts following its tile in their order with a line of accent
along their foot, and a press, Enter, or Alt with the left arrow closes it again. A screen reader
hears the tile as expanded or collapsed. Selecting the group's tile selects all its recordings, so
Delete moves the whole group to the Trash in one step, and in the Trash its tile restores the group
in one step too.

### Tracking data

Tracking data exports the analytics tables of recordings, as
[Analytics export](https://github.com/cuju-football/render/blob/main/docs/analytics-export.md) describes, from the page. Data in the bar of the selection
opens it for the recordings selected, and on Watch the Export tray holds it as a tile for the
recording in view. The panel offers a table, Tracking with its metadata, Events, Annotations, the
SecondSpectrum pair or the Metrica pair, both of which kloppy loads, a format of CSV or JSON lines
for the first three, and the coordinates, meters from the center of the pitch, the units of GRF or
the units of Metrica. Download saves each file of the table for each recording, named after the
recording and the table, through the route `GET /api/export/<id>`.

## Runs, checkpoints and provenance

A recording can name the run that it belongs to and the model that played it, so an evaluation
of many checkpoints, a benchmark or a batch of imports keeps its episodes together and a view
over them knows which model played each. `Recorder` takes both as keyword arguments.

```python
recorder = cuju.arena.Recorder(
    batch,
    environment=0,
    run={'id': 'wings-training', 'seed': 7},
    checkpoint={'id': 'step-120000', 'label': 'Wings', 'step': 120000},
)
```

`run` takes the identifier of the run as text, or a mapping of `id` and, where the program knows
it, `seed`, the seed that the batch was created with. The batch keeps that seed to itself, so the
program passes it along, and each recording then names it as `provenance.batch_seed`. Each
recording keeps `provenance.run`, a mapping of the run's `id` and the `episode`, the number of
episodes that the recorder ended before this one. A recorder that saw every reset since its batch
was created counts its episodes as the seed rule of the batch does, so the episode and the batch
seed together tell which episode of the seed rule the recording holds. A recording saved by
`recorder.save()` before its episode ends names no episode.

`checkpoint` takes an identifier as text or a whole number, a mapping of `id`, `label` and `step`,
or the path of the model's file or folder. A path names the model by the SHA-256 of its files,
their paths within the folder and their bytes, so the same files name the same checkpoint
wherever they lie, and any change of a file, a new file or a renamed one names another. The
digest changes when a tool rewrites the bytes of line endings. The first
16 characters of the digest become the `id`, the whole digest the `digest`, and the name of the
file or folder the `label`. Each recording keeps the mapping as `provenance.checkpoint`. A team
of the registry has a checkpoint of its own, named by the digest of the files of its submission,
which leaves out the name, kit, images and sounds that the Teams page edits, so renaming a team
keeps its checkpoint and retraining it gives a new one.

`home` names the side at home of the recordings, whose home ground, the stadium that the page
draws around the match, they then show, as a match of that side at home would draw it. A
recording of training names no team, so by default it shows the neutral ground. `home` takes an
entrant, a team, a team at a checkpoint, `builtin` or `random-actions`, or a mapping of
`entrant` with the `ability` that the built-in AI played at, or with the `key` of a policy, which
draws a ground of its own in the colors of the built-in AI. Each recording keeps the side as
`home` in its provenance, and [The ground](https://github.com/cuju-football/render/blob/main/docs/look-and-settings.md#the-ground) describes the draw.
An evaluation that plays a match again names the home side, the evaluated team, the key of a
named policy or the built-in AI at its level.

```python
recorder = cuju.arena.Recorder(batch, home='my-team')
```

Each recording holds the game alone, its frames and the sections beside them. Its annotations,
the overlays of a policy and its look, the kits, the sky and the ground, lie in attachment files
beside it, such as `<id>.annotations.cja`, each keyed by the digest of the recording, as
[Recording format](recording-format.md#attachments) describes. The library keeps them with their
recording, and a bundle carries a recording and its attachments in one `.cjz` file.

A run of many episodes keeps a manifest in the folder `runs` of the arena's home, one file
`runs/<id>.json` for each run, which [Folder and server](https://github.com/cuju-football/render/blob/main/docs/server.md) describes. A benchmark writes
its run there as it goes, and an evaluation and a batch of imports do the same, so the episodes of
a run, its checkpoints and its results stay together across restarts of the server.

The summary beside each recording tells what its file holds, read from the header once the file
is written. `has` lists the sections beside the frames, such as `PLYR` for the lanes of the
players, `ACTS` for the actions and `STAT` for the state of the kick off, with `ANNO` for the
annotations and `GRID` for the overlays that its attachments carry, so a list can offer the
inspector or a replay for exactly the recordings that hold what they need. `attachments` lists
the kinds of the attachment files beside it, and `digest` the key that they name. `fidelity` is `joints` for a recording with the pose of every player and `positions` for a
recording of positions alone, such as an import that kept the positions of its source. `run_key`
holds the key of the run that plays the episode again, with the digest of its state where the
recording keeps one. `run`, `checkpoint` and `engine_seed` repeat those of the provenance, so a
list filters by them without opening a file. A summary written by an earlier version leaves these
fields out, and the index of moments, which reads every recording, holds `has`, `run_key`, `run`
and `checkpoint` for each one all the same.

## Loops that step without Python

A step that a CUDA graph replays, and a step inside a function that `torch.compile` compiled, runs
without Python, so the batch calls no step observer for it and a recorder captures nothing by
itself. Such a loop records by calling the recorder by hand, `recorder.capture()` after the reset
and `recorder.capture(done)` after each replay, with the done flags that the step wrote. The batch
hands such a recorder no actions either, so its recordings keep the lanes of the players and the
state of the kick off and leave the actions out.

```python
recorder = cuju.arena.Recorder(batch, environment=0)
batch.reset()
recorder.capture()
graph = torch.cuda.CUDAGraph()
with torch.no_grad(), torch.cuda.graph(graph):
    observation, reward, done, information = batch.step(actions)
    actions.copy_(policy(observation).argmax(-1))
for _ in range(3000):
    graph.replay()
    recorder.capture(done)
```

Each capture reads a state, which waits for the step in flight, so a recorded loop runs at the
pace of its captures. The batch hands a recorder the rewards and the scoring signal of a step
through its call as an observer, and a replay makes no such call. The summaries of such a loop
therefore count a reward of 0, the recording keeps no `returns`, and with automatic resets the
last frame of an episode lacks the goal of the step that ended it. A batch with
`auto_reset=False` keeps that step in its frames, goal included.

[cuju-api-step-observers]: https://github.com/cuju-football/engine/blob/main/docs/api.md#step-observers
