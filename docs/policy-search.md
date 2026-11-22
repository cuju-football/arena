# Searching by policy signals

A search of moments usually asks about the match, the goals, the shots, the passes and the cards.
The policy signals ask about the model that played it. When a model annotates its recordings with
the values it predicts and the probabilities of its actions, as [Annotations](annotations.md)
describes, the search finds the moments where its estimate of the game fell sharply, where it
chose an action it thought unlikely, where it was most or least sure of itself, and where a named
value such as the reward crossed a bound. Those are the moments a person training a policy most
wants to watch, the seconds in which the model changed its mind or did something it did not
expect of itself, and the search puts them one click away across every recording of the library.

The same terms serve three places. The Search moments panel of the Recordings page and the command
palette take them in their query field, `cuju arena search` and `cuju.arena.search` take them
from a script, and the `keep` setting of a recorder takes them too, so a training run saves only
the episodes in which the policy did something worth a look. The Watch page also marks the frames
of the open recording where its value fell or its action was unlikely with small ticks along the
timeline.

## The question it answers

Suppose a model of five a side learns to keep the ball, and its value estimate sits near 0.6 for
most of a match. Somewhere in the second half it drops to minus 0.4 in a single step, recovers a
step later and plays on. Something happened there that the model did not see coming, perhaps a
tackle from behind or a pass it misread, and that one second says more about the model than the
other ninety minutes. Finding it by eye means watching every match. Finding it by search means
typing `value-drop`, which lists every such fall across the library, newest recording first, each
with the minute of the clock, the player, the team and a line such as `Value drop, number 2 of
Greens, value fell by 0.9`. A click on the result opens Watch four seconds before the fall.

The other terms ask the neighbouring questions. `surprise` finds the steps where the model chose an
action that its own policy gave a low probability, which often marks exploration or a forced
move. `entropy:>2` finds the steps where the model was least decided between its actions, and
`entropy:<0.2` those where it was most certain. `reward:>0` and `ann:value<-0.5` find where a named
value crossed a bound, whatever that value means to the model.

## The terms

