# Tournaments

A **tournament** plays many matches between many entrants and crowns a champion. Its entrants are
registered teams, the built in AI at chosen abilities, each a named entrant of its own, and people,
so agents and people take part in the same tournament on equal terms. The matches of agents play
headless in processes of their own, as requested matches do, and faster than real time. A match
with a person waits for that person, who plays it on the Play page when its turn comes. Every match
becomes a recording of the library, which opens from the tournament.

A tournament is a chain of one to eight **stages**. The first stage takes every entrant, and each
later stage takes those who advance from the stage before it, seeded by their standing there, at
random from the seed, or by rating. The tournament keeps going while results come in, so a stage
schedules each match as soon as both its sides are known, and the next stage starts once the
stage before it has ended. Tournaments take from 2 to 256 entrants.

## Entrants and their names

Every entrant carries a name of its own, which the brackets, tables, fixtures, recordings and the
trophy ceremony show. A registered team may enter the same tournament more than once, as a club
enters its first team and its reserves, or as a policy plays against an earlier copy of itself.
Each such entrant plays the same team of the registry under its own name, keeps its own place in
the draw, its own row in every table and its own path through a bracket, and two of them may meet
like any other pair of entrants.

Names are unique within a tournament. Two names that differ only in letter case or in the spaces
around and between their words count as one, so Rex FC, rex fc and REX FC with a space before
it all name the same entrant. A name typed for an entrant, on the page, with the option `--as` of
the command line or in a configuration file, that another typed name already holds is refused
with a message that names both entrants, in the language of the page, and in English on the
command line. An entrant without a typed name takes the name of its
team, of the built in AI at its ability or of its kind, and when another entrant already holds that
name it takes the first free number from 2, so a team entered three times plays as Rex, Rex 2 and
Rex 3. Such a numbered name steps around every typed name, wherever the typed one stands in the
list, so beside a person named Rex 2 the team's second entrant becomes Rex 3. An entrant at an
earlier checkpoint of its team, `rex@3fa9c1d200a1b2c3`, takes the name of its checkpoint after
the team's, its label such as Rex step 2M, else its step, else the first 8 characters of its id,
so two checkpoints of one team read apart by what they are.

A match between two entrants of one team is a match of that team against itself, as two different
teams that share a policy play. Each side loads its own copy of the submission, the recording names
the two sides by their entrants, and the right side wears its away kit, the colors of its shirt and
shorts swapped with its trim, so the two sides look apart on the pitch, in the fixture and in Watch.
Its goalkeeper takes a contrasting color too. A policy that fails in such a match is named by the
entrant whose side failed.

## Abilities

The **ability** of a side, a number from 0 to 1, is how well the built in AI plays the players it
controls on that side, GRF's team difficulty. It steers every player of a built in AI entrant, and
it steers the players that a registered team or a person leaves to the built in AI, such as the
goalkeeper of a team whose agents control the outfield, the players beyond those its agents
control, or the teammates of a person. So the ability matters for every kind of entrant, and every
entrant may carry one of its own, which its side plays with in each of its matches.

A tournament may set a **standard ability**, which every entrant and every stage without an ability
of their own play with. A match gives each side its ability in this order, the strongest first.

1. The entrant's own ability.
2. The ability of the stage, or of the tournament's `defaults`, which reach every stage.
3. The tournament's standard ability.
4. The scenario's own ability, when none of the above is set.

So a league whose standard is 0.8 plays every side at 0.8, a final whose stage sets 1.0 plays its
sides at 1.0, and an entrant set to 0.3 plays at 0.3 in every stage. Two entrants of one team may
carry different abilities, as a first team and a weaker reserve side, and they stay two entrants
with names of their own. The built in AI at an ability keeps its name, such as Built-in AI 0.50,
and its rating, so a tournament of an earlier version loads and plays as it did. The provenance
of every match keeps the ability that the tournament gave each side, home first, beside the
ability that each side played with.

The level of the built in AI is its **difficulty** in the engine and in Google Research
Football, and `difficulty` is the name that Python, the command line and the configuration files
take for it. `ability` is its earlier name and reads the same wherever a tournament, a stage, an
entrant or a preset takes one, as does `--difficulty` on the command line. A
mapping that names both with two values is refused. The files of tournaments and the answers of
the server keep the field `ability`, which the page reads.

## Stage types

