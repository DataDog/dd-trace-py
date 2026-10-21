from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._core import DDConfig


log = get_logger(__name__)

_DEFAULT_TIMEOUT_MS = 10_000


def _parse_timeout(raw: str) -> int:
    # Whole-second flooring in the client is intended, documented behavior: do not switch it to / 1000.
    # Only values that would floor to 0 seconds are rejected here.
    timeout = int(raw)
    if timeout // 1000 <= 0:
        log.warning(
            "Invalid value for %s: %d is applied in whole seconds and must be at least 1000; using the default of %d",
            AI_GUARD.ENV_TIMEOUT,
            timeout,
            _DEFAULT_TIMEOUT_MS,
        )
        return _DEFAULT_TIMEOUT_MS
    return timeout


class AIGuardConfig(DDConfig):
    _ai_guard_enabled = DDConfig.var(bool, AI_GUARD.ENV_ENABLED, default=False)
    _ai_guard_endpoint = DDConfig.var(str, AI_GUARD.ENV_ENDPOINT, default="")
    # POC: with the Agent transport, ask the Agent EVP proxy to attach its own application key.
    _ai_guard_agent_needs_app_key = DDConfig.var(bool, AI_GUARD.ENV_AGENT_NEEDS_APP_KEY, default=False)
    _ai_guard_block = DDConfig.var(bool, AI_GUARD.BLOCK_ENV, default=True)
    _ai_guard_max_content_size = DDConfig.var(int, AI_GUARD.ENV_MAX_CONTENT_SIZE, default=512 * 1024)
    _ai_guard_max_messages_length = DDConfig.var(int, AI_GUARD.ENV_MAX_MESSAGES_LENGTH, default=16)
    _ai_guard_timeout = DDConfig.var(int, AI_GUARD.ENV_TIMEOUT, default=_DEFAULT_TIMEOUT_MS, parser=_parse_timeout)
    # Global kill switch for sensitive data redaction: when false the tracer never applies the
    # redaction_replacements returned by the backend, even though evaluation still runs.
    _ai_guard_redaction_enabled = DDConfig.var(bool, AI_GUARD.ENV_REDACTION_ENABLED, default=True)
    _ai_guard_analyze_stream_responses_enabled = DDConfig.var(
        bool, AI_GUARD.ENV_ANALYZE_STREAM_RESPONSES_ENABLED, default=False
    )
    # Per-LLM kill switches: disable AI Guard auto-instrumentation for a specific
    # provider/framework when false, without affecting others or requiring a rollback.
    _ai_guard_openai_enabled = DDConfig.var(bool, AI_GUARD.ENV_OPENAI_ENABLED, default=True)
    _ai_guard_anthropic_enabled = DDConfig.var(bool, AI_GUARD.ENV_ANTHROPIC_ENABLED, default=True)
    _ai_guard_langchain_enabled = DDConfig.var(bool, AI_GUARD.ENV_LANGCHAIN_ENABLED, default=True)

    # for tests purposes
    _ai_guard_config_keys = [
        "_ai_guard_enabled",
        "_ai_guard_endpoint",
        "_ai_guard_agent_needs_app_key",
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
