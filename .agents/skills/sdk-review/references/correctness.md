# Correctness

Bugs, races, unhandled edge cases, and tests that pass when they shouldn't. For C, C++, Rust, and Cython changes, the [native code review guide](../../../../docs/native-code-review.md) also applies.

**When planning:** take the rules below into account when designing the change.

**When reviewing:** report a finding when the diff matches a rule's `Flag:` line and no `Not when:` applies, or when it has a clear problem in this lens's area that no rule covers (mark it `no rule`). Cite the rule ID, location, and the failing input or interleaving.

## 1. Fork, runtime ID and lifecycle

### COR-1.1: Preserve fork-time behaviour of runtime-ID and fork hooks
- Flag: a change to `_set_runtime_id`, `on_runtime_id_change`, or a fork hook that stops notifying existing subscribers, skips some registrations, or makes correctness depend on the order of runtime-ID regeneration and writer recreation.

### COR-1.2: Hold production hooks with strong references; make tests clean up instead
- Flag: a subscriber or hook registry switched to `weakref`/`WeakMethod` (often to help tests), which silently drops bound methods and lambdas that nothing else references.
- Not when: the registry maps per-request objects (for example, request contexts) that would leak if an end event is missed.
- ❌ `_subscribers.append(weakref.WeakMethod(fn))`
- ✅ `_subscribers.append(fn)`, with the test unregistering it in teardown

### COR-1.3: Unregister in a product's `stop()` what its `start()` registered
- Flag: a product that registers subscriber bundles or core listeners on start with no matching unregister on stop or disable.

## 2. Concurrency

### COR-2.1: Don't add locks that callers can bypass
- Flag: a lock around setters (for example, `sampling_priority`) for state that other code mutates directly (`ctx._meta[...]`, `ctx._metrics[...]`), or a lock that only repeats what the GIL already guarantees.

## 3. Edge cases

### COR-3.1: Build WSGI request paths from `SCRIPT_NAME` plus `PATH_INFO`
- Flag: a request path or route computed from `PATH_INFO` alone, which is wrong for apps mounted under a prefix.

### COR-3.2: Don't leave unmarked stubs reachable from Python or libdatadog
- Flag: new code that reaches `todo!()`/`unimplemented!()`/`raise NotImplementedError` with no TODO comment, or one that foreign code can call, which panics inside the customer process. This includes a stub in the diff, and new code that delegates to an existing stub through `Self::from`, `.into()` or a trait method. Follow each new delegation to the impl it calls.
- ❌ `fn from_owned(value: String) -> Self { Self::from(value) }`, where `impl From<String>` is `todo!()`
- ✅ implement `From<String>`; or remove `from_owned` until it is needed; or mark it `// TODO: unimplemented, From<String> is a todo!()` and make sure no caller can reach it

### COR-3.3: Keep package metadata consistent with what is published
- Flag: a trove classifier or supported-version claim for a Python version that `requires-python` excludes or that has no published wheels yet.

## 4. Tests

### COR-4.1: Reset benchmark state between pyperf runs
- Flag: a benchmark scenario that reuses a writer, buffer, or cache across runs, so later runs measure accumulated state.

## Don't flag
- A `contextlib.suppress(Exception)` or broad `except` around ddtrace-internal analysis in an instrumentation callback, which keeps ddtrace failures out of the application.
- Missing float handling for a header whose format the source documents as an integer.