Each term is a word or a filter of the [query language](moments-and-search.md#the-query-language),
and joins any other words and filters of the same query. A term of a signal adds its moments to
those of the words of kinds, so `goal value-drop` lists goals and value drops together, and the
filters narrow them all, so `value-drop team:left after:45` lists the value drops of the left team
in the second half. The team and the player of a moment are those of the player that the
annotation describes, and a value of the whole match belongs to no team.

| Term | Moments |
| --- | --- |
| `value-drop` | a named value of any player, or of the whole match, fell by at least `value_drop` within `window` seconds |
| `value-drop:value` | the same, for the values of that name alone, such as `value` or `advantage` |
| `surprise`, `unlikely` | the chosen action had a probability of at most `surprise`, or lay outside the probabilities kept |
| `surprise:0.05` | the chosen action had a probability of at most the number given, from 0 to 1 |
| `entropy:>1.5`, `entropy:<0.2` | the highest or the lowest entropy of each player's actions, where it passes the bound |
| `reward:>0` | a value named `reward` went past the bound, the same as `ann:reward>0` |
| `ann:value<-0.5` | a value of the name given went past the bound, with `>`, `>=`, `<` or `<=` |

A value drop counts the fall from one annotated frame to the next and joins the falls and climbs
of the same value that lie inside the window before it, so a fall that comes back within the same
second counts as no fall at all, and a fall spread over a few frames counts as one. The search
lists one value drop for each window, at the frame where the fall first reached the threshold. A
name compares without regard to case.

The entropy is that of the probabilities kept for a step, in nats, so it counts only the steps
whose annotations keep every probability, which `probabilities = "all"` of the `[annotations]`
settings asks for. A recording that keeps the three most likely actions alone has no entropy, and
`entropy:` finds nothing in it.

A value that goes past a bound gives one moment for each player whose value went past it. For a
bound above, the moment lies at the first kept climb after which the value surely stood past the
bound, or at the largest climb when the kept climbs cannot tell, and for a bound below at the
matching fall. A value that never climbs, for a bound above, or never falls, for a bound below,
gives no moment.

A term of a signal finds moments, so it takes no minus sign. A query such as `-surprise` stops the
search with a message that says so, as does a term out of shape, such as `surprise:2`, which asks
for a probability above 1, or `entropy:1.5`, which lacks its comparison.

## The thresholds

The thresholds of the terms without a number of their own live in the `[search]` table of the
settings, which [Configuration](configuration.md) describes.

| Setting | Default | Meaning |
| --- | --- | --- |
| `value_drop` | `0.3` | the least fall that counts as a value drop |
| `window` | `2.0` | the seconds of the recording over which the falls and climbs of a value join |
| `surprise` | `0.1` | the highest probability of the chosen action that counts as a surprise |

The index of the library keeps the extremes and the largest changes of every annotated value of
every recording, and the thresholds apply when a search runs. A change of a threshold therefore
takes effect at the next search, and the index reads no recording again. The index keeps the five
largest falls and the five largest climbs of each value, and the frames where the chosen action
had a probability under one tenth, at most a hundred for each player. A bound of `surprise` above
one tenth therefore finds those same frames, and a value that fell more than five times in one
recording shows its five largest falls.

A script passes other thresholds through its settings or its overrides.

```python
import cuju.arena

found = cuju.arena.search('value-drop', overrides=['search.value_drop=0.5'])
```

A file of settings that the variable `CUJU_ARENA_CONFIG` names does the same for every search,
those of the server and its page included, when the variable is set before the server starts.

```toml
[search]
value_drop = 0.5
window = 1.0
```

## In a recorder

The `keep` setting of a recorder, or its `keep` argument, takes a query in the same language. At
the end of each episode the recorder finds the moments of the query in that episode, with the same
rules and the same thresholds as a search of the saved recording, and saves the episode when it
finds at least one. The words `all` and `goals` and a function of the summary keep working as
before.

```python
import cuju
import cuju.arena

batch = cuju.BatchEnvironment('5_vs_5', environment_count=8, device='cuda')
recorder = cuju.arena.Recorder(batch, keep='value-drop surprise:0.02')
```

A query of signals alone reads the annotations of the episode, which takes some tens of
milliseconds for an episode of 600 steps with every probability kept. A query that names kinds of
moments, such as `goal team:left`, also finds the moments of the rules of play in the frames of
the episode, which takes a few tenths of a second for a full match of five a side. Both run at the
end of each episode, in the thread that ends it.

## On the timeline

Watch marks the frames of the open recording of the library where `value-drop` and `surprise` find
something, with the thresholds of the server, as ticks along the timeline. Each tick carries the
minute and the line of its moment in its tooltip, and the Series tab of the side panel lists the
kinds of signal that the recording holds, each with its color and its count. A file opened from the computer and a live
source have no index, and show no ticks.

## A worked example

The repository's `tests/support/query_cases.json` holds an annotated recording in the shape the
index keeps it, a match of two players a side at ten steps a second with a goal by the second
player of the Greens at step 100. Its value series for that player falls by 0.6 at step 50 and by
0.2 two steps later, climbs by 0.5 at step 60, and falls again by 0.25 at step 120 and by 0.1 at
step 121.

With the shipped thresholds, `value-drop` finds two moments. The fall at step 50 reaches 0.6 alone.
The fall at step 52 joins it inside the same window and is the same drop, so it adds no moment. The
falls at steps 120 and 121 come to 0.35 together inside two seconds, past the threshold of 0.3, so
step 121 is a value drop of its own. The second player of the Reds climbs by 0.3 at step 70 and
falls by 0.35 at step 80, which is a fall of 0.05 over the window, so it is no value drop.

```text
value-drop                  steps 50 and 121, the Greens
value-drop team:right       none
surprise                    steps 30, 55, 90 and 140
surprise:0.04               steps 55 and 140
entropy:>2                  step 55, the highest entropy of the Greens player
reward:>0.5                 step 100, where the reward of the match climbed to 1
ann:value<0                 step 50, where the value of the Greens player fell below 0
```

With `search.window=0.1`, a window of one step, the falls no longer join. The fall of 0.35 at
step 80 then counts on its own, and the falls at steps 120 and 121 each stay under the threshold.
With `search.value_drop=0.7` and the window of two seconds, the falls at steps 50 and 52 join to
0.8, so the search finds one drop, at step 52.

The same cases check the search of the server, the reader of the query on the page and the answers
of the mock server of the page checks, so the three read every query alike.
