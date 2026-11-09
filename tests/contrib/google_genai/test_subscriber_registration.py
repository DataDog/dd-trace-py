from ddtrace.contrib.internal.google_genai.patch import patch
from ddtrace.contrib.internal.google_genai.patch import unpatch


def test_direct_patch_restores_tracing_after_listener_reset(test_spans):
    from ddtrace import config
    from ddtrace._trace._registration import LLM_SUBSCRIBERS
    from ddtrace.contrib._events.llm import LlmRequestEvent
    from ddtrace.internal import core

    unpatch()
    try:
        for _ in range(2):
            LLM_SUBSCRIBERS.unregister()
            assert not core.has_listeners("context.started.llm.request")
            assert not core.has_listeners("context.ended.llm.request")
            patch()
            patch()
            event = LlmRequestEvent(
                component="google_genai",
                integration_config=config.google_genai,
                provider="test",
            )
            with core.context_with_event(event):
                pass
            spans = test_spans.pop()
            assert len(spans) == 1
            assert spans[0].name == "google_genai.request"
            assert spans[0].finished
    finally:
        unpatch()
