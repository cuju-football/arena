# Troubleshooting the arena

A command prints its answer on standard output. Errors and progress go to standard error.
Use `--json` to read the answer or the versioned error in a script. Use `--quiet` when the
script only needs errors. Use `--verbose` to include the traceback when an error needs
investigation.

A submission check can give a negative verdict even when the format and the machine are
working. Read the reason and the verdict fields before changing the submission. The
[format guide](formats.md#checking-a-folder-from-the-command-line) explains how to check a
folder and save its report. A format can also fail to run, for example when its own code
raises an exception or needs a package that is missing. The command reports this as an
environment error.

When a command cannot find a team, check its identifier with `cuju arena team list`.
If you work with several homes, pass the same `--home DIR` to the command that registered
the team and the command that plays it. Inspect the effective rules with
`cuju arena config show`, which names the layer that set each value.

A replay check compares the kept actions and scores with the result it plays again. Use
`cuju arena evaluation check ID` for an evaluation or `cuju arena rating check ID` for a
rating run. Keep the run and its files when a replay differs, so the identifiers and
reported scores are available for investigation.
