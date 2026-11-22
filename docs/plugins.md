# Plugins

A plugin is code of your own that the arena runs at the places where people differ. A club ranks
its models with a rating method that suits its league, a course draws its tournaments with a
seeding of its own, and a lab picks the next match of a rating run by a rule it trusts. Each of
these places is a protocol that the arena publishes with a version, a small set of functions that
an implementation provides and the arena calls. Everything that sits behind such a protocol is
yours to write, and everything that must hold for results to be comparable stays with the arena.

This guide explains how to write and install plugins, how settings and requests choose them,
and how failures are reported. It describes the data each protocol receives, with worked
examples for rating, tournament stages and tiebreakers, and recording analysis. The arena and
renderer ship their standards through the same mechanism.

The renderer offers its look producer as an installed plugin. Its `render.look` provider
writes the standard `look` attachment so recordings keep the same presentation when
opened later. The arena can record a match and its gameplay attachments on its own.

## What a plugin is

A plugin offers named implementations of one or more kinds. A kind is a protocol, written with its
major number, such as `rating_method/1`. The major belongs to the protocol, so a later version of
a protocol never changes what a plugin written for an earlier one reads. The arena serves the
major that its version of this guide names, and a plugin that names another major is broken until
it is updated.

| Kind | An implementation provides | What it decides |
|---|---|---|
| `rating_method/1` | `fit(rows, anchor, options)` | how the results of a scenario become a rating, with its interval, for every model |
| `evidence/1` | `keep(rows, options)` | which of the results that the arena accepted count toward the ratings |
| `seeding/1` | `order(entrants, context)` | the order in which the entrants of a stage are seeded |
| `pairing/1` | `next_pairs(state, slots)` | which pair plays next in a phase that picks its matches as results arrive |
| `stage_type/1` | `options()`, `schedule(plan)`, `standings(plan, scheduled)`, `complete(plan, scheduled)` | how a stage of a tournament pairs its entrants, ranks them and ends |
| `tiebreaker/1` | `key(rows, options)` | how entrants that are level in a table are told apart |
| `moments/1` | `detect(recording)` | which frames mark moments in the catalog and search |
| `statistics/1` | `compute(recording)` | which named values describe a recording |
| `analytics/1` | `table(recording, options)` | which columns an analytics export writes |
| `attachment/1` | `produce(context)` | a file that a recording carries beside it, such as the values of a model on each step |

Each implementation has a name. The standards that ship with the arena are the plugin `builtin`,
and their names stand bare, such as `points`. Every other plugin has an identifier, and its names
are written with it, `<identifier>.<name>`, such as `goals.rating`. A setting, a tournament or a
route chooses an implementation by that name.

## What stays with the arena

A plugin reads and answers. These parts of the arena are fixed and no plugin changes them.

- The lockstep stepping of a match and the arena's own measure of the time each side takes.
- The exactness of the engine. A plugin reads states and frames and never writes one.
- The integrity of recordings. A recording is keyed by its digest, and nothing attaches itself in a
  way that changes a frame.
- The safety ceilings of the arena, which are constants in code.
- The integrity checks of the evidence. The arena drops a duplicate result, a match of one model
  against itself and a match that did not finish before any evidence rule runs.
- The messages, the strictness of the settings and the loopback address of the server.

A plugin runs with the permissions of the person who runs the arena, inside the arena's own
processes. Submissions are another matter. They are untrusted, they play in processes of their
own under the ceilings, and no plugin code ever runs inside a submission's process.

## Finding plugins

The arena finds a plugin in two ways, and both lead to the same declaration.

### Installed packages

A package that is installed with pip names its plugin module as an entry point of the group
`cuju.arena.plugins`. In `pyproject.toml` that reads as follows.

```toml
[project.entry-points."cuju.arena.plugins"]
goals = "goals_plugin"
```

The name of the entry point is the identifier of the plugin, and the value is a module. The module
holds `PLUGIN`, a dictionary that declares the plugin, and `register(registry)`, which hands over
the implementations.

```python
PLUGIN = {
    'id': 'goals',
    'requires': '>=0.1.0',
    'provides': {'rating_method/1': ['rating']},
}


def register(registry):
    registry.provide('rating_method/1', 'rating', GoalRating())
```

The standards of the arena are found this way as well. The arena's own `pyproject.toml` names its
module `cuju.arena._builtin_plugins` under the identifier `builtin`, and the registry loads that
module first by name, so a copy of the sources that pip has not installed finds the standards too.

### Folders

A folder `<home>/plugins/<id>/` holds a plugin that a person added without packaging it. The
command `cuju arena plugin add PATH` copies a folder there. The folder holds a file
`plugin.toml` and the module that it names, which is `plugin.py` unless `module` says otherwise.

