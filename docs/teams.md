# Registering teams

A team is a submission and its competition identity in the arena registry. Register the
submission, choose its size and set which players it controls before running a match. A
checkpoint captures a version of a team's submission. Use [Team files](team-files.md#checkpoints)
to name and compare checkpoints.

On the command line, identify a registered team by its exact identifier, a unique prefix
of at least four characters after the type prefix, or its exact name ignoring case.
Quote a team name that contains spaces. If a reference matches several entries, the
command returns usage status 2 and lists up to five candidates in its hint.

## The command line

```sh
cuju arena team add path/to/submission --name "Rule-based baseline" --size 11v11
cuju arena team list
cuju arena play --left t-20261106-101500-3fa9 --right builtin --seed 7 --right-difficulty 0.4
cuju arena play --left random --right random --scenario 5_vs_5 --pool t-20261106-101500-3fa9,builtin
cuju arena team export t-20261106-101500-3fa9 --output baseline.zip
```

The team add command also accepts exported zip files, keeping the team name, format, control
settings and assets. The option `--name` can rename the import. From a folder it takes the files
themselves, leaves out `__pycache__` and every link, to a file or to a folder, and names each link
it leaves out in the log, so no file outside the folder reaches the team or its exports. A tar
archive with a link is refused, and a link inside a zip archive arrives as a plain file holding the
path it named, so the registry never stores a link. A tournament under the format `strict/1`
refuses a team whose folder holds a link all the same, since one may be made there by hand later,
as [The format strict/1](evaluate-tournaments.md#the-format-strict1) describes. The registry
leaves a link out at the add because the folder is the person's own and the rest of it still makes
a team, while `strict/1` refuses a link at play because the stored team was changed and its size
and digest no longer hold every file it would play.

A team name must be free of `@` because that character separates a team from its checkpoint.
Registration and renaming reserve the built-in identifiers regardless of case. These
include `builtin`, its difficulty forms such as `builtin:0.3`, `builtin:scenario`,
`random` and `random-actions`.
The rule applies to a name you supply, a name inferred from a folder or file, and a name
carried by an imported team. If an archive carries a name with `@`, use `--name` to choose
an accepted name for the import. When renaming a team, choose a name without `@`.

An explicit `--size` takes precedence over the size in an imported identity. If you omit it,
the identity supplies the size when available, and a plain submission starts as 11v11.

`cuju arena team show ID` prints a team with its control and the folder of a registered
submission, and `cuju arena team set ID` changes
its name with `--name`, its size with `--size` and its control with the options that `add`
also takes. `--players` sets the count of controlled players, `--assignment` takes `dynamic` or
`fixed`, `--selection` takes the fixed players as indices of the formation or as roles, such as
`1,2,3` or `CB,CM`, and makes the assignment fixed, `--goalkeeper` takes `agent`, `builtin` or
`scenario`, and `--action-set` takes `default` or `v2`. A change keeps every setting it leaves
out, and dynamic assignment drops the selection. The option `--json` of `list`, `show`, `add` and
`set` prints the team or the list as the server returns them, and `remove` deletes a team's
folder and says so. A new team takes the next of eight kits in turn, the one that fewest teams of
the registry wear, so teams added one after another differ on the pitch.

The command `cuju arena benchmark run` plays one or several teams headless on the academy tasks, as
described under [The academy benchmark](evaluate-benchmark.md), and `cuju arena benchmark tasks`
names the tasks.

## Control settings

Each submission sets its count, assignment, player selection, goalkeeper and action set in Control
of the Submission section, or with `cuju arena team set`. The count runs from one to every
controllable player of the scenario. A requested match checks both sides separately and reports the
team, scenario and conflicting setting when they cannot play together. A smaller scenario refuses an
excess count rather than reducing it. Exported teams carry these settings with them, and an import
keeps the control settings of its archive. Earlier teams with only a count and goalkeeper setting
load with the same control as before.

Dynamic assignment gives GRF the requested count of agents. With fewer agents than controllable
players, GRF switches them among players near the ball. One dynamic agent is the Kaggle case.
With every controllable player, each agent keeps a player when both sides use full counts or AI.
The scenario goalkeeper setting lets GRF decide whether the keeper can be selected. The agent
setting requires a controllable keeper, and leaves selection to GRF in dynamic assignment.

Fixed assignment takes a list of player indices starting at zero, or GRF roles, in agent order.
The roles are GK, CB, LB, RB, DM, CM, LM, RM, AM and CF. Repeated roles select successive players
of that role in lineup order. The small pitch of Control shows the default lineup of the size, and
its players pick the fixed players in agent order. The match checks against the actual scenario
lineup, which may differ. A missing role, repeated index or uncontrollable selected player gives an
error. Agent goalkeeper control requires the keeper in the selection. Builtin goalkeeper control
requires fixed assignment and leaves the controllable keeper outside the selection. Scenario
goalkeeper control in fixed assignment requires a keeper that the scenario makes uncontrollable.

Fixed assignment gives the engine every controllable agent of the side. On each step the runner
reads the active player of every agent from its raw observation. It sends the observations of the
selected players to the submission in the chosen order, and routes each returned action to the
agent holding that player. Agents holding players outside the selection send builtin AI, 19.
GRF clears the selected players of both sides at set pieces when either side uses dynamic
selection. A side with every controllable agent keeps every controllable player under control,
while the agents holding those players may change. The runner follows those changes on each step.
During a handover an agent may briefly report minus one as its active player. For that step the
fixed side uses the built in AI for all its players and skips the submission call. Other missing
selections receive replacement agent columns, and the submission is called in selected order even
when two agents report the same player. The built in AI then replaces its actions in the columns
of missing selected players.

The default action set has 19 actions. The v2 set adds builtin AI at index 19, so a submission may
hand any selected player to the AI for one step. Each side keeps its own action set, including
its range checks and negative indices, when the other side needs v2. Every player left to the
AI uses the ability of its side. A fixed team may meet a dynamic team, a single agent or the AI.

## Multi agent teams

A team trained with one network per player, such as GRF_MARL or TiZero, becomes a submission folder
whose `main.py` answers Kaggle's multi agent form. The observation `obs['players_raw']` then holds
one raw observation per controlled player, in agent order for dynamic assignment or selected
player order for fixed assignment, each with the
team as `left_team` and its own player as `active`, and `obs['controlled_players']` holds their
count. The agent returns one action per controlled player in the same order, each an index of the
action set, so a team of five that controls 4 players returns four actions and a team of eleven
that leaves its goalkeeper to the built in AI returns ten.

```python
import torch

weights = torch.load('weights.pt', map_location='cpu', weights_only=True)
networks = build_networks(weights)


def agent(obs):
    return [act(networks, raw) for raw in obs['players_raw']]
```

Here `build_networks` and `act` stand for the architecture and inference code of the training
project, which travel with `main.py`. Build the networks once when the file loads, put them in
evaluation mode and run inference without gradients. Reproduce the feature encoder, normalization,
available action masking and recurrent state handling used in training. A recurrent policy needs
separate memory for each controlled player and must clear it at the start of each episode.
A single controlled player may return one action, while several return a list of the same length
as `players_raw`. Keep `agent` as the last callable defined or imported in `main.py`.

The weights travel best as plain tensors, loaded with weights only enabled. A checkpoint saved
as a pickled module converts once inside a Docker container with the project code, then saves
its state dictionary as tensors. The submission builds the network and loads those tensors.
For one network per player, index the networks by the active player rather than the current
position of an observation in the list, as the following example does.

```python
import torch

weights = torch.load('weights.pt', map_location='cpu', weights_only=True)
networks = build_networks(weights)


def agent(obs):
    return [act(networks[raw['active']], raw) for raw in obs['players_raw']]
```

For GRF MARL training scenarios, choose fixed assignment, ten players for eleven a side or four
for five a side, indices one through ten or one through four, scenario goalkeeper control and
the action set used in training. For the Kaggle scenario with ten outfield players, choose the
builtin goalkeeper setting instead. A role selection such as CB, CB, CF keeps the first two
center backs and the center forward in that order. The checkpoint must use the same observation
features and action set as the submission.

## Export

A team leaves the page as its submission folder, a zip archive with `main.py` and the submission's
files at its top, as Kaggle reads them, a folder `cuju-team` that holds the team's size, control,
home scenario and the names and numbers of its players, and a folder `cuju-look` that holds its
kits, code, pictures, bodies and the look of each player. Kaggle ignores the extra folders, and a
submission folder that holds them brings the whole team back when it is added to a registry again,
on the page or from the command line, each file checked as an upload is. A folder without
`cuju-look` takes a new kit, as a new team does. The built in AI exports its look alone.

An import keeps every file of `cuju-team` and `cuju-look` that the page leaves unread, such as
notes, source artwork or a folder of drafts, and every file that an earlier version of the renderer
wrote there, so these files travel on with the next export. Each image and body that the page reads
is checked as an upload is, and a file that fails its check stays in the folder under its own name followed by
`.rejected`, which the page never reads, so the team plays without it and its bytes remain for its
owner. A file whose name is the name of such a file in another case or with more after the slot,
such as `Crest.png` or `crest.x.png`, is set aside as well, and the server serves each image and
body of a team by its exact name alone and never through a link, on every system, whether or not the
system finds names whatever their case. A link, which could point outside the team's folder,
and a file whose name opens a device on Windows, such as `con.txt`, are removed. The server's log
names every file set aside or removed and the reason. The team takes the identifier of its new
folder whatever its `team.json` says, each value of that file is read on its own and one of the
wrong type falls back to its default alone, as [Team files](team-files.md) describes, and an
import that fails at any step removes the folder it began, so the registry lists only whole
teams. The name in a team file, imported or edited by hand, keeps the characters that
print, and the escapes of a terminal, line breaks and the marks that turn the direction of text
become spaces, so `cuju arena team list` prints the name as the page shows it.

Duplicate, on the hub, makes a new team of the registry from a submission team, with the
submission's files, its size, controls, roster and its whole look, under the team's name
followed by `copy`. The two teams change independently from then on, so a copy is a starting point
for trying other controls or another look while the original keeps playing as before. The built in
AI is one shared entry of the registry and offers its look alone.
