# Remove wrapt Dependency from Lock Profiler

**Jira:** https://datadoghq.atlassian.net/browse/PROF-12854

---

## Description

This PR removes `wrapt` dependency from lock profiler - replaced with direct delegation using `__slots__`. It implements the following proposal in the Lock Profiler RFC: [Remove wrapt dependency](https://docs.google.com/document/d/12ao0XhiO8SJpEB1PNku-Brzkm6ru4OQqgVDosiW_Bi0/edit?tab=t.0#heading=h.l944x0fppaqe)

### Why

- **Reduce memory overhead:** 75% reduction in per-lock memory usage
- **Simpler code:** No external dependency, easier to maintain
- **Consistent behavior:** No more WRAPT_C_EXT detection (wrapt's compiled C extension vs pure Python)

### Changes

- Replaced `wrapt.ObjectProxy` with internal `_ProfiledLock` class using `__slots__`
  - `__slots__` explicitly declares data members without using `__dict__`
  - Eliminates wrapt's 360-byte hidden dictionary
- Added `_LockAllocatorWrapper` as protocol wrapper
- Removed environment-dependent frame depth logic (2 if WRAPT_C_EXT, else 3)
- Implemented essential special methods: `__hash__`, `__eq__`, `__getattr__`

### Performance

**Measurement tools:**
- Memory: `gc.get_referents()` + `sys.getsizeof()` (direct object measurement)
- Performance: Custom `benchmark.py` with `tracemalloc` and `time.perf_counter()`
- Verification: Cache-busted tests in fresh Python processes

**Test configuration:**
Ran empirical benchmarks at 1% and 100% sampling rates:
- Memory: 1,000 locks
- Creation: 10,000 locks (5 iterations)
- Acquire/Release: 100,000 operations (5 iterations)
- Throughput: 10 threads × 10,000 operations

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Memory (wrapper) | 416 bytes/lock | 104 bytes/lock | -75% |
| Creation | 1.58 µs/lock | 1.56 µs/lock | +1% |
| Acquire/Release | 1,429 ns/op | 1,419 ns/op | +1% |
| Throughput | 738k ops/sec | 737k ops/sec | ≈ Same |

**Key findings:**
- Memory savings are consistent across all sampling rates (1%, 100%, etc.)
- No performance regression in synthetic benchmarks
- At 100k locks: saves ~30 MB

_Note: These are synthetic benchmarks. Real-world impact will be measured using [dd-trace-doe](https://github.com/DataDog/dd-trace-doe) benchmarking framework._

_See [`benchmarks/lock_profiler_wrapt_removal/VERIFIED_COMPARISON.md`](https://github.com/DataDog/dd-trace-py/blob/vlad/benchmark-lock-profiler/benchmarks/lock_profiler_wrapt_removal/VERIFIED_COMPARISON.md) for details._

### Implementation Notes

`wrapt.ObjectProxy` provided automatic delegation. We now implement these features directly:
1. Attribute forwarding via `__getattr__`
2. Identity operations via `__hash__` and `__eq__`
3. Context management via `__enter__`, `__exit__`, `__aenter__`, `__aexit__`
4. Direct method wrapping for acquire/release profiling

Removed/Not Needed:
- Transparent `isinstance()` checks
- Full introspection support (`dir()`, `vars()`)
- Pickle (serialization) support
- Weak reference support

## Testing

- All existing tests pass (updated `test_patch` for new behavior)
- Empirically verified with cache-busted tests on both branches
- Full benchmark results: [`vlad/benchmark-lock-profiler`](https://github.com/DataDog/dd-trace-py/tree/vlad/benchmark-lock-profiler/benchmarks/lock_profiler_wrapt_removal)

## Risks

**Low:** Existing functionality intact, as shown by unit/e2e tests and empirical benchmarks.

**Potential issues:** Users with esoteric workflows depending on deep wrapt features (unlikely).