```
plugins/goals/
  plugin.toml   the declaration, id, requires, provides
  plugin.py     register(registry), imported as a package of its own
  helper.py     any other file of the plugin, imported with from . import helper
```

```toml
id = "goals"
version = "1.0"
requires = ">=0.1.0"

[provides]
"rating_method/1" = ["rating"]
```

The module of a folder is imported as a package whose folder is the plugin folder, so the plugin
imports its other files with a relative import and no file of a plugin shadows a module of
another. The arena reads the folders again when a file in them changes, so an edited plugin takes
effect at the next use without a restart. When the modification time changes, the arena reads
the current source of the declared module, even if the edit keeps its size and falls within
the same second.

A plugin is Python that runs with your permissions. The command shows that line each time it adds
one, and every other place that adds a plugin shows it as well. Add plugins that you wrote or
whose authors you trust.

## The declaration

| Key | What it says |
|---|---|
| `id` | the identifier, lowercase letters, digits and hyphens, at most 40 characters, the name of the folder or of the entry point, and never `builtin` |
| `version` | the version of the plugin itself, shown by the command, optional |
| `requires` | the versions of cuju-arena the plugin needs, such as `">=0.1.0"` or `">=0.1,<0.4"`, optional |
| `provides` | a table of each kind with its major to the names that the plugin offers under it |
| `module` | for a folder, the file of the folder that holds `register`, `plugin.py` by default |

A name is lowercase letters, digits and underscores, at most 40 characters. The module hands each
implementation over with `registry.provide(kind, name, implementation)`, where the kind carries
its major, as `rating_method/1`. The registry checks that the declaration lists the name, that the
name comes once and that the implementation has every member of the kind as a callable.

## Choosing a plugin

A setting or a request names an implementation, and the arena resolves the name when it needs it.

| Where | The key | The standards |
|---|---|---|
| the settings | `[ratings] method`, the method that ranks a table and seeds a draw | `points`, `bradley_terry` |
| the settings | `[ratings] rule`, the evidence rule | `default` |
| the settings | `[ratings.run] pairing`, the pairing rule of a rating run on demand | `least_settled` |
| the settings | `[evaluate] pairing`, the pairing rule of an evaluation on demand | `least_settled` |
| a tournament | `seeding`, and the `seeding` of each stage after the first | `random`, `manual`, `rating`, `standing` |
| a tournament stage | `format`, the stage type | `knockout`, `double_elimination`, `league`, `groups`, `swiss`, `ladder`, `king_of_the_hill`, `gauntlet` |
| a tournament stage | `tiebreakers`, the ordered list of tiebreakers | `goal_difference`, `goals_for`, `goals_against`, `head_to_head`, `wins`, `away_goals`, `fair_play`, `buchholz`, `lots` |
| the arena settings | `[search] detectors`, the detectors used by the catalog and search | `cuju`, `value_drop`, `surprise`, `entropy` |
| the Python API | `statistics(identifier, name=...)`, the provider of named statistics | `match`, `heat`, `signals` |
| the renderer settings or the export route | `[export] tables` or the `table` query parameter | `tracking`, `events`, `annotations`, `metrica`, `secondspectrum` |

A name that no plugin offers is refused with the names that are available. A name of a plugin that
is broken or turned off is refused with the reason.

The key `disabled` of `[plugins]` lists the identifiers of the plugins that are turned off. A plugin on the
list offers nothing and no context can choose it, and the tables that need it say so. The
standards stay on whatever the list says.

```toml
[plugins]
disabled = ["goals"]
```

## When a plugin breaks

There are two states, and each shows in `cuju arena plugin list`.

A plugin is broken when the arena cannot use it at all. Its declaration does not read, its
`requires` fails against the arena's version, one of its kinds names a major that the arena does
not serve, its module raises while it imports or in `register`, or `register` leaves a declared
name out. A broken plugin offers nothing, and its message tells why. The arena starts and works
without it.

A plugin is failing when its code raised while it ran, or when it gave an answer that the arena
refused, such as a rating that is not a number or an order that leaves an entrant out. The arena
marks it failing with the message, and the call that was running ends with that message. The match
processes and the engine are not touched. The mark stays until the plugin is replaced or removed.
A failing plugin can still be chosen, so a fixed version works at once, and the mark tells which
plugin to look at.

A standard of the arena that raises is a defect of the arena and passes through as it is.

## The command

`cuju arena plugin` keeps the plugins.

