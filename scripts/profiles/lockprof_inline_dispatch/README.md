# lockprof_inline_dispatch — micro-benchmark

Quantifies the cost of extracting the `sampler.capture()` check in
`_ProfiledLock` into a shared `def` helper vs. inlining it at each
entry point (`acquire`, `__enter__`, `__aenter__`, and their release
counterparts).

Review question that prompted this (PR #17639):

> Are we sure this is really worth it? Would these function calls be
> Python ABI calls or simply C calls (in which case they could/would
> probably be inlined by the compiler)?

Short answer: `_ProfiledLock` is a regular Python class (`class`, not
`cdef class`). A `def` method on a regular Python class goes through
the full Python call machinery — frame creation, argument tuple/dict
packing, ref-counting — even when the caller is Cython. The C compiler
cannot inline that.

## What's being measured

Three lock shapes, all wrapping a real `threading.Lock()` and all using
the production `CaptureSampler` configured at `capture_pct=0.0` so every
call stays on the unsampled hot path:

| Module             | Shape                                              | What it represents                      |
| ------------------ | -------------------------------------------------- | --------------------------------------- |
| `bench_inline`     | `class Lock`, sampler check inlined in `acquire`   | The PR shape                            |
| `bench_dispatch`   | `class Lock`, `acquire` → `_acquire` helper `def`  | The "refactored to shared helper" shape |
| `bench_cdef`       | `cdef class Lock`, `cdef inline bint` helper       | What a full refactor to cdef class buys |

## How to run

Needs a Python environment with ddtrace + Cython available, matching an
in-tree `_sampler.cpython-*.so`:

```bash
python scripts/profiles/lockprof_inline_dispatch/benchmark.py
```

Each bench module is cythonized on first run into a temp directory with
`include_path=[REPO_ROOT]` so the `cimport CaptureSampler` resolves to
the in-tree `ddtrace/profiling/collector/_sampler.pxd`.

## Representative results (Python 3.12.11 / macOS arm64)

```
acquire()+release() pair:
  NATIVE    :    38.3 ns/op
  INLINE    :   171.7 ns/op    +133.3 ns/pair vs native
  DISPATCH  :   318.5 ns/op    +280.2 ns/pair vs native
  CDEF      :   167.1 ns/op    +128.8 ns/pair vs native

DISPATCH − INLINE (acquire+release pair):  +146.9 ns/pair
DISPATCH − INLINE (acquire only):           +67.6 ns/op
DISPATCH − INLINE (release only):           +78.5 ns/op
```

## Interpretation

- Extracting the sampler check into a shared `def` helper costs
  ~70-80 ns **per** `acquire` and **per** `release`, i.e. ~150 ns
  per acquire/release pair.
- That's almost double the inlined wrapper overhead (+280 ns/pair
  vs +133 ns/pair over native `threading.Lock`).
- CDEF matches INLINE within noise, confirming the reviewer's
  intuition that a `cdef inline` helper *would* be free — but only
  as a `cdef class`, which `_ProfiledLock` currently cannot be
  (single inheritance, fixed layout, no dynamic Python attributes).
- At realistic lock traffic of ~1e7 acquire/release pairs/second in
  a busy async app, the difference is ~1.5 seconds of extra CPU per
  second of real time.

Conclusion: the duplication on the unsampled path is justified; the
`AIDEV-NOTE` on the PR warning against refactoring it into a shared
helper is correct.
