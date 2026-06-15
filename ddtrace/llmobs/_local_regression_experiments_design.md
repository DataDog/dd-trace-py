# Design: Trace-Seeded Local Regression Experiments

**Status:** Draft / brainstorm — not yet implemented.
**Author:** mehul
**Last updated:** 2026-06-04

## 1. Motivation

Today, running an LLMObs experiment requires the user to curate a `Dataset` and
author an `Experiment` harness. That's great for deliberate evaluation, but it
does not serve the *inner dev loop*: a developer who has just changed a prompt,
model, or piece of application logic and wants to answer one question quickly —

> "Did my local change alter what my app produces, compared to what it did in
> production, on real historical inputs?"

This is **snapshot / golden-master regression testing, seeded from production
traces** — the LLM analogue of unit tests. The developer's already-instrumented
app *is* the system under test; production traces *are* the fixtures.

## 2. Paradigm

After a user has instrumented their app with the LLMObs decorators
(`@workflow`, `@task`, `@agent`, `@llm`), they:

1. Mark a **top-level `@workflow`** as an experiment subject with a new,
   inert-by-default decorator.
2. (Optionally) author a small **YAML config** describing *where to pull data
   from* and how to run.
3. Run an **explicit command** — `ddtrace-experiment run [--config experiment.yaml]` —
   which pulls matching production traces, synthesizes an ephemeral dataset,
   re-executes the *current local code* against those inputs, compares against
   the historical output, and reports results both in the terminal and in the
   LLMObs UI.

Crucially, none of this fires during normal app execution — see §4.

## 3. Decisions (converged)

| Area | Decision |
|------|----------|
| **Subject scope** | Top-level `@workflow` only. Keeps replay inputs JSON-serializable and the capture boundary clean. |
| **Baseline** | Regression by default (`expected_output` = recorded prod output), but fully configurable — the user may pass their own `evaluators=[...]`. |
| **Default comparator** | LLM-judge equivalence (overridable + stackable). Handles non-deterministic text far better than string diff. *(Open: cheaper embedding-similarity default — see §8.)* |
| **Dataset lifetime** | Ephemeral, in-memory, synthesized from traces each run. Never persisted to the backend. |
| **Result surface** | Both — a terminal per-row diff table for the fast loop, and a streamed Experiment visible in the LLMObs UI with full history. |
| **Trigger** | Explicit command (hard gate) + optional YAML config (soft refinement). |
| **Config format** | Standalone YAML, passed via `--config`. |
| **Config requirement** | Optional. Missing config → run with sane defaults. |

## 4. Safety / gating — the crux

The new decorator lives on code that *also runs in production*. Replaying
traffic and re-executing live code in a prod process would be a severe footgun,
so the activation must be **structurally impossible in prod**, not merely
discouraged.

### Principle: positive, explicit activation — never environment detection

We do **not** try to auto-detect "local vs. prod." Every available signal
(`DD_ENV`, TTY presence, source-checkout, hostname) is a heuristic that
eventually *fails open* in production. A safety gate must never fail open.

Instead, activation requires a **deliberate signal that prod never produces**:

- **Primary gate (required):** the user invoked the `ddtrace-experiment`
  command. This flips an **in-process sentinel** *before* importing the user's
  module. Prod imports the module the normal way → sentinel never set → the
  decorator is completely inert (no trace queries, no network, no flag checks
  beyond the sentinel).
- **Secondary signal (optional):** a user-authored config file passed to the
  command. When present, it's a second independent signal prod never carries.
- **One-way fail-safe (backstop):** even if the sentinel were somehow set,
  additionally refuse to run when it smells like prod
  (`not sys.stdout.isatty()` **and** `DD_ENV == "prod"`). This can only ever
  make the gate *more* restrictive, never less.

The `pytest` plugin variant (§7) gets the same property for free: running under
pytest is both an explicit entrypoint and a context prod genuinely never enters.

### Why this is robust

All active behavior lives in the **launcher**, which only runs when the command
is invoked. The decorator shipped into production is a pure no-op marker. The
command is sufficient on its own; the optional config and the env fail-safe are
defense-in-depth.

## 5. Responsibility split

| Lives in… | Holds | Rationale |
|-----------|-------|-----------|
| **Decorator** (app code) | "this `@workflow` is an experiment subject" — a marker, nothing else | Stable, rarely changes, safe in prod because inert. |
| **Config file** (user-authored YAML) | *where to pull data* (ml_app, env, time window, sample size, dedup, include-errors), comparator + threshold, jobs/runs, project name, push-to-UI toggle | The volatile knobs — tune trace selection without editing instrumented code. |
| **Code** (optional) | custom `evaluators=[...]`, custom comparator | Evaluators are logic; they belong in Python. |

## 6. Execution flow

```
$ ddtrace-experiment run --config experiment.yaml
   1. Parse config (or fall back to defaults).
   2. Flip in-process experiment sentinel.
   3. Run prod fail-safe check; abort if it smells like prod.
   4. Import the user's module → @experiment decorators register (no longer no-op).
   5. Discover marked top-level workflow(s).
   6. Query production traces per config (ml_app / env / window / filters).
   7. Synthesize an ephemeral Dataset:
        input_data       = recorded workflow input
        expected_output  = recorded workflow output (regression baseline)
   8. Run the existing Experiment.run() engine:
        task       = the user's current local workflow
        evaluators = [default comparator] + any user-supplied evaluators
   9. Report:
        - terminal: per-row diff table (input, prod output, new output, verdict)
        - LLMObs UI: streamed as a normal Experiment with history
```

The heavy lifting (concurrency, retries, metric streaming, summary evaluators)
is **reused from the existing `Experiment.run()` engine** — this feature is
mostly *dataset synthesis from traces* + *a default comparator* + *the launcher
and gate*.

## 7. Trigger implementations (shared core)

Both ride a shared sentinel/registry core; the second can be added later.

- **CLI launcher** — `ddtrace-experiment`, a console-script sibling of the
  existing `ddtrace-run` (`pyproject.toml` → `ddtrace/commands/`). Max control,
  no pytest dependency.
- **pytest plugin** — decorated workflows collected as test items; pytest's
  collection is the gate. Purest "unit test" feel, free CI/reporting; reports a
  score/diff rather than a hard pass/fail (semantic equivalence is fuzzy).

## 8. Example config (illustrative)

```yaml
# experiment.yaml — all fields optional; shown with example defaults
data:
  ml_app: my-llm-app          # default: inferred from DD_LLMOBS_ML_APP
  env: prod                   # which traces to pull from
  time_window: 24h            # last 24h
  sample_size: 50             # cap number of seeded records
  include_errors: false       # skip traces that errored in prod
  dedup_by_input: true        # collapse duplicate inputs
comparator:
  type: llm_judge             # llm_judge | embedding_similarity | exact
  threshold: 0.8              # for similarity-based comparators
run:
  jobs: 10                    # concurrency
  runs: 1                     # passes (for non-deterministic tasks)
  project: local-regressions  # LLMObs project name
  push_to_ui: true
```

## 9. Open questions

1. **Zero-config default comparator** — LLM-judge (accurate, costs tokens) vs.
   embedding-similarity (cheaper, needs an embedding model). Currently leaning
   LLM-judge; revisit on cost.
2. **Trace-selection defaults** — exact defaults for window / sample size /
   error inclusion / dedup when no config is supplied.
3. **Multiple marked workflows** — run all discovered subjects, or require the
   command to name one?
4. **Input replayability** — guardrails for top-level workflows whose recorded
   input isn't cleanly JSON-serializable.
5. **Which trigger first** — CLI launcher vs. pytest plugin.
```
