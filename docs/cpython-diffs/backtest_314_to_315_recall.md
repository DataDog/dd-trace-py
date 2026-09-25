# Backtest recall: cpython_delta on v3.14.0 → v3.15.0a7

**Command:** `python3 scripts/cpython_delta/diff.py v3.14.0 v3.15.0a7 --backtest`  
**Artifacts:** `work_v3.14.0_to_v3.15.0a7.md` / `.json`  
**Ground truth:** `analysis_314_to_315.md` + merged #19269 (native ABI) + #19272 (`sys.monitoring` asyncio)

| ID | Source | Result |
| --- | --- | --- |
| frame_state_renumber | analysis §1 / #19269 tasks.h | HIT |
| frame_owned_by_cstack | analysis §2 / #19269 frame.cc | HIT |
| stackref_tags | analysis §3 | HIT |
| base_frame | analysis §5 | HIT |
| asyncio_debug_sym | analysis §6 | HIT |
| genobject_stack_peek | tasks.h / Objects/genobject.c | HIT |
| remote_debugging | tasks.h / Modules/_remote_debugging | HIT |
| asyncio_monitoring | #19272 `_asyncio.py` | HIT |

**Recall: 8/8 (100%).** Every real py-315 fix class the hand analysis and the two landed PRs needed appears as a work-list row (or an intersecting symbol). Agent reading pass still required for priority confirm/downgrade.
