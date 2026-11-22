# Annotations

Annotations put what a model thinks beside what it does. A training loop or an evaluation attaches
its value estimates, the probabilities of its actions, the action it chose, and maps and vectors
over the pitch to the steps that a recorder or a live source captures, and the page shows them
with the match. This guide is written for people who study models, and it starts from the shape a
training loop already holds, tensors indexed by environment and agent.

## A training loop

`cuju.arena.annotate` takes the tensors of a whole batch in one call. Every recorder and live
source attached to the batch takes the row of its own environment, and each agent goes to the
player that it controls at that step.

```python
import cuju
import cuju.arena

batch = cuju.BatchEnvironment('5_vs_5_l2_r1', environment_count=8, device='cpu', seed=1)
recorder = cuju.arena.Recorder(batch, environment=0, overrides=['annotations.probabilities=all'])
observation = batch.reset()
for step in range(400):
    logits, value = policy(observation)
    probabilities = logits.softmax(-1)
    actions = probabilities.argmax(-1).int()
    cuju.arena.annotate(
        batch, values={'value': value}, probabilities=probabilities, actions=actions
    )
    observation, reward, done, information = batch.step(actions)
    cuju.arena.annotate(batch, values={'reward': reward}, played=True)
recorder.save()
```

Here `policy` stands for the model, which returns logits of the shape `[E, A, actions]` and
values of the shape `[E, A]`, where `E` counts the environments of the batch and `A` its agents.
The first call describes the step that the policy looked at, the frame captured last, and the
second the step that the batch has just played, so its reward reaches the episode that the step
ended. The recorder keeps every action's probability, as its override asks, and writes the
recording at the end. The batch trains on all eight environments while the recorder keeps the
first.

The call costs nothing that a training loop would notice when nothing is attached. It looks at the
observers of the batch, finds no recorder and no live source, and returns 0 before it reads any
tensor, so the line stays in training code for good and starts to record the moment a recorder or
`cuju.arena.watch` attaches. With something attached, the rows of the watched environments reach
the host once for each tensor, through one copy taken on the tensor's own device. The return
value counts the recorders and live sources that took the annotation.

## Agents and players

An agent of cuju controls one player at a time, and the engine moves it from player to player as
play goes on, as GRF does. A value of agent 2 therefore belongs to a different player at different
steps. `annotate` finds the player of each agent in the frame that takes the annotation, from the
state of that frame, on both teams and under both values of GRF's reverse team processing, and
the player that the raw observation of that agent names as active is the one that takes the
numbers. A player that no agent controls at that step takes nothing, and an agent that controls no
player at that step, as happens for a moment at some restarts, gives nothing. A goalkeeper who is
sent off hands the role of goalkeeper to a team mate, and the agents follow their players as
before.

The agents count as the batch counts them, those of the left team first, so column `k` of a
tensor of the shape `[E, A]` is agent `k` of the batch, whatever order the recording lists the
teams in. `agent=k` takes the numbers of one agent without the axis of agents, such as `values` of
the shape `[E]`.

## What an annotation holds

`values` holds named numbers, each of the shape `[E, A]` for the agents or `[E]` for the whole
match. A step and player hold at most 16 names of at most 40 characters, or fewer as
`[annotations] max_values` sets, and each number is kept to six significant digits. Several calls
for one step and player join their numbers, and a later call replaces a number of the same name.

`probabilities` holds the probability of each action, of the shape `[E, A, actions]`, and
`logits` takes the logits in its place, which become probabilities through the softmax. By default
an annotation keeps the three most likely actions with their probabilities, the most likely first,
to four significant digits. `[annotations] probabilities` sets another count, or `"all"` keeps
every action, which lets a later look compute the entropy of the policy or the probability of an
action it did not choose. `actions` holds the chosen action of each agent, of the shape `[E, A]`.
`text` holds a short text of the whole match, the same for every environment or a mapping of
environment to text, cut at 200 characters.

The actions and the probabilities count the actions as the batch does, by their numbers in its
action set. A recorder and a live source write the name of that set into the provenance of the
recording as `action_set`, which is `default`, `v2` or `full`, and the page names each action from
it. The three sets of GRF share their numbers. `v2` adds the built-in AI as action 19 to the 19
actions of `default`, and `full` adds thirteen more after it, the keeper rush, pressure, team
pressure and switch and the releases of the passes, the shot and those four. An action therefore
reads the same in every set that holds it, and a recording that names no set, as one of an earlier
version, shows its actions by the names of the full set. An action outside the set of its
recording shows by its number.

