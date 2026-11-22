# Arena documentation

The arena checks and runs submissions and keeps the results. Start with
[Getting started](getting-started.md) to check a folder and register it. The
[Configuration guide](configuration.md) explains the arena home, settings files and command
line overrides. [Troubleshooting](troubleshooting.md) explains how to read a refusal or a
failed run and find its evidence.

## Submissions and identity

[Registering teams](teams.md) covers the team registry and the controls a submission uses
to play. [Team identity files](team-files.md) describes the identity manifest, checkpoints
and export. [Submission formats](formats.md) explains how a format declares its entry point
and how it checks and runs a submission. [Plugins](plugins.md) shows how to extend the
arena with a rating method, stage rule, attachment or analysis provider.

## Matches and results

[Requested matches](matches.md) covers teams, named scenarios and seeded draws.
[Evaluation runs](evaluation.md) explains how to compare a policy against a set of
opponents. The [academy benchmark](evaluate-benchmark.md) compares teams on fixed tasks,
and [Tournaments](evaluate-tournaments.md) explains how to schedule and score a series
under declared rules. [Ratings](evaluate-ratings.md) describes the rating tables and the
evidence they use.

## Recordings and evidence

[Recordings](recording.md) explains what the recorder keeps. [Importing matches](importing.md)
describes file types and how an import checks fidelity. [Annotations](annotations.md) shows
how to keep values, actions and spatial data alongside a match. [Moments and captions](moments-and-search.md)
covers search and WebVTT, and [Searching by policy signals](policy-search.md) describes
queries over values and actions. The [recording format](recording-format.md) specifies the
file and its attachments.

The [command line](cli.md) lists the arena commands and examples. The
[Python reference](reference.md) lists its public functions and classes. Use the
[renderer guides](https://github.com/cuju-football/render/blob/main/docs/README.md) to open, watch or export the results in the browser.
