# Recording format

Optional header fields carry scenario timing. The `duration` is the configured number of steps.
The `second_half` field is the configured step of the second half, or null when no half time occurs.
The clock threshold is `second_half` times 1800 milliseconds. The `full_match` field identifies a
scenario with a duration of at least 3000 steps and no early episode end conditions. Full time
needs `full_match` set to `true`. Missing timing leaves the recording playable, with End of episode
for an episode end flag and End of recording otherwise.

Bits 0 and 1 of the frame flags mark episode end and start respectively, with values 1 and 2.
The offside flag has value 4. Player references in the frame use indices in the header's player
list, including the scorer, foul offender, victim and offside player.

The optional `engine_scorer` header flag enables bits 8 through 12 of the frame flags. They hold the
engine scorer index in the header's player list plus one, with zero meaning the scorer is
unavailable. Older pages ignore these bits.
The optional `offside_called` header flag enables bit 2 of the frame flags. When set, the offside
integer holds the last toucher penalized at a buffered free kick without a foul. Otherwise it
retains the first offside candidate. Older recordings retain their candidate based interpretation.

A recording is a gzip compressed file with the extension `.cjr`. After decompression it holds the
seven bytes `CUJREC1` followed by a newline, the length of a header as an unsigned 32 bit integer,
the
header as UTF 8 JSON padded with spaces to a multiple of four bytes, and the frames. Every number is
little endian. Version 1 stores the frames back to back, each of the same size and aligned to four
bytes, and version 2 stores them coded in sections, as [Version 2](#version-2) describes. Both
versions hold the same frames bit for bit. The recorder, requested matches and games on the Play
page write version 2, `Recorder(batch, version=1)` writes version 1, and the page, the library and
the module `cuju.arena` read both. A live stream sends the header of version 1 and its frames
uncompressed. A recording of version 2 may keep more of its episode in sections beside the frames,
as [Sections beside the frames](#sections-beside-the-frames) describes, and a recording of version
3 keeps the positions of the players in place of the frames, as [Version 3](#version-3) describes.
A recording holds the game alone, and its annotations, overlays and look lie in files beside it,
as [Attachments](#attachments) describes.

The header records the version, 1, 2 or 3, the title, the scenario, the steps per second, 10, the
names of the fourteen joints and the parent of each, one entry per player with its team, its index
within the team, whether it is the goalkeeper and whether an agent controls it, the name of each
team with its `roster`, the name and number of each player, the size of a frame as version 1
stores it and the number of frames, which a live stream gives as null. What each team wears lies
in the attachment `look`, and the header of a live stream holds it as well.

The page, the library and the module `cuju.arena` hold a recording to the same limits, a header
of at most 1 MiB, at most 32 players, at most 50,000 frames and, in version 2, a keyframe interval
of exactly 100. These are far above the largest that cuju writes, a tournament match of 5000 steps
of eleven a side, which takes 41 MB in version 2. One limit differs between the two sides, the
bytes of a recording once decompressed. The library and `cuju.arena` read at most 256 MiB, since
they decode the frames in parts and hold little beyond the file, and the page reads at most
128 MB, since it decodes every frame into the memory of a browser tab, where the page also holds a
recording to one million frames times players. A recording between the two limits therefore reads
in Python and is refused by the page with a message that names the limit. A file of version 1
gives the size of a frame of its players, and a file past any of these limits ends its reading
with an error that names the limit. A header holds the numbers of JSON alone, which leave out NaN and the infinities.
Each field of the header that the library reads holds its type. The title, the scenario and the
name of each team are text or null, each player names its team, 0 or 1, its index and its flags,
true or false, and the steps per second are a number above 0. Importing a file whose header holds
another type ends with an error that names the field, and such a file copied into the folder of
the library lists under its identifier. A title or team name that names a message by `title_id` or
`name_id` names one of the names that cuju makes, under `server.names` or
`server.tournament.label`, and the library reads a name with any other message as its text. A last
frame of version 1 cut short is left out, as the page leaves it out.

A file is held to these limits before it is read. gzip never grows what it packs by more than a
few bytes in a thousand, so a file that is larger on disk than a recording may be once
decompressed is no recording that the readers take. The library and `cuju.arena` refuse a file
of more than 256 MiB by its size alone, and the page refuses a file of more than 128 MB when it is
dropped or chosen, each with a message that names the size of the file and the limit. A wrong file
of several gigabytes, a video dropped by mistake for example, therefore gets its message at once
and costs neither the memory of the tab nor the time to read it.

The header also holds a `provenance` object, which records what was known of the recording's
origin when it was made. Every recording names the version of cuju that made it as
`cuju_version` and its `source`, which is `requested_match`, `human_game`, `benchmark`, `recorder`
or `live`. A recording of an earlier release may name a source that this list lacks, and the page
then shows the source as the header names it.
A requested match adds its resolved `seed`, the `scenario`, the registry identifiers of both
`teams`, the `controls` of each side, the `agent_counts`, the engine's `action_set` and the
`ability` that played on each side. A game on the Play page adds its seed, scenario, the sides of
the people as `humans`, the action set and the ability. A match of a tournament adds `tournament`,
with the identifier and name of the tournament, the name of its stage, the label of the match and
its key, the names of its two entrants as the tournament shows them as `names`, home first,
which also name the teams of the recording, and the `ability` that the tournament gave each side,
home first, null for the scenario's own, and a match of a set length
adds `length`. An attempt of the academy benchmark adds the `task`, the index of the `attempt`
from zero, its seed, the scenario, the `teams` with the registry identifier of the left team, the
agent counts, the action set and the ability, and `failed` when the policy failed in it. A
recorder and a live source add the
`environment` index of the batch, the `action_set` of the batch, which numbers the actions of the
annotations, and `engine_seed`, the engine seed of the episode, GRF's `game_engine_random_seed`,
which every reset writes into the state and the recorder reads from the state of the first frame
of the episode. The seed that the batch was created with stays unknown to them, since a batch may
have been reset with other seeds before the capture began, while the engine seed belongs to the
episode itself. A reset of an environment with that seed, `batch.reset(seeds=[engine_seed])`,
plays the same episode again when the scenario, the options of the batch, the order of the teams
and the actions are the same, as cuju's guide to episodes and seeds describes. A live stream names
the seed of the episode that its source read last when the header was made, so a stream that runs
on into later episodes keeps the seed of the episode it began in. A field that is absent means its value is unknown. A
seed above `2**53 - 1` is written as a decimal string, so the page reads it exactly. A registry
identifier names a team of this machine, whose files may have changed since the match was played.

Requested matches and recorded evaluation and rating matches can keep `provenance.sides`,
in left and right order. Each entry holds a `checkpoint` snapshot with `id`, `label`
and `step`, or `null` for a side without a checkpoint. A recording drawn after a run
reads these snapshots from the saved result.

The library summary beside each recording carries the same provenance, and a recording copied into
the folder without a summary gets its provenance from its header. Provenance adds to the header
alone and leaves the version 1 frame layout as it was, so recordings made before it open as before,
with an empty provenance.

A frame opens with eight signed 32 bit integers, seven floats and four more integers, as the table
lists them.

| Field | Type | Content |
| ----- | ---- | ------- |
| step | int32 | the step of the episode, 0 at the reset |
| game mode | int32 | GRF's game mode, 0 in normal play and 1 to 6 for the restarts |
| score | int32 twice | the goals of the left team, then of the right team |
| ball owner team | int32 | the team that owns the ball, as GRF's raw observation gives it, or -1 |
| ball owner | int32 | the owner's index in the header's list of players, or -1 |
| clock | int32 | the match time in milliseconds, 90 minutes over the 3000 steps of a full match |
| flags | int32 | episode boundaries, called offside and scorer, as described above |
| ball | float32 seven times | the position of the ball, then its rotation as a quaternion |
| foul offender | int32 | the player who committed the referee's current foul, or -1 |
| foul victim | int32 | the player fouled, or -1 |
| foul kind | int32 | 0 for none, 1 for a foul, 2 for a yellow card and 3 for a red card |
| offside | int32 | the called player when flag 4 is set, otherwise the first candidate, or -1 |

The joints of every player follow, seven signed 16 bit integers per joint, fourteen joints per
player in the order of the header. The first three hold the world position in steps of 2 mm, which
spans 65 m either way of the center spot, and the last four the rotation as a quaternion, x, y, z
then w, in steps of one part in 32767. A full match of eleven a side occupies 13 MB of frames
before compression and 12 MB in a file of version 1, half the size of the same match stored as
32 bit floats, and 4.4 MB in a file of version 2. Every
position is in the frame of the pitch, with the center spot at the origin, the left team defending
negative x and the z axis pointing up.

Three runs of one byte per player close the frame, each padded with zeros to a multiple of four
bytes. The first holds bits, 1 for a player on the pitch, 2 for a yellow card, 4 for a red card
and 8 for a player that an agent controls on the step, so a red card sets 2 and 4 together.
Recordings of an older cuju carry no 8, and the page then marks the agents' players of the header.
The second holds the kind of the animation that the player runs on the step, in the numbering of
GRF's `e_FunctionType`, 0 for none, 1 for movement, 2 for ball control, 3 for a trap, 4 to 6 for
the short, long and high pass, 7 for a header, 8 for a shot, 9 and 10 for a deflection and a
catch, 11 for an interference, 12 for a trip, 13 for a slide and 14 for a special animation. The
kind tells what the body of the player does, a shot in mid swing for example, and it lasts as
long as the animation does. The action that an agent chose for the step is another thing, which a
frame leaves out and which [annotations](annotations.md) carry when a training loop gives them.
The page reads the run as `animations` and names the kinds in `ANIMATION` of
`recording/format.js`, and keeps the earlier names `actions` and `ACTION` for the same values. The third holds 0 for a player who did not touch the ball during the
step, and otherwise 1 plus GRF's kind of the touch, 1 for a kick, 2 for another intended touch and
3 for an accidental one.

A live stream sends the same bytes uncompressed.

A page that asks a live source for annotations, with `annotations=1` beside `sequence=1`, receives
a header with `stream_annotations` set to 1. Between the frames, such a stream sends batches of
annotations, each made of the serial `2**64 - 2`, the length of a UTF 8 JSON object as an unsigned
32 bit integer and the object. Its `records` take the form of the attachment `annotations`, with
`f` holding the serial of the annotated frame. The page of cuju 0.2.0 asks for no annotations and
reads the stream as before. A stream with annotations whose source is silent sends a batch with
an empty list of `records` every two seconds, which a reader passes over.

A page that asks a live source for the lanes of the players, the actions of the agents or the
overlays, with `players=1`, `actions=1` or `overlays=1` beside `sequence=1`, receives a header
with `stream_players`, `stream_actions` or `stream_overlays` set to 1. A stream with players or
actions lists `player_fields`, `agents` and `action_set` in its header. Before each frame, such a
stream sends one record of the serial `2**64 - 3`, the length of its payload as an unsigned 32 bit
integer, the serial of the frame, the lanes of the players as one row of `PLYR` laid out as the
Python reader's `row_type` packs it, little endian and without padding, and one byte for each
agent for the actions, 255 for none. Batches of overlays follow a frame, each made of the serial
`2**64 - 4`, the length of its payload, a serial and the body of an attachment `overlays`, whose
frame numbers count from that serial. A batch holds at most 1 MiB. A reader whose header names
none of the three never meets these serials, and the stream of such a viewer is the stream of
0.1.0.

## Version 2

A file of version 2 adds two fields to the header, `frame_count`, the number of frames, and
`keyframe_interval`, the number of frames from one keyframe to the next, 100 in the files that cuju
writes. Sections follow the header, each made of a tag of four ASCII letters, the length of its
payload as an unsigned 32 bit integer, the payload and zeros up to a multiple of four bytes. A
reader keeps the first section of each tag and skips the tags it does not know, so a later version
can add sections that an earlier reader passes over. The section `FRMS` holds the frames, and every
file of version 2 has it.

The frames section stores each field of the frames as a lane, the values of that field in every
frame in order, and its values decode to the frames of version 1 bit for bit. The coding starts
afresh at every frame whose index is a multiple of the keyframe interval, a keyframe, so a frame
decodes from the keyframe at or before it, and a reader that seeks decodes at most one interval of
frames. The page decodes every frame when it opens a recording, and `cuju.arena` decodes from the
keyframe before the first frame that it reads, so the library reads the last frame of a recording
for its summary at once.

A lane of 16 bits stores each value zigzag coded, so that 0, -1, 1 and -2 become 0, 1, 2 and 3. A
run of such lanes stores the low byte of every value of its first lane, then of its second lane and
so on, and the high bytes after all the low bytes. A lane of 32 bits stores four such planes, the
lowest byte first, and a lane of 8 bits stores its bytes as they are. A value coded as a difference
is the value less the value of the frame before, wrapped to the width of its lane, and a keyframe
stores the value itself. A value coded as a difference of differences takes the difference twice.
A byte coded as an exclusive or is the byte XOR the byte of the frame before, and a keyframe stores
the byte itself.

The section holds the runs of lanes in the order of the table, where n is the number of players and
p the bytes of each of the three byte runs that close a frame. The joints count from 1 to 13 in the
order of the header, after the root joint `player`. Lanes of the joints go joint by joint, then
component by component, then player by player, and lanes of the root go component by component,
then player by player.

| Run | Lanes | Width | Coding | Content |
| --- | ----- | ----- | ------ | ------- |
| head | 19 | 32 bits | difference | the 19 words of the frame head, the floats of the ball by their bits |
| root position | 3 n | 16 bits | difference of differences | x, y and z of the root joint of each player |
| root rotation | 4 n | 16 bits | difference | the rotation of the root joint of each player |
| local rotation | 39 n | 16 bits | difference | x, y and z of each joint's rotation relative to its parent |
| sign | 13 n | 8 bits | exclusive or | 1 where w of the local rotation is negative, else 0 |
| local offset | 39 n | 16 bits | difference | each joint's position relative to its parent, in the parent's frame |
| rotation residual | 52 n | 16 bits | none | each joint's rotation less the predicted rotation |
| position residual | 39 n | 16 bits | none | each joint's position less the predicted position |
| closing bytes | 3 p | 8 bits | exclusive or | the three byte runs that close a frame |

A reader rebuilds the joints of each player in the order of the header, so a parent comes before
its children, with `U = 32767`, the parent's decoded rotation `P` and position, the local rotation
`k` and the local offset `o`, all as integers.

1. `k` takes x, y and z from its lanes, and w is the integer square root of the larger of 0 and
   `U*U - x*x - y*y - z*z`, negated where the sign lane holds a value other than 0.
2. The rotation of the joint is the Hamilton product `P k`, each component `c` of it turned into
   `floor((2*c + U) / (2*U))`, plus the rotation residual, wrapped to 16 bits.
3. With `a` the x, y and z of `P` and `w` its w, the vector `s = U*U*o + 2*w*cross(a, o) +
   2*cross(a, cross(a, o))` turns the offset, and the position of the joint is the parent's
   position plus `floor((2*s + U*U) / (2*U*U))` for each component, plus the position residual,
   wrapped to 16 bits.

The Hamilton product takes quaternions stored x, y, z and w, as the frame stores them.

```text
x = w1*x2 + x1*w2 + y1*z2 - z1*y2
y = w1*y2 + y1*w2 + z1*x2 - x1*z2
z = w1*z2 + z1*w2 + x1*y2 - y1*x2
w = w1*w2 - x1*x2 - y1*y2 - z1*z2
```

Every product and sum of these steps is an integer below `2**53`, so a reader in double precision,
as JavaScript computes, reaches the same integers as one in 64 bit integers. The writer chooses the
local rotations and offsets and holds each one where it moves by one step or less, so the lanes
change slowly and gzip finds them, and the residuals carry whatever the prediction misses, which
keeps every frame exact. The shipped full match of eleven a side takes 4.4 MB in version 2 and
11.7 MB in version 1, and the shipped match of five a side 1.1 MB and 2.7 MB.

## Sections beside the frames

A frame holds what a viewer draws, and a recording of version 2 may keep more of its episode in
sections of their own after `FRMS`. The section `PLYR` holds what each player did on each step,
`ACTS` the actions that the agents chose and `STAT` the state of the kick off, from which the
episode plays again. Each section is optional, and the header says which ones a file holds. The
recorder writes `PLYR`, `ACTS` and `STAT` into every recording of version 2. A recording of an
earlier writer may also hold `ANNO`, its annotations, and `GRID`, the overlays of a policy, which
the readers take as embedded attachments.

The format grows by sections alone. The frames, the size of a frame and the section `FRMS` stay as
cuju-render 0.1.0 wrote them, and a reader of that release passes over the tags it does not know,
so a recording with every section opens there frame for frame and shows everything except the
new views and the parts that its attachments carry. A file without a section lacks its values and nothing else, so
a recording of an earlier release, or one of version 1, plays as before.

The header announces the sections through these fields, each of which a file may leave out.

| Field | Content |
| ----- | ------- |
| `sections` | the tags of the sections that follow the first one, in the order of the file, such as `PLYR`, `ACTS` and `STAT` |
| `fidelity` | `joints` for a recording whose frames hold every joint, `positions` for a recording of version 3 |
| `player_fields` | the names of the fields of `PLYR`, in the order of the section |
| `agents` | the number of agents of the left team, then of the right team |
| `action_set` | the action set of the batch, `default`, `v2` or `full`, which numbers the actions of `ACTS` |
| `run_key` | the key of the run, an object that [Kick off state and run key](#kick-off-state-and-run-key) describes |

A reader reads `sections` to learn what a file holds before it reads the sections themselves. A
file that an earlier release wrote has no such field, and a reader then finds the sections by
their tags. A tag that a reader does not know, and a name in `player_fields` that it does not
know, belong to a later release, and the reader passes over them. The page reads a malformed
header field of the sections, or a section shorter than its header gives, as absent, and plays the
frames.

### Player lanes

The section `PLYR` holds the lanes of the players, the values that an inspector shows for each
player on each step. They are the values of GRF's raw observation as an agent of the left team
receives it, read from the same state as the frame and stored as whole numbers. The section
stores them as `FRMS` stores its lanes, in runs of lanes with the same zigzag planes, differences
and keyframes, so a reader decodes any frame from the keyframe at or before it. The runs follow
one another in the order of `player_fields`, which for a recording of the recorder is the order
of the table, where n is the number of players. Lanes of a player go component by component, then
player by player, in the order of the header.

| Field | Lanes | Width | Coding | Content |
| ----- | ----- | ----- | ------ | ------- |
| `direction` | 2 n | 16 bits | difference | x of every player, then y, the movement over one step in GRF's raw units, times 100,000 |
| `tired` | n | 16 bits | difference | the tiredness of each player, from 0 to 1, times 32,767 |
| `sticky` | 2 n | 8 bits | exclusive or | the low byte of the sticky actions of every player, then the high byte |
| `agent` | n | 8 bits | exclusive or | 1 plus the index of the agent that controls the player within its team, or 0 |
| `role` | n | 8 bits | exclusive or | the role that the player plays, in GRF's numbering |
| `designated` | 2 | 8 bits | exclusive or | for the left team, then the right team, 1 plus the index of its designated player in the header, or 0 |
| `ball_momentum` | 3 | 16 bits | difference | x and y of the movement of the ball over one step in GRF's raw units, times 100,000, then its rise in millimeters |

Each value is rounded to the nearest whole number, a half to the even one, and held to the range
of its lane. A direction is GRF's `left_team_direction` or `right_team_direction`, the movement
of a player over one step with x divided by 54.4 and y by -83.6, so a stored value times 0.000544
gives meters along the pitch and a stored value times -0.000836 meters across it, in the frame of
the joints. The momentum of the ball is GRF's `ball_direction` in the same units, with its third
component, which GRF leaves in meters, stored in millimeters. Tiredness is GRF's
`left_team_tired_factor` or `right_team_tired_factor`. The stored steps are finer than the four
digits that an inspector shows, and the exact values of an observation come from playing the
episode again, as [Kick off state and run key](#kick-off-state-and-run-key) describes.

The sticky actions of a player are those of the agent that controls it, as a mask of 13 bits, and
0 for a player that no agent controls. Bits 0 to 7 stand for the directions left, top left, top,
top right, right, bottom right, bottom and bottom left, bit 8 for sprint and bit 9 for dribble,
as GRF's `sticky_actions` orders them. Bits 10 to 12 stand for the keeper rush, the pressure and
the team pressure, which the action set `full` adds. The directions are named in the frame of
the pitch, as an agent of the left team names them. An agent of the right team sees the pitch
turned by half a turn, so its own observation holds each direction as the opposite one, bit 0 as
bit 4 and so on, and its `left_team` is the right team of the recording with x and y negated.

A role is GRF's `e_PlayerRole`, 0 for the goalkeeper, 1 to 3 for the center back, the left back
and the right back, 4 to 8 for the defensive, central, left, right and attacking midfielder and 9
for the center forward. The recorder asks the batch for the roles on every step, so the lane
follows the hand over of the goalkeeper role when a goalkeeper is sent off, which the state alone
leaves out. The `agent` lane gives GRF's `active` of each agent, the player whose lane holds the
agent, and `designated` gives GRF's `designated` of each team.

A reader takes the fields in the order of `player_fields` up to the first name that it does not
know, so a later release adds fields at the end. A writer that knows only some values, such as an
import of positions, writes those fields and names them.

### Actions

The section `ACTS` holds the action that each agent gave on each step, one byte for each, in the
numbering of the action set that `action_set` names. The agents count as the batch counts them,
those of the left team first, and `agents` gives their numbers. The section stores one lane of
bytes for each agent, as they are, each of as many bytes as the recording has frames plus one.
Byte f of a lane is the action that led to frame f. Byte 0 is 255 in a recording that starts at
its kick off, since a reset plays no action, and the action that led to the first frame in one
whose capture began later.
The last byte, one past the last frame, is the action of the step that ended the episode when an
automatic reset replaced the frame of that step with the kick off of the next episode, and 255
otherwise. An action is the number that the agent gave, so a direction of an agent of the right
team is named in its own view of the pitch.

The recorder takes the actions from the batch, which hands them to its step observers from cuju
0.3.0 on. A value of 255 also marks an action that the recorder did not see. A capture by hand
that leaves steps out marks the frames after a gap, and a loop that steps without Python, as
[Recording](recording.md) describes it, marks every frame. A recording whose every action is
unknown leaves the section out.

### Kick off state and run key

The section `STAT` holds the state of the first frame as the batch returned it, GRF's state bytes,
when that frame is the kick off of its episode. cuju loads such a state into an environment of
the same configuration with `set_state`, and the actions of `ACTS` then play the episode again
step for step, with every frame and every observation exactly as the recorded batch produced
them. The last byte of each lane of `ACTS` plays the step that an automatic reset cut, so the run
reaches the true end of the episode. A state occupies about 48 KB for five a side and 77 KB for
eleven a side, and about 7 KB and 9 KB of the compressed file.

The header field `run_key` names what the run needs beside the state and the actions.

| Field | Content |
| ----- | ------- |
| `start` | `reset` when the first frame is the kick off, `mid` when the capture began later |
| `scenario` | the scenarios that the batch was created with, as its `scenario_name` gives them |
| `agents` | the number of agents of the left team, then of the right team |
| `action_set` | the action set of the batch |
| `engine_seed` | the engine seed of the episode |
| `reverse` | whether the episode plays with GRF's reverse team processing |
| `difficulty` | the difficulties that play on the left and on the right |
| `stacked` | `true` when the state holds GRF's stack of four observations, and absent otherwise |
| `state_digest` | the SHA-256 of the section `STAT`, as 64 hexadecimal digits |

A recording whose capture began after the kick off, such as the first one of a recorder that was
attached in the middle of an episode, says `start` is `mid` and keeps neither `STAT` nor
`state_digest`. Its lanes and its actions are complete from its first frame on. A reader checks
the state against `state_digest` before it loads it. A run needs the section `STAT` and lanes of
`ACTS` whose bytes from 1 to the last frame hold no 255. Two recordings whose keys are equal
started from the same kick off, so they align step for step while their agents choose the same
actions, which is what a comparison of two policies rests on.

The state is the one that the batch gives. A batch that stacks its observations returns states
with the stack, which load into a batch that stacks the same observations, and the key then says
`stacked`. A batch of several scenarios names them all in `scenario`, and the state loads into
the environment of the scenario that it belongs to. A state is GRF's pickle of the episode logic
before the bytes of the match, which cuju reads with a restricted unpickler, as cuju's guide to
states describes, so a recording is loaded from a source that is trusted.

## Attachments

A recording holds the game alone, its frames, the sections of the players, the actions and the
kick off, and the names of the teams and players. What a model or a viewer adds to a match
travels beside it in attachments, the annotations of a policy, its overlays and the look of the
match. Each attachment is a file of its own beside the recording, so a tool adds, replaces or
removes an attachment and leaves the game file, and the digest that names it, as it was. An
attachment never changes a frame.

The attachments follow the protocol `attachment/1`. Each is a file `<stem>.<kind>.cja` beside the
recording `<stem>.cjr`, such as `m-20261119-120000-ab12.look.cja`. A file is gzip compressed and
holds, once decompressed, the seven bytes `CUJATT1` followed by a newline, the length of a header
as an unsigned 32 bit integer, the header as UTF 8 JSON and the body, whose layout its kind
gives.

| Field | Content |
| ----- | ------- |
| `attachment` | 1, the major of the protocol |
| `kind` | the kind, such as `annotations`, `overlays` or `look` |
| `version` | the version of the kind, whose layout the body follows |
| `recording` | the key of the recording that it belongs to, `sha256:` and 64 hexadecimal digits |
| `frames` | the number of frames of that recording |
| `writer` | the program that wrote it, such as `cuju-arena 0.1.0` or `cuju-render 0.1.0` |
| `created` | when it was written, as local time `YYYY-MM-DDTHH:MM:SS` |

The key of a recording is the SHA-256 of the bytes of its file once decompressed. The library
never rewrites a recording, so the key holds for as long as the recording does, and the summary
of a recording keeps it as `digest`. A reader finds the attachments of a recording by the stem of
its file and checks each one against the key and the number of frames of the recording. A file
whose key names another recording, whose number of frames differs or that is no attachment is
left out with a note, never applied, and a request for it ends with a message that names the
file and both keys. A writer that is given a key other than the recording's is refused the same
way.

A kind that ships with cuju is lowercase letters, digits and dashes, and the kind of a plugin is
the name of the plugin and its kind joined by a dot, such as `myplugin.heat`. Each kind has its
own version. A reader passes over a kind that it does not know and a version newer than the one it
reads, so a later release adds kinds and versions that an earlier reader leaves alone.

| Kind | Body | Written by |
| ---- | ---- | ---------- |
| `annotations` | the annotations, in the layout that [Annotations](#annotations-body) gives | the arena, from the annotations of the recorder |
| `overlays` | the overlays of a policy, in the layout that [Overlays](#overlays) gives | the arena, from the overlays of the recorder |
| `look` | the look of the match, the JSON that [Look](#look) describes | the renderer, as the recording closes or when it is first shown |

Producers write the attachments as a recording closes. They are plugins of the kind
`attachment/1`, as [Plugins](plugins.md#attachments) describes, each named by the kind it writes.
The plugin `builtin` offers `annotations` and `overlays`, and the renderer registers `look` as it
loads, so a recording that closes in a process that holds the
renderer has its look at once. A recording made without the renderer, by a script that plays
matches alone, gets its look drawn the first time a page shows it, and the look is then stored
beside it, so it stays the same from then on. A producer that fails leaves its kind out with a
warning, and the recording and the other attachments stay.

The library keeps the attachments with their recording. A new title, a folder, a tag, a group,
the Trash and Restore change the summary alone, so the attachments stay where they are. Deleting a
recording for good deletes its attachments, and a recording file that is added from elsewhere
brings the attachments beside it whose key fits it. The summary lists the kinds of the attachment
files as `attachments`, and `has` names `ANNO` and `GRID` for a recording whose attachments carry
annotations or overlays, as it names the sections of an older file.

A recording of an earlier writer, every recording of cuju-render 0.1.0 among them, keeps its
annotations in the section `ANNO`, its overlays in the section `GRID` and its look in its header,
`kits`, `sky`, `ground` and the `color`, `secondary` and `style` of each team. The readers take
those parts as attachments of origin `embedded`, read only, so such a recording opens and shows
exactly as it did, and a file beside it of the same kind comes before them. No writer writes these
parts inline again. The header of a recording now names the `roster` of each team, the name and
number of each player by its index, which an older header keeps in the style of the team.

A live stream carries no attachment. Its header holds the look of the match, `kits`, `sky`,
`ground` and what each team wears, and the stream sends its annotations and overlays between its
frames, as the start of this page describes.

### Annotations body

The body of an attachment `annotations` holds the [annotations](annotations.md) of a recording, as a
UTF 8
JSON object whose `records` list one record for each frame and player, in the order of the frames.
A record holds `f`, the index of its frame, and as it needs them `p`, the index of its player in
the header, absent for the whole match, `v`, an object of named numbers, `t`, the text, `a`, the
action chosen, and `k`, the likely actions as pairs of an action and its probability, the most
likely first. A reader that meets a malformed body plays the frames without annotations.

### Overlays

The body of an attachment `overlays` holds the spatial overlays of a policy, numbers that belong to
a frame and,
optionally, to one player. A grid holds a value for each cell of a rectangle of the pitch, such as
a value map, and a vector holds a value for each player, as the targets of a pass, or for each
action, as a whole distribution. The body opens with the length of an index as an unsigned 32
bit integer, then the index as UTF 8 JSON padded with spaces so that what follows starts at a
multiple of four bytes, then one block of bytes that holds the numbers of every overlay. The
index is an object whose `records` list one record for each overlay, in the order of frame,
player and name.

| Field | Content |
| ----- | ------- |
| `f` | the index of the frame |
| `p` | the index of the player in the header, absent for the whole match |
| `name` | the name of the overlay, of 1 to 40 characters |
| `kind` | `grid` for a grid of cells, `targets` for a value for each player, `actions` for a value for each action |
| `shape` | the rows and the cells of each row of a grid, or the length of a vector |
| `type` | `uint8` or `float16`, how the block stores the numbers |
| `scale`, `offset` | for `uint8`, the value of a byte is the byte times `scale` plus `offset` |
| `side` | 0 or 1 for the side whose own view the numbers lie in, absent for the frame of the pitch |
| `extent` | the rectangle of a grid, x and y of its first corner and of its last, in GRF's raw units |
| `at` | the offset of the numbers in the block |

The numbers of a grid go row by row. A grid without `extent` covers GRF's raw rectangle, x from
-1 to 1 and y from -0.42 to 0.42, the area of GRF's minimap, and its natural shape is the 72 rows
of 96 cells of that minimap. An agent of the right team sees the pitch turned by half a turn, and
`side` set to 1 says that the numbers lie in that view, so a reader turns them into the frame of
the pitch. `side` names the team whose own view an overlay lies in, an overlay without `side` lies
in the view of the recording, the frame of the pitch, and the targets of a side list the players
of that side's team first. Numbers of the type `float16` are little endian and start at an even offset. Numbers
of the type `uint8` are quantized to 256 steps between the least and the greatest value of the
overlay.

A step and player take at most four grids and four vectors, a grid at most 72 rows of 96 cells, a
vector at most 64 values, and the body at most 32 MiB before the compression of the file. A
writer leaves out overlays past these limits, with the recording and its other attachments
written, and a reader leaves out a record that is malformed or past a limit and keeps the others. A value map of 16 rows of 24 cells on every step of a full
match takes 1.15 MB before compression.

### Look

The renderer writes a look attachment for a recording using the same container and digest as
other attachments. Its body names the kits, sky, ground and the appearance of the teams.
Read [Look attachment](https://github.com/cuju-football/render/blob/main/docs/look-and-settings.md#look-attachment) for the body format.

## Bundles

A bundle carries a recording and its attachments in one file, a ZIP archive with the extension
`.cjz`, which the Export tray of Watch and `GET /api/matches/<id>/bundle` write and every import
reads, as [Importing matches](importing.md) describes. The archive holds `bundle.json`, the
recording as it is as `recording.cjr` and each attachment file whose key fits as
`attachments/<kind>.cja`. `bundle.json` names `bundle`, 1, the key of the recording as
`recording`, the `kind`, `version`, `writer` and `created` of each attachment, the `writer` of the
bundle and when it was `created`. The parts that an older recording embeds stay inside its file.

An import checks every attachment of a bundle against the key of its recording before it writes
anything, and refuses the whole bundle, with a message that names the file and both keys, when
one belongs to another recording. A bundle whose recording the library holds already adds the
attachments that the recording lacks and opens it.

## Version 3

A recording of version 3 is a recording of positions. It keeps where every player and the ball
are on every step, with the score, the clock and the calls of the referee, and leaves the joints
out, which are about 95 percent of a file of version 2. It serves large evaluation runs, whose
thousands of episodes are looked at in two dimensions, and imports of matches whose source gives
positions alone. With `STAT` and `ACTS` such a recording plays again in cuju, which gives back
every joint.

The header of version 3 says `version` 3 and `fidelity` `positions`, keeps `frame_count` and
`keyframe_interval` as version 2 has them, and leaves `frame_bytes` out. The section `TRKS` takes
the place of `FRMS` as the first section, and `PLYR`, `ACTS` and `STAT` follow as in version 2. A reader of cuju-render 0.1.0 refuses such a file by its version, with the message
`expected a recording version from (1, 2), found 3` in Python and the message of an unknown
version on the page, so a recording of positions is never shown with joints that it lacks.
The page of cuju-render 0.2.0 opens a recording of version 3 in the 2D view, with each player at
the position of its root joint, and the 3D view says that the recording keeps positions alone.

The section `TRKS` holds track frames, each a frame without its joints. A track frame opens with
the same 76 bytes as a frame, then holds the position of the root joint of each player as three
signed 16 bit integers in steps of 2 mm, padded with zeros to a multiple of four bytes, then the
three runs of one byte per player that close a frame. The section stores them in three runs of
lanes with the codings of `FRMS`, where n is the number of players and p the bytes of each closing
run.

| Run | Lanes | Width | Coding | Content |
| --- | ----- | ----- | ------ | ------- |
| head | 19 | 32 bits | difference | the 19 words of the frame head |
| root position | 3 n | 16 bits | difference of differences | x, y and z of the root joint of each player |
| closing bytes | 3 p | 8 bits | exclusive or | the three byte runs that close a frame |

The track frames of a recording of version 1 or 2 are its frames with the joints other than the
root left out, so a reader that needs positions alone reads every version the same way.

## Sizes of the sections

The table gives the bytes per frame that each section adds to the compressed file, measured on
whole matches of 3001 frames that agents played on every player of the left team in five a side
and on all eleven in eleven a side. The lower figure is for agents that keep an action for
several steps, as a trained policy does, and the higher one for agents that draw a new action on
every step.

| Section | Five a side | Eleven a side |
| ------- | ----------- | ------------- |
| `FRMS` | 653 to 691 | 1380 to 1551 |
| `PLYR` | 25 to 30 | 53 to 69 |
| `ACTS` | 0.9 to 2.4 | 2.4 to 6.6 |
| `STAT`, for the whole file | 7.3 KB | 9.0 KB |
| `TRKS` | 44 to 47 | 69 to 80 |

The three sections of the recorder add 4 to 5 percent to a file of version 2. A recording of
version 3 with `PLYR`, `ACTS` and `STAT` takes 9 to 11 percent of the same match in version 2.