Each stage plays one of eight stage types, or a stage type that a [plugin](plugins.md#stage-types)
adds, which a stage names as the plugin's identifier, a dot and the name, such as
`finale.league_final`. The eight that ship are the plugin `builtin` through the same mechanism, so
a stage type of a plugin plays, ranks and shows in the Brackets view as they do, and its matches
carry keys, seeds and results as any other match does.

- **Knockout.** Single elimination, each tie between two entrants, the winner going on. A bracket
    whose entrant count is no power of two is filled in one of two ways, chosen in the stage.
    Byes for the top seeds fill the bracket up to the next power of two, so the best seeds start
    in the second round. A **play-in round** brings the count down to the power of two below it,
    the lowest seeds playing each other for the last places of the bracket. The bracket size may
    also be set by hand within the range that either way allows. A knockout may stop when a set
    number of entrants remain, a power of two, which makes it a qualifying stage of a chain, and
    may add a third place match between the losers of the semi-finals. Seed one meets the lowest
    seed in the first round, and the best seeds meet as late as the bracket allows.
- **Double elimination.** A winners bracket and a losers bracket. An entrant that loses in the
    winners bracket drops into the losers bracket, and leaves the tournament after a second
    defeat. The winners of both brackets meet in the grand final. When the winner of the losers
    bracket wins it, both have lost once, and a deciding match follows unless the stage turns it
    off. Byes fill the bracket.
- **League.** A round robin, every entrant meeting every other. The number of cycles sets how
    many times each pair meets, once for a single round robin, twice for home and away, three
    times or more beyond that, with the home side alternating from cycle to cycle. The legs of a
    league set how many matches each meeting holds, played back to back with the venue in turn,
    and each counts in the table on its own. An odd count of entrants gives one entrant a rest on
    each matchday.
- **Groups.** The entrants are drawn into groups, and each group plays a league. The stage sets
    the number of groups, or a group size from which the number follows, or the size of each group
    by hand, so groups of different sizes are allowed. The draw puts the seeded entrants into
    **pots**, any number of them, as even as the count allows, the best seeds in the first pot,
    shuffles each pot by the tournament seed and deals its entrants one at a time to the next group
    with room. One pot for each place of a group gives the classic draw of one seed in each group,
    and a single pot gives a draw at random. The **qualifiers** of each group go on, a count for
    every group or one count for each group, and the stage may add the best entrants of one place
    across the groups, such as the four best third places, or as many as fill a bracket of the
    next power of two. Entrants of the same place compare across groups by points, goal difference,
    goals and wins for each match played, which keeps groups of different sizes fair, and then by
    lots. The qualifiers go on group winners first, then runners up and so on, so a knockout seeded
    by standing pairs winners with runners up.
- **Swiss system.** A set number of rounds, by default the rounds that a knockout of the same
    entrants would take. The first round pairs the seeds in order. Each later round orders the
    entrants by the table so far and pairs each with the best placed entrant it has not met. An
    odd count gives a bye to the lowest entrant without one, worth a win, a draw or nothing as the
    stage sets. A bye adds its points and counts in the table's Byes column, apart from the
    matches played, won and drawn. Every pair meets at most once. Such a pairing always exists
    while each entrant has met fewer than half the field, so a Swiss stage plays at most half as
    many rounds as it has entrants, rounded up.
- **Ladder.** The entrants stand on a ladder in seed order. In each round, neighbours meet, the
    first and second, the third and fourth and so on, and in the next round the second and third,
    the fourth and fifth, in turn. A challenger who wins takes the higher place, and on a draw the
    holder keeps it unless the stage settles draws as ties. After as many rounds as entrants, the
    default, the ladder sorts any order of strength.
- **King of the hill.** The top seed starts as king, and every other entrant in turn
    challenges the king. A challenger who wins becomes king, and on a draw the king stays unless
    the stage settles draws as ties. Laps set how many times the challengers come round, and from
    the second lap a dethroned king challenges too. The last king wins, and the others rank by
    the ties they won.
- **Gauntlet.** The two lowest seeds meet first, and the winner climbs to meet the next higher
    seed, step by step up to the top seed, who plays only the last match. Drawn steps settle as
    ties, or go to the higher seed when the stage says so.

The final ranking of a tournament lists the last stage in its order, then those who left in
earlier stages, later stages first. A knockout ranks the champion, the runner up, the third place
match and then the round in which each entrant went out. A double elimination ranks by the round of
the second defeat.

A stage type of a plugin brings settings of its own, which a stage writes beside the others, such
as `final_name` for the stage type of the worked example in [Plugins](plugins.md). The plugin
declares each setting with a default and its range, and the tournament checks the values as it
checks the others, so a number out of range is refused with its range. A stage type that a plugin
supplies is chosen like any other, and a name that no plugin offers, or that a broken or
turned-off plugin offered, is refused with the list of the names that are available.

## Settings

Every setting below may be set for the whole tournament, as a default of every stage, and again
for one stage, which overrides the default. The standard ability, described under Abilities, is a
setting of the tournament itself.

| Setting | What it sets |
| ------- | ------------ |
| Scenario | Any scenario that every entrant can play, by default the Kaggle match, or five against five when every registered team is five a side. Stages may use different scenarios and stage types, such as groups of five against five and a final of eleven against eleven, when every entrant who could reach a stage can play its scenario. |
| Match length | The GRF duration, from 50 to 5000, or the scenario's own. |
| Ability | The ability of the stage, which every side of the stage whose entrant has no ability of its own plays with, in place of the standard ability. |
| Legs | The matches of a tie, from 1 to 10, the higher seed playing the last leg at home, and goals adding up across them. The final may have its own count. |
| Drawn ties | An ordered list of ways to settle a tie that ends level. Away goals count when the legs are even. A replay, up to five, is one more match. A deciding match plays a chosen scenario twice, such as an academy drill, each side attacking from the left once, and adds the goals of both. Lots drawn from the tournament seed always come last and always decide. |
| Points | The points for a win, a draw and a loss, any whole numbers from minus 100 to 100. |
| Tiebreakers | An ordered list of what separates entrants level on points, goal difference, goals scored, fewer goals conceded, the matches between them, wins, away goals, fair play from cards, Buchholz, the points of the opponents met, and lots from the seed, which always come last. A [plugin](plugins.md#tiebreakers) adds tiebreakers of its own, written as the plugin's identifier, a dot and the name. |
| Seeding | For the first stage, at random from the seed, the default, by hand in the order of the entrants, or by rating, where equal ratings keep the order of the entrants. For a later stage, by the standing of the stage before it, at random or by rating. |
| Matches at once | How many matches of agents play at the same time, from 1, the default, to 8. |
| Forfeit score | The goals by which a match awarded by hand is won, 3 by default. |

The settings of each stage type, the bracket, the stop of a knockout, the third place match, the reset
of a grand final, cycles, groups, pots, qualifiers, best places, rounds, the points of a bye, laps,
what a draw does and how many advance, are described with their stage types above. A setting that
cannot work, such as more Swiss rounds than allow a pairing without rematches, groups of one, a
bracket too small for its entrants, a stage that receives fewer than two entrants or a team in a
scenario of another size, is refused with a message that names the setting, the stage and why.
A team of five a side and a team of eleven a side play together only in scenarios of the academy,
so a full match among entrants of both sizes is refused with a message that names one team of
each size, before the message about any one team.

### Match length

The option `--length N` sets the GRF scenario's `game_duration` to N. GRF starts its counter
at minus one and ends the match when the counter reaches N. A match that reaches the time
limit plays N plus one steps at ten steps per second. A recording that keeps the initial
frame and every step has N plus two frames. A scenario may end earlier when its own end
condition takes effect.
[GRF game counter](https://github.com/google-research/football/blob/3d9e754720a95621bba6475c4d3b0d56fe919014/third_party/gfootball_engine/src/game_env.cpp#L339-L370).
[GRF time limit](https://github.com/google-research/football/blob/3d9e754720a95621bba6475c4d3b0d56fe919014/gfootball/env/football_env_core.py#L239-L242).
The deciding matches of a stage play a scenario of their own, an academy drill by default, and a
team that cannot play it, such as one that controls more players than the drill holds, is refused
with a message that names the deciding matches of that stage. Another scenario for them, in the
stage's Scenario of a deciding match on the page or its `decider_scenario` in a configuration
file, or drawn ties settled without a deciding match, in its `settle`, lets the team play.

Ratings come from the table of the scenario of the first stage, which
[Ratings](evaluate-ratings.md) describes. Every match that the renderer plays on a scenario is
evidence for its table, a match of a tournament among them, so the rating of an entrant reflects
everything it has played there. A match awarded by forfeit was never played, so it adds no
evidence. A registered team at the checkpoint of its files, the built in AI at each ability and
each person by name are models of the table. Every entrant of one team shares the team's rating,
and a match between two of them tells nothing, since the team both wins and loses it. An entrant's
rating may also be set by hand, on the scale of the tables, where the anchor stands at 1000.
A configuration file of 0.1.0 stated its ratings on the earlier scale, where every team started
at 1500, and names no `evidence`, the key that came with the tables. Such a file reads its
ratings on that earlier scale and takes 500 off each, the difference between the two starts, so
an entrant given 1650 there stands at 1150 on the tables, 150 above the anchor. A configuration
that names `evidence` reads its ratings on the scale of the tables, and `rating_scale` states
the scale outright, `table` or `earlier`, whatever else the file holds.
Seeding by rating uses the ratings as they stand when the tournament is made, or once its seeding
phase has ended.

## The draw

Once the entrants are set, the **draw** places them, the seeds of a knockout's bracket, the pots
and groups of a group stage and the order of a Swiss, a ladder or a gauntlet. By default the draw
is random, from the tournament seed, so every entrant has the same chance of any place, and the
same seed gives the same draw again. Every preset that ships draws this way. A person who wants to
set the bracket themselves seeds by hand, with Set the bracket by hand on the Entrants step, the
seeding Manual in Settings, `--seeding manual` on the command line or `"seeding": "manual"` in a
configuration, and the order of the entrants is then the order of the seeds, the first entrant
seed 1, who meets the lowest seed in a knockout's first round. Seeding by rating stays a choice
too, for entrants that the table of the scenario rates. A tournament of an earlier version, whose
preset seeded by rating, keeps the draw it was made with and resumes as it was drawn.

## Determinism

The same configuration and **tournament seed** give the same draw and the same seed for every
match, whatever order the matches finish in. Each match has a key, such as `0/w4-1/2` for the second
leg of the first semi-final of the first stage, and its seed comes from the tournament seed and that
key. The draw of groups, pots, random seeding and lots each draw from the tournament seed and their
own purpose. So a tournament repeats with the same seed, and a match that a stop cut short plays
again from the start with the same seed and gives the same result.

## Presets

A preset holds a tournament's stage types, its stages, defaults, seeding and standard ability, without
its entrants. Every preset that ships draws its first stage at random from the seed. Those
that ship fit any number of entrants from 2 to 256. While its stages stay as the preset ships them,
a group size, a best place, a number of Swiss rounds or of entrants that advance comes down to the
most that the entrants allow, so the Swiss of 16 plays two rounds with three entrants and the
league phase sends four of five entrants on. A preset of the user that saved the stages of a
shipped preset unchanged fits the entrants in the same way, whatever else it changed, such as its
seeding or defaults. Stages changed on the page or in a configuration are the user's own, and a
setting that the entrants cannot play is refused with the reason.

| Preset | Stage types |
| ------ | ------ |
| World Cup | Groups of four, the best two of each in a knockout of single matches with a third place match. |
| European Championship | Groups of four, the best two and enough of the best third places to fill a bracket. |
| Champions League | Groups of four home and away, then two legged ties settled by away goals, and a single final. |
| League phase and playoffs | A Swiss league phase of everyone, then the best eight in two legged ties. |
| Classic league | A double round robin for three points a win. |
| Round robin | A single round robin. |
| Quick 8 team cup | A knockout of short matches of 600 steps, drawn ties settled by lots. |
| Swiss of 16 | Five Swiss rounds ranked by points and Buchholz. |
| Double elimination | Winners and losers brackets with a deciding grand final. |
| League and playoffs | A round robin, then semi-finals and a final among the best four. |
| Cup with a play-in | A knockout whose lowest seeds play in, with a third place match. |
| Ladder | Neighbours meet each round, and winners climb. |
| King of the hill | Challengers try the king one after another. |
| Gauntlet | The lowest seeds climb toward the top seed. |

The page saves any setup as a preset of the user, which the setup lists beside those that ship.
Saving under the name of a preset of the user, and deleting one, ask first in place of the button,
and Escape or Keep it leaves the preset as it was.

## The Tournaments section

The **Tournaments** section of the Evaluate page, at the address
`#page=evaluate&section=tournaments`, lists every tournament on its left, with its state,
entrants, stage types,
progress, the champion of a finished one, the matches waiting for a person and, for a stopped
tournament, the failed matches that wait for an award or Resume. The progress counts the matches
that the whole tournament is expected to play, so it grows steadily while later rounds are drawn.
**New tournament** opens the setup on the right, in six steps that are tabs of their own, so any
step opens at any time, and the arrow keys move between them. Next and Back move the focus to the
tab of the step they open, and every control that redraws a step keeps the focus on itself or on
its nearest neighbour.

1. **Stage types** offers the presets, those that ship and those of the user, as a group of choices
    that the arrow keys move through. New tournament gives the focus to the first step, and Run
    again to the step it opens. The seeding, scenario, match length, standard ability, matches at
    once and forfeit score that the person set by hand stay when another preset is chosen, where
    the preset leaves them free. Where the preset sets its own, a notice names those settings,
    and Keep mine gives the person's back.
2. **Entrants** has Set the bracket by hand above its list, which turns the random draw into a
    draw in the order of the list, and it adds registered teams
    one at a time or every one at once, the built in AI at an ability, and people by name, with
    the built in AI or a team of dynamic assignment as their teammates. Enter in the name of a
    person adds the person, Add every team says so when every team has entered already, and the
    list stops at 256 entrants. Each row shows the entrant's number, the seed of a draw by hand
    and the number that the notes of this step name. Each entrant may be renamed, given an ability
    of its own, given a rating and moved up or down, which is the seeding when it is manual, or
    removed, and a screen reader hears the place an entrant moved to. A rating typed in a row is
    on the scale of the tables of Evaluate, with the anchor at 1000, and the field shows as its
    placeholder the rating that the table of the scenario of the first stage holds for the
    entrant, or 1000 where the table holds none. The select of teams, the
    select of a person's teammates and the row of a team name the team's size and the players it
    controls, such as 5v5, 2 controlled players, and a team registered elsewhere joins the select
    once the person focuses it or comes back to the page. A team added again, or any team whose
    name an entrant already shows, enters under the first free name, such as Rex 2, with its name
    field focused so the proposal can be changed at
    once. The built in AI added at an ability that an entrant already shows takes the next number
    in the same way, such as Built-in AI 0.50 2, which its name field shows until a name is typed,
    and an empty ability box adds nobody. A name that repeats
    another entrant's is marked beside its field with the entrant it repeats, a person is added
    only under a name of their own, and Review lists every repeated name until each entrant has
    its own. The Ability field of each row stays empty to follow the standard, which its
    placeholder names, such as Standard, 0.80.
3. **Format** chooses the submission format that every entrant plays under, with the Format
    choice that [Choosing a submission format](#choosing-a-submission-format) describes. It lists
    every team and every built in AI of the entrants with the verdict of each under the choice,
    so a person sees who a format refuses before the tournament starts, and a refused entrant
    stands excluded, with its reasons. Options that a format declares for single entrants show
    beside each entrant. A tournament that chose nothing plays Kaggle open, and a person and the
    random entrant play under no format.
4. **Settings** names the tournament, takes its seed or draws one, and sets the seeding, the
    scenario and match length of every stage, the standard ability, the matches at once and the
    forfeit score. The standard ability is a slider with its value beside it, set by its Set box,
    and with the box clear the value reads as the scenario's own.
5. **Stages** is the advanced editor. It holds the standard ability too, and each stage is a card
    with every setting that its stage type reads, the ordered lists of tiebreakers and ways to settle
    a tie, each name starting with a capital, and buttons to add, move and remove stages. A
    tiebreaker left out of use moves below those in use and has no button to move it. A stage
    without an ability of its own reads as following the standard. Edit as JSON shows the standard
    ability, the stages and defaults as JSON to change as text, and a mistake in the text is named
    by its line and column, with the caret placed on it.
6. **Review** checks the tournament on the server, which shows in place while it runs, and the
    name of a preset can be typed meanwhile. It then shows the standard ability, the entrants
    with an ability of their own, each stage with its entrants, those who advance, its scenario,
    its ability and the draw of its first round or its groups, or the reason it cannot be made. A
    team that cannot play the scenario of a stage, or of its deciding matches, comes with a button
    that opens that field of the stage. Teams of both sizes in a scenario of one size are told a
    scenario that fits both sizes and the players each team controls, or that none does. Review
    also saves the setup as a preset under a name of small letters, digits, dashes and
    underscores, which the page asks for before it saves, and a preset keeps the forfeit score
    with the rest. Start tournament begins it, and while Review shows a refusal it waits, saying
    the refusal as its description, and a press leads to the refusal.

A tournament whose draw the ratings seed may start with less evidence than the settings ask. The
server then refuses to make it, and the review lists each entrant that lacks, with what it still
needs, such as three more matches or two more against the anchor, beside two buttons. **Collect**
posts the tournament again with `evidence` as `collect`, and the open tournament then shows the
live view of the rating run that measures those entrants, a progress bar, the matches playing and
next, the table as it moves and a chart of the ratings, until the phase ends and the draw plays.
**Proceed** posts it with `evidence` as `proceed`, and the draw takes the ratings as they stand.
Every configuration that the page sends goes through the body of the Format choice, so a stated
rating carries the scale of the tables, and an open tournament shows the starting rating of each
entrant, its interval, and a badge that says where the rating came from, given by hand, read from
the table, measured by the seeding phase or lacking evidence.

A match that a side forfeited by a rule of its format shows a Terminal button for each side that
broke the rule, which opens the log that the side printed, as the Terminal tab of Watch shows it.
The terminal of a match is kept under the run `<tournament id>-<match key>`, and an entrant that
the pre-flight left out shows the terminal of its check the same way.

An open tournament shows its state, seed and progress, the matches playing now, and buttons to
cancel it, resume it after a failed match, hold its trophy ceremony, run it again with the same
settings and a new seed, or delete it, which leaves its recordings in Recordings, each button
with its words beside its symbol. A finished tournament reads as finished with the count of its
matches, also without spoilers, and a server's refusal that the page shows as a sentence of its
own starts with a capital. Cancel and
Delete ask first in place of the button, as Award does, and Escape or the second button leaves
the tournament as it was. A canceled tournament says so beneath its name, lists nothing in Up
next and reads each match still to play as not played. Run again starts from the stages as they
were set, so a bracket, a group size or a count of rounds follows the new entrants. Its stages are
tabs. A knockout or a double elimination shows its **bracket**, one column a round, each tie between
the two that feed it, with the play-in round, the losers bracket, the grand final and the third
place match in sections of their own, and a bracket wider than the page scrolls sideways. Leagues,
groups, Swiss rounds, ladders and the challenges of a king show **tables**, with the places that
qualify marked, a caption that names how the table is ordered, by points and then the tiebreakers
in turn, by place on a ladder, or by the king first and then the ties won, and a line for each
entrant that stands above another on equal points that names the tiebreaker that placed it there.
**Fixtures and results** list every round with its matches, their scores, or their state while
none of their matches has played, the score of a replay or of deciding matches and then how a tie
was decided, such as replay 0 to 0, by lots, the progress of a match that plays, and Watch, which
opens the recording of each match. A tie between two byes of a bracket plays nothing and leaves its
place empty. An entrant whose own ability differs from the standard shows
it beside its name in the bracket, the tables and the fixtures, such as Ability 0.30 after the
symbol of the built in AI, and the head of the tournament names its standard ability. A built in AI
whose name the rules made already holds its ability in its name. The page follows a running
tournament as it plays and announces a match that waits for a person and the end of a tournament.
A screen reader hears the result of each match as it ends and a tournament that stops, while the
view redraws in silence.

A match with a person shows **Play now**, which starts it as a game of the Play page with the
tournament's scenario, length, abilities and match seed. The person's name stands on the
scoreboard, and two people in one match play the game for two. A game that reaches its end gives
the result, and the end card leads back to the tournament. A game that ends early, by Quit or a
lost page, leaves the match to be played again, and a game that the other side's policy ends
names that failure on the match, beside Play now and Award. **Award** gives a match to either side
by the forfeit score, for a person who cannot play or a team that keeps failing. It asks first in
place of the button, naming the match, the winner and the score, and Award confirms it while Cancel
or Escape leaves the match as it was. An award is final once confirmed, since the matches after it
may already have played with its winner.

A match of agents that fails shows its error and Award for either side, and waits for an award or
a retry while the matches that do not depend on it play on. Once only failed matches hold the
tournament up, it stops with the first failure as its error, which names the team whose policy
failed by the name it entered under. The notice of a stopped tournament lists each failed match
with Award for either side, and Play now waits until the tournament runs again. An award that
leaves a match to play runs it on, and Resume plays every failed match again. A failure stays a
choice of the organizer, since a team that failed under a busy machine may well play on a retry,
so the tournament awards no match by itself.

The names that the rules give show in the language of the page, the rounds and ties such as
Semi-final 1, Matchday 2 and Group A, the sides still to come such as Winner of Semi-final 1, the
built in AI at an ability such as Built-in AI 0.50 with the decimal sign of the language, the stage
names of the presets and stage types, a tournament left without a name, the notes of a match and the
titles of its recordings. A name that a person typed, of a tournament, a stage or an entrant, shows
as typed, and so does the name of a registered team. The tournament files keep the English text of
each name with its message beside it, the command line prints the English text, and a tournament
of an earlier version shows its names as it saved them.

An open tournament lists **Up next** above its stages, the next matches as kickoff cards, each
with its stage and label, the two sides with their kits and the state of the match, the away side
of two entrants of one team in its away kit, as in the match. The matches that wait to be watched
come first, in the order to watch them, and then those still to play while the tournament runs.
A final, a grand final and its deciding match have a card of their own, marked with the trophy. Each
card takes a prediction of its result, a win for either side or a draw, which the browser keeps
and which counts once the match's result shows, so the bar of the tournament without spoilers
shows how many predictions came right, as in 2 of 3 right.

The **trophy ceremony** of a finished tournament opens the last match that the champion played on
the Watch page at its end, and holds the podium and confetti of the show after a match, with the
champion's players on the steps in the champion's colors, the champion's name, and the tournament
won with the runner up beneath it. On a phone its card sits low and compact, so the podium stays
in view above it. Close ends it, and so does opening another match.

## The Brackets view

**Brackets** on an open tournament, or Brackets of a tournament in the command palette, opens its
brackets, groups and tables in a view of their own, which takes the whole width of the page with
the list of tournaments set aside and larger type. The stages are tabs as in the tournament, and
the view follows a running tournament as it plays. Zoom in and Zoom out scale the view from half
to twice its size, the columns of a bracket with its type, and Actual size returns it, so a bracket
reads across a room or more of it fits a small window. A bracket or a table wider than the screen
scrolls sideways, so the view works on a phone
too. Back to the tournament returns to the tournament where it was. A tournament without spoilers
shows in the Brackets view as it does elsewhere, with Watch next beside the zoom.

## Watching without spoilers

A tournament may be watched without spoilers, game by game, as a person follows a competition
that played while they were away. **Watch without spoilers** on an open tournament turns it on for
that tournament, and the browser remembers it, with the matches the person has watched, so it
stays on after a reload and in later visits. While it is on, nothing that tells the outcome of a
played match that the person has not watched shows. The brackets, tables, fixtures and ranking
read as if only the watched matches had played, so a side that an unwatched match decides reads
as where it comes from, such as Winner of Semi-final 1, a table counts the watched matches alone,
and the champion, the ceremony and the toast of a finished tournament wait. A played match that
waits shows Played, not watched yet, with Watch and Reveal.

**Watch next** plays the next match to watch on the Watch page, in the order of the schedule, and
the match counts as watched when its recording plays to its end, and only then does its result show
in the bracket and the tables. While it plays, the timeline leaves its goals unmarked. At its end a
card names the next match, who plays it and nothing more, with Play now and Cancel, and a final has
a card of its own. With **Play the next match by itself** on, the card counts down ten seconds and
plays the next match, and Cancel, Escape or leaving Watch stops it and gives the focus back to
where it was, and a screen reader reads the count as a timer when asked, not every second.
**Reveal** shows the result of one match without watching it, and **Reveal all** shows every
result, after a question in place of the button that Escape or the close button answers with no. A
match awarded without a recording reveals at once. Once every played match is watched, a summary lists the results of the matches
watched or revealed in that visit, what the person missed, while the count of right predictions
stays in the bar above.

The lists of the page keep the secret too. Home, Recordings, the Trash, the details of a recording,
the command palette and the search of moments show a recording of an unwatched match without its
score and teams, under a title that names only its tournament, such as Spring cup, a match not
watched yet, and the search of moments leaves its moments out. A match that a person plays shows
when its turn comes, as it must. Turning the switch off shows everything again, and the matches
watched stay remembered. The page asks the server for the tournament as the person sees it with
`POST /api/tournaments/ID/spoilers`, whose body names the watched matches by their keys.

## Choosing a submission format

A tournament chooses the **submission format** that every match of it plays under, with the key
`submission_format` of its configuration. The value is the identifier of a format, such as
`kaggle` or `strict/1`, or an object that holds the `id` and, besides it, a `preset`, the
`options` to set and `per_entrant`, a mapping from the team of an entrant, such as `rex` or
`rex@3fa9c1d200a1b2c3`, to the options of that entrant alone. A format decides how a team is
checked, how it is called, how long it may take and what counts as a forfeit, as
[Submission formats](formats.md) describes. Every team of a tournament plays under the one
format, so the results compare like with like, and every team must pass the pre-check of the
format, which the pre-flight below runs.

A tournament that chooses no format plays every team as Kaggle open, the preset `open` of the
built-in format `kaggle/1`, which loads a team's `main.py` and calls its agent for each step, with
no limit of time, exactly as cuju-render played teams before formats. A tournament that chooses
`kaggle` itself plays Kaggle's own timing, 0.5 seconds for each step and a bank of 60 seconds of
overage, as the chapter Built-in formats of [Submission formats](formats.md) describes. The format `strict/1`
suits a contest between teams whose authors want every policy to compute on the same terms and
every failure to settle at once, as a course or a league of agents does, and the rest of this
section describes it. The command line chooses a format with `--format` and sets its options with
`--option KEY=VALUE`, which may be given again.

```sh
cuju arena tournament new --team rex --team bolt --format strict/1 --option call_seconds=0.5
cuju arena tournament new --tournament-preset round_robin --team rex --team bolt --format kaggle
```

A tournament that earlier versions made under the limits that they called strict, with the key
`strict` in its file, opens as it did and resumes under `strict/1`, with its stored limits as the
options of the format. A finished tournament stays as it was written, and a configuration that
still carries the key `strict` reads the same way. A configuration names the key `strict` or the
key `submission_format`, never both.

## The format strict/1

Under `strict/1` each registered team's policy plays in a process of its own. The process
imports the team's folder first and the installed packages after it, with the variable PYTHONPATH
and every other variable of Python left out, so two teams never share a module, even modules of
the same name, such as two files called `helper.py`. The folder of the renderer's own script for
the process stays off its import path on every supported version of Python, so a team that imports
a module by a bare name, such as `limits`, finds its own file of that name or an installed package,
never the renderer's. The variables `OMP_NUM_THREADS`,
`MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `NUMEXPR_NUM_THREADS`, `VECLIB_MAXIMUM_THREADS` and
`BLIS_NUM_THREADS` hold the threads of the process to the limit of the rules, one by default, and
torch takes the same limit once the team imports it. The process sees no GPU, since the
variables `CUDA_VISIBLE_DEVICES`, `HIP_VISIBLE_DEVICES` and `ROCR_VISIBLE_DEVICES` are empty,
unless the setting `gpu` allows one. On macOS the GPU of Metal stays visible, since no such
variable hides it. The process works in a private temporary folder, which the match removes
afterwards, and with frozen folders, the default, it plays the team from a copy of the team's
folder inside that private folder, whose files read only. So whatever a team writes goes with the
private folder at the end of the match, and no team learns from one match to the next. A team
reads its own files by their path beside its `main.py`, such as
`os.path.join(os.path.dirname(__file__), 'model.pt')`. Its standard input reads
as empty, and what it prints goes to its log, `logs/policies/ID.log` in the home folder, which
starts again once it grows past a megabyte. The match and each process talk in lines of JSON on
pipes of their own, each read on a thread of its own, the same on Windows, Linux and macOS, and
the match reads only JSON from a process. This is isolation for fairness, so that every team plays
on the same terms, and not a security sandbox, as with the renderer's other ways of playing a
submission. A team's code still runs with the rights of the person who
runs the renderer, so only teams one trusts belong in a tournament.

A team may define a function named `reset` in its `main.py`, beside its agent, which the format
calls with no arguments before the first observation of each match. The agent stays the last
callable of the file, as on Kaggle, so a `reset` defined before the agent is the team's reset.

Each limit is an option of the format, with these defaults and the ranges that the format accepts.

| Limit | Option | Default |
| ----- | ------ | ------- |
| Threads of each team's process | `threads` | 1 |
| Loading the team, from the start of its process, once the read only copy of its folder is made, to its answer that it is ready | `load_seconds` | 30 seconds |
| The first action call | `first_call_seconds` | 10 seconds |
| Every later action call | `call_seconds` | 1 second |
| The reset | `reset_seconds` | 5 seconds |
| The mean compute time of a call over the match, for all the team's controlled players together, leaving out the first call | `mean_call_ms` | 20 milliseconds |
| The size of the team's submission, which the check of a team holds it to | `folder_mb` | 100 MiB |
| The memory of each team's process | `memory_mb` | 2048 MiB |
| The compute time of all the team's action calls of a match together | `match_seconds` | 120 seconds |

Both sizes count in mebibytes, written MiB, of 1,048,576 bytes each, as competitions of agents
mean their limits and as the limit of an upload to the registry counts too, so the default
`folder_mb` of 100 allows 104,857,600 bytes. The size of
a team counts its submission alone, every file of its folder that the digest and the read only copy
keep. The team's own folders stay out, `cuju-team`, which holds its identity and its kept
checkpoints, and `cuju-look`, which holds its look, and so do the caches of Python. So a team that keeps many
checkpoints beside its submission still passes the check, and a submission that the competition
takes passes it too.

A submission under `strict/1` holds plain files and folders alone. A link, a symbolic link or on
Windows a junction, may point anywhere, so the files behind it would play in a match while the size
and the digest of the team never saw them. The format therefore refuses a team whose submission
holds a link, before its process starts, as a `load` forfeit with the detail `link`, and the
pre-flight and the check of a team refuse it alike. A link inside the renderer's own folder
`cuju-team` counts for nothing, since no match plays that folder. The registry stores no link when
a team is added, as [Teams](teams.md) describes, so a link in a team's folder comes from a change
made by hand afterwards, which the person removes or replaces with the file itself.

The clock of loading starts once the read only copy of the team's folder is made, and stops when
the team's answer that it is ready arrives, so neither the copy of its own folder nor the copy of
the other team's folder counts against its limit, however large the folders. Every action call
counts the same way, from the moment the match sends the team its observation to the moment the
team's answer arrives, which the match stamps as each line comes. Both teams compute at once, and
the match reads one after the other, so each team is judged on its own time and never on the time
that the match spent waiting for the other.

The default of `match_seconds` follows from the limits of a call. The longest match lasts 5000
steps, which at the mean of 20 milliseconds a call comes to 100 seconds, and the first call may
take 10 seconds more, so 120 seconds leave a team that keeps to the other limits room to spare,
while a team that is slow throughout forfeits before the match drags on.

Three switches go with the limits.

| Switch | Option | Default |
| ------ | ------ | ------- |
| Frozen team folders. Each match plays the team from a read only copy of its folder, and a team whose files changed since its pre-flight is refused at its next match. | `frozen` | on |
| A GPU for each team's process | `gpu` | off |
| Deterministic teams. The pre-flight refuses a team whose answers differ when the same steps play again from the same seed, which is otherwise a warning. | `deterministic` | off |

The pre-flight keeps the identifier of each team's checkpoint, the digest of its files, with the
team's own folders `cuju-team` and `cuju-look` and the caches of Python left out, and a team and its checkpoint that an entrant `TEAM@CHECKPOINT` names alike. A tournament
without a pre-flight keeps the identifier of the team's first match instead. The record of the
tournament says `digest_scheme` 2 for it, and a record without that key keeps the digest of the
whole folder that earlier versions read, so a tournament that earlier versions began resumes
with the digest it began with.

A side that crashes, runs out of time, answers an invalid action, fails to load or to reset, or
computes for longer on average than the limit allows, **forfeits**. The match ends at once, and the
side that forfeits loses it by the tournament's forfeit score, 0 to 3 by default, while the
tournament plays on. A late player is never handed to the built in AI, and the match settles
without waiting for an award. Each forfeit keeps one of the five kinds that every submission
format uses, a finer detail, its reason in the language of the page, the step and the score at
that moment, and the format that it came from. The five kinds and the rules of `strict/1` that
give them are these.

| Kind | Detail | What happened |
| ---- | ------ | ------------- |
| `load` | | the team failed to load, or loaded too late |
| `load` | `link` | its submission holds a link |
| `load` | `size` | its submission is larger than `folder_mb` allows |
| `timeout` | | an action call took longer than its limit |
| `timeout` | `reset` | its reset failed to finish in time |
| `crash` | | its agent raised an error, or its process ended |
| `invalid` | `count`, `type` or `range` | an answer was no valid action, with a wrong count of actions, an action that is no whole number, or an action outside the action set, as Kaggle's protocol checks every answer |
| `rule` | `mean` | the mean compute time of a call, measured by the team's process around its agent alone, passed its limit at the end of the match |
| `rule` | `total_time` | the compute time of all the team's calls of the match together passed `match_seconds` |
| `rule` | `memory` | the team's process used more memory than `memory_mb` allows |
| `rule` | `threads` | the process ran more threads than the format gives it, `threads` and an allowance of 10 for the interpreter's main thread, the thread that watches the match and those that the system and libraries such as PyTorch keep whatever the limit |
| `rule` | `process` | the team started another process, which the format refuses once the team has loaded |
| `rule` | `changed` | the team's files changed since its pre-flight, under frozen folders |
| `rule` | `deterministic` | at the pre-flight alone, the team's answers differed when the same steps played again, when the format asks for deterministic teams |

A record that earlier versions wrote names the rule itself as its kind, such as `mean` or `reset`,
and the Tournaments section reads it as the kind and detail of the table. The renderer's own
stops, such as an answer that never comes, forfeit a side as `ceiling.answer`, `ceiling.time`,
`ceiling.load`, `ceiling.crash`, `ceiling.memory`, `ceiling.threads` or `ceiling.process`.

Each system holds a team's process to its limits as far as it can. The host that starts the team
takes the measurements four times a second, outside the process that loads the team and applies
the spawn guard. The host ends the team's process as soon as it sees a limit broken.

| System | The system enforces | The renderer watches |
| ------ | ------------------- | -------------------- |
| Windows | The memory of the process, which a job object limits, and its processes, which the job allows no other, from the first instruction of the team | The threads of the process, and the job's peak of memory to name a forfeit |
| Linux | The memory, by a limit of the data segment, `RLIMIT_DATA`, which counts what the process allocates and leaves alone the address space that PyTorch reserves without using it, which a limit of the whole address space would refuse | The resident memory, the threads and the children of the process, from `/proc` |
| macOS | Nothing of these, since macOS does not hold allocations to a limit of data | The resident memory, the threads and the children of the process, with `ps` |

On every system the team's own process refuses to start another once the team has loaded, by an
audit hook of Python, which names the reason. While a team loads, a library may run a short helper
process, as Python's ctypes does on Linux to find a library, which Linux and macOS allow and the job
object of Windows refuses. A team that starts a process through native code, past Python's audit
hook, meets the job object on Windows and the watch of its children elsewhere.

The limits of memory, threads, processes and time hold the team's own computer, and so assume that
the team has no network. A team with network access could compute elsewhere, and isolation from the
network is not yet enforced, so a tournament that needs it runs its teams on a machine or in a
container without one.

Two sides that forfeit at the same step both lose, 0 to 0. A forfeit adds no evidence to the
ratings, as an award adds none. An error of the engine or of a
match's own process fails that match alone, which waits for an award or a retry as described under
The Tournaments section, and the matches that do not depend on it play on.

Each process of a team takes its threads from the machine's, beside the one of the match, so a
tournament under `strict/1` starts a match only while the threads of the matches at once fit the
machine's threads, whatever its matches at once allow. A slow machine then plays fewer matches at
once, and no team times out because its neighbours crowd it.

Every match keeps its steps, whether it was cut short, the calls of each side's policy with the
first, the mean, the mean after the first and the largest compute time in milliseconds, and the
count of each action it answered. Since cuju plays every match exactly from its seed and the
actions of each step, a match under `strict/1` also keeps its seed and those actions, in the
folder `ID.replays` beside the tournament's file, and `cuju arena tournament replay` plays it
again from them and says whether the replay ends with the same steps and score.

## The random entrant

The **random entrant** takes part beside the built in AI. It controls one player of either size,
the active one, with actions drawn at random from the 19 of the default set, while the built in AI
plays its teammates at the entrant's ability. Its generator is seeded from the match seed and its
side, so the same match plays the same actions again. It makes a floor, since a team that cannot
beat it plays little better than chance. The Entrants step adds it, and so do `--random` on the
command line and `{"kind": "random"}` in a configuration. Its key, `random:uniform`, holds a colon,
which no team's identifier holds, so it never reads as a team in the files of results.

A tournament or a preset may also name entrants to add, in `include`, such as
`[{"kind": "random"}, {"kind": "builtin"}]`, which join those entered unless an entrant of the same
kind and ability is already there.

## The pre-flight

A tournament may hold a **pre-flight**, its setting `preflight`, before its first match. Each
registered team goes through the pre-check of the tournament's format, which accepts it or refuses
it with a reason, and a tournament that chose no format checks each team as Kaggle open does, that
it holds a `main.py` with an agent in it that loads. The pre-check of `strict/1` loads the team
and plays a few steps against the built in AI under the options of the format, once on the left
and once on the right, where it sees the match turned, so a team that reads the turned view wrong
fails before the tournament. It then plays the same steps on the left again from the same seed and
compares the team's answers, and answers that differ give a warning, or refuse the team when the
option `deterministic` is on, since a team that cannot repeat itself cannot be replayed. The
pre-flight also keeps the identifier of each team's checkpoint, which frozen folders hold the
team's matches to. A team that fails is left out with its reason, which the tournament shows, and
the tournament is drawn again from its stages as given with the entrants that remain, and goes on
without the team. A pre-flight that leaves fewer entrants than the stages need stops the
tournament with the reason. Two entrants that would show the same name stay refused by the rules
of names, as before.

The same check of one team runs from Review for every team of a tournament still to start, from
`POST /api/teams/ID/preflight`, which takes the `submission_format`, its `options` and its
`preset` in its body, and from the command line.

```sh
cuju arena tournament preflight t-20261106-101500-3fa9
cuju arena tournament preflight t-20261106-101500-3fa9 --json
cuju arena tournament preflight t-20261106-101500-3fa9 --format strict/1 --option call_seconds=0.5
```

The check prints pass or fail, the rule a failing team broke, the format and its warnings. Its
exit status is 0 for a pass and 1 for a fail, so a script of continuous integration checks a team
before it enters. Use `TEAM@CHECKPOINT` to check a particular version, as described in the
[checkpoint guide](team-files.md#checkpoints). A missing checkpoint selector gives usage and
exit status 2. `--json` prints the whole report, with the verdict's problems, whether the
team's answers repeat, and the options of the format. The pre-check of `strict/1` holds the
team's submission to the option `folder_mb`, counted without the folder `cuju-team` as described
under the format, refuses a submission that holds a link, and gives warnings that never fail it,
one for each setting of the team's file `team.json` that the renderer does not know, and one when
a single action makes up more than nine in ten of all the actions the team answered, which often
means a policy that is stuck.

## More legs later

A league plays as many legs of each meeting as the stage sets, from 1 to 10, the venue in turn leg
by leg, so in ten legs each side plays five at home. A league or a group stage that is the last
stage of its tournament may take more legs later, while it runs or once it has finished, up to 100
in all. The matches of before keep their keys, seeds and results, and only the added matches play,
after every matchday of before, the venue going on in turn from the last leg. A finished
tournament runs again until they have played. Add legs on the tournament does this, and so do
`cuju arena tournament legs ID --legs 5` and `POST /api/tournaments/ID/legs`.

## The seeding phase

A tournament whose draw the ratings seed asks evidence of its entrants. The ratings seed a draw
when the tournament seeds its first stage by rating, with `seeding` as `rating`, or a later
stage does. The tournament then reads the table of the scenario of its first stage, as
[Ratings](evaluate-ratings.md) describes, and checks whether each entrant holds what the table
`[ratings.evidence]` asks, a number of matches, a number of them against the anchor, and an
interval that is narrow enough. It reports who lacks what, and the setting `evidence` decides
what follows.

| `evidence` | What the tournament does |
| --- | --- |
| `refuse` | It is not made, and the refusal names each entrant that lacks and what it misses, with the two ways on. This is the default. |
| `collect` | It opens with a **seeding phase**, which gathers the evidence first. |
| `proceed` | It draws with the ratings as they stand, and its file marks each entrant that lacks. |

The default refuses, so a command or a program never plays matches that nobody asked for and
never seeds a draw from too little without saying so. `cuju arena tournament check` and `POST
/api/tournaments/check` answer the same report under `evidence` without making or refusing
anything, with the `scenario`, the key of the `anchor`, the `method`, what is `required`, each
entrant that is `lacking` with what it misses, and whether the evidence is `complete`. The page
shows it and offers to collect, as [The Tournaments section](#the-tournaments-section)
describes.

An entrant whose rating the configuration states as `rating` lacks nothing, and its rating is
marked as given. A person plays no seeding match and lacks nothing either. The entrants of one
team share its key, so they share its evidence as they share its rating.

The seeding phase is a **rating run** for the entrants that lack, and for those alone. Each of
them first plays the anchor of the tables, the built in AI at the difficulty of `[ratings]
anchor`, as often as `[ratings.run] anchor_matches` says, and then meets the other entrants and
the anchor where the order is least settled, until the order is settled or the most matches are
played. The entrants that hold their evidence play only as opponents, and keep their ratings
through the phase. The run plays the scenario of the first stage at its own length, with as many
matches at once as the `concurrency` of the tournament and the tournament's seed, under the
settings of `[ratings.run]` as they stand when the phase begins. It plays as an evaluation does,
at the speed of a batch where every side is the built in AI and with each team in a process of
its own, keeps the seed and the actions of every match, and writes a recording only for the
matches that `[ratings.run] keep` names. The limits of `strict/1` hold the matches of the draw
and leave those of the phase alone.

Every result of the phase joins the table of the scenario, as a match of a rating run with the
phase `seeding`, and counts for every later tournament and table as well. A seeding match that
ends without a score, by a failure of a policy, counts as played and adds no evidence.

The run keeps its own document, so the phase goes on after a restart with the same pairings, and
a cancelled tournament ends it. No match of the draw plays until the phase has ended. When it
ends, each entrant takes the rating that the table then holds for it, by the method of
`[ratings] method`, and the seeding by rating reads it.

The file of the tournament keeps what seeded it. Its field `elo` names the `method` of its
ratings, the `scenario` of the table and the key of the `anchor`, and holds for each entrant key
`seeding` and `final`.

| Field of `seeding` | Meaning |
| --- | --- |
| `rating` | the rating that seeded the entrant |
| `interval` | its lower and upper bound, null for a given rating |
| `matches`, `anchor_matches` | the matches behind it, and those of them against the anchor |
| `origin` | `given` for a rating of the configuration, `known` for one that the table held with the evidence asked, `measured` for one whose evidence this tournament gathered in its seeding phase, and `lacking` for one that the tournament took with less than is asked |
| `measured` | whether the origin is `measured` |
| `checkpoint` | the checkpoint of the model that the rating belongs to |

`final` holds the `rating` of the entrant after the tournament, with its `interval` and its
`matches`, from the table as the last match left it. A tournament opened later shows the same
seeding without playing it again. A file of an earlier version, whose ratings followed the Elo
rule from 1500, reads with its numbers as they stand and says so with `scale` as `earlier`.

The table `[tournament]` of the settings holds the default of `evidence`, and a tournament that
names its own takes that.

```toml
[tournament]
evidence = "refuse"
```

A tournament in full, as `GET /api/tournaments/ID` answers it, tells of the phase in four
fields. `evidence` holds the report of who lacked what when the tournament was made. `phase` is
`seeding` until the phase has ended and `main` from then on. `seeding` is null for a tournament
without a phase, and else holds these fields, the same that the progress of a rating run holds,
as [Ratings](evaluate-ratings.md#progress) describes.

| Field | Meaning |
| --- | --- |
| `state` | `waiting` until a process plays the phase, `running`, or `done` |
| `run` | the identifier of the rating run of the phase, which `GET /api/ratings/runs/ID` answers in full |
| `mode` | `batched` or `on_demand` |
| `matches` | the matches that each entrant of the phase plays at most, the key `matches` of `[ratings.run]` |
| `played`, `total` | the seeding matches whose result counts, and the matches of the phase |
| `most`, `settled`, `done` | the most matches, whether every pair is settled, and whether the phase has ended |
| `playing` | the matches under way, each with its `number`, `index`, `seed`, `home` and `away` key |
| `next` | the matches to come, each marked `provisional` on demand |
| `ratings` | the rating by points of each side by key, as the run moved it, with its `rating`, `low`, `high`, `matches` and whether it is `measured` |
| `history` | each counted match in order, with its `number`, `index`, `seed`, `home`, `away`, `score` and the `ratings` of its two sides right after it |
| `entrants` | for each side of the run its name, its `kind`, its `checkpoint`, its `model`, whether it is `measured`, and the `index` and the `color` of the entrant that it stands for, null for the anchor where it is no entrant |
| `measured` | once the phase has ended, the keys of the entrants whose evidence it gathered |

`queue` holds `playing`, `next`, `played`, `most` and `settled` of the same phase, and the list
of tournaments gives `phase` for each.

## Ratings across tournaments, evaluations and matches

The ratings that seed a tournament are those of Evaluate, one table for each scenario, made
from every result that the renderer has played there, with an anchor at 1000, two methods and
the evidence behind each rating. [Ratings](evaluate-ratings.md) is their guide, with the routes
under `/api/ratings`, the command `cuju arena rating run` and `cuju.arena.rate`.

## Ratings and the files of results

Every tournament shows **Results**, the ratings of its entrants and the matrix of their goal
differences, which the page fetches while Results is open.

The ratings follow the **Bradley and Terry** model, fitted to every match the tournament has
played, forfeits and deciding matches included, a draw counting as half a win for each side. They
read on the Elo scale, 400 points for each tenfold of strength, centered so that their mean is
1000, and they list best first. As its prior, each entrant also plays one virtual draw against a
virtual entrant of the fixed strength 1, which keeps the rating of an entrant that won or lost
every match finite and draws an entrant of few matches toward the middle. Each rating carries a 95
percent interval from a bootstrap of 200 resamples, the played matches drawn again with
replacement, by a generator seeded with the SHA-256 digest of the tournament seed followed by
`/bootstrap`, so the same results always give the same intervals. The interval runs from the 2.5th
to the 97.5th percentile of the resamples. These ratings belong to the tournament alone, apart
from the ratings of the tables of Evaluate, which gather every result of a scenario.

The **matrix** holds the mean goal difference of every pair, the row's goals less the column's
over their matches, whichever side was at home, with the count of their matches. The page shows it
as a heat table that scrolls inside its own box on a phone, which the keyboard reaches, each cell
named for a screen reader.

The page exports eight files, and so does `cuju arena tournament export ID -o DIR`. The
server sends each at `GET /api/tournaments/ID/files/NAME`, and the whole analysis at
`GET /api/tournaments/ID/analysis`. The columns stay stable, so scripts outside the renderer can
read them. An entrant reads by its index in the tournament and by its `id`, its key with `#2`,
`#3` and so on for a second or third entrant of one key, as two entrants of one team share theirs.

| File | What it holds |
| ---- | ------------- |
| `standings.csv` | `position`, `entrant`, `id`, `name`, `kind`, `team`, `played`, `won`, `drawn`, `lost`, `goals_for`, `goals_against`, `goal_difference`, `points`, `forfeits`, `cut_short` and `rating`, one row for each entrant over all its matches, in the final ranking once there is one, before that by points, goal difference, goals and rating. |
| `ratings.csv` | `rank`, `entrant`, `id`, `name`, `rating`, `low`, `high` and `matches`, best first. |
| `ratings_method.json` | The model, its prior, the scale, the resamples and the tournament seed of the bootstrap, and how many played matches were cut short. |
| `pairwise_gd.csv` | `entrant`, `id`, `name`, `opponent`, `opponent_id`, `opponent_name`, `matches` and `mean_goal_difference`, one row for each ordered pair, the mean empty for a pair that has not met. |
| `pairwise_counts.csv` | The matrix of match counts, a header of `id` and every entrant's id, then a row for each entrant. |
| `matches.jsonl` | One JSON object for each played match, its `key`, `stage`, `label`, `scenario`, `home`, `away`, `home_name`, `away_name`, `seed`, `score`, `steps`, `cut_short`, `forfeit`, `rule` with its `side`, `kind`, `detail`, `reason`, `step` and the `score` at that moment, `timings` and `actions` for each side, and `recording`. |
| `entrants.json` | Each entrant with its `entrant`, `id`, `name`, `kind`, `team`, `key`, `ability`, `rating`, `low` and `high`. |
| `excluded.json` | Each entrant that the pre-flight left out, with its name, team, reason and timings. |

A match that ended early, by a rule a side broke, counts in the files as any other, and says so in
`cut_short` of its line and of the standings of both its sides, and `ratings_method.json` counts
them, so no report mixes them with full matches unseen.

A tournament keeps the terminal of each refusal of its pre-flight and of each forfeit until it
ends, whatever the size of the terminals folder, and once it is done or cancelled it keeps the
last 16 KiB of each in its own file. Those lines can hold what a submission printed about its
machine, so no file above holds them. An export that wants them asks for one more file,
`terminals.json`, with the tail of each terminal by its run and side.

## The command line and the server

```sh
cuju arena tournament new --tournament-preset world_cup --name "Office cup" --seed 7 \
    --team t-20261106-101500-3fa9 --team builtin:0.3 --team builtin:0.8 --person Ana
cuju arena tournament new --tournament-preset round_robin --team t-20261106-101500-3fa9 \
    --team t-20261106-101500-3fa9 --as "Reds reserves" --team builtin:0.5
cuju arena tournament new --tournament-preset classic_league --difficulty 0.8 \
    --team t-20261106-101500-3fa9 --difficulty 0.3 --person Ana --team builtin
cuju arena tournament check spring.json
cuju arena tournament list --json
cuju arena tournament show cup-20261110-090000-1a2b
cuju arena tournament run cup-20261110-090000-1a2b
cuju arena tournament forfeit cup-20261110-090000-1a2b --match 0/w4-1/1 --winner away
cuju arena tournament resume cup-20261110-090000-1a2b
cuju arena tournament cancel cup-20261110-090000-1a2b
cuju arena tournament remove cup-20261110-090000-1a2b
cuju arena tournament ratings
cuju arena tournament presets
```

To follow a tournament in a shell with `watch`, replace `ID` with the tournament
identifier. Press Ctrl+C to stop refreshing.

```sh
watch -n 5 cuju arena tournament show ID
```

In Windows PowerShell, use a loop with the same interval.

```powershell
while ($true) {
    Clear-Host
    cuju arena tournament show ID
    Start-Sleep -Seconds 5
}
```

`tournament ratings` lists each model with its newest scenario result. A team that played
under two checkpoints has a row for each checkpoint, identified by the model identifier in the
first column. With `--json`, the `models` list gives those rows in the same order, including
checkpoint labels and training steps when available. The `ratings` mapping gives each team
key the row of its newest result. Use `rating table --scenario NAME` for the full table of
one scenario.

The new command accepts `--tournament-preset`, with `quick_cup` as its default, or from a JSON or TOML
file named by its positional argument. Entrants play in the order you name them.
`--team` accepts an identifier, `builtin`, `builtin:0.3` or `random`. Use `--person` for a
person. A team may be named more than once, and
`--as` after an entrant gives it a name of its own, so the second example enters the team Reds
as Reds and as Reds reserves. A team named again without `--as` takes the first free number, Reds
2, which `check` shows before anything is made. `--difficulty` before every entrant sets the standard
ability, from 0 to 1, or `scenario` to leave it to the scenario, and `--difficulty` after an entrant
gives that entrant its own, or `standard` to follow the standard, so the third example plays a
league at 0.8 where Reds play at 0.3. The first line of `check` gives the standard ability.
The static shell script completes both keywords for `tournament new` and `tournament check`.
The `show` command gives the standard ability in its detail after the status line.
Both give a stage's ability beside its scenario and an entrant's own ability after its name,
such as Reds at 0.30. The options `--seeding`, `--scenario`,
`--length`, `--concurrency` and `--rounds`, the rounds of every Swiss or ladder stage, change the
preset, and rounds beyond what the entrants allow are refused with the most they allow. A
tournament's identifier starts with `cup-`. If neither the file nor `--seed` supplies a seed,
the new command draws one and reports it on standard error unless you use `--quiet`. The
identifier and name appear on standard output in text mode, and JSON includes the saved seed.
A render server serving the same arena home plays new tournaments by itself. Use `cuju arena tournament run ID` to play in the terminal until the
tournament ends, stops on failed matches or waits for people, who play their matches on the page.
One process at a time runs a tournament, and a lock file beside it
names the process, so the server leaves alone a tournament that `run` plays, and the page follows
it. A cancel from either reaches the process that runs it. An award or a resume from the command
line, or from the page while `run` holds the tournament, goes to the process that runs it, which
applies it within a moment and answers, and the command line names that process, which plays the
tournament on. The key of a match is checked first, so a wrong one reads
as such at once. A process that gives no answer within ten seconds leaves the change undone, and
the refusal names the route of its server to send it to, such as
`POST /api/tournaments/ID/forfeit`. A tournament that stopped leaves its lock, so the command line
awards or resumes it while a server runs, and the server plays it on.

Each tournament file that the renderer writes carries a seal, a keyed digest of the file under a
key that the home folder keeps in `tournaments.key`. A running tournament whose file has no valid
seal, a file copied in from elsewhere or edited by hand, loads stopped with a note that says so, and
it plays once a person resumes it on the page or runs it from the command line, which seals its
file again. Every tournament file plays at most eight matches at once, the most that a new
tournament may ask for.

The show command starts with `ID  STATE  played N of M`. An unstarted draw has the state
`drawn` in text. The name and seed follow, then each stage with its tables and results, a tie that has played none of its matches as not played, and
then every match still to play with its key, label, sides and state, with the error of a failed
one. A tie reads in the order it went, its score, its replay and its deciding matches, and then
who won it when the score alone does not tell, such as Rex wins by lots. An entrant with a bye
reads as Rex, a bye, and a cancelled tournament reads its unfinished stages and matches as
cancelled and not played. A tournament with a seeding phase shows it after that line, its way of
playing, its state and its matches played, and then each side with its rating, the best first.
A side that the phase measured reads with its interval and its matches, and every other side
says where its rating came from, the anchor of the table, given in the configuration, known
from the table of the scenario, or taken with less evidence than asked. `forfeit`, also named
`award`, gives the match of `--match` to the
side of `--winner`, `home` or `away`, by the forfeit score, as Award does on the page, and
`resume` plays the failed matches again, as `run` also does. The check command takes the options
of new and prints each stage with its entrants, those who advance, its scenario and its first draw
or groups, and makes nothing, or the reason the configuration cannot make a tournament. When
ratings seed a draw, it then prints the evidence, the table of the scenario with its method and
anchor and what each entrant needs, and either says that every entrant holds it or names each
entrant that lacks with what it misses, followed by the two ways on, `--evidence collect` to
gather the evidence in a seeding phase first and `--evidence proceed` to seed the draw with the
ratings as they stand, and what the tournament does as it is configured. A cancel of a finished
tournament reports that it is done and gives `cuju arena tournament remove ID`. The remove
command accepts a tournament that has ended, stopped or been cancelled, and leaves its
recordings in the library. The
list command prints a line for each tournament, or says that none is made yet. The ratings command
lists the team key, name, rating and match count, with two spaces between columns. Rows appear in
descending rating order. Each rating and count comes from the table of the scenario of that
team key's newest result.
A preview checks the scenario and the control of each selected checkpoint. Use
`--team TEAM@CHECKPOINT` to keep an earlier checkpoint in the draw, as in a requested match.

Use an exact tournament identifier, a unique prefix of at least four characters after
the type prefix, or `last` for the newest tournament. When someone cancels a running
tournament, its runner writes `cancelled` to standard error and exits with status 0. Ctrl+C
returns 130 and leaves the tournament stopped with its completed matches. Use `tournament run ID`
to continue from those results.

Every action takes `--json`, which prints what the server returns for it, the summary of a new
tournament, the summaries of `list`, the whole tournament of `show`, `forfeit` and `cancel`, the
resolved configuration and draw of `check`, the ratings, the presets, and for `run` the `outcome`,
`done`, `stopped`, `people` or `cancelled`, beside the whole `tournament`.

The server answers `GET /api/tournaments`, `GET /api/tournaments/ID`, `GET
/api/tournaments/presets`, `GET /api/tournaments/ratings`, `POST /api/tournaments` with a
configuration, `POST /api/tournaments/check`, which resolves a configuration and returns its draw,
`POST /api/tournaments/ID/cancel`, `resume`, `play`, `forfeit` and `spoilers`, `POST
/api/tournaments/presets/NAME`, and `DELETE` of a tournament or a preset, with the same Host and
Origin checks as every other route.

| Route | Body | Answer |
| ----- | ---- | ------ |
| `POST /api/tournaments/ID/forfeit` | `{"key": "0/w4-1/1", "winner": "away"}`, the winner `home` or `away` | the whole tournament, with the match awarded by the forfeit score |
| `POST /api/tournaments/ID/resume` | `{}` | the summary of the tournament, which plays its failed matches again |
| `POST /api/tournaments/ID/play` | `{"key": "0/w4-1/1"}`, a match with a person | the game of the Play page that plays it |
| `POST /api/tournaments/ID/cancel` | `{}` | the whole tournament, cancelled |
| `POST /api/tournaments/ID/spoilers` | `{"watched": ["0/w4-1/1"]}`, the keys of the watched matches | the whole tournament as evaluated from the watched results alone, each played match that waits with `state` `unseen` and its recording in `unseen`, and `spoilers` with the keys that can be watched now in order as `unseen`, every played key as `played` and the counts `hidden` and `watched` |

A match key comes from the `matches` of `GET /api/tournaments/ID`, each with its `key`, `label`,
`home` and `away` entrants and `state`, and the summary of a tournament counts its `failed`
matches. The summary also counts the matches `played`, those `scheduled` so far, whose sides are
known, and those `expected`, the matches that the plan of every stage holds, with the replays and
deciding matches that results have brought, never fewer than those scheduled and equal to those
played once the tournament has ended. The page measures the progress of a tournament against the
expected count, so it never runs backward when a new round is drawn. A refused change answers 400
with the reason, and an unknown tournament 404.

## Configuration files

The commands `cuju arena tournament new FILE` and `cuju arena tournament check FILE`
read a tournament from JSON or TOML. The routes `POST /api/tournaments` and
`POST /api/tournaments/check` read the same configuration as JSON. Use a TOML file, as [Configuration](configuration.md#tournament-files) describes. The page's Edit as JSON shows the
stages and defaults of the same form. A configuration that names a `preset` and has no `stages`
takes the preset's settings for those it leaves out, and the options of `new` change it as they
change a preset. Each stage starts from the defaults below, then takes the configuration's
`defaults`, then its own settings, so a setting of `defaults` reaches every stage that leaves it
out.

```json
{
  "name": "Spring league",
  "seed": 7,
  "seeding": "rating",
  "concurrency": 2,
  "ability": 0.6,
  "entrants": [
    {"kind": "team", "team": "t-20261106-101500-3fa9", "name": "Reds", "ability": 0.9},
    {"kind": "builtin", "ability": 0.3},
    {"kind": "builtin", "ability": 0.8, "rating": 1600},
    {"kind": "person", "name": "Ana"}
  ],
  "defaults": {"scenario": "5_vs_5", "length": 600},
  "stages": [
    {"format": "swiss", "name": "Swiss", "rounds": 2, "bye_points": "draw", "advance": 2,
     "tiebreakers": ["buchholz", "goal_difference", "head_to_head", "lots"]},
    {"format": "knockout", "name": "Final", "legs": 2, "final_legs": 1,
     "settle": ["away_goals", "decider", "lots"]}
  ]
}
```

This makes a Swiss stage of two rounds among four entrants, a bye worth a draw, whose best two
meet in a final of one match, settled by away goals, a deciding match and lots in turn. The players
that Reds leave to the built in AI play at 0.9, the two built in AI entrants at 0.3 and 0.8, and
the teammates of Ana at the standard of 0.6.

| Key of the tournament | Value | Default |
| --------------------- | ----- | ------- |
| `name` | text of 1 to 80 characters | `Tournament` |
| `seed` | a whole number from 0 | drawn at random |
| `entrants` | a list of 2 to 256 entrants, as the next table describes | |
| `seeding` | `random`, `manual` in the order of the entrants, `rating`, or the name of a seeding rule of a [plugin](plugins.md#seeding-rules) | `random` |
| `concurrency` | the matches of agents at once, 1 to 8 | `1` |
| `forfeit_score` | the goals of an awarded match, 1 to 10 | `3` |
| `ability` | the standard ability from 0 to 1 of every entrant and stage that sets none | null, the scenario's own |
| `defaults` | stage settings for every stage | `{}` |
| `stages` | a list of 1 to 8 stages, as the last table describes | |
| `difficulty` | the same as `ability`, under the name of the engine | null |
| `evidence` | `refuse`, `collect` or `proceed`, what a draw by rating does when an entrant lacks evidence, as the section on the seeding phase describes | that of `[tournament]` |
| `rating_scale` | `table` or `earlier`, the scale of the ratings that the entrants state, the scale of the tables or that of 0.1.0 from 1500, which reads 500 lower, as [Settings](#settings) describes | `table` when the configuration names `evidence`, else `earlier` |
| `preset` | the name of a preset, which fills what the configuration leaves out | none |

| Key of an entrant | Value |
| ----------------- | ----- |
| `kind` | `team`, `builtin` or `person`, `team` when absent |
| `team` | the identifier of a registered team, for a team |
| `ability` | from 0 to 1, the ability of the built in AI players of the entrant's side, for every kind of entrant, or null to follow the stage and the standard |
| `name` | the name the tournament shows, up to 40 characters and unique within the tournament without regard to case or spaces, by default the team's name or Built-in AI with its ability, numbered from 2 when another entrant holds it, and required for a person |
| `difficulty` | the same as `ability`, under the name of the engine |
| `teammates` | `builtin` or a registered team of dynamic assignment, for a person |
| `rating` | a rating from 0 to 10000 on the scale of `rating_scale`, which replaces the one of the table, and spares the entrant the seeding phase |

| Key of a stage | Formats | Default | Value |
| -------------- | ------- | ------- | ----- |
| `format` | all | `knockout` | `knockout`, `double_elimination`, `league`, `groups`, `swiss`, `ladder`, `king_of_the_hill`, `gauntlet`, or the name of a stage type of a [plugin](plugins.md#stage-types) |
| `name` | all | the format's name | text of up to 60 characters |
| `scenario` | all | the Kaggle match, or `5_vs_5` for teams of five a side | a scenario of the scenario list |
| `length` | all | null, the scenario's own | GRF duration from 50 to 5000 |
| `ability` | all | null, the standard | the ability from 0 to 1 of every side whose entrant sets none |
| `difficulty` | all | null | the same as `ability`, under the name of the engine |
| `legs` | all | `1` | the matches of a tie or meeting, 1 to 10 |
| `final_legs` | `knockout`, `double_elimination` | null, as `legs` | the matches of the final, 1 to 10 |
| `settle` | `knockout`, `double_elimination`, `ladder`, `king_of_the_hill`, `gauntlet` | `["replay", "lots"]` | an ordered list of `away_goals`, `replay`, `decider` and `lots`, which always comes last |
| `replays` | as `settle` | `1` | replays at most, 1 to 5 |
| `decider_scenario` | as `settle` | `academy_run_to_score_with_keeper` | the scenario of a deciding match |
| `decider_length` | as `settle` | null, the scenario's own | GRF duration from 50 to 5000 |
| `points` | `league`, `groups`, `swiss`, `ladder`, `king_of_the_hill` | `{"win": 3, "draw": 1, "loss": 0}` | whole numbers from minus 100 to 100 |
| `tiebreakers` | `league`, `groups`, `swiss` | `["goal_difference", "goals_for", "head_to_head", "wins", "fair_play", "lots"]` | an ordered list of `goal_difference`, `goals_for`, `goals_against`, `head_to_head`, `wins`, `away_goals`, `fair_play`, `buchholz`, the name of a tiebreaker of a [plugin](plugins.md#tiebreakers), and `lots`, which always comes last |
| `seeding` | a later stage | `standing` | `standing`, `random`, `rating`, or the name of a seeding rule of a [plugin](plugins.md#seeding-rules) |
| `bracket` | `knockout` | `byes` | `byes` or `play_in` |
| `bracket_size` | `knockout`, `double_elimination` | null, from the entrants | a power of two |
| `until` | `knockout` | `1` | a power of two below the bracket size, the entrants that remain |
| `third_place` | `knockout` | `false` | `true` or `false` |
| `grand_final_reset` | `double_elimination` | `true` | `true` or `false` |
| `cycles` | `league`, `groups` | `1` | the times each pair meets, 1 to 10 |
| `groups` | `groups` | null, from `group_size` | the number of groups |
| `group_size` | `groups` | `4` | entrants of a group, from 2 |
| `group_sizes` | `groups` | null | a list of sizes that add up to the entrants |
| `pots` | `groups` | null, the largest group size | the pots of the draw |
| `qualifiers` | `groups` | `2` | a count for every group, or a list of one count a group |
| `best_position` | `groups` | null | a place whose best entrants across the groups also qualify |
| `best_count` | `groups` | `0` | how many of them, or `"fill"` for enough to fill a bracket |
| `rounds` | `swiss`, `ladder` | null, from the entrants | Swiss rounds up to half the entrants rounded up, ladder rounds up to 1000 |
| `laps` | `king_of_the_hill` | `1` | the times the challengers come round, 1 to 10 |
| `bye_points` | `swiss` | `win` | `win`, `draw` or `none` |
| `draws` | `ladder`, `king_of_the_hill`, `gauntlet` | `holder`, or `settle` for a gauntlet | `holder` or `settle` |
| `advance` | every stage but the last | from the format | the entrants that go on to the next stage |

## Files

Each tournament lives in a JSON file of the folder `tournaments`, with its resolved configuration,
the result of every match and its state, written through a temporary file. The configuration also
keeps its stages and defaults as they were given, before the entrants resolved them, under `given`,
which `GET /api/tournaments/ID` returns with the rest, so Run again on the page starts from them
and resolves them anew for any number of entrants. A file that cannot be
read is skipped with a warning, and the server starts with the rest. A restart of the server
resumes every running tournament where it stopped, and a match that was playing plays again from
its start with the same seed. The presets of the user live in `presets.json` of the same folder, and the ratings live in the folder `ratings` of the home folder, which [Ratings](evaluate-ratings.md#files) describes, with the rating run of a seeding phase. A match length writes a small scenario file into the folder
`scenarios`, which plays the shipped scenario for that many steps, with the second half starting at
the same share of the match.

A tournament that uses a plugin, for a stage type, a tiebreaker or a seeding rule, records the
plugin's name and version in its configuration under `plugins`, as a list of `{"name", "version"}`
ordered by name, the version `null` for a plugin that declares none. A tournament of the standards
alone has no such list. The file also keeps the view of the tournament as of its last write, under
`view`. When a plugin that a file names is removed, broken or turned off, the file still opens and
shows what it kept, the matches, tables and ranking as they were. It opens stopped with a line that
names the missing plugins, and it plays no further, takes no awarded match and adds no legs, until
the plugin is back. It can still be cancelled and removed. A plugin that comes back at another
version works as before, and the file keeps the version that made it.

The file of a tournament keeps the names that its rules gave each entrant, so a tournament made
by an earlier version, sealed or not, loads, resumes and plays to its end as it did, even where the
rules of now would number or refuse a name of its entrants.
