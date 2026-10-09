# CI suites not yet on 3.15

[#20627](https://github.com/DataDog/dd-trace-py/pull/20627) keeps 3.15 on the default matrix only for variants whose GitLab job passed on `f1b97fc` (`gh pr checks 20627`, 2026-09-29). A green job covers every venv in that shard. Variants below were taken back off 3.15 because the job failed, or the suite did not run. `requires-python` / `MAX_PY` stay `<3.15`. Where a suite still has other 3.15 variants, only the failing one is listed.

Ticket keys are existing Jira issues. "no existing ticket" means a summary search for `Python 3.15` did not name that suite.

## Registry drift (tests passed; fold into #20627)

These 17 suites **passed every test**. The job failed afterwards on `./scripts/check-diff scripts/integration_registry/registry.yaml` ("Registry YAML file was modified") after the fresh 3.15 lockfiles pulled versions above the registry `max`. They are not Python 3.15 product failures — re-add `3.15`, regen locks, run `scripts/integration_registry/update_and_format_registry.py`, commit `registry.yaml` + `supported_versions.json` on #20627.

- `contrib::aiobotocore`, `contrib::aws_lambda`, `contrib::azure_cosmos`, `contrib::azure_servicebus`, `contrib::celery`, `contrib::elasticsearch`, `contrib::grpc` (plain), `contrib::mako`, `contrib::opensearch`, `contrib::protobuf`, `contrib::psycopg`, `contrib::snowflake`, `contrib::urllib3`
- `llmobs::claude_agent_sdk`, `llmobs::google_genai`, `llmobs::llama_index`, `llmobs::openai_agents`

Note: earlier wording of `requires-python <3.15` for celery / aws_lambda / protobuf / snowflake / openai_agents (and mistakenly mlflow) was a uv warning about ddtrace's own `requires-python`, not an upstream gate.

## Still failing (real)

- `crashtracker` — [PROF-15015](https://datadoghq.atlassian.net/browse/PROF-15015). Job `core/crashtracker` failed (missing `string_at` in the runtime stack). Closest also [PROF-14459](https://datadoghq.atlassian.net/browse/PROF-14459).
- `internal` — [APPSEC-69961](https://datadoghq.atlassian.net/browse/APPSEC-69961) for the monitoring-path variants (`core/internal` 2/6 and 4/6: weakref / context-manager). The pyarmor variant (`core/internal` 3/6) has no existing ticket — `pyarmor gen` segfaults on 3.15 before ddtrace runs.
- `runtime` — no existing ticket. Suite is `skip: true`; the new 3.15 env never ran.
- `openfeature` — no existing ticket. Job `core/openfeature` failed (`openfeature-sdk` 0.10+ drift).
- `debugging::debugger` — no existing ticket. Job `debugging/debugger` failed (frame capture via `sys._getframe(1)` hits injector frame on 3.15).
- `profiling::profile` — [APPSEC-69961](https://datadoghq.atlassian.net/browse/APPSEC-69961). Jobs `profiling/profile` 9/25, 16/25, and 23/25 are 3.15-only and failed in `monitoring.py`. Closest build-path ticket: [PROF-15923](https://datadoghq.atlassian.net/browse/PROF-15923).
- `profiling::profile-memalloc` — [APPSEC-69961](https://datadoghq.atlassian.net/browse/APPSEC-69961). Job `profiling/profile-memalloc` 7/7 is 3.15-only and failed.
- `aiguard::ai_guard_anthropic` — no existing ticket. Job `aiguard/ai_guard_anthropic` failed (anthropic 1.x API drift).
- `aiguard::ai_guard_litellm_guardrail` — no existing ticket. Job `aiguard/ai_guard_litellm_guardrail` 2/2 failed (`fastuuid` / PyO3 max 3.14).
- `aiguard::ai_guard_openai` — no existing ticket. Job `aiguard/ai_guard_openai` 2/2 failed (openai 3.x API drift).
- `appsec::appsec` — [APPSEC-70509](https://datadoghq.atlassian.net/browse/APPSEC-70509). Closest: [APPSEC-68821](https://datadoghq.atlassian.net/browse/APPSEC-68821), [APPSEC-68948](https://datadoghq.atlassian.net/browse/APPSEC-68948). Job `appsec/appsec` 1/2 failed (wrapping-storage leak on 3.15 when `__enter__` raises).
- `appsec::appsec_iast_packages` — [APPSEC-69649](https://datadoghq.atlassian.net/browse/APPSEC-69649). Job `appsec/appsec_iast_packages` 5/5 is 3.15-only and was OOM-killed (one sample; rerun first).
- `appsec::appsec_integrations_fastapi` — [APPSEC-69809](https://datadoghq.atlassian.net/browse/APPSEC-69809). Only job `appsec/appsec_integrations_fastapi` 7/8 failed; other 3.15 shards of this suite passed (`fastapi==0.86.0` / hypothesis `sre_constants`).
- `appsec::appsec_integrations_packages` — [APPSEC-70511](https://datadoghq.atlassian.net/browse/APPSEC-70511). Closest: [APPSEC-68821](https://datadoghq.atlassian.net/browse/APPSEC-68821). Job `appsec/appsec_integrations_packages` 1/3 failed (`pymysql` 1.2+ drift).
- `appsec::sca` — [APPSEC-70510](https://datadoghq.atlassian.net/browse/APPSEC-70510). Closest: [APPSEC-68821](https://datadoghq.atlassian.net/browse/APPSEC-68821). Job `appsec/sca` failed (`_first_instr_line` / `co_firstlineno` on 3.15).
- `contrib::aws_durable_execution_sdk_python` — [IDMPL-965](https://datadoghq.atlassian.net/browse/IDMPL-965). Only job 2/2 failed; job 1/2 passed (`ExecutionState` API on SDK 2.x).
- `contrib::gevent` — [IDMPL-970](https://datadoghq.atlassian.net/browse/IDMPL-970). Job `contrib/gevent` failed (`monitoring.py` / #20671).
- `contrib::graphql` — [IDMPL-971](https://datadoghq.atlassian.net/browse/IDMPL-971). Jobs `contrib/graphql` 1/3 and 2/3 failed (`middlewares_arg` hard-coded; graphql-core 3.2.13).
- `contrib::graphql:graphene` — [IDMPL-971](https://datadoghq.atlassian.net/browse/IDMPL-971). Job `contrib/graphql:graphene` failed (same middleware-arg issue).
- `contrib::httpx2` — [IDMPL-1020](https://datadoghq.atlassian.net/browse/IDMPL-1020). Job `contrib/httpx2` 1/2 failed (snapshot ignore for `meta.http.useragent`).
- `contrib::kafka` — [IDMPL-415](https://datadoghq.atlassian.net/browse/IDMPL-415). Job `contrib/kafka` 3/3 failed (`confluent-kafka` no cp315 wheel / librdkafka headers).
- `contrib::mlflow` — [IDMPL-972](https://datadoghq.atlassian.net/browse/IDMPL-972). Job `contrib/mlflow` 2/2 failed (`pyarrow` no cp315 wheel).
- `contrib::mysqlpython` — [IDMPL-1022](https://datadoghq.atlassian.net/browse/IDMPL-1022). Suite is `skip: true`; the new 3.15 env never ran.
- `contrib::opentelemetry` — [IDMPL-1024](https://datadoghq.atlassian.net/browse/IDMPL-1024). Job `contrib/opentelemetry` 9/10 failed (exporter 1.45 urllib3 transport; tests still mock `requests`).
- `contrib::pymongo` — [IDMPL-974](https://datadoghq.atlassian.net/browse/IDMPL-974). Jobs `contrib/pymongo` 1/4 and 2/4 failed; 3/4 passed (pymongo 4.18 dropped `Server.run_operation` / `checkout`).
- `contrib::redis` — [IDMPL-975](https://datadoghq.atlassian.net/browse/IDMPL-975). Job `contrib/redis` 3/3 failed (`@pytest.mark.asyncio` on fixtures; pytest 9.1 rejects; collection aborted).
- `contrib::sqlalchemy` — [IDMPL-1028](https://datadoghq.atlassian.net/browse/IDMPL-1028). Job `contrib/sqlalchemy` failed (SQLAlchemy 2.1 bare `postgresql://` → psycopg 3).
- `llmobs::anthropic` — no existing ticket. Job `llmobs/anthropic` 1/2 failed (anthropic 1.x API drift).
- `llmobs::google_adk` — no existing ticket. Only job `llmobs/google_adk` 4/5 failed; 2/5 passed (`_call_tool_async` moved in ADK 2.10).
- `llmobs::langgraph` — no existing ticket. Jobs `llmobs/langgraph` 2/6, 4/6, and 6/6 failed (`ormsgpack` / PyO3).
- `llmobs::litellm` — no existing ticket. Job `llmobs/litellm` 4/4 failed (`fastuuid` / PyO3).
- `llmobs::mcp` — no existing ticket. Job `llmobs/mcp` 3/3 failed (mcp 2.x API drift).
- `llmobs::pydantic_ai` — no existing ticket. Jobs `llmobs/pydantic_ai` 1/4 and 3/4 failed (`pydantic==2.12.0a1` / pydantic-core PyO3).
