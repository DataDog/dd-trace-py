# Python Profiler Documentation

This directory documents the Python profiler: the code in `ddtrace/profiling/`, `ddtrace/internal/profiling/` and `ddtrace/internal/datadog/profiling/`.

Doc file names use PascalCase, except this `README.md`.
When you add a doc, add a row for it to this table.

| Doc | Covers |
|-----|--------|
| [Architecture](Architecture.md) | Components, lifecycle, collectors, how samples reach the upload, fork, gevent and uWSGI handling, design decisions |
| [Testing](Testing.md) | Building the profiler and every local and CI check (Python suites, native CTest, sanitizers, Valgrind, clang-tidy, Rust, wheels, fuzzing, prof-correctness, GPU, benchmarks) |
| [Standalone](Standalone.md) | Building and testing the native code outside `setup.py` |
| [GCPause](GCPause.md) | Design of the native GC pause tracker (`ddtrace.internal.runtime._gc_pause`) behind the GC Time collector, and the `gc.callbacks` helpers it shares with the stack sampler |

See also the [native code review guide](../../../../../docs/native-code-review.md) and [architectural constraints](../../../../../docs/contributing-architecture.rst).