| Command | What it does |
|---|---|
| `plugin list` | lists every plugin, the built-in one first, with its state and how many names it offers, with `--json` for the full records |
| `plugin show ID` | shows one plugin, its version, what it needs, what it offers and why it is broken or failing |
| `plugin add PATH` | adds a folder that holds `plugin.toml`, after the trust line, with `--replace` to replace a plugin of the same identifier |
| `plugin remove ID` | removes the folder of a plugin |

Shell completion for `plugin show ID` offers `builtin` and identifiers from installed plugin
entry points. Generate a new script with `cuju completion SHELL` after installing a plugin.
Use `plugin list` to find the identifiers of plugins added from folders in your Arena home.

An add reads the plugin once from a copy before it keeps it, which runs its module, so a plugin
that is broken is never added. The identifier of an installed package belongs to pip, and the
command asks you to uninstall the package to change it.

The renderer also answers `GET /api/plugins` with one JSON record per plugin. The record has
the plugin identifier, its declared version or `null`, its source, and `provides`, which maps
each protocol and its major to the names the plugin declares. The `state` is `available`,
`broken`, `disabled`, or `failing`. The `broken` and `failing` fields carry details when
present, and the `disabled` field records whether the settings turn the plugin off.

## The protocols

### Rating methods

A rating method turns the results of one scenario into a rating for every model of its table. The
arena calls

```python
fit(rows, anchor, options)
```

- `rows` is a list of `(home, away, score)`, one for each result that counts, in the order the
  results were kept. `home` and `away` are model identifiers, such as `rex@3fa9c1d200a1b2c3` or
  `builtin:0.60`, and `score` is the list of the goals of the home side and of the away side.
  The list is a copy, and a method that depends on the order has the order the results were kept.
- `anchor` is the identifier of the model that stands still. It is the built-in AI at the
  difficulty that `[ratings] anchor` names.
- `options` is a mapping of `confidence`, the confidence of the intervals, `quantile`, the two
  sided quantile of the normal distribution at that confidence, and `anchor_rating`, 1000.

The method answers a mapping from each model identifier to `{rating, low, high, deviation}`. The
keys are every model that appears in the rows and the anchor. The anchor stands at `anchor_rating`
with a deviation of 0, since every table reads against it. `low` and `high` may be left out, and
the arena then takes the rating less and plus the deviation times the quantile. The numbers are
finite, the deviation is 0 or more and `low` is at most the rating, which is at most `high`. The
arena rounds all four to two decimals.

A table always holds the rating of every model by `points` and by `bradley_terry`, so a view
that compares them keeps working. When `[ratings] method` names a plugin, its rating stands as
`rating`, `low`, `high` and `deviation` of each row, ranks the table and seeds a draw, and the
row also holds it under the name of the method. The table says in `methods` which plugin the
method comes from.

### Evidence rules

The arena records every result once, a duplicate, a match of one model against itself and an
unfinished match are dropped when it is recorded, and an evidence rule then chooses which of the
recorded results count toward a table. It calls

```python
keep(rows, options)
```

- `rows` is a list of the recorded results of one scenario, each a mapping with `scenario`,
  `home` and `away`, each a side with its `key` and `checkpoint`, `score`, `source`, and where
  they exist `ref`, `phase`, `time`, `seed`, `recording` and `actions`. The rows are read only,
  and a rule that wants to change one makes a copy for itself.
- `options` is a mapping of `sources`, the list that `[ratings] sources` names.

The rule answers the rows that count, taken from the rows it received, in the order it received
them. It can drop rows and cannot add a row or change one, and an answer that does is refused. The
rule `default` keeps the rows whose `source` stands in `sources`, which is the whole of what a
table did before the rule was a protocol.

### Seeding rules

A seeding rule orders the entrants of a stage. A tournament names its rule in `seeding`, and each
stage after the first names one in its own `seeding`. The arena calls

```python
order(entrants, context)
```

- `entrants` is a list of the entrants of the stage, each the mapping of the tournament's
  configuration with its `key`, its name and its `rating`, in the order they arrive. For the
  first stage that is the order they were entered, and for a later stage it is the order of the
  standings of the stage before.
- `context` is a mapping of `stage`, the index of the stage from 0, `seed`, the seed of the
  tournament, and `rng`, a `random.Random` that the arena seeds for this stage from the seed of
  the tournament. A rule that shuffles uses `rng`, so a draw is the same on every machine.

The rule answers a list of positions in `entrants`, each once, the first position being the top
seed. The standards are `random`, which shuffles with `rng`, `manual`, which keeps the order as it
comes, `rating`, which puts the highest rating first and keeps level entrants in their order, and
`standing`, which keeps the order of the standings and is the name a later stage uses for it.

