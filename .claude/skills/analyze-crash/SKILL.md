---
name: analyze-crash
description: Native crash log analysis for dd-trace-py
argument-hint: <crash-uuid-or-paste-log>
disable-model-invocation: false
context: fork
agent: general-purpose
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - TodoWrite
  - WebSearch
---

# Native crash investigation

Crashtracker captures process-level native crashes, including failures outside dd-trace-py.
Establish evidence before attributing a crash; a frame identifies executing code, not necessarily
the component that corrupted memory or caused the failure.

## Obtain the crash

Use a supplied log directly. For a UUID, the bundled `fetch_crash.py` fetches and processes
metadata, frames, binary mappings, and a log link. Read its `--help` for supported filters.
Run from the repository root with an authenticated environment containing `DD_API_KEY` and
`DD_APP_KEY`, without printing them:

```bash
uv run --no-project --with datadog-api-client python .claude/skills/analyze-crash/fetch_crash.py <uuid>
```

Use an available credential wrapper if needed. Do not ask for a UUID already supplied or require
optional filters before the first lookup. If fetching is unavailable, explain the missing access
and continue with any supplied crash data.

## Match the binary to source

1. Prefer a supplied build SHA or crash metadata identifying the exact release/build.
2. Resolve a release version to its tag and verify the commit exists. Fetch a missing ref when
   needed; lack of a local tag does not mean the release is unknown.
3. Inspect files at that revision with `git show <ref>:<path>` or a separate checkout. Merely
   putting the right ref in a URL while reading another revision produces misleading citations.
4. If the build cannot be identified, label source inspection as exploratory. Ask for the missing
   version when it blocks a conclusion; never substitute the local branch or `main` as the crashing build.

Use the resolved SHA for GitHub file/line links. Match CPython and native dependency versions too;
release-wheel symbols and layout may differ from an editable build.

## Investigate

- Identify the crashing thread, signal, architecture, runtime, and loaded binaries. Resolve
  addresses with mappings and available symbols. Stripped symbols may only approximate a location.
- Distinguish similarly named modules by their full paths. For example, Pyroscope's
  `_native__lib` under `pyroscope/` is different from ddtrace's `_native` under
  `ddtrace/internal/native/`. Neither name alone establishes root cause.
- Locate relevant source using symbol names and build configuration. Read the full lifetime,
  lock, or error path needed to explain the failure, rather than a fixed line count.
- Use `.cursor/rules/native-code.mdc` to select relevant sections of `docs/native-code-review.md`.
  For periodic threads and fork hooks, also read the relevant `ddtrace/internal/README.md` sections.
- Verify version-dependent behavior against the actual CPython/library source. Historical crash
  patterns are hypotheses, not proof that this crash has the same cause.
- Inspect history scoped to the crashing revision (`git log <ref> -- <path>`), then separately
  check later fixes. Link relevant PRs and read nearby invariant comments.
- Check upstream issues when third-party or CPython behavior is involved. Distinguish a library
  fault from interaction between components; missing ddtrace frames do not rule out earlier corruption.

## Report

Lead with what is established, the source revision used, and the remaining uncertainty.
Include only relevant frames, source links, supporting history, and the next evidence needed.
Separate observations from hypotheses in the proposed crash flow. Attribution may remain unknown.

For a triage request, focus on diagnosis. If the user also requests a fix, use the investigation
to guide it rather than treating this skill as a prohibition on implementation. Unless the user
requests an inline-only answer, save a Markdown report in the task's output location and link it.
Keep reports outside the skill's source directory and out of commits; crash metadata may be sensitive.
