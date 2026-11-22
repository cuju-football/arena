# Arena Python reference

Import the arena as `cuju.arena`. The [guides](README.md) show how to register teams,
play matches and work with recordings.

<!-- The part below is written by ci/make_reference.py from the code. Edit the text above it. -->

## Names

| Name | Call | Description | Guide |
| --- | --- | --- | --- |
| `add_recordings` | `add_recordings(*paths, config=None, overrides=())` | Imports GRF dumps, Kaggle episodes, action logs, recordings and bundles into the library. | [Importing matches](importing.md) |
| `add_team` | `add_team(folder, name=None, format='11v11', control=None, submission_format=None, *, scenario=None)` | Stores a submission folder or archive as a new team and returns its entry. | [Registering teams](teams.md) |
| `annotate` | `annotate(batch, *, values=None, probabilities=None, logits=None, actions=None, targets=None, grids=None, arrows=None, text=None, extent=None, agent=None, step=None, played=False)` | Attaches what a policy computed for every agent of a batch to the step its sources hold. | [Annotations](annotations.md) |
| `benchmark` | `benchmark(policy=None, config=None, overrides=(), *, progress=None)` | Plays a policy on the academy tasks and returns the benchmark as an `Evaluation`. | [The academy benchmark](evaluate-benchmark.md) |
| `captions` | `captions(recording, path=None)` | Returns the caption cues of a recording and writes them as WebVTT when given a path. | [Moments and captions](moments-and-search.md) |
| `Config` | `Config(values, sources, layers)` | The resolved settings, a read-only mapping of each table by its name. | [Arena configuration](configuration.md) |
| `config` | `config(*paths, overrides=())` | Returns the settings of the package, resolved from their layers. | [Arena configuration](configuration.md) |
| `delete_group` | `delete_group(group)` | Moves every recording of a group to the Trash at once, and returns their identifiers. | [Recordings](recording.md) |
| `evaluate` | `evaluate(policy=None, config=None, overrides=(), *, progress=None)` | Plays a policy against each opponent of the settings and returns the evaluation. | [Evaluation runs](evaluation.md) |
| `Evaluation` | `Evaluation(identifier)` | An evaluation run or a benchmark of a policy, read from the runs manifest. | [Evaluation runs](evaluation.md) |
| `formats` | `import cuju.arena.formats` | The library of submission formats, versioned by the contract that a format folder follows. | [Submission formats](formats.md) |
| `Frames` | `Frames(path, header, heads, roots, tails, sections, joints)` | The frames of one recording as numpy arrays, one row for each frame. | [Recording format](recording-format.md) |
| `frames` | `frames(recording)` | Returns the frames of a recording as numpy arrays, a `Frames` with one row for each frame. | [Recording format](recording-format.md) |
| `home` | `home()` | Returns the folder of the arena, `~/.cuju/arena` or the one `CUJU_ARENA_HOME` names. | [Arena configuration](configuration.md#the-two-home-folders) |
| `moments` | `moments(recording)` | Returns the moments of a recording, each with its outcome, receiver and positions. | [Moments and captions](moments-and-search.md) |
| `play` | `play(left, right, scenario='11_vs_11_kaggle', seed=None, title=None, device='cpu', difficulty=(None, None), *, ability=None)` | Plays a match between two teams of the registry, saves it and returns the recording. | [Requested matches](matches.md) |
| `plugins` | `import cuju.arena.plugins` | The one mechanism through which code of a person's own joins the arena. | [Plugins](plugins.md) |
| `PolicyError` | `PolicyError(cuju.errors.Error, RuntimeError)` | Raised when a policy fails, answers with anything but actions, or stops answering. | [Registering teams](teams.md) |
| `rate` | `rate(models, scenario, config=None, overrides=(), *, progress=None, submission_format=None)` | Gathers evidence for models on one scenario and returns the rating run. | [Ratings](evaluate-ratings.md) |
| `record` | `record(batch, environment=0, **options)` | Records the matches of one environment of a batch, as a `Recorder` that also serves as a context manager. | [Recordings](recording.md) |
| `Recorder` | `Recorder(batch, environment=0, title=None, teams=None, scenario=None, version=FROM_CONFIG, keep=FROM_CONFIG, every=FROM_CONFIG, min_reward=FROM_CONFIG, config=None, overrides=(), *, run=None, checkpoint=None, home=None)` | Records the matches of one environment of a batch into the renderer's library. | [Recordings](recording.md) |
| `recordings` | `recordings(group=None, trash=False)` | Returns the summaries of the library, as the Recordings page lists them, newest first. | [Recordings](recording.md) |
| `restore_group` | `restore_group(group)` | Restores every recording of a group from the Trash, and returns their identifiers. | [Recordings](recording.md) |
| `runs` | `runs(kind=None, run=None)` | Returns the manifests of the runs of matches, newest first. | [Evaluation runs](evaluation.md) |
| `search` | `search(query='', limit=None, *, config=None, overrides=())` | Finds moments across every recording of the library, as `cuju arena search` does. | [Moments and captions](moments-and-search.md) |
| `statistics` | `statistics(identifier, *, name=None)` | Returns the statistics of a recording of the library, found first when none are kept. | [Recordings](recording.md) |

## Classes

### `Config`

| Member | Description |
| --- | --- |
| `layers` | The layers that the settings came from, the lowest first. |
| `source(key)` | Returns the layer that set a key, as `layers` names it. |
| `sources()` | Returns the layer of every key, each key with its tables, in the order of the tables. |
| `to_dict()` | Returns the settings as plain dictionaries and lists, a copy that may change. |
| `to_toml(sources=False)` | Returns the settings as TOML, which reads back to the same settings. |
| `table(name, **arguments)` | Returns a table as a plain dictionary, with keyword arguments of a call laid over it. |

### `Evaluation`

| Member | Description |
| --- | --- |
| `id` | The identifier of the run, such as `e-20261117-101500-3fa9`. |
| `name` | The name of the model. |
| `run` | The series that the run belongs to, or `None`. |
| `checkpoint` | The checkpoint of the model, a mapping of `id`, `label` and `step`, or `None`. |
| `kind` | `evaluation` or `benchmark`. |
| `state` | `queued`, `running`, `done`, `failed` or `cancelled`. |
| `config` | The resolved settings, stored with the results. |
| `summary` | The results overall and against each opponent, as `summarize` describes them. |
| `episodes` | The matches, each with its `number`, `opponent`, `index`, `side`, `seed`, `goals_for`, `goals_against`, `outcome`, `length` and `recording`. |
| `recordings` | The identifiers of the recordings kept. Reading it waits for the process that draws them. |
| `progress` | Where the run stands, as `progress_of` describes it. |
| `queue` | The state of its schedule, with `playing`, `next`, `played`, `most` and `settled`. |
| `wait(timeout=None)` | Waits until the run has ended and its kept matches are drawn. |
| `scalars(prefix='eval')` | Returns the numbers that `log` writes, by tag. |
| `log(target, step=None, prefix='eval')` | Writes the results as scalars to a TensorBoard writer or a W&B run. |
| `to_dict()` | Returns the evaluation as plain data, as `GET /api/evaluations/ID` answers it. |

### `Frames`

| Member | Description |
| --- | --- |
| `path` | The file that the frames come from. |
| `header` | The header of the recording, as the file holds it. |
| `version` | The version of the file, 1, 2 or 3. |
| `frame_rate` | The frames of a second of play, the header's steps per second, 10. |
| `lineup` | The players of the header in their order, each with its `team`, `index`, `goalkeeper` and `controlled`. The axis of the players of every array follows it. |
| `step` | `[T]` `int32`, the step of the episode, 0 at its reset. |
| `clock` | `[T]` `int32`, the match time in milliseconds, 90 minutes over 3000 steps. |
| `game_mode` | `[T]` `int32`, GRF's game mode, 0 in normal play and 1 to 6 for the restarts. |
| `score` | `[T, 2]` `int32`, the goals of the left team and of the right team. |
| `ball` | `[T, 3]` `float32`, the position of the engine's ball in meters. |
| `ball_rotation` | `[T, 4]` `float32`, the rotation of the ball as a quaternion x, y, z, w. |
| `owner` | `[T, 2]` `int32`, the team that owns the ball and the owner's index in the lineup, each -1 for none. |
| `frame_flags` | `[T]` `int32`, the bits of the frame, 1 at the end of an episode, 2 at its start, 4 for a called offside and the scorer in bits 8 to 12. |
| `referee` | `[T, 4]` `int32`, the foul offender, the victim, the kind of the foul and the offside player, as the frame holds them. |
| `positions` | `[T, P, 3]` `float32`, the position of the root joint of each player in meters. |
| `flags` | `[T, P]` `uint8`, 1 for a player on the pitch, 2 for a yellow card, 4 for a red card and 8 for a player that an agent controls on the step. |
| `animations` | `[T, P]` `uint8`, the kind of animation that each player runs, GRF's `e_FunctionType`. |
| `touches` | `[T, P]` `uint8`, 0 for no touch of the ball on the step, else 1 plus GRF's kind of the touch. |
| `players` | The lanes of `PLYR` by field in the units of GRF's raw observation, as `players.scaled` gives them, or `None` for a file without. |
| `actions` | `[T + 1, agents]` `uint8`, the actions of `ACTS`, or `None` for a file without. |
| `annotations` | The records of the attachment `annotations`, or of the section `ANNO` of an older file, as `annotations.decode` gives them. |
| `fidelity` | `joints` for frames with every joint, `positions` for a recording of version 3. |
| `provenance` | The provenance of the header, empty when the file has none. |
| `joints` | `[T, P, 14, 7]` `float32`, the position in meters and the rotation of every joint. |
| `overlays` | The overlays of the attachment `overlays`, or of the section `GRID` of an older file. |
| `rooted()` | Returns the frames as version 1 stores them, with the root joint of each player alone. |

### `PolicyError`

`PolicyError` adds no public member of its own.

### `Recorder`

| Member | Description |
| --- | --- |
| `environment` | The index of the environment. |
| `saved` | The paths of the recordings written so far, each added once its file is written. |
| `version` | The version of the recording files. |
| `episodes` | The number of episodes that ended, saved, dropped or still held for the next call of the batch. |
| `dropped` | The number of episodes that the filters dropped. |
| `capture(done=None)` | Adds the environment's current step by hand, and saves the recording when its episode ended. |
| `annotate(values=None, text=None, player=None, step=None, probabilities=None, action=None, played=False, *, agent=None, logits=None, targets=None, grids=None, arrows=None, extent=None)` | Attaches numbers, text or overlays to a captured step, which the page shows with the recording. |
| `save()` | Writes the frames captured so far as one recording, starts a new one, and waits until every recording handed to the writer is written. |
| `flush()` | Waits until every recording handed to the writer is written, after handing over an episode that ended at the last call of the batch. |
| `close()` | Detaches the recorder from its batch, saves the frames captured so far and waits until every recording handed to the writer is written. |

<!-- The part above is written by ci/make_reference.py. The text below is written by hand. -->
