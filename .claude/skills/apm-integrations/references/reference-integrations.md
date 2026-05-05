# Reference Integrations

Canonical dd-trace-py integrations organized by the 13 integration categories.
**Read the canonical integration for your category before writing code.**

All patch modules live in `ddtrace/contrib/internal/{name}/`.

## By Category

| Category | Canonical | Secondary | Notes |
|----------|-----------|-----------|-------|
| cache | `redis/patch.py` | `pymemcache/patch.py` | Key-value stores, `cache.*` span tags, uses Pin + `context_with_data` via redis_utils |
| cloud-provider | `botocore/patch.py` | `google_genai/patch.py` | AWS services via botocore (Pin + `context_with_data`), GCP via google libs (LLM pattern) |
| database | `psycopg/patch.py` | `mysql/patch.py` | SQL clients, `db.*` span tags, DBM support, uses Pin + dbapi helpers |
| faas | `aws_lambda/patch.py` | `azure_functions/patch.py` | Serverless function wrappers, uses `ddtrace.internal.wrapping` |
| generative-ai | `anthropic/patch.py` | `litellm/patch.py` | `BaseLLMIntegration.trace()`, `integration.llmobs_set_tags()`, streaming support |
| graphql | `graphql/patch.py` | -- | GraphQL resolvers and operations, Pin + `tracer.trace` |
| http-client | `httpx/patch.py` | `requests/connection.py` | Outbound HTTP, `http.*` span tags. httpx uses `context_with_data`; requests uses Pin + `tracer.trace` |
| http-server | `flask/patch.py` | `django/patch.py` | Web frameworks, request/response spans, Pin + `context_with_data` |
| logging | `logging/patch.py` | `loguru/patch.py` | Log correlation injection (trace ID, span ID) -- no spans created |
| messaging | `kafka/patch.py` | `kombu/patch.py` | Message brokers, DSM support, Pin + `tracer.trace` |
| object-store | `botocore/patch.py` (S3) | -- | S3 via botocore service-specific handlers |
| orchestration | `celery/patch.py` | -- | Task orchestration, distributed tracing, Pin + `tracer.trace` via signals |
| rpc | `grpc/patch.py` | -- | RPC frameworks, client + server spans, Pin + `tracer.trace` |

## LLM / Generative AI Detail

LLM integrations use `BaseLLMIntegration` for span management. Key characteristics:

- Patch code creates a `BaseLLMIntegration` subclass instance and stores it on the module (e.g., `anthropic._datadog_integration`)
- Patch code calls `integration.trace()` to create spans (internally uses `tracer.start_span()`)
- Patch code directly manages spans: `span.set_exc_info(*sys.exc_info())`, `span.finish()`
- Patch code calls `integration.llmobs_set_tags(span, args=[], kwargs=kwargs, response=result)` for LLMObs
- LLMObs subclass in `ddtrace/llmobs/_integrations/{name}.py` handles message/token extraction
- Streaming uses span handoff to stream handlers (span is not finished until iterator exhausted)
- For streaming pattern, read `litellm/patch.py`; for agentic/multi-op, read `crewai/patch.py`
