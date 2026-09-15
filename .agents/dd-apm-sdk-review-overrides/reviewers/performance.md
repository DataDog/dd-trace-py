Override for `reviewers/performance.md` (in the core skill folder) — read that file first, then this.

# Performance — dd-trace-py specifics

This file starts with one confirmed pattern and should grow. Do not treat it as exhaustive.

The source of truth is **AGENTS.md** as written. Quote these two lines; do not paraphrase them into a weaker rule:

- Project rule 9: "Performance matters — This library runs in production hot paths. Benchmark changes to C/C++/Cython/Rust code."
- Key Architecture: "Performance-critical code uses C/C++/Cython/Rust — profile and benchmark when touching these paths."

`.cursor/rules/dd-trace-py.mdc` only redirects to `AGENTS.md`. It has no performance section of its own.

## Do not reimplement native encode/tagset helpers in Python

`ddtrace/internal/_encoding.pyx` and `ddtrace/internal/_tagset.pyx` (`encode_tagset_values`) already own per-request payload / propagation-tagset encoding. A new Python function that rebuilds that same `key=value,key=value` (or msgpack/payload) work on the request path is at least **P1**. The fix is to call the existing helper, not to add another Cython module. Import-time or one-off setup Python is not this finding.

AGENTS.md requires a profile or benchmark **before** prescribing new C/Cython. Do not recommend a native rewrite from the snippet alone.

This is **not** ordinary span tagging. `span.set_tag` / `set_http_meta` / a small per-request tag dict in an integration (`ddtrace/_trace/span.py`, `ddtrace/contrib/internal/trace_utils.py`, ASGI/WSGI middleware) is the house pattern. Do not raise this finding for that.
