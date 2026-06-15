# Trace-seeded local regression experiments — POC

Stdlib-only, self-contained prototype. **Not wired into ddtrace yet** — it
exists to validate the `@experiment_start` / `@experiment_end` decorator +
stop-point mechanics before integrating with the LLMObs SDK.

See the design doc: `ddtrace/llmobs/_local_regression_experiments_design.md`
and RFC-002 in the Obsidian vault.

## Run it

```bash
cd poc_experiments

# 1) Capture a baseline from a real run (emit side-effects fire, cases recorded)
python experiment_poc.py capture example_app:generate_traffic

# 2) Replay with no code change -> all MATCH, and NO emit side-effects
#    (the @experiment_end stop-point unwinds before emit runs)
python experiment_poc.py replay example_app

# 3) Simulate a local change and replay -> CHANGED rows with a before/after diff
PROMPT_SUFFIX='!' python experiment_poc.py replay example_app
```

`experiment_cases.jsonl` is the local "span dump" (the ephemeral dataset stand-in).

## What this proves

| Property | Where you see it |
|---|---|
| Input-in / output-out in **different functions**, replayed as **one unit** | `ingest` (start) vs `emit` (end); replay invokes only `ingest` |
| `@experiment_end` is a real **stop point** | replay shows no `[emit side-effect]` lines |
| Decorators are **inert in prod** (gated) | `import example_app` runs normally, nothing recorded |
| **Regression detection** | step 3 reports `CHANGED` baseline→new |

## Known POC shortcuts (to address on the path to real)

- Sync only (real workflows are async — `ContextVar` already propagates across `await`).
- Comparator is exact-match (`_default_comparator`); swap for LLM-judge equivalence.
- Span source is a local JSONL capture; later swap for a Datadog trace pull.
- Trigger is `python experiment_poc.py`; later a `ddtrace-experiment` console script.
- One end-hit per unit assumed; loops/retries (multiple `emit`s) not yet handled.
