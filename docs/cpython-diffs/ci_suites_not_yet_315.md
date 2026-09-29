# CI suites not yet on 3.15

[#20627](https://github.com/DataDog/dd-trace-py/pull/20627) keeps 3.15 on the default matrix only for variants whose GitLab job passed on `f1b97fc` (`gh pr checks 20627`, 2026-09-29). A green job covers every venv in that shard. Variants below were taken back off 3.15 because the job failed, or the suite did not run. `requires-python` / `MAX_PY` stay `<3.15`. Where a suite still has other 3.15 variants, only the failing one is listed.

Ticket keys are existing Jira issues. "no existing ticket" means a summary search for `Python 3.15` did not name that suite.

- `crashtracker` — [PROF-15015](https://datadoghq.atlassian.net/browse/PROF-15015). Job `core/crashtracker` failed (missing `string_at` in the runtime stack). Closest also [PROF-14459](https://datadoghq.atlassian.net/browse/PROF-14459).
- `internal` — [APPSEC-69961](https://datadoghq.atlassian.net/browse/APPSEC-69961) for the monitoring-path variants (`core/internal` 2/6 and 4/6: weakref / context-manager). The pyarmor variant (`core/internal` 3/6) has no existing ticket.
- `runtime` — no existing ticket. Suite is `skip: true`; the new 3.15 env never ran.
- `openfeature` — no existing ticket. Job `core/openfeature` failed.
- `debugging::debugger` — no existing ticket. Job `debugging/debugger` failed.
- `profiling::profile` — [APPSEC-69961](https://datadoghq.atlassian.net/browse/APPSEC-69961). Jobs `profiling/profile` 9/25, 16/25, and 23/25 are 3.15-only and failed in `monitoring.py`. Closest build-path ticket: [PROF-15923](https://datadoghq.atlassian.net/browse/PROF-15923).
- `profiling::profile-memalloc` — [APPSEC-69961](https://datadoghq.atlassian.net/browse/APPSEC-69961). Job `profiling/profile-memalloc` 7/7 is 3.15-only and failed.
- `aiguard::ai_guard_anthropic` — no existing ticket. Job `aiguard/ai_guard_anthropic` failed.
- `aiguard::ai_guard_litellm_guardrail` — no existing ticket. Job `aiguard/ai_guard_litellm_guardrail` 2/2 failed (`fastuuid` / PyO3 max 3.14).
- `aiguard::ai_guard_openai` — no existing ticket. Job `aiguard/ai_guard_openai` 2/2 failed.
- `appsec::appsec` — [APPSEC-70509](https://datadoghq.atlassian.net/browse/APPSEC-70509). Closest: [APPSEC-68821](https://datadoghq.atlassian.net/browse/APPSEC-68821), [APPSEC-68948](https://datadoghq.atlassian.net/browse/APPSEC-68948). Job `appsec/appsec` 1/2 failed.
- `appsec::appsec_iast_packages` — [APPSEC-69649](https://datadoghq.atlassian.net/browse/APPSEC-69649). Job `appsec/appsec_iast_packages` 5/5 is 3.15-only and was OOM-killed.
- `appsec::appsec_integrations_fastapi` — [APPSEC-69809](https://datadoghq.atlassian.net/browse/APPSEC-69809). Only job `appsec/appsec_integrations_fastapi` 7/8 failed; other 3.15 shards of this suite passed.
- `appsec::appsec_integrations_packages` — [APPSEC-70511](https://datadoghq.atlassian.net/browse/APPSEC-70511). Closest: [APPSEC-68821](https://datadoghq.atlassian.net/browse/APPSEC-68821). Job `appsec/appsec_integrations_packages` 1/3 failed.
- `appsec::sca` — [APPSEC-70510](https://datadoghq.atlassian.net/browse/APPSEC-70510). Closest: [APPSEC-68821](https://datadoghq.atlassian.net/browse/APPSEC-68821). Job `appsec/sca` failed.
- `contrib::aiobotocore` — [IDMPL-1016](https://datadoghq.atlassian.net/browse/IDMPL-1016). Job `contrib/aiobotocore` 4/4 failed.
- `contrib::aws_durable_execution_sdk_python` — [IDMPL-965](https://datadoghq.atlassian.net/browse/IDMPL-965). Only job 2/2 failed; job 1/2 passed.
- `contrib::aws_lambda` — [IDMPL-1015](https://datadoghq.atlassian.net/browse/IDMPL-1015). Closest: [APMSVLS-612](https://datadoghq.atlassian.net/browse/APMSVLS-612) (Lambda runtime, not this contrib suite). Job `contrib/aws_lambda` failed (`requires-python <3.15`).
- `contrib::azure_cosmos` — [IDMPL-1017](https://datadoghq.atlassian.net/browse/IDMPL-1017). Job `contrib/azure_cosmos` 2/3 failed.
- `contrib::azure_servicebus` — [IDMPL-1018](https://datadoghq.atlassian.net/browse/IDMPL-1018). Job `contrib/azure_servicebus` 4/4 failed.
- `contrib::celery` — [IDMPL-1019](https://datadoghq.atlassian.net/browse/IDMPL-1019). Job `contrib/celery` 4/4 failed (`requires-python <3.15`).
- `contrib::elasticsearch` — [IDMPL-968](https://datadoghq.atlassian.net/browse/IDMPL-968). Only job `contrib/elasticsearch` 3/25 failed; the other 3.15 shards passed.
- `contrib::gevent` — [IDMPL-970](https://datadoghq.atlassian.net/browse/IDMPL-970). Job `contrib/gevent` failed.
- `contrib::graphql` — [IDMPL-971](https://datadoghq.atlassian.net/browse/IDMPL-971). Jobs `contrib/graphql` 1/3 and 2/3 failed.
- `contrib::graphql:graphene` — [IDMPL-971](https://datadoghq.atlassian.net/browse/IDMPL-971). Job `contrib/graphql:graphene` failed.
- `contrib::grpc` — [IDMPL-1014](https://datadoghq.atlassian.net/browse/IDMPL-1014). Closest: [IDMPL-417](https://datadoghq.atlassian.net/browse/IDMPL-417) (`grpc_aio`, a different variant). The dropped variant is plain `grpc` on 3.14+3.15; job `contrib/grpc` 2/3 failed.
- `contrib::httpx2` — [IDMPL-1020](https://datadoghq.atlassian.net/browse/IDMPL-1020). Job `contrib/httpx2` 1/2 failed.
- `contrib::kafka` — [IDMPL-415](https://datadoghq.atlassian.net/browse/IDMPL-415). Job `contrib/kafka` 3/3 failed (`confluent-kafka` build).
- `contrib::mako` — [IDMPL-1021](https://datadoghq.atlassian.net/browse/IDMPL-1021). Only job `contrib/mako` 2/3 failed; 1/3 passed.
- `contrib::mlflow` — [IDMPL-972](https://datadoghq.atlassian.net/browse/IDMPL-972). Job `contrib/mlflow` 2/2 failed (`pyarrow` / `requires-python`).
- `contrib::mysqlpython` — [IDMPL-1022](https://datadoghq.atlassian.net/browse/IDMPL-1022). Suite is `skip: true`; the new 3.15 env never ran.
- `contrib::opensearch` — [IDMPL-1023](https://datadoghq.atlassian.net/browse/IDMPL-1023). Only job `contrib/opensearch` 3/3 failed; 1/3 and 2/3 passed.
- `contrib::opentelemetry` — [IDMPL-1024](https://datadoghq.atlassian.net/browse/IDMPL-1024). Job `contrib/opentelemetry` 9/10 failed.
- `contrib::protobuf` — [IDMPL-1025](https://datadoghq.atlassian.net/browse/IDMPL-1025). Job `contrib/protobuf` failed (`requires-python <3.15`).
- `contrib::psycopg` — [IDMPL-1026](https://datadoghq.atlassian.net/browse/IDMPL-1026). Jobs `contrib/psycopg` 2/5 and 4/5 failed.
- `contrib::pymongo` — [IDMPL-974](https://datadoghq.atlassian.net/browse/IDMPL-974). Jobs `contrib/pymongo` 1/4 and 2/4 failed; 3/4 passed.
- `contrib::redis` — [IDMPL-975](https://datadoghq.atlassian.net/browse/IDMPL-975). Job `contrib/redis` 3/3 failed.
- `contrib::snowflake` — [IDMPL-1027](https://datadoghq.atlassian.net/browse/IDMPL-1027). Job `contrib/snowflake` 1/3 failed (`requires-python <3.15`).
- `contrib::sqlalchemy` — [IDMPL-1028](https://datadoghq.atlassian.net/browse/IDMPL-1028). Job `contrib/sqlalchemy` failed.
- `contrib::urllib3` — [IDMPL-1029](https://datadoghq.atlassian.net/browse/IDMPL-1029). Job `contrib/urllib3` 2/3 failed.
- `llmobs::anthropic` — no existing ticket. Job `llmobs/anthropic` 1/2 failed.
- `llmobs::claude_agent_sdk` — no existing ticket. Only job `llmobs/claude_agent_sdk` 4/4 failed; 2/4 passed.
- `llmobs::google_adk` — no existing ticket. Only job `llmobs/google_adk` 4/5 failed; 2/5 passed.
- `llmobs::google_genai` — no existing ticket. Job `llmobs/google_genai` 1/3 failed.
- `llmobs::langgraph` — no existing ticket. Jobs `llmobs/langgraph` 2/6, 4/6, and 6/6 failed (`ormsgpack` / PyO3).
- `llmobs::litellm` — no existing ticket. Job `llmobs/litellm` 4/4 failed (`fastuuid` / PyO3).
- `llmobs::llama_index` — no existing ticket. Job `llmobs/llama_index` 2/4 failed.
- `llmobs::mcp` — no existing ticket. Job `llmobs/mcp` 3/3 failed.
- `llmobs::openai_agents` — no existing ticket. Job `llmobs/openai_agents` 5/5 failed (`requires-python <3.15`).
- `llmobs::pydantic_ai` — no existing ticket. Jobs `llmobs/pydantic_ai` 1/4 and 3/4 failed (`pydantic-core` / PyO3).
