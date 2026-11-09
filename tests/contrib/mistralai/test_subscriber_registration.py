from ddtrace.contrib.internal.mistralai.patch import patch
from ddtrace.contrib.internal.mistralai.patch import unpatch


def test_tracing_startup_owns_listeners_for_direct_patch(test_spans):
    from ddtrace import config
    from ddtrace._trace._registration import LLM_SUBSCRIBERS
    from ddtrace._trace._registration import initialize_subscribers
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
            assert not core.has_listeners("context.started.llm.request")
            initialize_subscribers()
            event = LlmRequestEvent(
                component="mistralai",
                integration_config=config.mistralai,
                provider="test",
            )
            with core.context_with_event(event):
                pass
            spans = test_spans.pop()
            assert len(spans) == 1
            assert spans[0].name == "mistralai.request"
            assert spans[0].finished
    finally:
        unpatch()
