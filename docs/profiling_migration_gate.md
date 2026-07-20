# Profiler migration gate (major Python upgrades)

Use this checklist when landing a **major interpreter migration** (e.g. 3.14 → 3.15) or
any change that touches frame layout, bytecode, async generators, or native unwinding.

The gate is a **three-tier pyramid**. Each tier catches what the tier below mocks away.

## Tier 1 — Unit tests (`tests/profiling/`)

- White-box, in-process, deterministic, fast.
- Run via the normal `run-tests` / riot matrix on the new interpreter.
- Catches collector logic, serialization, config, and bytecode-level wrapping bugs.

## Tier 2 — prof-correctness (E2E, statistical)

- Black-box Docker scenarios in [`DataDog/prof-correctness`](https://github.com/DataDog/prof-correctness).
- Asserts real pprof output against `expected_profile.json` with error margins.
- **Required scenario families on the new interpreter:**
  - Baseline (**3.14**, latest released): `python_*_3.14` — mem-domain, exceptions,
    async-gen, lock (plus existing `python_cpu`, `python_basic_memory_*`, etc.)
  - Candidate (**3.15**): matching `python_*_3.15` scenarios, run via downstream CI
    with `DDTRACE_INSTALL_URL` (S3 wheel from the PR under test)
- **CI:** GitLab `prof-correctness` job (triggers `downstream-python.yml`) and GitHub
  Actions `.github/workflows/prof-correctness.yml` on profiling path changes.
- **Feedback loop:** any frame-level bug found in Tier 3 should be distilled into a new
  prof-correctness scenario so it becomes a permanent PR gate.

## Tier 3 — A/B staging (representative)

- Real service under identical load in ddstaging (see
  `experimental/teams/profiling-python/ddtrace-upgrade/`).
- For ai_gateway migrations, run split campaigns (do **not** combine knobs in one TD):
  - `ai_gateway_mem_domain` — mem_domain off vs on, same wheel
  - `ai_gateway_code_cache` — cache wheel vs baseline, mem_domain on both
- **Query discipline:** always scope CompView by `service:rapid-td-ab-<td>-{a,b}`.
- **Verdict:** ADR-11 (`PASS` / `INCONCLUSIVE` / `CONFOUNDED` / `REGRESSION`) via
  `staging_ab/analyze_run.py` + staging-profiling MCP.
- **Local bridge:** `ai_gateway_ab/local_repro/` promotes representative workloads into
  prof-correctness scenarios.

## Shared vocabulary

- prof-correctness `regular_expression` stack assertions and A/B `HYPOTHESIS_FRAMES` are
  the same concept: frames we expect to see or change. Keep one list per feature and reuse
  it in both tools.

## References

- A/B design: `experimental/teams/profiling-python/ddtrace-upgrade/ab_staging_experiments_design.md`
- Jul 17 results: `experimental/teams/profiling-python/ddtrace-upgrade/staging_ab/EXPERIMENT_RESULTS_20260717.md`
- prof-correctness README: https://github.com/DataDog/prof-correctness