### Pairing rules

A phase that picks its matches as results arrive, a rating run on demand or an evaluation on
demand, asks a pairing rule for each next match. The arena calls

```python
next_pairs(state, slots)
```

with `slots` set to 1, since each pick reads exactly the results that its match number may know.
The rule answers a list of at most `slots` pairs, each `(home, away)` of side keys, or an empty
list when it has nothing more to play. A pair is one that may meet, and where the phase gave its
pairs with a home side the home side is binding.

`state` is a read only view with these attributes.

| Attribute | What it holds |
|---|---|
| `number` | the number of the match to pick |
| `sides` | the keys of the sides |
| `fixed` | the keys whose rating is given, which stay where they are |
| `pairs` | the pairs that may meet, each with its home side first |
| `explicit` | whether the home sides of the pairs are binding |
| `limit` | the most meetings of one pair, or `None` |
| `anchor`, `anchor_matches` | the side that every measured side meets, and how many matches against it |
| `ratings`, `matches` | the rating and the match count of each side after the known results |
| `games`, `homes` | the matches each side has started, and those as home side |
| `rank` | the place of each side in the order that the seed draws, which settles ties |

The methods are `meetings(a, b)`, `deviation(key)`, `ratio(a, b)`, `unsettled(a, b)` and
`owed(a, b)`. The standard `least_settled` is a few lines of these, and its source in
`_evaluate/schedule.py` is the best place to start a rule of your own. The arena keeps the
determinism of a phase, so a rule that draws numbers draws them from a generator it seeds itself
from something in the state, never from the clock.

### Stage types

A stage type is the way one stage of a tournament works. It decides which matches the stage
plays, in what order they come and who plays them, how the entrants stand along the way and when
the stage is over. The eight types that ship, `knockout`, `double_elimination`, `league`, `groups`,
`swiss`, `ladder`, `king_of_the_hill` and `gauntlet`, are written this way, so each is an example
of the protocol. A stage names its type in `format`, and a type of a plugin is written as the
plugin's identifier, a dot and its name, such as `finale.league_final`.

The arena replays a tournament from its configuration and its results every time it needs a view,
so a stage type holds no memory of its own between evaluations. Each evaluation makes a new plan,
calls the type, and keeps what it answered. The arena calls four members, and a fifth is optional.

```python
options()
schedule(plan)
standings(plan, scheduled)
complete(plan, scheduled)
planned(settings, count)  # optional
```

`options()` answers the settings of the type, a mapping from each option's name to its rules.
The name is lowercase letters, digits and underscores, at most 30 characters, and is none of the
settings that every stage has, such as `legs`. The rules are a mapping with a `default` and,
optionally, a `type` of `integer`, `number`, `boolean` or `string`, `choices`, a list, and
`minimum` and `maximum`, which a number needs both of. A stage writes an option beside its other
settings, the arena checks the value against the rules, and the plan holds it in `plan.settings`.
A tournament's `defaults` may name an option when every stage of the tournament has a type that
declares it.

`schedule(plan)` is the heart of a type. It registers the matches that the entrants it knows
allow, and answers `{'rounds': [...]}`, where each round has a name and a list of fixtures. It
runs every time the arena evaluates the tournament, and the results of the matches already played
are in the plan, so a round that depends on earlier results is built from them. The key `bracket`
may stand beside `rounds`, `'knockout'` or `'double'`, to ask the page for the shape of a bracket.

`complete(plan, scheduled)` answers whether the stage is over. It is true only when every fixture
of `scheduled` is done. The stage then ends, and the next stage seeds from its ranking.

`standings(plan, scheduled)` answers `{'tables': [...], 'ranking': [...]}`. The tables are what
the page shows, each made by `plan.standing`. The ranking is the order of every entrant of the
stage, the best first. It is `None` until `complete` is true and a list of every entrant once it
is. The first entrants of the ranking, as many as the stage's `advance`, go to the next stage.

`planned(settings, count)` is optional. It answers how many matches a stage of `count` entrants
plays by its plan, so the page counts the matches of a tournament from the start. A type without
it plans `count - 1` matches for each leg.

#### The plan

Every call gets the plan of the stage being evaluated.

