# Paste-ready PR body corrections (version-claim audit)

Do **not** `gh pr edit`. Paste locally. Stack order: #19267 → #19269 → #19270 → #19272 → #19273 → #19254.

Diff each PR against its parent in the stack, not `origin/main`.

---

## #19267 (`vlad/315-peripheral-compat`) — drop the starts_line claim

Another agent owns the `_instrumenter.py` hunk. Live body still asserts a 3.15 `dis.Instruction.starts_line` change. That became a bool with `line_number` in **3.13** (What's New in Python 3.13, `dis` section). `main`'s existing `>= (3, 14)` branch already uses `line_number`.

Keep the logging `__version__` bullet (What's New in Python 3.15 deprecates `logging.__version__`, removal in 3.20).

---

## #19269 — keep (native ABI is 3.15)

Live body is accurate: PyFrameState renumber, `FRAME_OWNED_BY_CSTACK` removal, layout-contract tests. Primaries: `Include/internal/pycore_frame.h` and `pycore_interpframe_structs.h` at `v3.15.0a7`; `docs/cpython-diffs/analysis_314_to_315.md`.

Do not claim this PR lifts `setup.py` native-build gating.

---

## #19270 — body claimed a lift the code did not do

Live summary said `setup.py` can build natives on 3.15. The three native skips (`rust_features`, `build_libdd_wrapper`, CMake memalloc/ddup/stack) were still `< (3, 15)`. Local commit on this branch lifts them to `< (3, 16)` so the body becomes true.

Draft delta vs #19269:

```
## Summary

Python collector updates for 3.15 **and** riotfile/CI matrix wiring (formerly #19271).
Lifts the remaining `setup.py` native-build skips from `< (3, 15)` to `< (3, 16)`
(Rust `profiling` feature, `libdd_wrapper`, memalloc/ddup/stack) so a 3.15 wheel
actually compiles the ABI from #19269. Cython profiling modules were already
un-gated to `< (3, 16)`.

`PY_UNWIND` as a per-code local event is a real 3.15 change (What's New in
Python 3.15 / gh-146182). `sys.monitoring` itself is 3.12 (PEP 669); the
module-level 3.15 ImportError is pre-existing and is #19601's job to lower.

| Layer | This PR |
| --- | --- |
| Compiled | setup.py native-build un-gate for 3.15 |
| Armed | collectors + CI/build matrix |
| Observable | profiling suites run on 3.15 in CI |
```

---

## #19272 — keep the 3.15 gate; do not claim the API is new

`sys.monitoring` was added in 3.12 (What's New in Python 3.12 / PEP 669). The `>= (3, 15)` gate stays because wrapping is unavailable on 3.15. Reconcile with #19601 (`vlad/multiplexer_312+`), which enables the multiplexer on 3.12+.

Draft:

```
## Summary

Replace bytecode wrapping with a profiling-local `sys.monitoring` PY_RETURN
path in `_asyncio.py`. Gated at Python 3.15 because wrapping is unavailable
there, not because `sys.monitoring` is new (PEP 669, 3.12). Other asyncio
hooks stay as attribute replacement. Rolling the path down to 3.12–3.14
waits on #19601 (multiplexer 3.12+) plus CI coverage.

| Layer | This PR |
| --- | --- |
| Compiled | — |
| Armed | asyncio task-creation hooks on 3.15 via sys.monitoring |
| Observable | asyncio profiling parity on 3.15 without wrapping |
```

---

## #19273 — docs only; runbook no longer says hasattr-for-3.15

Local commit rewords `docs/contributing-profiling-new-cpython.rst` so the 3.15 row cites this stack and PEP 669, not closed #17294 / hasattr guards.

---

## #19254 — packaging `<3.16` is mechanical; leave it

`requires-python = ">=3.9,<3.16"` and SSI max `(3, 16)` are upper bounds, not CPython-behavior claims. After #19270's native un-gate is rebased through, drop the pyproject comment that natives are "still gated until the stack lands."
