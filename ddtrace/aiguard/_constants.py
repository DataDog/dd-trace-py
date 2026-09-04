from typing import Literal  # noqa:F401

from ddtrace.internal.constants import Constant_Class


class AI_GUARD(metaclass=Constant_Class):
    # environment variables
    BLOCK_ENV: Literal["DD_AI_GUARD_BLOCK"] = "DD_AI_GUARD_BLOCK"

    # span related information
    RESOURCE_TYPE: Literal["ai_guard"] = "ai_guard"

    TAG: Literal["ai_guard"] = "ai_guard"
    ACTION_TAG: str = TAG + ".action"
    REASON_TAG: str = TAG + ".reason"
    TARGET_TAG: str = TAG + ".target"
    BLOCKED_TAG: str = TAG + ".blocked"
    REDACTED_TAG: str = TAG + ".redacted"
    TOOL_NAME_TAG: str = TAG + ".tool_name"
    EVENT_TAG: str = TAG + ".event"

    # core-context key used to stash the candidate client IP during an HTTP request, so it can be
    # applied to the service-entry span only if an ai_guard span is actually created.
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

    # Values of the "type" tag on the error metric.
    ERROR_CLIENT: Literal["client_error"] = "client_error"
    ERROR_BAD_STATUS: Literal["bad_status"] = "bad_status"
    ERROR_BAD_RESPONSE: Literal["bad_response"] = "bad_response"
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

    # environment variables
    ENV_ENABLED: Literal["DD_AI_GUARD_ENABLED"] = "DD_AI_GUARD_ENABLED"
    ENV_ENDPOINT: Literal["DD_AI_GUARD_ENDPOINT"] = "DD_AI_GUARD_ENDPOINT"
    ENV_MAX_CONTENT_SIZE: Literal["DD_AI_GUARD_MAX_CONTENT_SIZE"] = "DD_AI_GUARD_MAX_CONTENT_SIZE"
    ENV_MAX_MESSAGES_LENGTH: Literal["DD_AI_GUARD_MAX_MESSAGES_LENGTH"] = "DD_AI_GUARD_MAX_MESSAGES_LENGTH"
    ENV_REDACTION_ENABLED: Literal["DD_AI_GUARD_REDACTION_ENABLED"] = "DD_AI_GUARD_REDACTION_ENABLED"
    ENV_TIMEOUT: Literal["DD_AI_GUARD_TIMEOUT"] = "DD_AI_GUARD_TIMEOUT"
    ENV_ANALYZE_STREAM_RESPONSES_ENABLED: Literal["DD_AI_GUARD_ANALYZE_STREAM_RESPONSES_ENABLED"] = (
        "DD_AI_GUARD_ANALYZE_STREAM_RESPONSES_ENABLED"
    )
    # Per-LLM kill switches: DD_AI_GUARD_<LLM>_ENABLED, true by default, set to
    # false to disable AI Guard auto-instrumentation for that specific provider.
    ENV_OPENAI_ENABLED: Literal["DD_AI_GUARD_OPENAI_ENABLED"] = "DD_AI_GUARD_OPENAI_ENABLED"
    ENV_ANTHROPIC_ENABLED: Literal["DD_AI_GUARD_ANTHROPIC_ENABLED"] = "DD_AI_GUARD_ANTHROPIC_ENABLED"
    ENV_LANGCHAIN_ENABLED: Literal["DD_AI_GUARD_LANGCHAIN_ENABLED"] = "DD_AI_GUARD_LANGCHAIN_ENABLED"
