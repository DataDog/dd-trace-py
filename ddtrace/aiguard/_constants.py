from typing import Literal  # noqa:F401

from ddtrace.internal.constants import Constant_Class


class AI_GUARD(metaclass=Constant_Class):
    # span related information
    RESOURCE_TYPE: Literal["ai_guard"] = "ai_guard"

    TAG: Literal["ai_guard"] = "ai_guard"
    ACTION_TAG: str = TAG + ".action"
    REASON_TAG: str = TAG + ".reason"
    TARGET_TAG: str = TAG + ".target"
    DIRECTION_TAG: str = TAG + ".direction"
    SOURCE_TAG: str = TAG + ".source"
    INTEGRATION_TAG: str = TAG + ".integration"
    BLOCKED_TAG: str = TAG + ".blocked"
    REDACTED_TAG: str = TAG + ".redacted"
    TOOL_NAME_TAG: str = TAG + ".tool_name"
    EVENT_TAG: str = TAG + ".event"
    # OpenAI runs hosted MCP tools itself and does not report whether it reached the server over
    # streamable HTTP or SSE, so their servers carry the unknown transport.
    MCP_TRANSPORT_UNKNOWN: Literal["unknown"] = "unknown"
    # managed_by of an MCP server whose client is run by the model provider (OpenAI hosted MCP).
    MCP_MANAGED_BY_PROVIDER: Literal["provider"] = "provider"

    # Core-context key for the (client_ip, peer_ip) pair, applied to the service-entry
    # span only if an ai_guard span is created during the request.
    CLIENT_IP_CORE_KEY: Literal["ai_guard.http.client_ip"] = "ai_guard.http.client_ip"

    # Tags copied from the local root (service-entry) span to every AI Guard span with the
    # `ai_guard.` prefix, so anomaly detection at intake can correlate without waiting for
    # the service-entry span to arrive in the same trace chunk.
    # Spec: https://datadoghq.atlassian.net/wiki/spaces/AIGuard/pages/6596165672
    ANOMALY_DETECTION_TAGS: tuple[str, ...] = (
        "http.useragent",
        "http.client_ip",
        "network.client.ip",
        "usr.id",
        "usr.session_id",
    )

    # meta struct
    STRUCT: Literal["ai_guard"] = "ai_guard"

    # metrics
    # Reported under the dedicated ai_guard telemetry namespace, so the full metric ids are
    # ai_guard.<name>. Spec: https://datadoghq.atlassian.net/wiki/spaces/AIGuard/pages/6600426215
    REQUESTS_METRIC: Literal["requests"] = "requests"
    TRUNCATED_METRIC: Literal["truncated"] = "truncated"
    ERROR_METRIC: Literal["error"] = "error"
    # Which transport the client chose, reported with the "transport" tag.
    TRANSPORT_METRIC: Literal["transport"] = "transport"
    # One point per evaluation sent through the Agent route, tagged status_code, error_type
    # and agent_error.
    AGENT_REQUESTS_METRIC: Literal["agent.requests"] = "agent.requests"
    TRANSPORT_TAG: Literal["transport"] = "transport"
    AGENT_ERROR_TAG: Literal["agent_error"] = "agent_error"
    ERROR_TYPE_TAG: Literal["error_type"] = "error_type"
    STATUS_CODE_TAG: Literal["status_code"] = "status_code"
    # Header the Agent sets on the errors it generates itself, as opposed to Datadog's responses
    # which it forwards untouched.
    AGENT_ERROR_HEADER: Literal["Datadog-Agent-Proxy-Error"] = "Datadog-Agent-Proxy-Error"
    # Every registered Agent endpoint answers with this header, so a 404 without it did not come
    # from the Agent route.
    AGENT_VERSION_HEADER: Literal["Datadog-Agent-Version"] = "Datadog-Agent-Version"
    # Statuses reported by the agent.requests metric, which unlike the error metric also has to
    # describe the requests that succeeded. Still an allowlist, so the tag stays bounded.
    AGENT_STATUSES: tuple[int, ...] = (200, 400, 401, 403, 404, 408, 413, 429, 500, 502, 503, 504)
    # Reported when the request never produced a response at all.
    STATUS_NONE: Literal["none"] = "none"
    AGENT_STATUS_OTHER: Literal["other"] = "other"
    # Values of the "error_type" tag on the agent.requests metric.
    AGENT_ERROR_NONE: Literal["none"] = "none"
    AGENT_ERROR_OTHER: Literal["other"] = "other"

    # Values of the "transport" tag: how evaluations reach the AI Guard API.
    TRANSPORT_DIRECT: Literal["direct"] = "direct"
    TRANSPORT_AGENT: Literal["agent"] = "agent"

    # Values of the "type" tag on the error metric.
    ERROR_CLIENT: Literal["client_error"] = "client_error"
    ERROR_BAD_STATUS: Literal["bad_status"] = "bad_status"
    ERROR_BAD_RESPONSE: Literal["bad_response"] = "bad_response"
    # Transport failures, split out of client_error so a customer-side network problem is
    # distinguishable from a request that reached the service and was rejected.
    ERROR_CONNECTION: Literal["connection_failed"] = "connection_failed"
    ERROR_TIMEOUT: Literal["timeout"] = "timeout"
    ERROR_INVALID_CONFIG: Literal["invalid_config"] = "invalid_config"
    ERROR_NETWORK: Literal["network_error"] = "network_error"
    # Anything raised inside our own code rather than by the transport. Kept separate so tracer
    # bugs do not inflate the transport buckets that customer-egress alerting reads.
    ERROR_INTERNAL: Literal["internal_error"] = "internal_error"

    # Values of the "http_status" tag, set only on bad_status errors. Clamped to this allowlist so
    # the tag cannot grow a new series per status a service ever returns.
    STATUSES: tuple[int, ...] = (401, 403, 404, 408, 413, 429, 500, 502, 503, 504)
    STATUS_OTHER: Literal["other"] = "other"
    # A replacement the service asked for could not be applied. Reported per affected path, and
    # never fails the evaluation, see the redaction errors addendum of the AI Guard redaction RFC.
    ERROR_REDACTION: Literal["redaction_error"] = "redaction_error"

    # Values of the "source" tag: which call path reached the evaluation. sdk means the
    # customer called evaluate() directly, auto means our AI package instrumentation did.
    SOURCE_SDK: Literal["sdk"] = "sdk"
    SOURCE_AUTO: Literal["auto"] = "auto"

    # Values of the "integration" tag: the auto-instrumented AI package name, or none when
    # the evaluation came from a direct SDK call.
    INTEGRATION_NONE: Literal["none"] = "none"
    INTEGRATION_OPENAI: Literal["openai"] = "openai"
    INTEGRATION_ANTHROPIC: Literal["anthropic"] = "anthropic"
    INTEGRATION_LANGCHAIN: Literal["langchain"] = "langchain"
    INTEGRATION_LITELLM: Literal["litellm"] = "litellm"
    INTEGRATION_STRANDS: Literal["strands"] = "strands"

    # The Agent route that proxies evaluations to api.<site> with the Agent's own credentials,
    # as advertised in the Agent's /info endpoint list.
    AGENT_PROXY_ENDPOINT: str = "/ai_guard/proxy/"
    # The AI Guard API path, appended to the Agent route in Agent transport mode.
    AGENT_PROXY_API_PATH: str = "api/v2/ai-guard"

    # Closed tag sets: anything else reaching the metrics is clamped back to these defaults,
    # so a bad value from a caller cannot invent telemetry series.
    SOURCES: tuple[str, ...] = (SOURCE_SDK, SOURCE_AUTO)
    INTEGRATIONS: tuple[str, ...] = (
        INTEGRATION_NONE,
        INTEGRATION_OPENAI,
        INTEGRATION_ANTHROPIC,
        INTEGRATION_LANGCHAIN,
        INTEGRATION_LITELLM,
        INTEGRATION_STRANDS,
    )
