from typing import Literal  # noqa:F401

from ddtrace.appsec._constants import Constant_Class


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
    TOOL_NAME_TAG: str = TAG + ".tool_name"
    EVENT_TAG: str = TAG + ".event"

    # core-context key used to stash the candidate client IP during an HTTP request, so it can be
    # applied to the service-entry span only if an ai_guard span is actually created.
    CLIENT_IP_CORE_KEY: Literal["ai_guard.http.client_ip"] = "ai_guard.http.client_ip"

    # Tags copied from the local root (service-entry) span to every AI Guard span with the
    # `ai_guard.` prefix, so anomaly detection at intake can correlate without waiting for
    # the service-entry span to arrive in the same trace chunk.
    # Spec: https://datadoghq.atlassian.net/wiki/spaces/AIGuard/pages/6596165672
    ANOMALY_DETECTION_TAGS: tuple = (
        "http.useragent",
        "http.client_ip",
        "network.client.ip",
        "usr.id",
        "usr.session_id",
    )

    # meta struct
    STRUCT: Literal["ai_guard"] = "ai_guard"

    # metrics
    METRIC_PREFIX: Literal["ai_guard"] = "ai_guard"
    REQUESTS_METRIC: str = METRIC_PREFIX + ".requests"
    TRUNCATED_METRIC: str = METRIC_PREFIX + ".truncated"

    # environment variables
    ENV_ENABLED: Literal["DD_AI_GUARD_ENABLED"] = "DD_AI_GUARD_ENABLED"
    ENV_ENDPOINT: Literal["DD_AI_GUARD_ENDPOINT"] = "DD_AI_GUARD_ENDPOINT"
    ENV_MAX_CONTENT_SIZE: Literal["DD_AI_GUARD_MAX_CONTENT_SIZE"] = "DD_AI_GUARD_MAX_CONTENT_SIZE"
    ENV_MAX_MESSAGES_LENGTH: Literal["DD_AI_GUARD_MAX_MESSAGES_LENGTH"] = "DD_AI_GUARD_MAX_MESSAGES_LENGTH"
    ENV_TIMEOUT: Literal["DD_AI_GUARD_TIMEOUT"] = "DD_AI_GUARD_TIMEOUT"
    ENV_ANALYZE_STREAM_RESPONSES_ENABLED: Literal["DD_AI_GUARD_ANALYZE_STREAM_RESPONSES_ENABLED"] = (
        "DD_AI_GUARD_ANALYZE_STREAM_RESPONSES_ENABLED"
    )
    # Per-LLM kill switches: DD_AI_GUARD_<LLM>_ENABLED, true by default, set to
    # false to disable AI Guard auto-instrumentation for that specific provider.
    ENV_OPENAI_ENABLED: Literal["DD_AI_GUARD_OPENAI_ENABLED"] = "DD_AI_GUARD_OPENAI_ENABLED"
    ENV_ANTHROPIC_ENABLED: Literal["DD_AI_GUARD_ANTHROPIC_ENABLED"] = "DD_AI_GUARD_ANTHROPIC_ENABLED"
    ENV_LANGCHAIN_ENABLED: Literal["DD_AI_GUARD_LANGCHAIN_ENABLED"] = "DD_AI_GUARD_LANGCHAIN_ENABLED"
