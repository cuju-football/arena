# Team identity files

The arena keeps identity, size and checkpoint history in the team folder. This guide describes
the manifest, how it reads old team files and what travels when you export a submission. The
renderer keeps the appearance of the team in its [look file](https://github.com/cuju-football/render/blob/main/docs/team-files.md#the-look-file).

## The folder

A team's folder in the registry looks like this, and an exported zip holds the same files.

```
teams/<id>/
  main.py, weights, ...       the submission, as Kaggle reads it
  cuju-team/
    team.json                 the manifest, the identity of the team
    checkpoints.json          the list of the team's checkpoints
    checkpoints/<id>/...      the files of the earlier checkpoints
    notes.txt, ...            any other file, kept and carried along
  cuju-look/
    look.json                 the look file
    crest.png, print.png      the crest and the chest print of Uniform 1
    bodies/body1.glb, ...     the team's own bodies, up to four
    notes.txt, ...            any other file, kept and carried along
```

The registry reads the manifest and the list of checkpoints by their names. The renderer reads the
look file, the pictures and the bodies by their names, and checks each one as an upload is
checked. Every other file of either folder stays where it is, travels with every export and is
never read, so notes, source artwork and drafts are safe there. Neither ever deletes a file of a
team. A file that fails its check stays in its folder under its own name followed by `.rejected`,
which nothing reads, so the team plays without it and its bytes remain for its owner.

Every folder of a team whose name starts with `cuju-`, other than `cuju-team`, belongs to a tool,
as `cuju-look` belongs to the renderer. The registry keeps such a folder, carries it in exports and
copies, and leaves it out of the submission, its checkpoints and its digests. A team's look, then,
never changes its checkpoints, its ratings or its home ground, and the look follows the team
through a rename, an export and an import.

## The manifest

The manifest is the file `cuju-team/team.json`. Its second version, which cuju-arena 0.1.0 writes,
carries the key `"identity": 2`. The manifest of a team of five a side reads as follows.

```json
{
  "identity": 2,
  "name": "Redwood",
  "created": "2026-11-17T14:02:00",
  "size": "5v5",
  "control": {"players": 4, "assignment": "dynamic", "selection": [],
              "goalkeeper": "scenario", "action_set": "default"},
  "scenario": "5_vs_5_counterattack",
  "roster": [
    {"name": "Keeper", "number": 1},
    {"name": "Playmaker-v3", "number": 10}
  ],
  "ground": {"checkpoint": "9f3c0a1b22d4e6f8..."},
  "kept": {}
}
```

Each key has one meaning.

- `name` is the team's name, up to 80 characters, and `created` the moment it joined the registry.
- `size` is the team's size for competition, `5v5` or `11v11`, which sets the length of its
  roster. A file of 0.1.0, or of a draft of this version, spells it `format`, which reads the same
  where `size` is absent, and the next save writes `size`. The word format names a submission
  format everywhere else, as [Submission formats](formats.md) describes.
- `control` names the players that the submission controls, as the Teams guide describes. It is
  the control of the checkpoint that plays.
- `scenario` is the team's home scenario. The manifest stores it only when it differs from the
  default of the team's size, and the section on the home scenario below says how it is chosen.
- `roster` lists the players by index, each with a name of up to 24 characters and a shirt number
  from 0 to 99 that no other player of the squad wears. Captions, the search of moments, the
  player of the match and the statistics read them, so they belong to the team's identity.
- `ground` holds the digest of the team's first checkpoint, which seeds the look of its home ground.
- `kept` holds what the registry does not read, as described below.

The registry writes the manifest indented, so a person can read it and edit it by hand.

## Reading a team, one field at a time

The registry reads each value of a manifest on its own, and the renderer reads each value of a
look file the same way. A value of the wrong type, or outside its choices, falls back to its
default, and every other value keeps what the file says. A kit with one color out of range keeps
its pattern, its other colors, the roster, the names and the numbers. A team whose model the
renderer no longer ships wears GRF's player and keeps the rest of its look. A team that names a
body of its own that its folder no longer holds wears GRF's player, and its players who wore that
body follow the team.

The entry of a team lists the paths of the values its reading set aside under `set_aside`, such as
`roster.1.number` of the manifest or `wardrobe.uniform.shirt` of the look file, so the Teams page
can say what changed. The value itself is never lost. The next save of the file copies it into
`kept`, under `kept.refused` with its path, and from then on the team reads without anything set
aside. A manifest that holds no JSON object at all reads as a team with every default, named after
its folder, and its `set_aside` names `team.json`. The next save keeps that file beside the new
manifest as `team.json.rejected`, and an export carries it the same way. A look file that holds no
JSON object reads as the default look and gives way to a new one at the next change of the look,
kept beside it as `look.json.rejected`.

Reading a team changes no file, apart from the look file that a team without one gains on its
first read, which writes inside `cuju-look` alone and once, as the takeover of a look of 0.1.0
below does. Only a save writes either file, such as
a change on the Teams page, a new credit, or the first time the registry needs the team's first
checkpoint.

## What the files keep

The block `kept` of each file carries every key that this version does not read, word for word, at
the same place it had in the file. A key that a later version adds to the top of the file lands at
the top of `kept`, and a key that it adds to a player lands in a list of the roster's length under
`kept.roster`, at that player's place. Each save carries `kept` on, so a team that passes through
this version and returns to a later one loses nothing.

`kept.refused` holds the values a reading refused, each under its path in the file it came from.
Values stay there after the field is set again, as a record of what the file once held, and
nothing reads them.

## Teams of cuju render 0.1.0

A team file of cuju render 0.1.0 carries no `identity`, and it holds the team's identity and its
look together. The registry reads its identity field by field, as it reads the second version,
and turns the file into the second version at the team's next save.

| In the file of 0.1.0 | What the manifest makes of it |
| --- | --- |
| `name`, `created`, `control` | the same keys |
| `format`, the team's size | `size` |
| the `name` and `number` of each player of `style.players` | `roster` |
| the rest of `style` and `model` | kept under `kept.style` and `kept.model`, where the renderer finds them |
| `id`, `kind`, `name_id`, `name_values`, `images`, `sounds`, `sounds_version`, `clips` | left out, since the team's folder gives them |
| any other key | kept under `kept`, at its own path |

## The home ground and the first checkpoint

A team's home ground takes its look from a seed, the digest of the team's first checkpoint. A
checkpoint is the team's submission at one moment, named by the SHA-256 of the files of its folder
without `cuju-team` and the folders of tools, the digest that the ratings already use for a
checkpoint. When a team joins
the registry, its submission as it comes is its first checkpoint, and the manifest records the
digest under `ground.checkpoint`. A team that brings its own `ground`, such as an export of a team
that already has one, keeps it, so a team keeps its home wherever it goes.

New weights make a new checkpoint and leave `ground` as it is, so a team's home ground stays put
while it trains. A team of 0.1.0 has no `ground` until the renderer first needs it or the team is
saved, and then records the digest of its submission as it stands, which is the only checkpoint a
team of 0.1.0 can have had. The built in AI has no files and takes its ground from its own rating
key instead.

## Checkpoints

A team keeps one identity through many sets of weights. A checkpoint is the team's submission at
one moment, every file of its folder outside `cuju-team` and the folders of tools such as
`cuju-look`. Its identifier is the first 16
characters of the SHA-256 of those files, which is the identifier that the ratings use. Only the
files give the name. Renaming the team, changing its kit, editing its roster or setting its
control never changes it, and every checkpoint and rating of cuju render 0.1.0 keeps its
identifier.

New files make a new checkpoint of the same team. The team keeps its name, its kit, its roster, its
pictures and its home ground, and the new checkpoint starts without evidence in every ratings
table, since a model is a team at a checkpoint. The files that played until now move into
`cuju-team/checkpoints/<id>/`, where `<id>` is their identifier, and the new files take their place
at the top of the folder, where Kaggle reads a submission. Adding a checkpoint first records the
team's ground, so a team of 0.1.0 that was never saved again keeps the home that its first files
gave it.

The page and the command line add a checkpoint the same way. The route `PUT /api/teams/ID/files`
takes a folder, a zip, a tar or one entry file under the same safety rules as a new team, with
`filename`, `label`, `step` and `note` in its query. A label is up to 80 characters, a note up to
500, and the training step a whole number from zero. An archive that carries its own `cuju-team`
or `cuju-look` folder, such as the export of another team, brings its files alone, so the identity
and the look of the team never change. Files that match the ones that play, or an earlier checkpoint that the team still keeps,
are refused with a message that says so, and a refused upload changes nothing.

```
cuju arena checkpoint add ID PATH --label "step 1.2M" --step 1200000
cuju arena checkpoint list ID
```

A training step records how many steps of training the files represent. A label gives
those files a name that is easy to recognize, such as `baseline`. The checkpoint table
shows both, and you can select the files with `TEAM@step:3000` or `TEAM@baseline`.
Leading zeros in a training step have the same numeric value. If a step selector matches
no checkpoint, the command exits with status 1 and points you to the checkpoint listing.
Use `TEAM@ID`, a unique identifier prefix of at least four characters, or `TEAM@latest`
for the newest kept checkpoint. A full checkpoint identifier selects that checkpoint. A label
can have the same spelling as a shorter identifier prefix, `latest`, or `step:3000`. When
both forms select the same checkpoint, the reference works. When they select different
checkpoints, the command exits with status 2 and gives up to five full references in a hint.
Use the full reference for the files you want. Use a label such as `baseline` to keep its
meaning as new checkpoints are added.

The `@` character separates a team from its checkpoint, so team names supplied when
registering or renaming a team must be free of `@`. A legacy team already named
`Harbour@baseline` can still share a reference with the `baseline` checkpoint of Harbour.
When both match, the command exits with status 2 and lists the team identifier and the full
checkpoint reference. Use the exact team identifier to select the team, or combine the
exact team and checkpoint identifiers to select the files.

The rating table shows the checkpoint identifier, label and
step where available. A recording keeps the explicit checkpoint reference in its
provenance, where `recording show` displays the digest. The result of `play --json`
gives the identifier, label and step under `left_checkpoint` and `right_checkpoint`.
The checkpoint listing also shows the three fields.

The first command adds the folder, archive or file at `PATH`, and the second lists the checkpoints
of the team. Both commands take the team identifier, a unique prefix or the team name by itself.
Use `team show TEAM@CHECKPOINT` to inspect the team entry for a specific checkpoint. When an at
sign introduces a checkpoint selector, specify the checkpoint after it. The command prints its
usage and exits with status 2 if the selector is missing. Each line of
the listing starts with the checkpoint identifier, and the checkpoint that plays is marked
`current`. Both commands take `--json`.

### The list of checkpoints

The file `cuju-team/checkpoints.json` lists them, oldest first.

```json
{
  "identity": 1,
  "current": "9f3c0a1b22d4e6f8",
  "checkpoints": [
    {"id": "9f3c0a1b22d4e6f8", "digest": "9f3c0a1b22d4e6f8...", "label": "step 1.2M",
     "step": 1200000, "added": "2026-11-18T12:00:00", "note": "", "files": 14,
     "bytes": 8123456, "control": {"players": 1, "assignment": "dynamic", "selection": [],
                                   "goalkeeper": "scenario", "action_set": "default"},
     "pinned": false}
  ]
}
```

Each checkpoint holds its identifier and full digest, a label, an optional training step, when it
was added, a note, the number of its files and their bytes, the control it uses and a pin. A team
of 0.1.0 has no such file and reads as one checkpoint, which the renderer writes the first time it
needs the list. A file that holds anything the renderer cannot read gives way to a list made from
the files that play, and an earlier checkpoint whose files are gone leaves the list. Files at the
top of the folder that change by hand, outside the renderer, count as a new checkpoint that starts
with the control of the one it replaces.

The page asks `GET /api/teams/ID/checkpoints` for the list, the newest first, with the limits of an
upload and the trust line. `GET /api/teams/ID/checkpoints/CHECKPOINT` answers one checkpoint.
`POST /api/teams/ID/checkpoints/CHECKPOINT` takes a JSON object with any of `label`, `note`,
`step`, `pinned` and `control`, and `current` set to `true`, which makes that checkpoint the one
that plays by swapping its files with the current ones. `DELETE` removes an earlier checkpoint with
its files, and the current one stays, since a team always plays one.

### Entrants

An entrant is the identifier of a team, which means its current checkpoint, or `team@checkpoint`,
which names an earlier one by its 16 characters. A match, a tournament, a rating run and an
evaluation take either. A team at an earlier checkpoint plays its own files, with the control that
checkpoint uses, and its results belong to that checkpoint alone. The header of a recording names
the checkpoint of such a side, and the header of the current one stays as it was. The built in AI
and the random entrant have no files and take no checkpoint.

The format `strict/1` measures, checks for links, digests and copies the files of the checkpoint
that an entrant names, so a tournament under it holds an earlier checkpoint to the same limits as
the current one. The academy benchmark names such a model by the team's name followed by the label of
its checkpoint, or by the first eight characters of the checkpoint's identifier when it has no
label, so two checkpoints of one team stand apart in its table.

### Control per checkpoint

New weights may drive a different number of players. Each checkpoint stores the control it uses,
and a new one starts with the control of the one before. The `control` of the manifest always
equals the control of the checkpoint that plays, so changing the control of the team changes it for
that checkpoint, and making another checkpoint play gives the team its control.

### Keeping checkpoints

A team keeps eight checkpoints or 500 MiB of them, whichever limit it reaches first. When a new
checkpoint passes either, the renderer drops the oldest checkpoint that is not pinned, with its
files, and repeats until the team fits. The checkpoint that plays and every pinned checkpoint stay.
The ratings of a dropped checkpoint stay in the tables. The limits are the settings `checkpoints`
and `checkpoint_mib` of the table `[teams]`.

```toml
[teams]
checkpoints = 8
checkpoint_mib = 500
```

### Exporting checkpoints

An export writes the checkpoint that plays at the top of the zip, so the file stays a Kaggle
submission. The earlier checkpoints and their list join it with `cuju arena team export ID
--all-checkpoints`, or with `all_checkpoints=true` on the export route. Importing such a zip gives
the new team the same list.

## The home scenario

A team names a size, five or eleven a side, and gains one home scenario, a scenario its size admits
and its control fits. The home scenario is the table that the team's page and cards show first. It
moves no evidence, since every scenario keeps its own ratings.

The scenarios a team can play are computed from its size and its control each time they are asked
for, never stored as a list. A full game of the other size is refused, and so is a scenario whose
players the control cannot drive, such as a game whose goalkeeper no agent can control when the
control gives the goalkeeper to the agent. The manifest stores the home scenario as `scenario` and
leaves it out when it equals the default of the size, `5_vs_5` or `11_vs_11_kaggle`, so a team that
changes its size takes the default of the new one.

A stored scenario that the team can no longer play, for example after a change of control or size,
falls back to the default of its size. The entry names `scenario` among the values it set aside,
which is the one quiet line of the team's cleaning report, and the old value stays in the manifest
under `kept.refused.scenario`.

## Versions

| File | Version | Written by | Mark |
| --- | --- | --- | --- |
| `cuju-team/team.json` | 1 | cuju render 0.1.0 | no `identity` key, the look inside |
| `cuju-team/team.json` | 2 | cuju-arena 0.1.0 | `"identity": 2`, the identity alone |
| `cuju-look/look.json` | 1 | cuju render 0.2.0 | `"look": 1` |

A manifest of a later version, with an `identity` of 3 or more, reads as far as this version
understands it, and a look file of a later version reads the same way. Every key it does not know
goes to `kept` and comes back at the next save, so a team from a newer release still plays here
with its name, its roster and its kit.
