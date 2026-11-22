# Changelog

## [0.1.0] 2026-11-22

Cuju Arena is the headless match and competition package for Cuju. It runs beside
cuju-engine 0.3 and keeps its own settings and match library. Cuju Render can open the
same recordings for viewing.

### Added

- Set the episode count with `evaluation run --episodes`.
- Keep each side's checkpoint identifier, label and step in saved match results.

- Resolve team and checkpoint references by name, step or unique identifier prefix, and open
  the newest recording, evaluation or tournament with `last`.
- Read play defaults from settings and include checkpoint identity in match JSON.
- Filter recordings by day and search moments by the final score of their recording.
- Name an evaluation series directly and identify the run in its progress.

- Publish the arena as a separate package with its own Python API, command line, settings and
  built-in plugins.
- Register teams and keep checkpoints. Validate submissions with the built-in Kaggle and
  Strict formats, or install a format or plugin from a trusted source.
- Play and record seeded matches. Import, search and caption recordings, and export bundles
  that carry a match and its attachments.
- Schedule and run tournaments, benchmark teams on academy tasks, and gather evidence through
  evaluations and rating runs. Replay kept match actions to check earlier results.
- Preserve the match draw seed stream across the move.
- Record the package versions for all new rating results, including the renderer when installed.
- Show the summary of one library recording in text or JSON.

### Changed

- Count playable benchmark cells and report when no task fits.
- Show shell polling for tournaments and compact JSON lines for scripts.
- Explain the credited team in moment search and which academy tasks fit the Squad example.

- Give rating runs separate list and show commands. Print the recording path when a rating
  match is drawn.
- Separate listing columns with two spaces, and give empty listings a next command.
- Show the final score on standard error after playing a match.
- Show tournament progress on one line and report user cancellation as success.
- Offer shipped scenario, team, format and plugin choices in static shell completion.
- Keep tournament instructions on standard error. Put a short match and a tournament in the first guide.
- Report the seed drawn by tournament creation on standard error.
- Mark shipped samples that are ready to add and give the command to add one.
- Count the recordings with matching moments in search.
- Explain when the render server plays newly created tournaments.

### Fixed

- Retry updates to live terminals when a Windows reader holds a file open.
- Save completed benchmark results before reporting that the run is done.
- Verify that Windows jobs end their child processes through native process status queries.
- Keep full checks and answer pipes working in Windows virtual environments.
- Run each check match environment on its calling thread.
- Read Windows platform details from the host for guarded formats.
- Exercise blocked match load and answer forfeits in isolated Windows match processes.
- Coordinate reads and writes of rating run progress during seeding and cancellation.
- Exit quietly when Windows closes the version command's output pipe.
- Use the original file bytes in checkpoint reuse tests on every system.
- Verify host sampling for nested check matches even when a match ends quickly.
- End pooled matches on Windows and close a format's answer pipe when its process exits.
- Complete the built-in difficulty values used in the guides.
- Reserve built-in identifiers when registering or renaming a team.

- Report a damaged engine installation with a recovery command in text and JSON.
- Resolve team references for the credited side in moment searches.
- Offer installed plugin identifiers in static shell completion for `plugin show`.
- Complete the tournament difficulty keywords in static shell scripts.
- Complete the `last` reference for recordings, evaluations and tournaments.
- Explain the team argument in checkpoint listings and report a misplaced selector as a usage error.
- Report ambiguous checkpoint labels and missing checkpoint selectors as usage errors.
- Include checkpoint identifier prefixes when checking whether a label is ambiguous.
- Report an exact team name that also selects a checkpoint as ambiguous.
- Reject new team names containing `@`, the separator for checkpoint references.
- Report an unmatched checkpoint step as missing even for a long decimal selector.
- Read evaluation series names as text when they look like numbers or other TOML values.
- Quote known evaluation series names in a single line when a series filter finds nothing.
- Report a full-match scenario that conflicts with a team's size as a play usage error.
- Spell the match count correctly in tournament rating rows.
- Use singular match counts in rating and evaluation text when one match was played.
- Resolve recording and tournament references in moment searches and reject impossible dates.
- Report invalid search numbers as usage errors.
- Require finite match clock times in search filters.
- Describe checkpoint fields, shell quoting and the full check requirement in command help.
- Show shell syntax for negated queries and multiword filter values in search help and the guide.
- Show the captions output path as printed in the command example.
- Save a tournament as stopped after Ctrl+C and describe where the status and ability appear.
- Accept earlier checkpoints in tournament scenario checks and distinguish their benchmark groups.
- Give isolated format checks separate terminals when benchmark tasks run concurrently.
- Serialize format status updates across benchmark workers to preserve failure reasons.
- Apply an explicit team size when registering a folder with a saved identity.
- Describe the Kaggle load check coverage in its verdict and explain command defaults and inputs.
- Verify Kaggle overage charges against measured round trips on each process sampler.

- Keep different checkpoints of one team separate in a rating run and in the evidence.
- Report a repeated rating model as a usage error.
- Keep checkpoint labels and training steps in ratings after tournament matches.
- List each rated checkpoint separately in the tournament ratings command.
- Report an invalid training step as a usage error.
- Defer numerical imports until the arena needs them to read or play frames.
- Read saved statistics when showing a completed evaluation.
- Summarize active and older evaluations without importing NumPy.
- Read saved rating runs and fit built-in rating tables without importing NumPy.
- Document the process tree boundary for guarded submissions in mixed matches.

- Print the command that finds valid names after a mistaken team, format, plugin or run identifier.
- Distinguish bad arguments from refusals and environment failures in headless checks.
- Let one caller adopt the earlier home when two callers start together.
- Keep the engine version under the cuju key in evaluation manifests.
- Keep home adoption notices quiet on request. Show submission diagnostics on standard error with
  the verbose flag. Report a missing preflight dependency as an environment problem.
- Keep checkout ignore files out of the source distribution.
- Put the Torch stand-in first on the import path in the thread limit test.
- Read the current source of folder plugin modules after edits within the same second.
- Name the arena in the message for a refused process start.
- Measure the process trees of nested check matches from the host.
- Give a completed benchmark attempt time to release its format worker and CPU allocation.
- Point submission checks given a registered team identifier to its folder.
- Describe the team sizes that academy matches accept.
- Name the field that keeps match difficulty.
- Explain how GRF counts steps at the time limit.
- List the default Kaggle actions and explain what the full load check covers.
- Point a cancel of a finished tournament to the removal command.
