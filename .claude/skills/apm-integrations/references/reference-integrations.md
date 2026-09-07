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
| generative-ai | `anthropic/patch.py` | `litellm/patch.py` | LLM/AI integrations; use `llmobs-integrations` for LLMObs lifecycle, extraction, streaming, and tests |
| graphql | `graphql/patch.py` | -- | GraphQL resolvers and operations, Pin + `tracer.trace` |
| http-client | `httpx/patch.py` | `requests/connection.py` | Outbound HTTP, `http.*` span tags. httpx and requests use `context_with_event` |
| http-server | `flask/patch.py` | `django/patch.py` | Web frameworks, request/response spans, Pin + `context_with_data` |
| logging | `logging/patch.py` | `loguru/patch.py` | Log correlation injection (trace ID, span ID) -- no spans created |
| messaging | `kafka/patch.py` | `kombu/patch.py` | Message brokers, DSM support, Pin + `tracer.trace` |
| object-store | `botocore/patch.py` (S3) | -- | S3 via botocore service-specific handlers |
| orchestration | `celery/patch.py` | -- | Task orchestration, distributed tracing, Pin + `tracer.trace` via signals |
| rpc | `grpc/patch.py` | -- | RPC frameworks, client + server spans, Pin + `tracer.trace` |

## DBAPI Cursor Subclasses

Normalize queries in _normalize_dbapi_query(), not in _trace_method(). The shared
synchronous _prepare_dbapi_query() is also inherited by async cursors: it prepares
one resource for tracing/fetch spans and DbQueryEvent while forwarding the original
query and parameters to the driver. Rendering failures skip the event, not execution;
blocking listeners still run when tracing is disabled.

For psycopg3 templates, inspect SQL structure with $n placeholders for bound values
(default, :s, :t, :b), never by literal-adapting those values. Normalization supports
nested :q templates and built-in SQL/Composed/Identifier nodes, plus :i strings.
Literal interpolation (:l), Literal-containing composed trees, custom composable
subclasses, conversions, and unsupported formats fail open without rendering or
emitting a query event. These forms can invoke stateful adapters and must be left
to the driver. Legacy standalone psycopg SQL/Composed rendering is unchanged;
do not extend it to arbitrary as_string methods or custom composable subclasses.
Discover the already loaded driver/template modules independently of psycopg
patch/config state, since Django-only instrumentation supplies its own
IntegrationConfig and wrapper cursor.

aiopg did not previously render composables. Its normalization uses the native
_impl cursor and only built-in SQL/Identifier/Placeholder nodes and Composed trees
containing those nodes. Literal/custom nodes fail open, leaving adaptation to the
driver. Reuse _render_composable_query() instead of calling as_string() on an
unchecked tree.

When reusing `TracedCursor` or `TracedAsyncCursor`, check every inherited method
against the adapter's `_trace_method` signature. An adapter that retains a custom
signature can accidentally break inherited methods such as `callproc()`.
For example, aiomysql explicitly forwards `callproc()` to the wrapped driver to
preserve its previously untraced behavior. Cover positional and keyword arguments,
return values, and exceptions with tracing enabled and disabled.

## LLM / Generative AI Detail

This APM reference lists LLM/AI integrations only to help choose comparable
contrib patch modules. For LLMObs-specific architecture, provider extraction,
streaming, and test transport guidance, use the `llmobs-integrations` skill.
