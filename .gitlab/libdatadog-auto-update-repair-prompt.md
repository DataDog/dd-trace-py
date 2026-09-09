# libdatadog Auto-Update Repair

You are repairing the dd-trace-py CI pipeline after an automated bump of the
pinned libdatadog dependency to the new commit listed in the Environment
section. The full CI pipeline for the bumped branch ran and some jobs failed.
Your task:

1. Read the CI summary and the failure trace files whose paths are given in
   the Environment section.
2. Classify each failure as: an API change to fix / a libdatadog bug to
   report / a flaky or unrelated failure to ignore.
3. For API changes: edit the dd-trace-py native (Rust) sources to adapt.
4. Finish with a brief report of what you found and did.

## Important constraints

- You **cannot** run cargo, make, or any other build/test commands. There is
  no way for you to verify your changes. Reason about them from the error
  messages and the source code alone.
- Do **not** edit anything outside the dd-trace-py repository root.
- Do **not** skip existing code just to satisfy tests: do not comment out
  failing tests, do not weaken assertions, lints, or trait bounds, and do not
  downgrade version requirements to hide incompatibilities.
- Keep the changes as small and mechanical as possible. An independent
  reviewer agent will reject non-mechanical changes.

## Classification rules

### Fix (API changes in libdatadog)

These are expected during libdatadog development and should be adapted:

- A crate was renamed or moved → update the entries in
  `src/native/Cargo.toml` (a failing cargo resolution error points at the old
  name)
- A type, function, method, or module was renamed → update all call sites
- A function signature changed (new parameters, changed return type) → update
  call sites
- A struct gained required fields → add them with sensible defaults
- A struct lost fields → remove all references
- A trait gained required methods → implement them
- An enum variant was renamed or added → update match arms and constructors
- A public re-export moved to a different path → update `use` statements

Look up the new API in the libdatadog source tree whose path is given in the
Environment section before editing.

### Report but do NOT fix (libdatadog bugs or design issues)

Do not try to work around these — leave the code unchanged and report them in
your final summary:

- A panic or unexpected behaviour coming from *inside* libdatadog (not from
  our code calling it incorrectly)
- A regression where functionality that previously worked no longer does (and
  there is no obvious new API to call instead)
- An API change so large it would require significant redesign of our
  architecture. But first give it a try! Maybe it's not that bad.

### Ignore (test flakiness or unrelated failures)

Make no change for these; mention them in your final summary:

- Failures that mention timing, sleep, race condition, network, or `flaky`
- Failures in tests completely unrelated to libdatadog
- A single failure in a test where the log shows an external resource issue

## Final summary

End with a short report: what you changed (file + why), which failures you
classified as libdatadog bugs, and which you ignored as flaky/unrelated. If you
decide no change is appropriate, say so explicitly and make no edits.
