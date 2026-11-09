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
uv run --no-project --with datadog-api-client python .agents/skills/analyze-crash/fetch_crash.py <uuid>
```

Use an available credential wrapper if needed. Do not ask for a UUID already supplied or require
optional filters before the first lookup. If fetching is unavailable, explain the missing access
and continue with any supplied crash data.

For pasted JSON, inspect `error.stack` (JSON text, a frame list, or an object containing `frames`)
and `files["/proc/self/maps"]`; `fetch_crash.py::process_crash_log` documents the supported shapes.
Keep available instruction addresses, binary paths/offsets, symbols, and source coordinates;
record incomplete stacks as a limitation. Extract the signal, thread name, tracer/Python versions,
platform/architecture, and relevant feature flags from the supplied data rather than guessing.

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

These are starting points for locating native frames, not an attribution table. Verify mappings
in `setup.py`, the relevant CMake files, and `src/native/Cargo.toml` at the crashing revision;
extension suffixes vary by platform and Python version.

| Binary or symbol clue | Source starting point |
|-----------------------|-----------------------|
| `_threads`; `PeriodicThread_*`, `GILGuard`, `AllowThreads` | `ddtrace/internal/_threads.cpp`; lifecycle callers in `ddtrace/internal/threads.py` |
| `_native` under `ddtrace/internal/native/`; `TraceExporterPy`, crashtracker symbols | Rust in `src/native/`, especially `data_pipeline/mod.rs` and `crashtracker.rs`; dependency symbols may belong to a pinned crate |
| `_memalloc`; `memalloc_*` | `ddtrace/profiling/collector/_memalloc.cpp`, `_memalloc_tb.cpp`, and adjacent allocator helpers |
| `_ddup`; `ddup_*` | `ddtrace/internal/datadog/profiling/ddup/` and the C++ interface in `ddtrace/internal/datadog/profiling/dd_wrapper/` |
| `_stack`; sampler, renderer, or Echion frames | `ddtrace/internal/datadog/profiling/stack/`, including `src/echion/` |
| `libdd_wrapper`; `Datadog::Profile`, `Datadog::Sample`, `Datadog::Uploader` | `ddtrace/internal/datadog/profiling/dd_wrapper/` |
| `_native` under `ddtrace/appsec/_iast/_taint_tracking/`; taint operations | `ddtrace/appsec/_iast/_taint_tracking/` |
| `_stacktrace` under `ddtrace/appsec/_shared/` | `ddtrace/appsec/_shared/_stacktrace.c` |
| `__pyx_*` in `_threading`, `_exception`, `_sampler`, `_lock`, `_task`, or `_encoding` | Cython sources under `ddtrace/profiling/`, `ddtrace/profiling/collector/`, or `ddtrace/internal/_encoding.pyx`; resolve the owning module before mapping generated C/C++ lines to `.pyx` |

Treat `Py*`/`_Py*` runtime frames as CPython investigation leads and `pthread_*`, `malloc`,
`free`, or `abort` as system-library leads. Generic Rust/pyo3 or Cython symbol prefixes do not
identify ddtrace ownership; corroborate them with the mapped binary path.

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
- Use the historical PR links in `docs/native-code-review.md` when a pattern fits. Read the PR
  explanation and discussion, not only its commit title; verify that the affected version and
  prerequisites match before describing a historical fix as applicable.
- Check upstream issues when third-party or CPython behavior is involved. Distinguish a library
  fault from interaction between components; missing ddtrace frames do not rule out earlier corruption.

## Report

Lead with what is established, the source revision used, and the remaining uncertainty.
Include only relevant frames, source links, supporting history, and the next evidence needed.
Separate observations from hypotheses in the proposed crash flow. Attribution may remain unknown.

Cover the following when the evidence permits; explicitly identify material gaps:

- Tracer/build and Python versions, OS/architecture, signal, crashing thread, relevant feature
  flags, and the supplied crash identifier/log link. Include service context only when useful.
- The thread's role and entry point, intended operation, transitions between components, and
  observed failure point. Distinguish the observed signal from a hypothesized cause such as
  use-after-free, a race, allocator corruption, or exception unwinding.
- Relevant frame ownership, source evidence and history, whether a known pattern actually fits,
  and why attribution is ddtrace, CPython, third-party code, an interaction, or still unknown.
- Missing symbols, build details, other-thread stacks, or reproduction evidence needed to test
  the hypothesis. Do not fill gaps with the most familiar historical crash narrative.

For a triage request, focus on diagnosis. If the user also requests a fix, use the investigation
to guide it rather than treating this skill as a prohibition on implementation. Unless the user
requests an inline-only answer, save a Markdown report in the task's output location and link it.
Keep reports outside the skill's source directory and out of commits; crash metadata may be sensitive.
