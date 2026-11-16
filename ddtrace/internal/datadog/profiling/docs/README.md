# Python Profiler Documentation

Docs for the profiler in `ddtrace/profiling/`, `ddtrace/internal/profiling/` and `ddtrace/internal/datadog/profiling/`.

File names use PascalCase. `README.md` is the only exception.
Add a row here for each new doc.

| Doc | Covers |
|-----|--------|
| [Architecture](Architecture.md) | Components, lifecycle, collectors, sample flow to upload, fork/gevent/uWSGI handling, design decisions |
| [Standalone](Standalone.md) | Building and testing the native code outside `setup.py` |

Related: [native code review guide](../../../../../docs/native-code-review.md).