| Member | What it is |
|---|---|
| `plan.index` | the number of the stage, from 0 |
| `plan.settings` | the stage's settings in full, with the options of the type, a copy that the type may change as it likes |
| `plan.seeds` | the entrants of the stage in seed order, each an index into the tournament's entrants, a copy |
| `plan.seed` | the seed of the tournament |
| `plan.state` | a dictionary for what the type carries from one of its calls to the next in this evaluation |
| `plan.match(name, label, home, away)` | registers one match and answers its result, or `None` while it is unplayed |
| `plan.fixture(name, label, home, away)` | registers a match as `match` does and answers a fixture for a round |
| `plan.tie(name, label, first, second, legs=None, holder=None, section=None)` | plays a tie of several legs, settled by the stage's `settle`, and answers the tie |
| `plan.later(label)` | stands in for a side that an earlier tie decides, to pass to `tie` |
| `plan.round(label, fixtures, **fields)` | answers a round of fixtures or ties |
| `plan.table(members, name='table', extra=None)` | answers the table of some entrants from the stage's matches, ordered by points and then by the stage's tiebreakers |
| `plan.standing(label, rows, qualify=None)` | answers a table for `standings`, with `qualify` of its first rows advancing |
| `plan.rng(*parts)` | answers a `random.Random` that the tournament seed, the stage and the parts fix |

A name is 1 to 24 lowercase letters, digits and hyphens. The key of a match that `match` or
`fixture` registers is the stage, a slash, an `m` and the name, such as `0/mfinal`. The key of a
tie that `tie` plays is the stage, a slash, a `t` and the name, and its matches add the leg, and
its replays and deciding matches add their own, as the ties of the standards do. The arena draws
each match's seed from the tournament seed and its key, so the same tournament gives the same
seeds on every machine. Labels are text. Entrants are the indexes that `plan.seeds` holds. A bye
is `None` in a tie, and the other side wins unplayed.

The plan keeps a stage honest. It refuses an entrant that the stage does not hold, a match of an
entrant against itself, a name that is not shaped as above, a tie with no legs or a holder who is
not one of its sides, and a stage that registers more than 100,000 matches. A refusal marks the
plugin failing with its message.

The arena checks every answer. The rounds are a list of at most 1,000, with at most 10,000
fixtures in all, each fixture as the plan makes it, with its id, its state and keys of matches that
the stage registered. A fixture's sides and winner are entrants of the stage. `complete` is a
bool and cannot be true while a fixture is open. The tables are at most 64, each as `standing`
makes it, with rows as `table` makes them. The ranking holds each entrant of the stage once.
An answer that does not fit marks the plugin failing and ends the evaluation with its message.

The arena hands over copies, and copies what a type answers before it keeps it, so a plugin that
holds on to a plan or an answer changes no tournament.

### Tiebreakers

A tiebreaker tells apart entrants that are level on points so far. A stage lists its tiebreakers
in order, and lots from the tournament seed always come last. The arena sorts the entrants by
points, and each group that stays level goes to the first tiebreaker, then each group that is
still level goes to the next. The arena calls

```python
key(rows, options)
```

- `rows` is a mapping from each level entrant to its row, with `entrant`, `played`, `won`,
  `drawn`, `lost`, `for`, `against`, `difference`, `points`, `away_goals`, `yellow`, `red`,
  `fair_play`, `byes` and `buchholz`. The rows are copies.
- `options` is a mapping of `entries`, the matches of the stage played among the level entrants,
  each with `home`, `away`, `score` and `cards`, `points`, the stage's points for a win, a draw and
  a loss, `seed`, the seed of the tournament, and `purpose`, a text that names the table, which a
  tiebreaker that draws numbers puts in the seed of its own generator.

The tiebreaker answers a mapping from each level entrant to a sort key. The key is a finite
number or a tuple of up to eight, the same shape for every entrant. The higher key stands above,
and equal keys stay level for the next tiebreaker. Each entrant of `rows` has a key and no other
entrant does.

The nine standards are `goal_difference`, `goals_for`, `goals_against`, which reads fewer as
better, `head_to_head`, the table of the matches among the level entrants alone, `wins`,
`away_goals`, `fair_play`, `buchholz`, the points of the opponents met, and `lots`.
### Moments

A detector implements `detect(recording)`. The recording has a `header`, a tuple of raw
`frames`, a mapping of `sections` from tags to bytes, decoded `annotations`, and an
`actions` array when the recording contains actions. The mapping also gives the `ANNO` and
`GRID` bytes from supported annotation and overlay attachments. For a recording of positions, each frame
has the same layout as a full frame, with the root positions filled in and the remaining joints
set to zero. The recording format guide describes these fields. [Recording format](recording-format.md)

Return a list of dictionaries. Each has a `kind` in lowercase letters, digits and underscores,
beginning with a letter, and a `step` that gives the position of the frame, counting from zero.
A moment can also give a `team`, 0 for left and 1 for right, a `player` as an index in the
header lineup, and a `data` dictionary of JSON values. The arena supplies the identifier,
qualified kind, time, clock and score from the recording. When a player is given, the team
defaults to that player's team.

