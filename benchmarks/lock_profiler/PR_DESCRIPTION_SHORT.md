# Remove wrapt Dependency from Lock Profiler

**Jira:** https://datadoghq.atlassian.net/browse/PROF-12854

## What & Why

Replaces `wrapt.ObjectProxy` with internal `__slots__`-based wrapper, eliminating 360-byte hidden dict allocation per lock.

**Benefits:**
- **75% memory reduction** (416 → 104 bytes per lock)
- **No performance regression** (equivalent or +1% faster)
- **Simpler code** (no external dependency)
- **Consistent behavior** (no WRAPT_C_EXT detection)

## Changes

- Replaced `wrapt.ObjectProxy` with `_ProfiledLock` using `__slots__`
- Implemented `__hash__`, `__eq__`, `__getattr__` for compatibility
- Removed unused `_self_endpoint_collection_enabled` attribute
- Removed environment-dependent frame depth logic

## Performance (Empirically Verified)

### Memory
```
wrapt:    416 bytes (56 visible + 360 hidden dict)
unwrapt:  104 bytes (no hidden dict)
Savings:  312 bytes per lock (75% reduction)

At 100k locks: Saves ~30 MB
```

### Speed
| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Lock Creation | 1.58 µs | 1.56 µs | +1% |
| Acquire/Release | 1,429 ns | 1,419 ns | +1% |
| Throughput | 738k ops/sec | 737k ops/sec | ≈ Same |

**Note:** Memory savings consistent across all sampling rates (1%, 100%, etc.)

## Testing

- All existing tests pass
- Empirically verified with cache-busted tests
- Tested on 1% (production) and 100% (stress) sampling rates
- Full results: [`benchmarks/lock_profiler_wrapt_removal/VERIFIED_COMPARISON.md`](https://github.com/DataDog/dd-trace-py/blob/vlad/benchmark-lock-profiler/benchmarks/lock_profiler_wrapt_removal/VERIFIED_COMPARISON.md)

## Risks

**Low:** Existing functionality intact, verified by tests and benchmarks.

