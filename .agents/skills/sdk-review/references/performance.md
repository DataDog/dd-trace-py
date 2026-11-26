# Performance

Allocations and CPU cost on hot paths (span creation and finish, tagging, propagation, instrumentation callbacks), at startup (including import time), and in tooling.

This lens is a static review of the frozen diff. Do not run the [run-benchmarks](../../run-benchmarks/SKILL.md) or [profile](../../profile/SKILL.md) skills from this lens, because they execute the changed code. When [AGENTS.md](../../../../AGENTS.md) requires benchmarks for the change and the evidence shows none, report the missing benchmark coverage and recommend `run-benchmarks`.

**When planning:** take the rules below into account when designing the change.

**When reviewing:** report a finding when the diff matches a rule's `Flag:` line on a hot, startup, or high-volume path, or when it has a clear performance problem on such a path that no rule covers (mark it `no rule`). Cite the rule ID and say which path it is on.

## 1. Native boundary

### PERF-1.1: Stay in Rust when native code calls native code
- Flag: Rust that calls a native `#[pyclass]` through Python (`call_method1`, `getattr("service")`, calling the Python type with kwargs), or Python `Span.__init__` doing setup that native `SpanData.__new__` could do with the arguments it already has.
- ❌ `ctx.call_method1("copy", ())?`
- ✅ `ctx.borrow(py).copy()` (call the Rust method directly)

### PERF-1.2: Use Rust synchronisation inside native types
- Flag: a native `#[pyclass]` that holds a Python `threading.Lock` object.

## 2. Hot paths

### PERF-2.1: Register feature-specific listeners only when the feature applies
- Flag: a per-event check of the environment or feature state (for example, "running in an AWS Lambda MicroVM?") inside a listener registered for everyone, instead of registering the listener only when the condition holds.

### PERF-2.2: Keep imports at module level in per-request and per-span callbacks
- Flag: a function-local import inside a subscriber or callback that runs per request or per span.
- Not when: the import is a deliberate lazy import for startup cost or gevent compatibility and the callback is registered only when that feature is enabled.

### PERF-2.3: Don't repeat type checks on the request path
- Flag: the same `isinstance` check evaluated in several sequential `if` blocks on a per-request path, or a broad `isinstance(x, dict)` filter where a cheaper targeted check works.
- ✅ one `if`/`elif`/`else` chain

## 3. Startup

### PERF-3.1: Don't load product code at import time when the product is disabled
- Flag: product integrations (for example, LLMObs) loaded from `ddtrace/contrib/__init__.py` or another always-imported module, or blanket lazy exports added to get an import-time win instead of removing the eager load.

## 4. Benchmarks

### PERF-4.1: Show benchmark results for hot-path changes
- Flag: a change to span creation, finish, flushing, context, or propagation with no benchmark evidence in the PR. See [AGENTS.md](../../../../AGENTS.md).

### PERF-4.2: Keep benchmark scenarios realistic and measure only the operation
- Flag: correctness assertions inside the timed loop, no untimed warm-up, a single hard-coded trace/parent ID pair, or no variant that compares with and without the feature (for example, with and without propagation headers).

## Don't flag
- `Option` fields in native types kept deliberately for lazy allocation.