## Overlays

Three kinds of overlay draw a policy over the pitch, and a recording of version 2 keeps them in its
section of overlays, which the [recording format](recording-format.md) describes.

- **Grids.** `grids` holds named grids of the shape `[E, A, rows, columns]` for the agents or
  `[E, rows, columns]` for the whole match, such as a map of values over the pitch. A grid has at
  most 72 rows of 96 columns, the size of GRF's minimap, or fewer cells as
  `[annotations] grid_cells` sets. Its first row lies at the top of the pitch and its first column
  at the left of the view. `extent` gives the rectangle that the grids cover, x and y of the first
  corner and of the last, and by default a grid covers the whole pitch, x from -1 to 1 and y from
  -0.42 to 0.42. A grid is kept as bytes, each value one of 256 steps between its least and its
  greatest value, and `[annotations] grid_every` keeps a grid only at every few steps.
- **Targets.** `targets` holds named values over the players, of the shape `[E, A, players]`, such
  as the chance of a pass to each team mate. The players of the agent's own team come first, each
  team in the order of its indices, as the agent's own observation lists them.
- **Arrows.** `arrows` holds named values over the actions, of the shape `[E, A, actions]`, such as
  a whole distribution, which the page draws as arrows from the player.

A step and player hold at most four grids and four vectors, targets and arrows together, and an
overlay of the same name replaces the earlier one. The overlays of one recording take at most
32 MiB before compression. Targets and arrows are kept as 16-bit floats.

## The step that takes an annotation

By default an annotation goes to the step captured last. A call before `batch.step` therefore
describes the step that the policy looked at, and a call after it the step that the batch has just
played. `step` names a step of the current episode by its number, as the frame counts it.

With automatic resets, the step that ends an episode already shows the kick off of the next one.
`played=True` says that the annotation describes the step just played, such as its reward, so after
an ending step it goes to the last frame of the ended episode and joins the summary that a
[filter](recording.md) reads, where an annotation without it would go to the next kick off. After
any other step the two agree. A recorder that captures by itself keeps an ended episode until the
next call of the batch, so this annotation still reaches the file.

## Recorders

A recorder keeps the annotations and overlays of the recording it is capturing and writes them with
it. Version 2 holds them, and a recorder of version 1 takes nothing from `annotate`. The
annotations join the summary that a filter reads, so a filter can keep the episodes whose value
estimate fell, for example.

## Calls by player

`Recorder.annotate` attaches one annotation at a time, for one player or
for the whole match. It takes the same parts for one player, `values`, `text`, `probabilities` or
`logits`, `action`, `targets`, `grids`, `arrows` and `extent`, each without the axes of
environments and agents, and `step` and `played` as above.

```python
recorder.annotate(values={'value': 0.4}, agent=0, probabilities=probabilities[0, 0])
recorder.annotate(text='a long pass', player=(0, 3))
```

`agent` names the agent, whose player at that step takes the annotation, and `player` names the
player directly, as a number that counts the players of the left team first and then those of the
right team, each team from its goalkeeper on, or as a pair of the team, 0 for the left, and the
index within the team. Without either the annotation belongs to the whole match. The file keeps
the player's index in the header's list of players, which may list the right team first, and the
summary that a filter reads names the player by its number again. A recorder raises
`ArgumentError` when it has captured no step, for a step outside the current episode, and for an
agent that controls no player then. For a live source, see [Showing annotations](https://github.com/cuju-football/render/blob/main/docs/annotations.md#live-sources).

## Limits and errors

Every part is checked when the call is made. A shape that does not fit the batch, a number that is
not finite, a value beyond what a 16-bit float holds, or a part past a limit raises `ArgumentError`
before any recorder or live source takes anything, so a refused call leaves every recording as it
was and the writer of recordings never meets a bad overlay. Numbers of numpy and PyTorch arrive as
plain numbers.

Annotating reads nothing of the batch and changes nothing of it, so every observation, reward and
state stays as it was, bit for bit.

## Settings

The table `[annotations]` of the [settings](configuration.md) holds the keys of this guide,
`probabilities`, `max_values`, `perspective`, `grid_cells` and `grid_every`. A recorder and a live
source read it from their own `config=` and `overrides=`, as `Recorder(batch, overrides=[...])` and
`cuju.arena.watch(batch, config='run.toml')` show, so two recorders of one batch may keep
annotations differently.
