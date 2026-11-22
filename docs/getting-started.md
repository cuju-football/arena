# Getting started with the arena

Install the arena as described in the [project README](../README.md), then run `cuju arena`
to list its commands. The arena uses `~/.cuju/arena` as its default home. Use `--home DIR`
to practice in a separate folder.

On the first use of the default home, the arena moves its data from the earlier renderer
home and reports the move. The first caller that creates the new home carries out the
move. The renderer's settings stay in its home, and a backup of the original
`render.toml` stays beside them.

## Samples

The sample matches and starter team give you a library to explore before you register your
own submission. Use `sample list` to see the shipped examples and their states. A sample
marked `not added` is ready to add with `cuju arena sample add NAME`. Leave the name out to
add every sample.

A sample ready to add has the state `missing` in JSON and reads as not added in text.

```sh
cuju arena sample list
cuju arena sample add sample-5v5
cuju arena recording list
cuju arena recording show sample-5v5
```

The show command reads the details of one recording. Use `cuju render open sample-5v5`
to watch the match in the renderer. Run `cuju arena search goal` to find its goals.

## A first submission

Use `cuju arena sample add starter-team` to register the team shipped with the arena.
Read its identifier with `cuju arena team list`. The starter team plays eleven a side,
so choose `11_vs_11_kaggle` when you play it.

Check the folder under the [submission format](formats.md) you will use to play. Use the folder
path in `check`. Run `cuju arena team show ID` to find the folder of a registered submission.
The verdict gives the reason when a format refuses an entry. Register the folder, choose its
size and read the identifier from `team list`. A team that plays five a side uses
`--size 5v5`.

```sh
cuju arena check ./submission --format kaggle
cuju arena team add ./submission --size 5v5 --format kaggle
cuju arena team list
cuju arena play --left TEAM --right builtin:0.3 --scenario academy_empty_goal_close --seed 7
```

Replace `TEAM` with the identifier of the registered team. The academy scenario keeps
the first match short. The last command prints the path of the new recording. Use the
same seed to repeat the setup, or leave it out to draw another. To measure a policy
against several opponents, continue with
[Evaluation runs](evaluation.md). To compare teams on fixed tasks, use the
[academy benchmark](evaluate-benchmark.md).

## A first tournament

Use two built-in entrants to try a tournament before adding more teams. The check
previews the schedule without creating a tournament. The new command saves a draw,
prints its identifier and gives the command for playing it on standard error.

```sh
cuju arena tournament check --team builtin:0.3 --team builtin:0.7 \
    --scenario academy_empty_goal_close --length 50 --seed 7
cuju arena tournament new --team builtin:0.3 --team builtin:0.7 \
    --scenario academy_empty_goal_close --length 50 --seed 7
cuju arena tournament run ID
cuju arena tournament show ID
```

Replace `ID` with the identifier printed by the new command. Use the run command to play
in the terminal. A render server serving the same arena home plays new tournaments by itself.
Use `cuju arena tournament list` to find the identifier again. A match with `--length 50`
plays 51 steps if it reaches the time limit, under the
[GRF duration rule](evaluate-tournaments.md#match-length). See
[Tournaments](evaluate-tournaments.md) for stages, presets and seeding.
