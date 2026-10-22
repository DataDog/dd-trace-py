# CI suites not yet on Python 3.15

**Deadline: 2026-10-28.** Owners have **6 days** from 2026-10-22 to put 3.15 on these suites before the next version cut. Suites still missing 3.15 after that date ship as-is for this cut; follow-ups stay on #17809.

Refreshed from `main` @ `1cb9f945a5`. `DEFAULT_PYTHON_VERSIONS` already includes `3.15`. A suite is listed when its matrix has no 3.15 environment (explicit `python:` list tops out at ≤3.14), a named variant still omits 3.15, or the suite is `skip: true`.

Meshed with Fede’s draft [#21192](https://github.com/DataDog/dd-trace-py/pull/21192) (“Suites kept off 3.15” / `TODO(py-315)`). Slack thread permalink was blocked for agents; source for Fede’s enumeration is that PR body plus its `TODO(py-315)` comments. Process notes below also reflect the Fede/brett Slack thread (2026-10-22).

This is repo-wide CI, so #19273 should not carry it. Part of #17809.

## Fede / #21192 notes (process)

From the Fede/brett thread and #21192:

1. **Many of these suites also lack 3.14** — not only 3.15. Of the 50 fully-off suites below, **31** have no 3.14 environment either. Fixing “3.15 support” announcements without 3.14 coverage repeats the 3.14 gap Brett called out.
2. **Do not announce 3.15 support until red 3.15 dependency failures are triaged.** Fede’s read: most failures look like dependency installs on 3.15 (missing wheels / pins), not an inherent dd-trace-py incompatibility with 3.15 itself. #21192 adds matrices where green; keeps blockers off with `TODO(py-315)`.
3. **Owner deadline remains 2026-10-28** for putting 3.15 on suites that can take it. After that, the version cut does not wait on remaining gaps.

### Count check vs #21192 unfurl (“47 more suites”)

The Slack GitHub unfurl about enabling `profile-uwsgi` and “47 more suites” (aiguard 3 + appsec 13 + ci_visibility 3 + contrib 18 + llmobs 9 + `tracer-uwsgi`) is **#21192**, not this gap checklist. Arithmetic: `3+13+3+18+9+1 = 47` (verified). That card counts suites #21192 *adds* 3.15 to (before later reverts). This doc counts suites that still *omit* 3.15 on `main`: **50** fully off + **5** partial + skips/non-goals/infra below.

### Merge vs Fede’s kept-off list

- In both (still off on `main` and in Fede’s kept-off / TODO set): **30**
- Only Fede (kept-off but already green on `main`): **0** — none; nothing to drop
- Only ours (off on `main`; Fede did not list as kept-off — expect #21192 enablement or follow-up triage): **20**

## No 3.15 environment (50 suites)

| Suite | Why off 3.15 | Tracker / ticket | Fede / #21192 |
| --- | --- | --- | --- |
| `aiguard::ai_guard_anthropic` | python lists max 3.14 | — | not in Fede kept-off list |
| `aiguard::ai_guard_langchain` | python lists max 3.13 | #17823 | Fede keeps off (#21192) |
| `aiguard::ai_guard_litellm_guardrail` | python lists max 3.14 | — | Fede keeps off (#21192) |
| `appsec::appsec_iast_packages` | python lists max 3.14 | [APPSEC-69649](https://datadoghq.atlassian.net/browse/APPSEC-69649) | not in Fede kept-off list |
| `appsec::appsec_integrations_flask_testagent` | python lists max 3.13 | — | not in Fede kept-off list |
| `appsec::appsec_integrations_langchain` | python lists max 3.13 | — | Fede keeps off (#21192) |
| `appsec::appsec_integrations_packages` | python lists max 3.14 | [APPSEC-70511](https://datadoghq.atlassian.net/browse/APPSEC-70511) | not in Fede kept-off list |
| `appsec::appsec_integrations_pygoat` | python lists max 3.12 | — | Fede keeps off (#21192) |
| `appsec::appsec_threats_aiohttp_no_iast` | python lists max 3.14 | — | not in Fede kept-off list |
| `appsec::appsec_threats_aiohttp_rc` | python lists max 3.14 | — | not in Fede kept-off list |
| `appsec::appsec_threats_django_rc` | python lists max 3.13 | — | not in Fede kept-off list |
| `appsec::appsec_threats_fastapi_rc` | python lists max 3.13 | — | not in Fede kept-off list |
| `appsec::appsec_threats_flask_iast` | python lists max 3.13 | — | not in Fede kept-off list |
| `appsec::appsec_threats_flask_no_iast` | python lists max 3.13 | — | not in Fede kept-off list |
| `appsec::appsec_threats_flask_rc` | python lists max 3.13 | — | not in Fede kept-off list |
| `appsec::iast_aggregated_leak_testing` | python lists max 3.12 | — | Fede keeps off (#21192) |
| `ci_visibility::ci_visibility` | python lists max 3.13 | #17838 | Fede keeps off (#21192) |
| `ci_visibility::ci_visibility:snapshot` | python lists max 3.13 | #17838 | Fede keeps off (#21192) |
| `ci_visibility::selenium` | python lists max 3.12 | #17841 | not in Fede kept-off list |
| `contrib::azure_durable_functions` | python lists max 3.13 | #17837 | Fede keeps off (#21192) |
| `contrib::azure_eventhubs` | python lists max 3.13 | #17837 | not in Fede kept-off list |
| `contrib::azure_functions` | python lists max 3.13 | #17837 | Fede keeps off (#21192) |
| `contrib::azure_functions:cosmos` | python lists max 3.13 | #17837 | Fede keeps off (#21192) |
| `contrib::azure_functions:eventhubs` | python lists max 3.11 | #17837 | Fede keeps off (#21192) |
| `contrib::azure_functions:servicebus` | python lists max 3.11 | #17837 | Fede keeps off (#21192) |
| `contrib::django:djangorestframework` | python lists max 3.13 | #17829 | Fede keeps off (#21192) |
| `contrib::django_hosts` | python lists max 3.13 | #17829 | Fede keeps off (#21192) |
| `contrib::gevent` | python lists max 3.14 | [IDMPL-970](https://datadoghq.atlassian.net/browse/IDMPL-970) | not in Fede kept-off list |
| `contrib::graphql` | python lists max 3.14 | [IDMPL-971](https://datadoghq.atlassian.net/browse/IDMPL-971) | Fede keeps off (#21192) |
| `contrib::graphql:graphene` | python lists max 3.14 | [IDMPL-971](https://datadoghq.atlassian.net/browse/IDMPL-971) | Fede keeps off (#21192) |
| `contrib::kafka` | python lists max 3.14 | #17831, [IDMPL-415](https://datadoghq.atlassian.net/browse/IDMPL-415) | Fede keeps off (#21192) |
| `contrib::mlflow` | python lists max 3.14 | [IDMPL-972](https://datadoghq.atlassian.net/browse/IDMPL-972) | Fede keeps off (#21192) |
| `contrib::pytorch` | python lists max 3.14 | — | Fede keeps off (#21192) |
| `contrib::ray` | python lists max 3.13 | #17828 | Fede keeps off (#21192) |
| `contrib::ray_serve` | python lists max 3.13 | #17828 | Fede keeps off (#21192) |
| `contrib::redis` | python lists max 3.14 | [IDMPL-975](https://datadoghq.atlassian.net/browse/IDMPL-975) | not in Fede kept-off list |
| `internal` | python lists max 3.14 | [APPSEC-69961](https://datadoghq.atlassian.net/browse/APPSEC-69961) | not in Fede kept-off list |
| `llmobs::anthropic` | python lists max 3.14 | — | Fede keeps off (#21192) |
| `llmobs::crewai` | python lists max 3.12 | #17822 | Fede keeps off (#21192) |
| `llmobs::langchain` | python lists max 3.12 | #17819 | Fede keeps off (#21192) |
| `llmobs::langgraph` | python lists max 3.14 | #17820 | not in Fede kept-off list |
| `llmobs::litellm` | python lists max 3.14 | #17821 | not in Fede kept-off list |
| `llmobs::llmobs` | python lists max 3.13 | #17827 | Fede keeps off (#21192) |
| `llmobs::mcp` | python lists max 3.14 | — | Fede keeps off (#21192) |
| `llmobs::openai` | python lists max 3.13 | #17818 | Fede keeps off (#21192) |
| `llmobs::pydantic_ai` | python lists max 3.14 | — | not in Fede kept-off list |
| `llmobs::vertexai` | python lists max 3.12 | #17825 | Fede keeps off (#21192) |
| `llmobs::vllm` | python lists max 3.13 | — | Fede keeps off (#21192) |
| `profiling::profile-uwsgi` | python lists max 3.13 | — | Fede keeps off (#21192) |
| `tracer-uwsgi` | python lists max 3.13 | — | not in Fede kept-off list |

## Partial — some variants already on 3.15 (5 suites)

| Suite | Gap | Tracker / ticket | Fede / #21192 |
| --- | --- | --- | --- |
| `contrib::django` | variants without 3.15: `django:celery` (max 3.9, 3.12) | #17829 | not in Fede kept-off list |
| `contrib::flask` | variants without 3.15: `flask_cache` (max 3.11, 3.12, 3.13) | — | not in Fede kept-off list |
| `contrib::grpc` | variants without 3.15: `grpc:grpc_aio` (max 3.9, 3.10, 3.11) | #17833 | not in Fede kept-off list |
| `contrib::stdlib` | variants without 3.15: `sqlite3` (max 3.10, 3.11, 3.12) | #17832 | not in Fede kept-off list |
| `tracer` | variants without 3.15: `tracer-legacy-attrs` (max 3.9) | — | not in Fede kept-off list |

## Skipped suites (2)

- `contrib::mysqlpython` — `skip: true`; 3.15 never runs.
- `runtime` — `skip: true`; 3.15 never runs.

## Non-goals (from #17809; not part of this deadline)

These were already capped below current Python long before 3.15. Listed only because suitespec still excludes 3.15. Fede #21192 also keeps `aredis` / `rediscluster` / `asynctest` off with `TODO(py-315)`.

- `contrib::aredis` — python lists max 3.9
- `contrib::asynctest` — python lists max 3.9
- `contrib::rediscluster` — python lists max 3.11
- `contrib::vertica` — skip: true

## Infra / harness (not owner migration work)

- `conftest` — python lists max 3.10
- `contrib::integration_registry` — python lists max 3.13
- `wait` — python lists max 3.9

## Fede #21192 kept-off enumeration (source)

Verbatim categories from [#21192](https://github.com/DataDog/dd-trace-py/pull/21192) (gnufede), mapped to suitespec names where obvious:

- **No cp315 wheels:** `contrib::pytorch`, `llmobs::vllm` (torch; also onnxruntime), `contrib::ray`, `contrib::ray_serve`, `llmobs::crewai` (chromadb, onnxruntime)
- **Environment-setup failures:** azure functions family (`contrib::azure_durable_functions`, `contrib::azure_functions`, `:cosmos`, `:eventhubs`, `:servicebus`), `contrib::aredis`, `contrib::rediscluster`, profiling `protobuf==4.22.0` row (other profile rows cover 3.15); plus `profiling::profile-uwsgi` (uwsgi build)
- **Test failures / follow-ups:** `ci_visibility::ci_visibility` (+ snapshot), `llmobs::llmobs`; other known-failing suites may keep 3.15 rows in #21192 while still red (appsec flask threats, aiguard langchain cassettes, llmobs anthropic/openai/vertexai, `contrib::integration_registry`)
- **Excluded here (separate branch):** `contrib::graphql`, `contrib::graphql:graphene`

Additional suites still off on `main` with `TODO(py-315)` on the #21192 tip (kafka, mlflow, django_hosts, djangorestframework, pygoat, iast leak testing, litellm guardrail, mcp, …) stay in the tables above.

## Tracker rows that are already green on suitespec

These open #17809 sub-issues no longer match a suitespec gap (suite already has a 3.15 environment on main). Close or re-scope the issue:

- #17824 `google_generativeai` — `llmobs::google_genai` already includes 3.15
- #17826 `openai_agents` — `llmobs::openai_agents` already includes 3.15
- #17815 classifier / #17839–#17840 pytest rows — not a suitespec `python:` omission for the named suites (pytest / pytest_bdd / pytest_benchmark already include 3.15); keep only if the issue is about product behavior, not CI matrix coverage

