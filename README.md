# cuju-arena

Cuju Arena plays, judges and records matches of [cuju](https://github.com/cuju-football/engine),
a batched reimplementation of Google Research Football. It keeps submission formats, teams and
checkpoints, a library of recordings, evaluations, ratings and tournaments.

Install the arena with `pip install cuju-arena`. The installation brings in cuju-engine. Run
`cuju arena --help` to see the commands, or call the same interface as `python -m cuju.arena`.
The arena stores its data in `~/.cuju/arena`. Set `CUJU_ARENA_HOME` to keep another library.

If an upgrade from cuju 0.2.1 leaves engine files missing, run `python -m cuju.arena config show`
for the recovery command. Arena reports the damaged installation with exit status 3 and shows how
to reinstall the installed engine version. The `--json` option also writes the error to standard
output as a JSON object with the recovery command in `hint`.

Start with the [guide][getting-started] to add a team, play a match and run a small
tournament. The [documentation index][documentation] links to the Python and command-line
references.

The [Squad format example][squad] uses fixed CB, CM and CF roles. With the
built-in goalkeeper, it fits the academy benchmark tasks `corner`,
`counterattack_easy`, `counterattack_hard` and `single_goal_versus_lazy`.

Install [cuju][family] with `pip install cuju` to get the whole family. Install
[Cuju Render][render] with `pip install cuju-render` to view recordings in a browser and
use its presentation and export tools.

Cuju Arena is distributed under the [Apache License 2.0][license]. [NOTICE][notice]
credits the upstream work.

[getting-started]: https://github.com/cuju-football/arena/blob/main/docs/getting-started.md
[documentation]: https://github.com/cuju-football/arena/tree/main/docs
[squad]: https://github.com/cuju-football/arena/blob/main/docs/formats.md#squad
[family]: https://pypi.org/project/cuju/
[render]: https://github.com/cuju-football/render
[license]: https://github.com/cuju-football/arena/blob/main/LICENSE
[notice]: https://github.com/cuju-football/arena/blob/main/NOTICE
