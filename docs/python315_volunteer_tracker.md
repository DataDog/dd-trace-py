# Python 3.15 integration parity tracker

This issue tracks bringing dd-trace-py integration coverage on **Python 3.15**
to parity with the 3.14 release. The list below is the inverse of the
[dd-trace-py v3.16.0 release notes "still does not work with Python 3.14"](https://github.com/DataDog/dd-trace-py/releases/tag/v3.16.0)
section — for 3.15 we expect the same set of items to be skipped until each
row's blocker is resolved.

## How this works

- **Each checkbox below should result in one independent PR.**
  The 3.14 migration followed the same pattern (see @emmettbutler's PR series linked at the bottom).
- **Each unchecked checkbox below is also a real GitHub sub-issue** (created via the task-list "convert to issue" option on each bullet). The parent checkbox auto-flips when the sub-issue closes. Rows already pre-checked (`- [x]`) are already merged on `main` and don't need a sub-issue.
- **To claim a row**: click into the row's sub-issue (each `- [ ]` bullet links to one), then either self-assign on that sub-issue page (top-right "Assignees" → "assign yourself") or leave a `Taking this` comment **on the sub-issue itself** and a maintainer will assign you. Don't claim on this parent tracker — comments here are ambiguous about which row and add noise.
- **PRs that close a row** should use the
  [`python_315_bump`](https://github.com/DataDog/dd-trace-py/blob/main/.github/PULL_REQUEST_TEMPLATE/python_315_bump.md)
  PR template (added in [#17791](https://github.com/DataDog/dd-trace-py/pull/17791); open via `?template=python_315_bump.md`) and include `Closes #<sub-issue-number>` in the description. 
  That auto-closes the sub-issue on merge, which auto-flips the parent checkbox.
- **Most rows are a one-line `riotfile.py` `max_version=` bump + `riot generate <suite>` regen + lockfile commit.** A few are real integration work and are called out below.

## Tier A: wheel-build prerequisites

These must land before we can flip the cp315 wheel job from `allow-fail` to required. Scope is intentionally narrow — anything that can be *skipped at runtime on 3.15* the way 3.14 skipped Profiling / IAST / `ci_visibility` (see [dd-trace-py v3.16.0 release notes](https://github.com/DataDog/dd-trace-py/releases/tag/v3.16.0)) lives in Tier B, not here.

### Already on `main`

- [x] Test-runner / pyenv 3.15-dev — [#17710](https://github.com/DataDog/dd-trace-py/pull/17710) (merged) — `@vlad-scherbich`
- [x] CI Visibility coverage instrumentation for Python 3.13+ (partial) — [#17672](https://github.com/DataDog/dd-trace-py/pull/17672) (merged) — `@gnufede`

### In flight, owned

- [ ] **Tracer wrapping 3.15** (coroutine + generator + bytecode injection + `CLEANUP_THROW`) — [#17531](https://github.com/DataDog/dd-trace-py/pull/17531) (open) — `@vlad-scherbich` / `@DataDog/debugger-python` (this is planned to be replaced with @Gab's upstreamed approach employing `sys.monitoring`, which should replace bytecode-wrapping altogether)

### Needs an owner — initial commits exist but no committed owner

- [ ] **Crashtracker 3.15 verification** — `c41713059f fix(crashtracker): gate PyFrame_GetBack on 3.14+ and Py_LIMITED_API` on `vlad/ddtracepy-315-profiling-only` is a *defensive* 3.14+ guard (forward-covers 3.15 but is not a 3.15 verification). End-to-end crashtracker behavior on 3.15 has **not been validated**. Crashtracker is built into the wheel's native extensions; if it segfaults at import it kills startup, so this **is** strictly Tier A. Needs `@DataDog/profiling-python` `@DataDog/apm-core-python` to drive the verification + any follow-up fixes.
- [ ] **Native build / `requires-python` / cp315 wheel allow-fail** — currently bundled inside `vlad/ddtracepy-315-profiling-only` (commits: `44e689e849 chore(py315): allow Python 3.15 in requires-python`, `641e47e1d7 ci(package): allow cp315 wheel build to fail until manylinux image catches up`, `411f1e8114 fix(profiling): set PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1`). Should be **extracted into a standalone PR** so the wheel pipeline doesn't depend on profiling review velocity. Note: the strict requirement here is that the *profiling extension doesn't break the wheel build* — satisfiable by either making profiling functional ([#17624](https://github.com/DataDog/dd-trace-py/pull/17624), tracked in Tier B) or excluding it from the build like 3.14 did ([#14669](https://github.com/DataDog/dd-trace-py/pull/14669) / [#14677](https://github.com/DataDog/dd-trace-py/pull/14677)). Either path unblocks the wheel. Needs `@DataDog/python-guild` `@DataDog/apm-core-python` to drive.

### Needs an owner — new work

- [ ] Lib-injection max-Python bump in `lib-injection/sources/requirements.csv` (analogue of [#12283](https://github.com/DataDog/dd-trace-py/pull/12283)) — `@DataDog/apm-core-python`
- [ ] Regen `supported_versions_table.csv`, `supported_versions_output.json`, `requirements.csv` for 3.15 — `@DataDog/apm-core-python` `@DataDog/apm-idm-python`
- [ ] Add `Programming Language :: Python :: 3.15` classifier to `pyproject.toml` — `@DataDog/python-guild`
- [ ] cp315 smoke tests (`tests/smoke_test.py`, `tests/lib-injection`) green → flip cp315 wheel job from `allow-fail` to required — `@DataDog/python-guild` `@DataDog/apm-core-python`

## Tier B: integration parity (volunteer pool)

> **Notes:**
> - **None of these rows block the cp315 wheel cutover.** They are integration-parity work that ships *as each row lands*, same model as the [dd-trace-py v3.16.0 release notes](https://github.com/DataDog/dd-trace-py/releases/tag/v3.16.0)' "still does not work with Python 3.14" list (those wheels still shipped). Each row failing only means that one integration / product is silently skipped on 3.15.
> - **One row is currently in flight by `@vlad-scherbich` — Profiling 3.15 ([#17624](https://github.com/DataDog/dd-trace-py/pull/17624)).** All others are unclaimed.
> - **All caps in `riotfile.py` are at `max_version="3.13"`** (not `"3.14"`). A volunteer PR must therefore do a `3.13 → 3.15` jump and validate the integration on **both 3.14 and 3.15**, then remove any `# X doesn't yet work with Python 3.14` comments left over from previous iterations.
> - **A few rows are more nuanced than the [dd-trace-py v3.16.0 release notes](https://github.com/DataDog/dd-trace-py/releases/tag/v3.16.0) flatten them**; see notes inline below.

### Profiling — `@DataDog/profiling-python`

- [ ] **Profiling 3.15** — [#17624](https://github.com/DataDog/dd-trace-py/pull/17624) (open, draft) — claimed by `@vlad-scherbich`. Stretch goal that makes profiling *functional* on 3.15 rather than excluded from the build. The 3.14 fallback ([#14669](https://github.com/DataDog/dd-trace-py/pull/14669) / [#14677](https://github.com/DataDog/dd-trace-py/pull/14677)) — disabling and excluding the extension — is the strict Tier A path; this row is the better outcome layered on top.

### LLM Observability — `@DataDog/ml-observability`

Most are blocked on `tiktoken` or `protobuf` cutting a 3.15 release; bump the upstream pin and regen the lockfile.

- [ ] `openai` — re-pin `tiktoken`
- [ ] `langchain` — re-pin
- [ ] `langgraph` — re-pin `tiktoken`
- [ ] `litellm` — re-pin `tiktoken`
- [ ] `crewai` — re-pin `tiktoken`
- [ ] `ai_guard_langchain` — re-pin `tiktoken`
- [ ] `google_generativeai` — re-pin `protobuf`
- [ ] `vertexai` — re-pin upstream
- [ ] `openai_agents` — re-pin upstream
- [ ] `llmobs` (ragas test path) — re-pin `ragas`
- [ ] `ray` — re-pin upstream

### Frameworks / IDM — `@DataDog/apm-idm-python`

- [ ] `django` (+ `django:celery`, `django:django_hosts`, `django:djangorestframework`) — bump to django 6.1, lift `max_version="3.13"` in `riotfile.py`
- [ ] `dramatiq` — re-pin upstream (`riotfile.py:1143`)
- [ ] `kafka` — re-pin `confluent-kafka`; only the `>=2.0.2` sub-venv is capped at `"3.13"` (`riotfile.py:3289`); old `~=1.9.2` sub-venv stops at `"3.10"` and needs no change
- [ ] `sqlite3` — capped at `"3.12"` (`riotfile.py:2698`), not `"3.13"`. Blocker is `pysqlite3-binary` being Linux-only-installable, **not just a 3.14 thing**. Needs platform-aware test setup, not a simple re-pin.
- [ ] `grpc_aio` — port test fixtures to `pytest-asyncio>=1.0` (real integration work)
- [ ] `rq` — investigate, fix integration (real integration work)

### API SDK — `@DataDog/apm-sdk-capabilities-python`

- [ ] `opentelemetry` — re-pin `opentelemetry-exporter-otlp` once it ships 3.15

### Serverless — `@DataDog/apm-serverless`

- [ ] `aws_lambda` — coordinate with `@DataDog/serverless-aws` to get `datadog-lambda` 3.15 support
- [ ] `azure_functions` — coordinate with `@DataDog/serverless-azure-and-gcp`; suite is also stale since 3.11

### CI Visibility — `@DataDog/ci-app-libraries`

- [ ] **CI Visibility coverage broader 3.13-3.15** — `gnufede/SDTEST-2766-support-coverage-3.14` and `gnufede/coverage-3.15-dis-refactor` branches contain WIP beyond [#17672](https://github.com/DataDog/dd-trace-py/pull/17672) (the partial coverage already merged on `main`). Driver: `@gnufede`. Foundation already shipped, so this is incremental work and `ci_visibility` already shipped without 3.14 in dd-trace-py v3.16.0 — same fallback applies for 3.15.
- [ ] `pytest` integration — real integration work
- [ ] `pytest_plugin_v2` — currently capped at `max_version="3.13"`; real integration work
- [ ] `pytest_bdd`, `pytest_benchmark`, `unittest`, `freezegun`, `selenium` — re-validate at 3.15 once `pytest` is unblocked
- [ ] `dd_coverage` — currently capped at `max_version="3.12"` in `riotfile.py:547` — investigate

### ASM — `@DataDog/asm-python`

- [ ] **IAST 3.15** — initial commit `84ad6c9f2b feat(iast): enable IAST on Python 3.15 — taint tracking and AST patching verified working` lives on `vlad/ddtracepy-315-tracing-wrapping` for reference, but **no PR, no owner**. dd-trace-py v3.16.0 wheels shipped without IAST on 3.14; the same fallback (skip on 3.15) is acceptable here. Needs `@DataDog/asm-python` to validate, take ownership, and ship.
- [ ] `appsec_integrations_fastapi` — only the **old fastapi sub-venvs** (`==0.86.0`, `==0.94.1`) are capped at `"3.13"` (`riotfile.py:340-345`); the newer sub-venvs (`~=0.114.2`, `latest`) already test 3.14+ once IAST works there. Real blocker is likely IAST runtime, not the fastapi integration itself — i.e. this row is implicitly gated on the **IAST 3.15** row above.

### Data Streams — `@DataDog/data-streams-monitoring`

- [ ] `datastreams` — re-validate at 3.15 (was enabled on 3.14 in [#14793](https://github.com/DataDog/dd-trace-py/pull/14793))

## Non-goals (called out for visibility only, no action required for the py-315 migration itself)

The integrations below are **explicitly out of scope** for the 3.15 effort. They were already capped below 3.14
long before 3.15 came up, so flipping on cp315 wheels does not change anything for them.
They are listed here only as an opportunity for a future cleanup / deprecation initiative to happen independently
of the 3.15 cycle (all are likely owned by `@DataDog/apm-idm-python` / `@DataDog/apm-core-python`).

- `vertica` (capped at 3.9) — **already deprecated in dd-trace-py v3.16.0**; on its way out.
- `bottle` (capped at 3.10) — Bottle the framework is dormant upstream.
- `aredis` (capped at 3.9) — library abandoned upstream; superseded by `redis>=4.2.0`.
- `yaaredis` (capped at 3.10) — library abandoned upstream; superseded by `redis>=4.2.0`.
- `rediscluster` (capped at 3.11) — `redis-py-cluster` superseded by native cluster support in `redis>=4.2.0`.
- `asynctest` (capped at 3.9) — library obsoleted by stdlib `unittest.IsolatedAsyncioTestCase` in Python 3.8.
- `pynamodb` (capped at 3.11) — test-harness incompat at 3.12+; **orthogonal to 3.15** (fixing it would unblock 3.12-3.15 in one shot). Belongs in a general test-hygiene backlog.

If any of these turn out to need attention, file a separate issue —
**don't reopen this tracker for them.**

## References

- [dd-trace-py v3.16.0 release notes](https://github.com/DataDog/dd-trace-py/releases/tag/v3.16.0) — source list of "doesn't work on 3.14"
- [PR #14264 — initial 3.14 support](https://github.com/DataDog/dd-trace-py/pull/14264) — riotfile bump precedent
- [emmettbutler's 3.14 follow-up PR series](https://github.com/DataDog/dd-trace-py/pulls?q=is%3Apr+is%3Amerged+author%3Aemmettbutler+title%3A3.14) — pattern for per-integration enablement
- [PR #17624 — profiling 3.15 support](https://github.com/DataDog/dd-trace-py/pull/17624) — Tier B reference (in flight)
- [PR #17791 — adds `python_315_bump` PR template](https://github.com/DataDog/dd-trace-py/pull/17791) — referenced from "How this works" above; the `?template=python_315_bump.md` URL only resolves once this merges
