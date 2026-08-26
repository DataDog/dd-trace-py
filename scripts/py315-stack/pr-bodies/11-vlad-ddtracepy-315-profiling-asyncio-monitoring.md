**prev:** [#19270](https://github.com/DataDog/dd-trace-py/pull/19270) | **next:** [#19273](https://github.com/DataDog/dd-trace-py/pull/19273)

## Summary

Replace bytecode wrapping with a profiling-local `sys.monitoring` PY_RETURN path in `_asyncio.py`. Gated at Python 3.15 because wrapping is unavailable there, not because `sys.monitoring` is new (PEP 669, 3.12).

## Test plan

- [ ] Asyncio profiling tests green on 3.15
- [ ] Stack merges cleanly into the next PR's base branch
