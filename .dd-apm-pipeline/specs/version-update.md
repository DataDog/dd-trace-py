---
max_test_iterations: 10
---

Update this integration to support the latest version of the library it instruments. If the work is a tracer
runtime bump (Python itself, not a package being instrumented), use a runtime-update specification instead.

## What To Do

1. Identify the current state and reproduce the existing failure.
2. Check the library release notes, changelog, and source for relevant breaking behavior between the previously
   supported version and the upgraded version.
3. Update only the instrumentation surface affected by that behavior.
4. Fix affected tests and expectations only when the new library behavior requires it.
5. Verify all previously instrumented behavior remains correct with the new version.

## Constraints

- Reuse the existing integration tracing code wherever the library change permits it.
- Preserve span names, tags, resource naming, and distributed-tracing behavior unless the library makes this
  impossible.
- Do not remove instrumentation for still-supported features.
- Do not add unrelated new instrumentation.
- Keep compatibility with older supported package versions unless concrete upstream behavior makes that
  impossible.
- All applicable tests must pass after the update.