The standard `cuju` detector finds the moments of play. The standards `value_drop`, `surprise`
and `entropy` answer their respective signal queries using the summary of the recording's
annotations. The search thresholds apply when the query runs. The `detectors` setting selects
the detectors available to the catalog and search. A detector that you select
contributes its moments to the catalog, and search can find them by qualified kind.

For example, a folder plugin named `scout` can mark every frame that carries a high pressure
annotation. Declare its detector in `plugin.toml`.

```toml
id = "scout"
version = "1.0"

[provides]
"moments/1" = ["pressure"]
```

The module reads a value named `pressure` from each annotation and includes the measured value
with the moment.

```python
class Pressure:
    def detect(self, recording):
        return [
            {
                'kind': 'pressure',
                'step': note['f'],
                'player': note.get('p'),
                'data': {'value': note['v']['pressure']},
            }
            for note in recording.annotations
            if 0 <= note['f'] < len(recording.frames)
            and note.get('v', {}).get('pressure', 0) >= 0.8
        ]


def register(registry):
    registry.provide('moments/1', 'pressure', Pressure())
```

After adding the plugin, include it alongside the standards in the arena settings. Search for
`kind:scout.pressure` to find its moments.

```toml
[search]
detectors = ["cuju", "value_drop", "surprise", "entropy", "scout.pressure"]
```

### Statistics

A statistics provider implements `compute(recording)`. It receives the same recording as a
detector, with the standard moments of play in `recording.moments`. It returns a dictionary
whose keys name the statistics. Each entry has a text `label`, a text `unit`, and a `value`
that can be represented as JSON. Use an empty string for a unitless value. Numbers must be
finite. The arena caches successful answers by recording digest and provider, and refreshes
them when the installed plugin files or the section attachments change.

The standard `match` provider describes play, including goals, passes and possession. The
`heat` provider counts the frames spent in each part of the pitch. The `signals` provider
summarizes the annotated series, unlikely actions and entropy. Calling `statistics(identifier)`
gives the usual match statistics. Passing `name` gives the named values from a provider.

For example, the `tagcount` plugin measures how many annotation records a recording holds.
Declare the provider as follows.

```toml
id = "tagcount"
version = "1.0"

[provides]
"statistics/1" = ["coverage"]
```

The module labels the value and provides its unit so a caller can display it alongside values
from other sources.

```python
class Coverage:
    def compute(self, recording):
        return {
            'annotations': {
                'label': 'Annotation count',
                'unit': 'records',
                'value': len(recording.annotations),
            },
        }


def register(registry):
    registry.provide('statistics/1', 'coverage', Coverage())
```

With the plugin installed, ask for the provider by name and read its labelled value.

```python
from cuju.arena import statistics

values = statistics('sample-5v5', name='tagcount.coverage')
print(values['annotations'])
```

### Analytics

