cuju-arena runs submissions under declared rules and rates them

Start with a submission you trust. A submission format defines how the arena loads the
submission, checks its entry point, runs it in a match and records a forfeit. Use the same
format and scenario when you compare teams. A stored verdict identifies the checkpoint and
format that it checked.

Register each submission as a team. Its identity file records its name, size, roster and
control settings. Add a checkpoint when you have new weights or files, so an earlier result
can still name the submission that played it. The renderer reads this identity and keeps the
team's appearance in its own files.

A requested match answers one match question. An evaluation plays a team or policy against
declared opponents, and the academy benchmark plays the same tasks on the same seeds. A
tournament groups matches under a schedule and tiebreaker rules. Rating runs collect evidence
for a scenario and show how much the order of the teams depends on the matches played so far.

The arena keeps recordings, verdicts, results and the evidence behind ratings in its home.
A recording can travel as a bundle with its annotations, overlays and appearance. The
renderer can open the same recording in the browser.

The arena commands use `cuju arena`. Start with the
[arena guide index](index.md) for a first match, or read the [command line](cli.md) and
[Python reference](reference.md) for the available operations.
