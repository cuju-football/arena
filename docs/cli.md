# Arena command line

Run `cuju arena` for help. The commands share the settings and home described in
[Arena configuration](configuration.md). The [Python reference](reference.md) describes the API.

Use `cuju completion bash`, or select `zsh`, `fish` or `powershell`, to generate
the family completion script. With the script loaded, Tab offers command words and
fixed values, including shipped scenarios, formats, built-in difficulty examples
and the identifiers of built-in and installed Arena plugins. Tab also offers `last`
where a command accepts the newest recording, evaluation or tournament as an argument.
The script takes its words from the packages installed when you generate it.

## Scripting

Each call with `--json` prints one JSON object. To collect several results in a
single file with one object on each line, pipe the results through `jq -c`.
The version command exits quietly if the reader of its output pipe has already closed.

```sh
for seed in 7 8 9
do
    cuju arena play --scenario academy_empty_goal_close --seed "$seed" --json
done | jq -c . > matches.jsonl
```

<!-- The part below is written by ci/make_reference.py from the code. Edit the text above it. -->

## Arena root options

| Option | Values | Default | Help |
| --- | --- | --- | --- |
| `-V, --version` |  |  | show program's version number and exit |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

## Commands

| Command | Does | Guide |
| --- | --- | --- |
| [`cuju arena team`](#cuju-arena-team) | manage teams in the registry | [Registering teams](teams.md) |
| [`cuju arena team add`](#cuju-arena-team-add) | register a submission | [Registering teams](teams.md) |
| [`cuju arena team list`](#cuju-arena-team-list) | list the teams in the registry | [Registering teams](teams.md) |
| [`cuju arena team show`](#cuju-arena-team-show) | show a registered team | [Registering teams](teams.md) |
| [`cuju arena team set`](#cuju-arena-team-set) | change a registered team | [Registering teams](teams.md) |
| [`cuju arena team export`](#cuju-arena-team-export) | export a team as a zip archive | [Registering teams](teams.md) |
| [`cuju arena team remove`](#cuju-arena-team-remove) | remove a team from the registry | [Registering teams](teams.md) |
| [`cuju arena checkpoint`](#cuju-arena-checkpoint) | manage checkpoints of registered teams | [Team identity files](team-files.md) |
| [`cuju arena checkpoint add`](#cuju-arena-checkpoint-add) | add a checkpoint to a team | [Team identity files](team-files.md) |
| [`cuju arena checkpoint list`](#cuju-arena-checkpoint-list) | list the checkpoints of a team | [Team identity files](team-files.md) |
| [`cuju arena play`](#cuju-arena-play) | play and record a match | [Requested matches](matches.md) |
| [`cuju arena recording`](#cuju-arena-recording) | manage recordings in the library | [Recordings](recording.md) |
| [`cuju arena recording list`](#cuju-arena-recording-list) | list the recordings in the library | [Recordings](recording.md) |
| [`cuju arena recording show`](#cuju-arena-recording-show) | show a recording in the library | [Recordings](recording.md) |
| [`cuju arena recording add`](#cuju-arena-recording-add) | import recordings and bundles | [Recordings](recording.md) |
| [`cuju arena recording export`](#cuju-arena-recording-export) | export a recording and its attachments | [Recordings](recording.md) |
| [`cuju arena recording remove`](#cuju-arena-recording-remove) | move a recording group to the trash | [Recordings](recording.md) |
| [`cuju arena recording restore`](#cuju-arena-recording-restore) | restore a recording group from the trash | [Recordings](recording.md) |
| [`cuju arena sample`](#cuju-arena-sample) | manage the shipped samples | [Getting started with the arena](getting-started.md#samples) |
| [`cuju arena sample list`](#cuju-arena-sample-list) | list the samples and their states | [Getting started with the arena](getting-started.md#samples) |
| [`cuju arena sample add`](#cuju-arena-sample-add) | add or restore shipped samples | [Getting started with the arena](getting-started.md#samples) |
| [`cuju arena search`](#cuju-arena-search) | find moments in the recording library | [Moments and captions](moments-and-search.md#searching-moments) |
| [`cuju arena captions`](#cuju-arena-captions) | write captions of a recording | [Moments and captions](moments-and-search.md#captions) |
| [`cuju arena tournament`](#cuju-arena-tournament) | manage tournaments | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena tournament new`](#cuju-arena-tournament-new) | create a tournament | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena tournament check`](#cuju-arena-tournament-check) | preview and check a tournament | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena tournament list`](#cuju-arena-tournament-list) | list the tournaments | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena tournament presets`](#cuju-arena-tournament-presets) | list the tournament presets | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena tournament ratings`](#cuju-arena-tournament-ratings) | list the tournament ratings | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena tournament show`](#cuju-arena-tournament-show) | show a tournament | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena tournament run`](#cuju-arena-tournament-run) | run a tournament | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena tournament resume`](#cuju-arena-tournament-resume) | resume a tournament | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena tournament cancel`](#cuju-arena-tournament-cancel) | cancel a tournament | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena tournament remove`](#cuju-arena-tournament-remove) | remove a tournament | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena tournament forfeit`](#cuju-arena-tournament-forfeit) | award a match by forfeit | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena tournament replay`](#cuju-arena-tournament-replay) | replay a kept tournament match | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena tournament preflight`](#cuju-arena-tournament-preflight) | check a team before a tournament | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena tournament export`](#cuju-arena-tournament-export) | export the tournament results | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena tournament legs`](#cuju-arena-tournament-legs) | add legs to the last stage | [Tournaments](evaluate-tournaments.md) |
| [`cuju arena benchmark`](#cuju-arena-benchmark) | run benchmarks on academy tasks | [The academy benchmark](evaluate-benchmark.md) |
| [`cuju arena benchmark tasks`](#cuju-arena-benchmark-tasks) | list the academy tasks | [The academy benchmark](evaluate-benchmark.md) |
| [`cuju arena benchmark run`](#cuju-arena-benchmark-run) | play teams on academy tasks | [The academy benchmark](evaluate-benchmark.md) |
| [`cuju arena check`](#cuju-arena-check) | check a submission under a format | [Submission formats](formats.md#checking-a-folder-from-the-command-line) |
| [`cuju arena config`](#cuju-arena-config) | inspect the settings of the arena | [Arena configuration](configuration.md) |
| [`cuju arena config show`](#cuju-arena-config-show) | show the resolved arena settings | [Arena configuration](configuration.md) |
| [`cuju arena config check`](#cuju-arena-config-check) | check files of arena settings | [Arena configuration](configuration.md) |
| [`cuju arena evaluation`](#cuju-arena-evaluation) | run and inspect evaluations | [Evaluation runs](evaluation.md) |
| [`cuju arena evaluation run`](#cuju-arena-evaluation-run) | run an evaluation | [Evaluation runs](evaluation.md) |
| [`cuju arena evaluation list`](#cuju-arena-evaluation-list) | list evaluations | [Evaluation runs](evaluation.md) |
| [`cuju arena evaluation show`](#cuju-arena-evaluation-show) | show an evaluation | [Evaluation runs](evaluation.md) |
| [`cuju arena evaluation remove`](#cuju-arena-evaluation-remove) | remove an evaluation | [Evaluation runs](evaluation.md) |
| [`cuju arena evaluation check`](#cuju-arena-evaluation-check) | replay and check an evaluation | [Evaluation runs](evaluation.md) |
| [`cuju arena format`](#cuju-arena-format) | manage submission formats | [Submission formats](formats.md#adding-a-format) |
| [`cuju arena format list`](#cuju-arena-format-list) | list the available formats | [Submission formats](formats.md#adding-a-format) |
| [`cuju arena format show`](#cuju-arena-format-show) | show a format and its details | [Submission formats](formats.md#adding-a-format) |
| [`cuju arena format add`](#cuju-arena-format-add) | add a format you trust | [Submission formats](formats.md#adding-a-format) |
| [`cuju arena format export`](#cuju-arena-format-export) | export a format as a zip archive | [Submission formats](formats.md#adding-a-format) |
| [`cuju arena format remove`](#cuju-arena-format-remove) | remove an added format | [Submission formats](formats.md#adding-a-format) |
| [`cuju arena plugin`](#cuju-arena-plugin) | manage arena plugins | [Plugins](plugins.md#the-command) |
| [`cuju arena plugin list`](#cuju-arena-plugin-list) | list the available plugins | [Plugins](plugins.md#the-command) |
| [`cuju arena plugin show`](#cuju-arena-plugin-show) | show a plugin and its details | [Plugins](plugins.md#the-command) |
| [`cuju arena plugin add`](#cuju-arena-plugin-add) | add a plugin you trust | [Plugins](plugins.md#the-command) |
| [`cuju arena plugin remove`](#cuju-arena-plugin-remove) | remove an added plugin | [Plugins](plugins.md#the-command) |
| [`cuju arena rating`](#cuju-arena-rating) | gather and inspect rating evidence | [Ratings](evaluate-ratings.md#rating-runs) |
| [`cuju arena rating run`](#cuju-arena-rating-run) | play matches to gather rating evidence | [Ratings](evaluate-ratings.md#rating-runs) |
| [`cuju arena rating table`](#cuju-arena-rating-table) | show ratings for a scenario | [Ratings](evaluate-ratings.md#rating-runs) |
| [`cuju arena rating list`](#cuju-arena-rating-list) | list the rating runs | [Ratings](evaluate-ratings.md#rating-runs) |
| [`cuju arena rating show`](#cuju-arena-rating-show) | show a rating run | [Ratings](evaluate-ratings.md#rating-runs) |
| [`cuju arena rating check`](#cuju-arena-rating-check) | replay and check a rating run | [Ratings](evaluate-ratings.md#rating-runs) |
| [`cuju arena rating draw`](#cuju-arena-rating-draw) | save a rating match as a recording | [Ratings](evaluate-ratings.md#rating-runs) |

### `cuju arena team`

| Command | `cuju arena team` |
| --- | --- |
| Does | manage teams in the registry |
| Description | Manage teams in the registry. |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena team add`

| Command | `cuju arena team add` |
| --- | --- |
| Does | register a submission |
| Description | Register a submission. Run format list to find submission formats and format show for options. Kaggle checks load the agent. Play a match to exercise its actions. The --full option requires --each. Use a team name without @. |
| Example | `cuju arena team add ./submission --size 5v5 --selection CB,CM` |
| Example | `cuju arena team add ./submissions --each --size 5v5 --format kaggle` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `PATH` |  |  | a submission folder, Python file or archive |
| `--size NAME` | `5v5`, `11v11` |  | the team size, the imported size or 11v11 by default |
| `--format NAME` |  |  | the submission format, Kaggle open by default, see cuju arena format list |
| `--option KEY=VALUE` |  |  | an override such as players=one, see cuju arena format show NAME, may repeat |
| `--preset NAME` |  |  | a preset of format defaults applied before option overrides |
| `--scenario SCENARIO` |  |  | the scenario for the check, the scenario of the team size by default |
| `--full` |  |  | request the full check, its coverage depends on the format |
| `--each` |  |  | register each submission in a directory |
| `--name NAME` |  |  | the name of the team, the folder name by default |
| `--players N` |  |  | the players the submission controls, one by default |
| `--assignment NAME` | `dynamic`, `fixed` |  | dynamic for players nearest the ball or fixed for a selection, dynamic clears it |
| `--selection NAME` |  |  | zero-based indices or roles such as CB,CM, makes the assignment fixed |
| `--goalkeeper NAME` | `agent`, `builtin`, `scenario` |  | agent, builtin or scenario to use the scenario rule |
| `--action-set NAME` | `default`, `v2` |  | default for actions 0 to 18, v2 also offers action 19 to delegate |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena team list`

| Command | `cuju arena team list` |
| --- | --- |
| Does | list the teams in the registry |
| Description | List the teams in the registry. |
| Example | `cuju arena team list --json` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena team show`

| Command | `cuju arena team show` |
| --- | --- |
| Does | show a registered team |
| Description | Show a registered team. The text output includes the folder of a registered submission. After the at sign use a checkpoint identifier, unique prefix, label, step:N or latest. A prefix needs at least four characters. If a legacy team name matches a checkpoint reference, use the team identifier or the full team identifier and checkpoint as shown in the hint. Use the full TEAM@ID if a label and another selector match different checkpoints. |
| Example | `cuju arena team show greens` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `TEAM` |  |  | a team identifier, prefix of at least four characters or exact name ignoring case |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena team set`

| Command | `cuju arena team set` |
| --- | --- |
| Does | change a registered team |
| Description | Change a registered team. |
| Example | `cuju arena team set greens --size 11v11` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `TEAM` |  |  | a team identifier, prefix of at least four characters or exact name ignoring case |
| `--size NAME` | `5v5`, `11v11` |  | the team size, the current size by default |
| `--name NAME` |  |  | the new name of the team, without @ |
| `--players N` |  |  | the players the submission controls, the current count by default |
| `--assignment NAME` | `dynamic`, `fixed` |  | dynamic for players nearest the ball or fixed for a selection, dynamic clears it |
| `--selection NAME` |  |  | zero-based indices or roles such as CB,CM, makes the assignment fixed |
| `--goalkeeper NAME` | `agent`, `builtin`, `scenario` |  | agent, builtin or scenario to use the scenario rule |
| `--action-set NAME` | `default`, `v2` |  | default for actions 0 to 18, v2 also offers action 19 to delegate |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena team export`

| Command | `cuju arena team export` |
| --- | --- |
| Does | export a team as a zip archive |
| Description | Export a team as a zip archive. |
| Example | `cuju arena team export greens -o greens.zip` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `TEAM` |  |  | a team identifier, prefix of at least four characters or exact name ignoring case |
| `-o FILE, --output FILE` |  |  | the zip file, the team name with .zip by default |
| `--all-checkpoints` |  |  | include every checkpoint, the current one by default |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena team remove`

| Command | `cuju arena team remove` |
| --- | --- |
| Does | remove a team from the registry |
| Description | Remove a team from the registry. |
| Example | `cuju arena team remove greens` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `TEAM` |  |  | a team identifier, prefix of at least four characters or exact name ignoring case |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena checkpoint`

| Command | `cuju arena checkpoint` |
| --- | --- |
| Does | manage checkpoints of registered teams |
| Description | Manage checkpoints of registered teams. |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena checkpoint add`

| Command | `cuju arena checkpoint add` |
| --- | --- |
| Does | add a checkpoint to a team |
| Description | Add a checkpoint to a team. The new files become current and the earlier files go to history. Label, step and note are optional. The step is supplied by the user. Use TEAM@ID if a label and latest or step:N match different checkpoints. |
| Example | `cuju arena checkpoint add greens ./team --step 3000 --label baseline` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `TEAM` |  |  | a team identifier, prefix of at least four characters or exact name ignoring case |
| `PATH` |  |  | the folder, archive or entry file of the checkpoint |
| `--label NAME` |  |  | a label of up to 80 characters, usable in TEAM@LABEL |
| `--step N` |  |  | a training step from zero, usable in TEAM@step:N |
| `--note NAME` |  |  | a note of up to 500 characters, displayed in JSON |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena checkpoint list`

| Command | `cuju arena checkpoint list` |
| --- | --- |
| Does | list the checkpoints of a team |
| Description | List the checkpoints of a team. Use TEAM by itself to list all its checkpoints. The table shows each checkpoint identifier, label and training step. |
| Example | `cuju arena checkpoint list greens` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `TEAM` |  |  | a team identifier, prefix of at least four characters or exact name ignoring case |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena play`

| Command | `cuju arena play` |
| --- | --- |
| Does | play and record a match |
| Description | Play and record a match. Use TEAM, TEAM@CHECKPOINT, builtin, builtin:D or random for either side. TEAM can be an identifier, unique prefix or exact name ignoring case. After the at sign use a checkpoint identifier, unique prefix, label, step:N or latest. A prefix needs at least four characters. If a legacy team name matches a checkpoint reference, use the team identifier or the full team identifier and checkpoint as shown in the hint. Use the full TEAM@ID if a label and another selector match different checkpoints. Academy scenarios accept either team size when the requested players exist. Use random for a side to draw a team, or for the scenario to draw a scenario that fits. Shell completion lists shipped scenario names. In JSON, left_checkpoint and right_checkpoint give the identifier, label and step of each registered side. A label names a version. A step records its training progress. |
| Example | `cuju arena play --scenario academy_empty_goal_close --right builtin:0.3` |
| Example | `cuju arena play --left greens@step:3000 --left-difficulty 0.3` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--left TEAM` |  |  | the left team, builtin by default |
| `--left-difficulty DIFFICULTY` |  |  | the left built-in players, from 0 to 1, the scenario value by default |
| `--right TEAM` |  |  | the right team, play.right by default, shipped as builtin |
| `--right-difficulty DIFFICULTY` |  |  | the right built-in players, from 0 to 1, the scenario value by default |
| `--scenario SCENARIO` |  |  | the scenario or random, play.scenario by default, shipped as 11_vs_11_kaggle |
| `--pool TEAM` |  |  | comma-separated teams for a draw, all registered teams by default |
| `--same-team` |  |  | allow a random draw to pick the same team for both sides |
| `--seed N` |  |  | the seed, play.seed by default, drawn when unset |
| `--device DEVICE` |  | `cpu` | cpu, cuda or cuda:N, cpu by default |
| `--format NAME` |  |  | the submission format, Kaggle open by default, see cuju arena format list |
| `--option KEY=VALUE` |  |  | an override such as players=one, see cuju arena format show NAME, may repeat |
| `--preset NAME` |  |  | a preset of format defaults applied before option overrides |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena recording`

| Command | `cuju arena recording` |
| --- | --- |
| Does | manage recordings in the library |
| Description | Manage recordings in the library. |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena recording list`

| Command | `cuju arena recording list` |
| --- | --- |
| Does | list the recordings in the library |
| Description | List the recordings in the library. Use --json to find a group identifier under group.id. A group contains recordings kept by the same run. |
| Example | `cuju arena recording list --json` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--group ID` |  |  | list the recordings of this group |
| `--date NAME` |  |  | list recordings from today, yesterday or a day as YYYY-MM-DD |
| `--trash` |  |  | list the recordings in the trash |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena recording show`

| Command | `cuju arena recording show` |
| --- | --- |
| Does | show a recording in the library |
| Description | Show a recording in the library. Provenance gives the checkpoint digest in text and JSON when the recording kept one. Run cuju arena sample add sample-5v5 to try the example. |
| Example | `cuju arena recording show sample-5v5` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `REC` |  |  | an identifier, unique prefix of at least four characters or last for the newest |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena recording add`

| Command | `cuju arena recording add` |
| --- | --- |
| Does | import recordings and bundles |
| Description | Import recordings and bundles. Imports also accept GRF dumps, Kaggle episodes and action logs. Use --set import.fidelity=exact to require exact replay. |
| Example | `cuju arena recording add ./match.cjz` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `PATH [PATH ...]` |  |  | the files to import |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena recording export`

| Command | `cuju arena recording export` |
| --- | --- |
| Does | export a recording and its attachments |
| Description | Export a recording and its attachments. Run cuju arena sample add sample-5v5 to try the example. |
| Example | `cuju arena recording export sample-5v5 -o match.cjz` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `REC` |  |  | an identifier, unique prefix of at least four characters or last for the newest |
| `-o FILE, --output FILE` |  |  | the .cjz bundle, the recording identifier plus .cjz by default |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena recording remove`

| Command | `cuju arena recording remove` |
| --- | --- |
| Does | move a recording group to the trash |
| Description | Move a recording group to the trash. Find a group identifier under group.id in cuju arena recording list --json. |
| Example | `cuju arena recording remove --group regular-season` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--group ID` |  |  | the recording group |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena recording restore`

| Command | `cuju arena recording restore` |
| --- | --- |
| Does | restore a recording group from the trash |
| Description | Restore a recording group from the trash. Find a group identifier under group.id in cuju arena recording list --trash --json. |
| Example | `cuju arena recording restore --group regular-season` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--group ID` |  |  | the recording group |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena sample`

| Command | `cuju arena sample` |
| --- | --- |
| Does | manage the shipped samples |
| Description | Manage the shipped samples. |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena sample list`

| Command | `cuju arena sample list` |
| --- | --- |
| Does | list the samples and their states |
| Description | List the samples and their states. Samples ready to add show as not added in text and missing in JSON. |
| Example | `cuju arena sample list` |
| Example | `cuju arena sample list --json` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena sample add`

| Command | `cuju arena sample add` |
| --- | --- |
| Does | add or restore shipped samples |
| Description | Add or restore shipped samples. Choose sample-5v5, sample-11v11 or starter-team. See cuju arena sample list. |
| Example | `cuju arena sample add sample-5v5` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `[NAME]` | `sample-11v11`, `sample-5v5`, `starter-team` |  | the sample to add, every sample by default |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena search`

| Command | `cuju arena search` |
| --- | --- |
| Does | find moments in the recording library |
| Description | Find moments in the recording library. The score filter uses the score at the moment. The final filter uses the final score. The after and before filters require finite match clock times. The team, match and tournament filters accept exact identifiers and unique prefixes of at least four characters after the type prefix. Team also accepts an exact registered name ignoring case. Match and tournament accept last for the newest. Quote comparison filters in the shell. For a negated term, quote the whole query or put -- after the options and before the query. For a filter value with spaces, put double quotes around the value and single quotes around the whole filter. |
| Example | `cuju arena search goal --limit 10` |
| Example | `cuju arena search 'card -yellow'` |
| Example | `cuju arena search --json -- pass -team:left` |
| Example | `cuju arena search 'team:"Harbour Rovers"'` |
| Filter example | `kind:goal` |
| Filter example | `team:Harbour` |
| Filter example | `player:9` |
| Filter example | `scenario:5_vs_5` |
| Filter example | `source:import` |
| Filter example | `tournament:cup-1` |
| Filter example | `match:sample-5v5` |
| Filter example | `date:today` |
| Filter example | `since:2026-11-01` |
| Filter example | `until:2026-11-20` |
| Filter example | `after:60` |
| Filter example | `before:75` |
| Filter example | `score:0-0` |
| Filter example | `final:2-1` |
| Filter example | `outcome:saved` |
| Filter example | `limit:10` |
| Filter example | `value-drop:value` |
| Filter example | `surprise:0.05` |
| Filter example | `'entropy:>1.5'` |
| Filter example | `'reward:>0'` |
| Filter example | `'ann:value<0.5'` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `QUERY [QUERY ...]` |  |  | the terms and filters of the search |
| `--limit N` |  |  | the most results, from 1 to 1000, 50 by default |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena captions`

| Command | `cuju arena captions` |
| --- | --- |
| Does | write captions of a recording |
| Description | Write captions of a recording. Run cuju arena sample add sample-5v5 to try the example. |
| Example | `cuju arena captions sample-5v5` |
| Example | `cuju arena captions sample-5v5 -o match.vtt` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `REC` |  |  | an identifier, unique prefix of at least four characters or last for the newest, or a recording file |
| `-o FILE, --output FILE` |  |  | the WebVTT file, standard output by default |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament`

| Command | `cuju arena tournament` |
| --- | --- |
| Does | manage tournaments |
| Description | Manage tournaments. |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament new`

| Command | `cuju arena tournament new` |
| --- | --- |
| Does | create a tournament |
| Description | Create a tournament. Use a TOML or JSON file or name two or more entrants with --team or --person. Use tournament presets to find the shipped draws. Use tournament run with the returned identifier to play it. |
| Example | `cuju arena tournament new --team builtin:0.3 --team builtin --length 50` |
| Example | `cuju arena tournament new --person Ana --team builtin --scenario 5_vs_5` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `[FILE]` |  |  | the tournament configuration |
| `--tournament-preset NAME` |  |  | the tournament preset, quick_cup by default |
| `--name NAME` |  |  | the tournament name |
| `--seed N` |  |  | the seed, drawn when absent |
| `--team TEAM` |  |  | TEAM, TEAM@CHECKPOINT, builtin, builtin:DIFFICULTY or random, may repeat |
| `--person NAME` |  |  | a person by name, may repeat |
| `--as NAME` |  |  | a display name for the immediately preceding entrant, may repeat |
| `--difficulty DIFFICULTY` |  |  | the built-in player difficulty from 0 to 1, or scenario before entrants and standard after an entrant, may repeat |
| `--seeding NAME` | `random`, `manual`, `rating` |  | the rule for the first draw, random by default |
| `--evidence NAME` | `refuse`, `collect`, `proceed` |  | refuse, collect or proceed when rating evidence is lacking, refuse by default |
| `--scenario SCENARIO` |  |  | the scenario of every stage, use academy_empty_goal_close for a short match |
| `--length N` |  |  | the GRF duration N, from 50 to 5000, plays N plus 1 steps at the time limit |
| `--concurrency N` |  |  | the matches played at once, from 1 to 8 |
| `--rounds N` |  |  | the rounds of each Swiss or ladder stage |
| `--format NAME` |  |  | the submission format, Kaggle open by default, see cuju arena format list |
| `--option KEY=VALUE` |  |  | an override such as players=one, see cuju arena format show NAME, may repeat |
| `--preset NAME` |  |  | a preset of format defaults applied before option overrides |
| `--preflight` |  |  | load and check submissions, during a preview when requested |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament check`

| Command | `cuju arena tournament check` |
| --- | --- |
| Does | preview and check a tournament |
| Description | Preview and check a tournament. Use a TOML or JSON file or name two or more entrants with --team or --person. Use tournament presets to find the shipped draws. The preview saves no tournament. Use tournament new to save it. |
| Example | `cuju arena tournament check --team builtin:0.3 --team builtin --length 50` |
| Example | `cuju arena tournament check --person Ana --team builtin --scenario 5_vs_5` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `[FILE]` |  |  | the tournament configuration |
| `--tournament-preset NAME` |  |  | the tournament preset, quick_cup by default |
| `--name NAME` |  |  | the tournament name |
| `--seed N` |  |  | the seed, drawn when absent |
| `--team TEAM` |  |  | TEAM, TEAM@CHECKPOINT, builtin, builtin:DIFFICULTY or random, may repeat |
| `--person NAME` |  |  | a person by name, may repeat |
| `--as NAME` |  |  | a display name for the immediately preceding entrant, may repeat |
| `--difficulty DIFFICULTY` |  |  | the built-in player difficulty from 0 to 1, or scenario before entrants and standard after an entrant, may repeat |
| `--seeding NAME` | `random`, `manual`, `rating` |  | the rule for the first draw, random by default |
| `--evidence NAME` | `refuse`, `collect`, `proceed` |  | refuse, collect or proceed when rating evidence is lacking, refuse by default |
| `--scenario SCENARIO` |  |  | the scenario of every stage, use academy_empty_goal_close for a short match |
| `--length N` |  |  | the GRF duration N, from 50 to 5000, plays N plus 1 steps at the time limit |
| `--concurrency N` |  |  | the matches played at once, from 1 to 8 |
| `--rounds N` |  |  | the rounds of each Swiss or ladder stage |
| `--format NAME` |  |  | the submission format, Kaggle open by default, see cuju arena format list |
| `--option KEY=VALUE` |  |  | an override such as players=one, see cuju arena format show NAME, may repeat |
| `--preset NAME` |  |  | a preset of format defaults applied before option overrides |
| `--preflight` |  |  | load and check submissions, during a preview when requested |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament list`

| Command | `cuju arena tournament list` |
| --- | --- |
| Does | list the tournaments |
| Description | List the tournaments. |
| Example | `cuju arena tournament list --json` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament presets`

| Command | `cuju arena tournament presets` |
| --- | --- |
| Does | list the tournament presets |
| Description | List the tournament presets. |
| Example | `cuju arena tournament presets --json` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament ratings`

| Command | `cuju arena tournament ratings` |
| --- | --- |
| Does | list the tournament ratings |
| Description | List the tournament ratings. Lists each model using its newest scenario result. Different checkpoints of a team have separate rows. In JSON, models holds those rows and ratings gives the newest result by team. Use rating table for a single scenario. |
| Example | `cuju arena tournament ratings --json` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament show`

| Command | `cuju arena tournament show` |
| --- | --- |
| Does | show a tournament |
| Description | Show a tournament. Shows the draw, match keys and progress. |
| Example | `cuju arena tournament show last` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | an identifier, unique prefix of at least four characters or last for the newest |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament run`

| Command | `cuju arena tournament run` |
| --- | --- |
| Does | run a tournament |
| Description | Run a tournament. Plays the saved draw in this process and retries stopped matches. |
| Example | `cuju arena tournament run last` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | an identifier, unique prefix of at least four characters or last for the newest |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament resume`

| Command | `cuju arena tournament resume` |
| --- | --- |
| Does | resume a tournament |
| Description | Resume a tournament. Queues a stopped draw or retries failed matches in a running draw. |
| Example | `cuju arena tournament resume last` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | an identifier, unique prefix of at least four characters or last for the newest |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament cancel`

| Command | `cuju arena tournament cancel` |
| --- | --- |
| Does | cancel a tournament |
| Description | Cancel a tournament. Stops a running or stopped draw. Finished draws can be removed. |
| Example | `cuju arena tournament cancel last` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | an identifier, unique prefix of at least four characters or last for the newest |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament remove`

| Command | `cuju arena tournament remove` |
| --- | --- |
| Does | remove a tournament |
| Description | Remove a tournament. Removes a finished, stopped or cancelled draw, keeping its recordings. |
| Example | `cuju arena tournament remove last` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | an identifier, unique prefix of at least four characters or last for the newest |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament forfeit`

| Command | `cuju arena tournament forfeit` |
| --- | --- |
| Does | award a match by forfeit |
| Description | Award a match by forfeit. |
| Example | `cuju arena tournament forfeit cup-1 --match group/1 --winner home` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | an identifier, unique prefix of at least four characters or last for the newest |
| `--match ID` |  |  | the key from tournament show |
| `--winner NAME` | `home`, `away` |  | home or away, as shown in the fixture |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament replay`

| Command | `cuju arena tournament replay` |
| --- | --- |
| Does | replay a kept tournament match |
| Description | Replay a kept tournament match. |
| Example | `cuju arena tournament replay cup-1 --match group/1` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | an identifier, unique prefix of at least four characters or last for the newest |
| `--match ID` |  |  | the key from tournament show |
| `--device DEVICE` |  | `cpu` | cpu, cuda or cuda:N, cpu by default |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament preflight`

| Command | `cuju arena tournament preflight` |
| --- | --- |
| Does | check a team before a tournament |
| Description | Check a team before a tournament. The format determines whether its check plays matches. Kaggle checks load the agent. After the at sign use a checkpoint identifier, unique prefix, label, step:N or latest. A prefix needs at least four characters. If a legacy team name matches a checkpoint reference, use the team identifier or the full team identifier and checkpoint as shown in the hint. Use the full TEAM@ID if a label and another selector match different checkpoints. |
| Example | `cuju arena tournament preflight greens --format strict` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `TEAM` |  |  | a team identifier, prefix of at least four characters or exact name ignoring case |
| `--format NAME` |  |  | the submission format, the configured tournament format by default, see cuju arena format list |
| `--option KEY=VALUE` |  |  | an override such as players=one, see cuju arena format show NAME, may repeat |
| `--preset NAME` |  |  | a preset of format defaults applied before option overrides |
| `--scenario SCENARIO` |  |  | the scenario of the check |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament export`

| Command | `cuju arena tournament export` |
| --- | --- |
| Does | export the tournament results |
| Description | Export the tournament results. |
| Example | `cuju arena tournament export cup-1 -o ./results` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | an identifier, unique prefix of at least four characters or last for the newest |
| `-o DIR, --output DIR` |  |  | the directory for CSV and JSON results, the current directory by default |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena tournament legs`

| Command | `cuju arena tournament legs` |
| --- | --- |
| Does | add legs to the last stage |
| Description | Add legs to the last stage. The last stage must be a league or groups. A finished tournament runs again for the added matches. |
| Example | `cuju arena tournament legs cup-1 --legs 2` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | an identifier, unique prefix of at least four characters or last for the newest |
| `--legs N` |  |  | the legs to add |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena benchmark`

| Command | `cuju arena benchmark` |
| --- | --- |
| Does | run benchmarks on academy tasks |
| Description | Run benchmarks on academy tasks. |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena benchmark tasks`

| Command | `cuju arena benchmark tasks` |
| --- | --- |
| Does | list the academy tasks |
| Description | List the academy tasks. Each task has five fixed seeds. The same seeds serve every model. |
| Example | `cuju arena benchmark tasks --json` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena benchmark run`

| Command | `cuju arena benchmark run` |
| --- | --- |
| Does | play teams on academy tasks |
| Description | Play teams on academy tasks. Use benchmark tasks to find the eleven tasks. Each task uses five fixed seeds. Use --set benchmark.format=NAME to choose a format. The score is the mean share of attempts with a goal across the tasks. Use --json to see each cell, its attempts and any failure. After the at sign use a checkpoint identifier, unique prefix, label, step:N or latest. A prefix needs at least four characters. If a legacy team name matches a checkpoint reference, use the team identifier or the full team identifier and checkpoint as shown in the hint. Use the full TEAM@ID if a label and another selector match different checkpoints. |
| Example | `cuju arena benchmark run builtin:0.3 --task empty_goal_close` |
| Example | `cuju arena benchmark run greens@baseline builtin --task corner --json` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `[TEAM ...]` |  |  | TEAM, TEAM@CHECKPOINT, builtin, builtin:DIFFICULTY or random, the policy of the settings if omitted |
| `--task NAME` |  |  | an academy task, all eleven by default, may repeat |
| `--device DEVICE` |  |  | cpu, cuda or cuda:N, the device of the settings by default |
| `--keep-recordings` |  |  | keep attempts as recordings |
| `--workers N` |  |  | model and task pairs played at once, from 1 to 8, automatic by default |
| `--csv` |  |  | print the table as CSV |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena check`

| Command | `cuju arena check` |
| --- | --- |
| Does | check a submission under a format |
| Description | Check a submission under a format. Kaggle checks load the agent. Its match actions are checked in play. Use format list to find formats and format show to see options. |
| Example | `cuju arena check ./submission --format kaggle --json` |
| Example | `cuju arena check ./team --format strict --full -o verdict.json` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `PATH` |  |  | the folder of the submission |
| `--format NAME` |  |  | the submission format |
| `--option KEY=VALUE` |  |  | an override such as players=one, see cuju arena format show NAME, may repeat |
| `--preset NAME` |  |  | a preset of format defaults applied before option overrides |
| `--scenario SCENARIO` |  |  | the scenario for the check, the scenario of the team size by default |
| `--full` |  |  | request the full check, its coverage depends on the format |
| `--max-steps N` |  |  | the most steps if the format plays a check match |
| `-o FILE, --output FILE` |  |  | the JSON verdict file, standard output by default |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena config`

| Command | `cuju arena config` |
| --- | --- |
| Does | inspect the settings of the arena |
| Description | Inspect the settings of the arena. |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena config show`

| Command | `cuju arena config show` |
| --- | --- |
| Does | show the resolved arena settings |
| Description | Show the resolved arena settings. |
| Example | `cuju arena config show --json` |
| Example | `cuju arena config show --config arena.toml --set play.seed=7` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena config check`

| Command | `cuju arena config check` |
| --- | --- |
| Does | check files of arena settings |
| Description | Check files of arena settings. |
| Example | `cuju arena config check ./arena.toml` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `[FILE ...]` |  |  | a TOML or JSON file of settings |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena evaluation`

| Command | `cuju arena evaluation` |
| --- | --- |
| Does | run and inspect evaluations |
| Description | Run and inspect evaluations. |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena evaluation run`

| Command | `cuju arena evaluation run` |
| --- | --- |
| Does | run an evaluation |
| Description | Run an evaluation. The default plays 100 episodes per opponent on 11_vs_11_kaggle against builtin:0.05, builtin:0.6 and builtin:1. The seed repeats the match setup. The recording rule keeps losses by default. After the at sign use a checkpoint identifier, unique prefix, label, step:N or latest. A prefix needs at least four characters. If a legacy team name matches a checkpoint reference, use the team identifier or the full team identifier and checkpoint as shown in the hint. Use the full TEAM@ID if a label and another selector match different checkpoints. Use --episodes 1 for one episode per opponent, as with --set evaluate.episodes=1. |
| Example | `cuju arena evaluation run builtin` |
| Example | `cuju arena evaluation run builtin --episodes 1 --series baseline` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `[TEAM]` |  |  | TEAM, TEAM@CHECKPOINT, builtin, builtin:DIFFICULTY or random, the policy of the settings by default |
| `--episodes N` |  |  | the episodes per opponent, evaluate.episodes by default |
| `--series NAME` |  |  | the series name as text, or none to unset it, evaluate.run by default |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena evaluation list`

| Command | `cuju arena evaluation list` |
| --- | --- |
| Does | list evaluations |
| Description | List evaluations. |
| Example | `cuju arena evaluation list --series baseline` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--series NAME` |  |  | the series name set by evaluation run or evaluate.run |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena evaluation show`

| Command | `cuju arena evaluation show` |
| --- | --- |
| Does | show an evaluation |
| Description | Show an evaluation. Shows the summary and gives the individual matches in JSON. |
| Example | `cuju arena evaluation show last` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | an identifier, unique prefix of at least four characters or last for the newest |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena evaluation remove`

| Command | `cuju arena evaluation remove` |
| --- | --- |
| Does | remove an evaluation |
| Description | Remove an evaluation. |
| Example | `cuju arena evaluation remove last` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | an identifier, unique prefix of at least four characters or last for the newest |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena evaluation check`

| Command | `cuju arena evaluation check` |
| --- | --- |
| Does | replay and check an evaluation |
| Description | Replay and check an evaluation. Replays kept actions from the saved setup and seeds and compares the results. |
| Example | `cuju arena evaluation check last` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | an identifier, unique prefix of at least four characters or last for the newest |
| `--device DEVICE` |  | `cpu` | cpu, cuda or cuda:N, cpu by default |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena format`

| Command | `cuju arena format` |
| --- | --- |
| Does | manage submission formats |
| Description | Manage submission formats. |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena format list`

| Command | `cuju arena format list` |
| --- | --- |
| Does | list the available formats |
| Description | List the available formats. |
| Example | `cuju arena format list --json` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena format show`

| Command | `cuju arena format show` |
| --- | --- |
| Does | show a format and its details |
| Description | Show a format and its details. |
| Example | `cuju arena format show kaggle` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | the identifier from format list |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena format add`

| Command | `cuju arena format add` |
| --- | --- |
| Does | add a format you trust |
| Description | Add a format you trust. A format can run code from its folder. Add a format from a source you trust, then use format show and check with its identifier. |
| Example | `cuju arena format add ./docs/formats/bot` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `PATH` |  |  | a folder or zip archive containing format.toml |
| `--id ID` |  |  | the identifier to register, the manifest identifier by default |
| `--replace` |  |  | replace the format of the same identifier |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena format export`

| Command | `cuju arena format export` |
| --- | --- |
| Does | export a format as a zip archive |
| Description | Export a format as a zip archive. |
| Example | `cuju arena format export kaggle -o kaggle.zip` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | the format to export |
| `-o FILE, --output FILE` |  |  | the zip file, the format identifier with .zip by default |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena format remove`

| Command | `cuju arena format remove` |
| --- | --- |
| Does | remove an added format |
| Description | Remove an added format. |
| Example | `cuju arena format remove example` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | an added identifier from format list |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena plugin`

| Command | `cuju arena plugin` |
| --- | --- |
| Does | manage arena plugins |
| Description | Manage arena plugins. |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena plugin list`

| Command | `cuju arena plugin list` |
| --- | --- |
| Does | list the available plugins |
| Description | List the available plugins. |
| Example | `cuju arena plugin list --json` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena plugin show`

| Command | `cuju arena plugin show` |
| --- | --- |
| Does | show a plugin and its details |
| Description | Show a plugin and its details. |
| Example | `cuju arena plugin show builtin` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | the identifier from plugin list |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena plugin add`

| Command | `cuju arena plugin add` |
| --- | --- |
| Does | add a plugin you trust |
| Description | Add a plugin you trust. A plugin runs code when loaded. Its manifest names its identifier. Use plugin show to find the qualified names of its providers. |
| Example | `cuju arena plugin add ./docs/plugins/goal-rating` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `PATH` |  |  | a folder containing plugin.toml |
| `--replace` |  |  | replace the plugin of the same identifier |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena plugin remove`

| Command | `cuju arena plugin remove` |
| --- | --- |
| Does | remove an added plugin |
| Description | Remove an added plugin. |
| Example | `cuju arena plugin remove example` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | an added identifier from plugin list |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena rating`

| Command | `cuju arena rating` |
| --- | --- |
| Does | gather and inspect rating evidence |
| Description | Gather and inspect rating evidence. |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena rating run`

| Command | `cuju arena rating run` |
| --- | --- |
| Does | play matches to gather rating evidence |
| Description | Play matches to gather rating evidence. Each model first plays the built-in anchor at difficulty 0.6 by default. Use --set ratings.run.most=N to bound the run. After the at sign use a checkpoint identifier, unique prefix, label, step:N or latest. A prefix needs at least four characters. If a legacy team name matches a checkpoint reference, use the team identifier or the full team identifier and checkpoint as shown in the hint. Use the full TEAM@ID if a label and another selector match different checkpoints. |
| Example | `cuju arena rating run builtin:0.3 --scenario 11_vs_11_kaggle` |
| Example | `cuju arena rating run greens@baseline greens@latest --scenario 5_vs_5` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `TEAM [TEAM ...]` |  |  | TEAM, TEAM@CHECKPOINT, builtin, builtin:DIFFICULTY or random |
| `--scenario SCENARIO` |  |  | the scenario to rate on |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena rating table`

| Command | `cuju arena rating table` |
| --- | --- |
| Does | show ratings for a scenario |
| Description | Show ratings for a scenario. A model row shows the checkpoint identifier, label and training step when known. |
| Example | `cuju arena rating table --json` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--scenario SCENARIO` |  |  | the scenario of the ratings |
| `--method NAME` |  |  | points, bradley_terry or a plugin method, points by default, requires --scenario |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena rating list`

| Command | `cuju arena rating list` |
| --- | --- |
| Does | list the rating runs |
| Description | List the rating runs. |
| Example | `cuju arena rating list --json` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena rating show`

| Command | `cuju arena rating show` |
| --- | --- |
| Does | show a rating run |
| Description | Show a rating run. Use --json to see matches and their zero-based numbers. |
| Example | `cuju arena rating show rating-1` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | the identifier from rating list |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena rating check`

| Command | `cuju arena rating check` |
| --- | --- |
| Does | replay and check a rating run |
| Description | Replay and check a rating run. Replays the saved actions and compares match results. |
| Example | `cuju arena rating check rating-1` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | the identifier from rating list |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

### `cuju arena rating draw`

| Command | `cuju arena rating draw` |
| --- | --- |
| Does | save a rating match as a recording |
| Description | Save a rating match as a recording. |
| Example | `cuju arena rating draw rating-1 0` |

| Argument | Values | Default | Help |
| --- | --- | --- | --- |
| `ID` |  |  | the identifier from rating list |
| `N` |  |  | the zero-based match number from rating show --json |
| `--json` |  |  | print the answer as JSON |
| `-q, --quiet` |  |  | print only errors on standard error |
| `-v, --verbose` |  |  | print diagnostic lines and tracebacks |
| `--home DIR` |  |  | the arena home, from CUJU_ARENA_HOME or ~/.cuju/arena |
| `--config FILE` |  |  | a TOML or JSON settings file, may repeat |
| `--set KEY=VALUE` |  |  | a final setting such as play.seed=7, may repeat |

<!-- The part above is written by ci/make_reference.py. The text below is written by hand. -->
