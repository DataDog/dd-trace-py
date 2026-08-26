**prev:** [#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | **next:** [#19272](https://github.com/DataDog/dd-trace-py/pull/19272)

## Summary

Python collector updates for 3.15 and riotfile/CI matrix wiring. Lifts remaining `setup.py` native-build skips to `< (3, 16)` so 3.15 wheels compile the ABI from #19269.

## Test plan

- [ ] Native extensions compile on 3.15
- [ ] Stack merges cleanly into the next PR's base branch
