# CI suites not yet on Python 3.15

**Deadline: 2026-10-28.** Owners have **6 days** from 2026-10-22 to put 3.15 on these suites before the next version cut. Suites still missing 3.15 after that date ship as-is for this cut; follow-ups stay on #17809.

Refreshed from `main` @ `052acc726c`. `DEFAULT_PYTHON_VERSIONS` already includes `3.15`. A suite is listed when its matrix has no 3.15 environment (explicit `python:` list tops out at ≤3.14), a named variant still omits 3.15, or the suite is `skip: true`.

This is repo-wide CI, so #19273 should not carry it. Part of #17809.

## No 3.15 environment (50 suites)

| Suite | Why off 3.15 | Tracker / ticket |
| --- | --- | --- |
| `aiguard::ai_guard_anthropic` | python lists max 3.14 | — |
| `aiguard::ai_guard_langchain` | python lists max 3.13 | #17823 |
| `aiguard::ai_guard_litellm_guardrail` | python lists max 3.14 | — |
| `appsec::appsec_iast_packages` | python lists max 3.14 | [APPSEC-69649](https://datadoghq.atlassian.net/browse/APPSEC-69649) |
| `appsec::appsec_integrations_flask_testagent` | python lists max 3.13 | — |
| `appsec::appsec_integrations_langchain` | python lists max 3.13 | — |
| `appsec::appsec_integrations_packages` | python lists max 3.14 | [APPSEC-70511](https://datadoghq.atlassian.net/browse/APPSEC-70511) |
| `appsec::appsec_integrations_pygoat` | python lists max 3.12 | — |
| `appsec::appsec_threats_aiohttp_no_iast` | python lists max 3.14 | — |
| `appsec::appsec_threats_aiohttp_rc` | python lists max 3.14 | — |
| `appsec::appsec_threats_django_rc` | python lists max 3.13 | — |
| `appsec::appsec_threats_fastapi_rc` | python lists max 3.13 | — |
| `appsec::appsec_threats_flask_iast` | python lists max 3.13 | — |
| `appsec::appsec_threats_flask_no_iast` | python lists max 3.13 | — |
| `appsec::appsec_threats_flask_rc` | python lists max 3.13 | — |
| `appsec::iast_aggregated_leak_testing` | python lists max 3.12 | — |
| `ci_visibility::ci_visibility` | python lists max 3.13 | #17838 |
| `ci_visibility::ci_visibility:snapshot` | python lists max 3.13 | #17838 |
| `ci_visibility::selenium` | python lists max 3.12 | #17841 |
| `contrib::azure_durable_functions` | python lists max 3.13 | #17837 |
| `contrib::azure_eventhubs` | python lists max 3.13 | #17837 |
| `contrib::azure_functions` | python lists max 3.13 | #17837 |
| `contrib::azure_functions:cosmos` | python lists max 3.13 | #17837 |
| `contrib::azure_functions:eventhubs` | python lists max 3.11 | #17837 |
| `contrib::azure_functions:servicebus` | python lists max 3.11 | #17837 |
| `contrib::django:djangorestframework` | python lists max 3.13 | #17829 |
| `contrib::django_hosts` | python lists max 3.13 | #17829 |
| `contrib::gevent` | python lists max 3.14 | [IDMPL-970](https://datadoghq.atlassian.net/browse/IDMPL-970) |
| `contrib::graphql` | python lists max 3.14 | [IDMPL-971](https://datadoghq.atlassian.net/browse/IDMPL-971) |
| `contrib::graphql:graphene` | python lists max 3.14 | [IDMPL-971](https://datadoghq.atlassian.net/browse/IDMPL-971) |
| `contrib::kafka` | python lists max 3.14 | #17831, [IDMPL-415](https://datadoghq.atlassian.net/browse/IDMPL-415) |
| `contrib::mlflow` | python lists max 3.14 | [IDMPL-972](https://datadoghq.atlassian.net/browse/IDMPL-972) |
| `contrib::pytorch` | python lists max 3.14 | — |
| `contrib::ray` | python lists max 3.13 | #17828 |
| `contrib::ray_serve` | python lists max 3.13 | #17828 |
| `contrib::redis` | python lists max 3.14 | [IDMPL-975](https://datadoghq.atlassian.net/browse/IDMPL-975) |
| `internal` | python lists max 3.14 | [APPSEC-69961](https://datadoghq.atlassian.net/browse/APPSEC-69961) |
| `llmobs::anthropic` | python lists max 3.14 | — |
| `llmobs::crewai` | python lists max 3.12 | #17822 |
| `llmobs::langchain` | python lists max 3.12 | #17819 |
| `llmobs::langgraph` | python lists max 3.14 | #17820 |
| `llmobs::litellm` | python lists max 3.14 | #17821 |
| `llmobs::llmobs` | python lists max 3.13 | #17827 |
| `llmobs::mcp` | python lists max 3.14 | — |
| `llmobs::openai` | python lists max 3.13 | #17818 |
| `llmobs::pydantic_ai` | python lists max 3.14 | — |
| `llmobs::vertexai` | python lists max 3.12 | #17825 |
| `llmobs::vllm` | python lists max 3.13 | — |
| `profiling::profile-uwsgi` | python lists max 3.13 | — |
| `tracer-uwsgi` | python lists max 3.13 | — |

## Partial — some variants already on 3.15 (5 suites)

| Suite | Gap | Tracker / ticket |
| --- | --- | --- |
| `contrib::django` | variants without 3.15: `django:celery` (max 3.9, 3.12) | #17829 |
| `contrib::flask` | variants without 3.15: `flask_cache` (max 3.9, 3.11, 3.13) | — |
| `contrib::grpc` | variants without 3.15: `grpc:grpc_aio` (max 3.9, 3.10, 3.11) | #17833 |
| `contrib::stdlib` | variants without 3.15: `sqlite3` (max 3.12) | #17832 |
| `tracer` | variants without 3.15: `tracer-legacy-attrs` (max 3.9) | — |

## Skipped suites (2)

- `contrib::mysqlpython` — `skip: true`; 3.15 never runs. [IDMPL-1022](https://datadoghq.atlassian.net/browse/IDMPL-1022)
- `runtime` — `skip: true`; 3.15 never runs. —

## Non-goals (from #17809; not part of this deadline)

These were already capped below current Python long before 3.15. Listed only because suitespec still excludes 3.15:

- `contrib::aredis` — python lists max 3.9
- `contrib::asynctest` — python lists max 3.9
- `contrib::rediscluster` — python lists max 3.11
- `contrib::vertica` — skip: true

## Infra / harness (not owner migration work)

- `conftest` — python lists max 3.10
- `contrib::integration_registry` — python lists max 3.13
- `wait` — python lists max 3.9

## Tracker rows that are already green on suitespec

These open #17809 sub-issues no longer match a suitespec gap (suite already has a 3.15 environment on main). Close or re-scope the issue:

- #17824 `google_generativeai` — `llmobs::google_genai` already includes 3.15
- #17826 `openai_agents` — `llmobs::openai_agents` already includes 3.15
- #17815 classifier / #17839–#17840 pytest rows — not a suitespec `python:` omission for the named suites (pytest / pytest_bdd / pytest_benchmark already include 3.15); keep only if the issue is about product behavior, not CI matrix coverage
