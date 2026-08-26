**prev:** [#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | **next:** [#19272](https://github.com/DataDog/dd-trace-py/pull/19272)

## Summary

Python collector updates for 3.15 **and** riotfile/CI matrix wiring (formerly #19271). Lifts the remaining `setup.py` native-build skips from `< (3, 15)` to `< (3, 16)` (Rust `profiling` feature, `libdd_wrapper`, memalloc/ddup/stack) so a 3.15 wheel actually compiles the ABI from #19269.

`PY_UNWIND` as a per-code local event is a real 3.15 change (What's New in Python 3.15 / gh-146182). `sys.monitoring` itself is 3.12 (PEP 669).

Replaces merged **#19251**.

## Test plan

- [ ] Profiling collector tests green on 3.15
- [ ] Native extensions compile on 3.15 (`setup.py` no longer skips memalloc/ddup/stack)
- [ ] Stack merges cleanly into the next PR's base branch
