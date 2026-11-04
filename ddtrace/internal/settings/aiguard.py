from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._core import DDConfig


log = get_logger(__name__)

_DEFAULT_TIMEOUT_MS = 10_000

ENV_ENABLED = "DD_AI_GUARD_ENABLED"
ENV_ENDPOINT = "DD_AI_GUARD_ENDPOINT"
ENV_BLOCK = "DD_AI_GUARD_BLOCK"
ENV_MAX_CONTENT_SIZE = "DD_AI_GUARD_MAX_CONTENT_SIZE"
ENV_MAX_MESSAGES_LENGTH = "DD_AI_GUARD_MAX_MESSAGES_LENGTH"
ENV_TIMEOUT = "DD_AI_GUARD_TIMEOUT"
ENV_REDACTION_ENABLED = "DD_AI_GUARD_REDACTION_ENABLED"
ENV_ANALYZE_STREAM_RESPONSES_ENABLED = "DD_AI_GUARD_ANALYZE_STREAM_RESPONSES_ENABLED"
# Per-LLM kill switches: DD_AI_GUARD_<LLM>_ENABLED, true by default, set to
# false to disable AI Guard auto-instrumentation for that specific provider.
ENV_OPENAI_ENABLED = "DD_AI_GUARD_OPENAI_ENABLED"
ENV_ANTHROPIC_ENABLED = "DD_AI_GUARD_ANTHROPIC_ENABLED"
ENV_LANGCHAIN_ENABLED = "DD_AI_GUARD_LANGCHAIN_ENABLED"


def _parse_timeout(raw: str) -> int:
    # Whole-second flooring in the client is intended, documented behavior: do not switch it to / 1000.
    # Only values that would floor to 0 seconds are rejected here.
    timeout = int(raw)
    if timeout // 1000 <= 0:
        log.warning(
            "Invalid value for %s: %d is applied in whole seconds and must be at least 1000; using the default of %d",
            ENV_TIMEOUT,
            timeout,
            _DEFAULT_TIMEOUT_MS,
        )
        return _DEFAULT_TIMEOUT_MS
    return timeout


class AIGuardConfig(DDConfig):
    _ai_guard_enabled = DDConfig.var(bool, ENV_ENABLED, default=False)
    _ai_guard_endpoint = DDConfig.var(str, ENV_ENDPOINT, default="")
    _ai_guard_block = DDConfig.var(bool, ENV_BLOCK, default=True)
    _ai_guard_max_content_size = DDConfig.var(int, ENV_MAX_CONTENT_SIZE, default=512 * 1024)
    _ai_guard_max_messages_length = DDConfig.var(int, ENV_MAX_MESSAGES_LENGTH, default=16)
    _ai_guard_timeout = DDConfig.var(int, ENV_TIMEOUT, default=_DEFAULT_TIMEOUT_MS, parser=_parse_timeout)
    # Global kill switch for sensitive data redaction: when false the tracer never applies the
    # redaction_replacements returned by the backend, even though evaluation still runs.
    _ai_guard_redaction_enabled = DDConfig.var(bool, ENV_REDACTION_ENABLED, default=True)
    _ai_guard_analyze_stream_responses_enabled = DDConfig.var(bool, ENV_ANALYZE_STREAM_RESPONSES_ENABLED, default=False)
    # Per-LLM kill switches: disable AI Guard auto-instrumentation for a specific
    # provider/framework when false, without affecting others or requiring a rollback.
    _ai_guard_openai_enabled = DDConfig.var(bool, ENV_OPENAI_ENABLED, default=True)
    _ai_guard_anthropic_enabled = DDConfig.var(bool, ENV_ANTHROPIC_ENABLED, default=True)
    _ai_guard_langchain_enabled = DDConfig.var(bool, ENV_LANGCHAIN_ENABLED, default=True)

    # for tests purposes
    _ai_guard_config_keys = [
        "_ai_guard_enabled",
        "_ai_guard_endpoint",
        "_ai_guard_block",
        "_ai_guard_max_content_size",
        "_ai_guard_max_messages_length",
        "_ai_guard_timeout",
        "_ai_guard_redaction_enabled",
        "_ai_guard_analyze_stream_responses_enabled",
        "_ai_guard_openai_enabled",
        "_ai_guard_anthropic_enabled",
        "_ai_guard_langchain_enabled",
    ]

    def reset(self) -> None:
        """For testing purposes, reset the configuration to its default values given current environment variables."""
        self.__init__()  # type: ignore[misc]


aiguard_config = AIGuardConfig()
