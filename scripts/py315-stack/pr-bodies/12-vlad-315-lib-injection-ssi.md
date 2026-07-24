**prev:** [#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | **next:** [#19275](https://github.com/DataDog/dd-trace-py/pull/19275)

## Summary

SSI allow-list + wheel download for 3.15 auto-instrumentation (GAP-01). Closes #17813.


Replaces merged **#19258**.

## Test plan

- [ ] `scripts/run-profiling-tests --check-only` passes on 3.15 (when available)
- [ ] Docs build / link check