An analytics implementation provides `table(recording, options)`. The recording is a set of
decoded frame arrays with its header, lineup, annotations, actions and overlays. It has the
same fields as the result of `cuju.arena.frames`. The options give the output name in
`recording`, the coordinate system in `coordinates`, and the frame interval in `every`. Use
those settings when they affect the rows of your table. [Analytics export](https://github.com/cuju-football/render/blob/main/docs/analytics-export.md)

Return a sequence of columns, each a tuple of the column name, the type and a list of values.
Column names are unique and every column has the same length. The type `int` takes integers,
`text` takes strings or `None`, and `json` takes JSON values with finite numbers. A whole
number from 0 through 15 as the type gives decimal precision for a numeric column. A numeric
column can use `None` for a missing measurement. The renderer writes the table with its CSV,
JSON Lines or Parquet writer. The standard Metrica and SecondSpectrum pairs have their own
fixed file formats.

For example, a plugin named `review` can export the frame numbers that carry annotations.
Declare the table in `plugin.toml`.

```toml
id = "review"
version = "1.0"

[provides]
"analytics/1" = ["annotated_frames"]
```

The module writes a row per distinct annotated frame that falls on the requested frame interval.

```python
class AnnotatedFrames:
    def table(self, recording, options):
        every = options['every']
        steps = sorted({note['f'] for note in recording.annotations if note['f'] % every == 0})
        return [
            ('frame', 'int', steps),
            ('clock_ms', 'int', [int(recording.clock[step]) for step in steps]),
        ]


def register(registry):
    registry.provide('analytics/1', 'annotated_frames', AnnotatedFrames())
```

After adding the plugin, choose the table and run the export as usual. This example writes
`review.annotated_frames.jsonl` alongside `metadata.json`, which records the plugin version.
You can also ask for the custom table by its qualified name in the export route.

```toml
[export]
tables = ["review.annotated_frames"]
format = "jsonl"
every = 10
```

### Attachments

A recording holds the game alone, and what a model or a viewer adds lies in attachments beside it,
as [Recording format](recording-format.md#attachments) describes. As a recording closes, the
arena asks every producer of the kind `attachment/1` for the body of its attachment.

```python
produce(context)
```

`context` holds `path`, the recording file, `header`, its header as the file holds it, `digest`,
its key, `presentation`, what the header held beside the game, `annotations` and `overlays`. The
producer answers the bytes of the body, or `None` when the recording has nothing for it. The
arena writes the body as the attachment of the producer's qualified name, so the producer `heat`
of the plugin `acme` writes `<id>.acme.heat.cja`, keyed by the digest of the recording. An
implementation may hold `version`, the version of the layout of its body, 1 by default, and
`writer`, the program that the file names, by default the plugin and its version.

The standards are `annotations` and `overlays`, which write the annotations and the overlays that
the recorder took. A producer that raises leaves its attachment out and is marked failing, and
the recording and its other attachments are written as they are. A producer never changes the
recording, whose frames and digest stay as the arena wrote them.

## A worked example, a rating method

The folder `docs/plugins/goal-rating/` holds a complete plugin that ranks models by the goals
they score and concede. A model starts from the average goal difference of its matches, the anchor
is set to the rating of the table, and every other model reads against it, 200 points for each
goal of difference per match. The more matches a model has, the smaller its deviation.

The declaration is the file `plugin.toml`.

```toml
id = "goals"
version = "1.0"
requires = ">=0.1.0"

[provides]
"rating_method/1" = ["rating"]
```

The module is the file `plugin.py`.

```python
import math

SCALE = 200.0


class GoalRating:
    def fit(self, rows, anchor, options):
        gaps = {anchor: []}
        for home, away, score in rows:
            difference = score[0] - score[1]
            gaps.setdefault(home, []).append(difference)
            gaps.setdefault(away, []).append(-difference)
        mean = {key: sum(gaps[key]) / len(gaps[key]) if gaps[key] else 0.0 for key in gaps}
        found = {}
        for key in gaps:
            found[key] = {
                'rating': options['anchor_rating'] + SCALE * (mean[key] - mean[anchor]),
                'deviation': 0.0 if key == anchor else SCALE / math.sqrt(len(gaps[key]) + 1),
            }
        return found


def register(registry):
    registry.provide('rating_method/1', 'rating', GoalRating())
```

The plugin leaves `low` and `high` out, so the arena draws the interval from the deviation. It
puts every key of the rows, and the anchor, in its answer, and it holds the anchor at
`anchor_rating` with a deviation of 0 by reading every other model against the anchor's own mean.

Add the folder, choose the method and read the table.

```sh
cuju arena plugin add docs/plugins/goal-rating
cuju arena rating table --scenario 5_vs_5 --method goals.rating
```

The first command prints the trust line and the plugin as it reads. The second prints the table of
the scenario ranked by the new method. To make it the method of every table, set it in a settings
file.

```toml
[ratings]
method = "goals.rating"
```

The same module packages as an installable plugin. Put `plugin.py` in a package, add the `PLUGIN`
dictionary to it, and name it under the entry point group in `pyproject.toml` as shown in
[Installed packages](#installed-packages). After `pip install` the plugin is found with no
command, and `cuju arena plugin list` shows it with the source `entry point`.

To see what a plugin did to a run, read the plugin's row in `plugin list` and the `methods` entry
of the table, which names the plugin that produced the ranking.

## A worked example, a stage type and a tiebreaker

The folder `docs/plugins/league-final/` holds a plugin that offers a stage type and a tiebreaker.
The stage type `league_final` plays one round robin and then a final between the first two of the
table. The tiebreaker `clean_sheets` puts the entrant with more matches without a goal conceded
above a level one.

The declaration offers one name under each kind.

```toml
id = "finale"
version = "1.0"
requires = ">=0.1.0"

[provides]
"stage_type/1" = ["league_final"]
"tiebreaker/1" = ["clean_sheets"]
```

The module is `plugin.py`.

```python
class LeagueFinal:
    def options(self):
        return {'final_name': {'type': 'string', 'default': 'Final'}}

    def schedule(self, plan):
        seeds = plan.seeds
        pairs = [(a, b) for place, a in enumerate(seeds) for b in seeds[place + 1 :]]
        fixtures = [
            plan.fixture(f'g{number}', f'Match {number}', home, away)
            for number, (home, away) in enumerate(pairs, start=1)
        ]
        rounds = [plan.round('League', fixtures)]
        # The table is read before the final, whose matches count in the stage's matches too.
        plan.state['table'] = plan.table(seeds)
        plan.state['final'] = None
        if all(fixture['state'] == 'done' for fixture in fixtures):
            first, second = (row['entrant'] for row in plan.state['table'][:2])
            name = plan.settings['final_name']
            final = plan.tie('final', name, first, second)
            rounds.append(plan.round(name, [final]))
            plan.state['final'] = final
        return {'rounds': rounds}

    def complete(self, plan, scheduled):
        final = plan.state['final']
        return final is not None and final['state'] == 'done'

    def planned(self, settings, count):
        return count * (count - 1) // 2 + 1

    def standings(self, plan, scheduled):
        table = plan.state['table']
        ranking = None
        if self.complete(plan, scheduled):
            final = plan.state['final']
            ranking = [final['winner'], final['loser']]
            ranking += [row['entrant'] for row in table if row['entrant'] not in ranking]
        return {'tables': [plan.standing('League', table, 2)], 'ranking': ranking}


class CleanSheets:
    def key(self, rows, options):
        found = dict.fromkeys(rows, 0)
        for match in options['entries']:
            home, away = match['score']
            if away == 0:
                found[match['home']] += 1
            if home == 0:
                found[match['away']] += 1
        return found


def register(registry):
    registry.provide('stage_type/1', 'league_final', LeagueFinal())
    registry.provide('tiebreaker/1', 'clean_sheets', CleanSheets())
```

Before any match is played the stage schedules the league alone, and `complete` is false. Once
every fixture of the league is done, the next evaluation reads the table, takes the first two
entrants and plays the final through `plan.tie`, which settles a level final by the stage's
`settle`, a replay and then lots by default. When the final is done, `complete` is true and
`standings` answers the ranking, the winner, the loser and then the order of the table. The table
is read before the final is registered because the final's matches are among the stage's matches
too, and a table made after them would count them.

Add the folder and name the stage type and the tiebreaker in a tournament.

```sh
cuju arena plugin add docs/plugins/league-final
```

```json
{
  "name": "League and final",
  "entrants": [
    {"kind": "builtin", "ability": 0.2},
    {"kind": "builtin", "ability": 0.4},
    {"kind": "builtin", "ability": 0.6},
    {"kind": "builtin", "ability": 0.8}
  ],
  "defaults": {"scenario": "5_vs_5"},
  "stages": [
    {
      "format": "finale.league_final",
      "final_name": "Grand final",
      "tiebreakers": ["finale.clean_sheets", "goal_difference"]
    }
  ]
}
```

The tournament records `{"name": "finale", "version": "1.0"}` under `plugins` in its file. The
Tournaments page shows the stage like any other, as rounds of fixtures and a table with the first
two rows marked as qualifying.

## Files that a plugin made

A tournament, a rating run or a table that a plugin shaped records the qualified name of the
plugin's implementation, such as `goals.rating`, and beside it the plugin's name and version as
`{"name", "version"}`, the version `null` when the plugin declares none.

| File | Where it records the plugins |
|---|---|
| a tournament that uses a stage type, a tiebreaker or a seeding rule of a plugin | `plugins` of its configuration, a list ordered by name |
| the ratings that seeded a tournament by the method of a plugin | `plugins` of its `elo` field, beside `method` |
| a rating run whose method, evidence rule or pairing rule comes from a plugin | `plugins` of its document, beside `settings` |

A file that uses the standards alone has no such list. A later read without the plugin still shows
the stored values, read only. The arena does not check a stored name against what is installed, so
a file stays readable when its plugin is gone. A tournament keeps the view of its last write for
this, and opens stopped with a line that names the plugins it needs. It plays nothing, awards no
match and adds no legs until the plugins are back, and it can still be cancelled and removed. A
plugin at another version than the stored one counts as available, and the file keeps the version
that made it.

## Testing a plugin

A plugin is plain Python, so the implementation tests without the arena. Call `fit` with a list of
rows and read the mapping. To test it inside the arena, put the folder in a temporary home and add
it.

```python
import os
from cuju.arena import plugins

os.environ['CUJU_RENDER_HOME'] = '/tmp/arena-test-home'
found = plugins.add('docs/plugins/goal-rating')
assert found.broken is None
method = plugins.get('rating_method', 'goals.rating')
```

`plugins.call` runs a member the way the arena does, with the failure marks, and `plugins.refresh`
makes the arena read the folders again after a test changes them.
