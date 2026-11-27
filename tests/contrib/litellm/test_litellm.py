import pytest

from ddtrace.contrib._events.litellm import LiteLLMCallEvent
from ddtrace.contrib.internal.litellm.patch import get_version
from ddtrace.internal import core
from ddtrace.internal._exceptions import DDBlockException
from ddtrace.internal.core.subscriber import ContextSubscriber
from ddtrace.internal.utils.version import parse_version
from tests.contrib.litellm.utils import get_cassette_name
from tests.utils import override_global_config


def test_global_tags(litellm, request_vcr, test_spans):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        cassette_name = "completion.yaml"
        with request_vcr.use_cassette(cassette_name):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
            )

    span = test_spans.pop_traces()[0][0]
    assert span.resource == "completion"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("litellm.request.model") == "gpt-3.5-turbo"


@pytest.mark.parametrize("metadata", [None, {}])
def test_litellm_completion_with_metadata_none(litellm, request_vcr, test_spans, metadata):
    with request_vcr.use_cassette("completion.yaml"):
        litellm.completion(
            model="gpt-3.5-turbo",
            messages=[{"content": "Hey, what is up?", "role": "user"}],
            metadata=metadata,
        )

    span = test_spans.pop_traces()[0][0]
    assert span.resource == "completion"
    assert span.error == 0


@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
def test_litellm_completion(litellm, snapshot_context, request_vcr, stream, n):
    shadow_ignores = [
        "metrics._dd.llmobs.input_tokens",
        "metrics._dd.llmobs.output_tokens",
        "metrics._dd.llmobs.total_tokens",
    ]
    with snapshot_context(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=shadow_ignores):
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
            )
            if stream:
                for _ in resp:
                    pass


@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
async def test_litellm_acompletion(litellm, snapshot_context, request_vcr, stream, n):
    shadow_ignores = [
        "resource",
        "metrics._dd.llmobs.input_tokens",
        "metrics._dd.llmobs.output_tokens",
        "metrics._dd.llmobs.total_tokens",
    ]
    with snapshot_context(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=shadow_ignores):
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = await litellm.acompletion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
            )
            if stream:
                async for _ in resp:
                    pass


@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
def test_litellm_text_completion(litellm, snapshot_context, request_vcr, stream, n):
    shadow_ignores = [
        "resource",
        "metrics._dd.llmobs.input_tokens",
        "metrics._dd.llmobs.output_tokens",
        "metrics._dd.llmobs.total_tokens",
    ]
    with snapshot_context(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=shadow_ignores):
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            resp = litellm.text_completion(
                model="gpt-3.5-turbo",
                prompt="Hello world",
                stream=stream,
                n=n,
            )
            if stream:
                for _ in resp:
                    pass


@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
async def test_litellm_atext_completion(litellm, snapshot_context, request_vcr, stream, n):
    shadow_ignores = [
        "resource",
        "metrics._dd.llmobs.input_tokens",
        "metrics._dd.llmobs.output_tokens",
        "metrics._dd.llmobs.total_tokens",
    ]
    with snapshot_context(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=shadow_ignores):
        with request_vcr.use_cassette(get_cassette_name(stream, n)):
            resp = await litellm.atext_completion(
                model="gpt-3.5-turbo",
                prompt="Hello world",
                stream=stream,
                n=n,
            )
            if stream:
                async for _ in resp:
                    pass


@pytest.mark.parametrize("model", ["cohere_chat/command-r", "anthropic/claude-sonnet-4-5-20250929"])
def test_litellm_completion_different_models(litellm, snapshot_context, request_vcr, model):
    model_base = model.split("/")[0]
    is_new_litellm = parse_version(get_version()) >= (1, 74, 15)

    if model_base == "cohere_chat" and is_new_litellm:
        pytest.skip("Cassette not yet generated for cohere_chat/command-r on litellm >= 1.74.15")

    if is_new_litellm:
        cassette_name = f"completion_{model_base}_v1_74_15.yaml"
    else:
        cassette_name = f"completion_{model_base}.yaml"

    with snapshot_context(
        token="tests.contrib.litellm.test_litellm.test_litellm_completion",
        ignores=[
            "meta.litellm.request.model",
            "meta._dd.llmobs.model_name",
            "meta._dd.llmobs.model_provider",
            "metrics._dd.llmobs.input_tokens",
            "metrics._dd.llmobs.output_tokens",
            "metrics._dd.llmobs.total_tokens",
        ],
    ):
        with request_vcr.use_cassette(cassette_name):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            litellm.completion(
                model=model,
                messages=messages,
                stream=False,
                n=1,
            )


@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
def test_litellm_router_completion(litellm, snapshot_context, request_vcr, router, stream, n):
    shadow_ignores = [
        "metrics._dd.llmobs.input_tokens",
        "metrics._dd.llmobs.output_tokens",
        "metrics._dd.llmobs.total_tokens",
    ]
    with snapshot_context(
        token="tests.contrib.litellm.test_litellm.test_litellm_router_completion", ignores=shadow_ignores
    ):
        with request_vcr.use_cassette(get_cassette_name(stream=stream, n=n)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = router.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
            )
            if stream:
                for _ in resp:
                    pass


@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
async def test_litellm_router_acompletion(litellm, snapshot_context, request_vcr, router, stream, n):
    with snapshot_context(
        token="tests.contrib.litellm.test_litellm.test_litellm_router_completion",
        ignores=[
            "resource",
            "metrics._dd.llmobs.input_tokens",
            "metrics._dd.llmobs.output_tokens",
            "metrics._dd.llmobs.total_tokens",
        ],
    ):
        with request_vcr.use_cassette(get_cassette_name(stream=stream, n=n)):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            resp = await router.acompletion(
                model="gpt-3.5-turbo",
                messages=messages,
                stream=stream,
                n=n,
            )
            if stream:
                async for _ in resp:
                    pass


@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
def test_litellm_router_text_completion(litellm, snapshot_context, request_vcr, router, stream, n):
    with snapshot_context(
        token="tests.contrib.litellm.test_litellm.test_litellm_router_completion",
        ignores=[
            "resource",
            "metrics._dd.llmobs.input_tokens",
            "metrics._dd.llmobs.output_tokens",
            "metrics._dd.llmobs.total_tokens",
        ],
    ):
        with request_vcr.use_cassette(get_cassette_name(stream=stream, n=n)):
            prompt = "What color is the sky?"
            resp = router.text_completion(
                model="gpt-3.5-turbo",
                prompt=prompt,
                stream=stream,
                n=n,
            )
            if stream:
                for _ in resp:
                    pass


@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
async def test_litellm_router_atext_completion(litellm, snapshot_context, request_vcr, router, stream, n):
    with snapshot_context(
        token="tests.contrib.litellm.test_litellm.test_litellm_router_completion",
        ignores=[
            "resource",
            "metrics._dd.llmobs.input_tokens",
            "metrics._dd.llmobs.output_tokens",
            "metrics._dd.llmobs.total_tokens",
        ],
    ):
        with request_vcr.use_cassette(get_cassette_name(stream=stream, n=n)):
            prompt = "What color is the sky?"
            resp = await router.atext_completion(
                model="gpt-3.5-turbo",
                prompt=prompt,
                stream=stream,
                n=n,
            )
            if stream:
                async for _ in resp:
                    pass


def test_litellm_router_stream_handler_attribute(litellm, request_vcr, router, test_spans):
    """
    Regression test for litellm>=1.74.15 FallbackStreamWrapper compatibility.
    Ensures spans are properly finished when handler attribute is not available.
    """
    with request_vcr.use_cassette(get_cassette_name(stream=True, n=1)):
        messages = [{"content": "Hey, what is up?", "role": "user"}]
        resp = router.completion(
            model="gpt-3.5-turbo",
            messages=messages,
            stream=True,
        )

        # The response should be consumable without AttributeError
        chunks_received = 0
        for chunk in resp:
            chunks_received += 1

        assert chunks_received > 0, "Should have received at least one chunk"

        # Verify that a span was created and finished
        spans = test_spans.pop_traces()
        assert len(spans) > 0, "Should have created at least one trace"
        assert len(spans[0]) > 0, "Should have created at least one span"
        span = spans[0][0]
        assert span.duration > 0, "Span should be finished with a duration set"


async def test_litellm_router_astream_handler_attribute(litellm, request_vcr, router, test_spans):
    """
    Regression test for litellm>=1.74.15 FallbackStreamWrapper compatibility (async).
    Ensures spans are properly finished when handler attribute is not available.
    """
    with request_vcr.use_cassette(get_cassette_name(stream=True, n=1)):
        messages = [{"content": "Hey, what is up?", "role": "user"}]
        resp = await router.acompletion(
            model="gpt-3.5-turbo",
            messages=messages,
            stream=True,
        )

        # The response should be consumable without AttributeError
        chunks_received = 0
        async for chunk in resp:
            chunks_received += 1

        assert chunks_received > 0, "Should have received at least one chunk"

        # Verify that a span was created and finished
        spans = test_spans.pop_traces()
        assert len(spans) > 0, "Should have created at least one trace"
        assert len(spans[0]) > 0, "Should have created at least one span"
        span = spans[0][0]
        assert span.duration > 0, "Span should be finished with a duration set"


class _StopCall(DDBlockException):
    pass


@pytest.mark.parametrize("stream", [False, True])
async def test_litellm_call_events(litellm, request_vcr, stream):
    calls = []

    class CallSubscriber(ContextSubscriber[LiteLLMCallEvent]):
        event_names = (LiteLLMCallEvent.event_name,)

        @classmethod
        def on_started(cls, ctx):
            assert isinstance(ctx.event, LiteLLMCallEvent)
            ctx.set_item("seen", True)
            calls.append(("started", ctx.event.operation, ctx.event.request_kwargs.get("model"), ctx.event.stream))

        @classmethod
        def on_ended(cls, ctx, exc_info):
            assert isinstance(ctx.event, LiteLLMCallEvent)
            assert exc_info == (None, None, None)
            calls.append(
                (
                    "ended",
                    ctx.event.operation,
                    ctx.get_item("seen"),
                    ctx.event.stream,
                    ctx.event.response is not None,
                )
            )

    CallSubscriber.register()
    try:
        messages = [{"content": "Hey, what is up?", "role": "user"}]
        with request_vcr.use_cassette(get_cassette_name(stream, 1)):
            resp = litellm.completion(model="gpt-3.5-turbo", messages=messages, stream=stream)
            if stream:
                for _ in resp:
                    pass
        with request_vcr.use_cassette(get_cassette_name(stream, 1)):
            resp = await litellm.acompletion(model="gpt-3.5-turbo", messages=messages, stream=stream)
            if stream:
                async for _ in resp:
                    pass
    finally:
        CallSubscriber.unregister()

    # A streamed call leaves response as None on ended: its answer arrives later, chunk by chunk.
    expected = [
        ("started", "completion", "gpt-3.5-turbo", stream),
        ("ended", "completion", True, stream, not stream),
        ("started", "acompletion", "gpt-3.5-turbo", stream),
        ("ended", "acompletion", True, stream, not stream),
    ]
    assert calls == expected


@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize("use_router", [False, True])
async def test_litellm_stream_base_exception_finishes_span(litellm, router, test_spans, use_router, is_async):
    class BlockingSubscriber(ContextSubscriber[LiteLLMCallEvent]):
        event_names = (LiteLLMCallEvent.event_name,)

        @classmethod
        def on_started(cls, ctx):
            raise _StopCall()

    target = router if use_router else litellm
    BlockingSubscriber.register()
    try:
        messages = [{"content": "Hey, what is up?", "role": "user"}]
        with pytest.raises(_StopCall):
            if is_async:
                await target.acompletion(model="gpt-3.5-turbo", messages=messages, stream=True)
            else:
                target.completion(model="gpt-3.5-turbo", messages=messages, stream=True)
    finally:
        BlockingSubscriber.unregister()

    spans = [span for trace in test_spans.pop_traces() for span in trace]
    assert spans, "The call should have created a span"
    for span in spans:
        assert span.duration is not None, f"{span.resource} span should be finished"
        assert span.error == 1


@pytest.mark.parametrize("is_async", [False, True])
async def test_litellm_ended_base_exception_finishes_span(litellm, request_vcr, test_spans, is_async):
    class BlockingResponseSubscriber(ContextSubscriber[LiteLLMCallEvent]):
        event_names = (LiteLLMCallEvent.event_name,)

        @classmethod
        def on_ended(cls, ctx, exc_info):
            assert ctx.event.response is not None
            raise _StopCall()

    context_before = core.current
    BlockingResponseSubscriber.register()
    try:
        messages = [{"content": "Hey, what is up?", "role": "user"}]
        with request_vcr.use_cassette(get_cassette_name(False, 1)):
            with pytest.raises(_StopCall):
                if is_async:
                    await litellm.acompletion(model="gpt-3.5-turbo", messages=messages, stream=False)
                else:
                    litellm.completion(model="gpt-3.5-turbo", messages=messages, stream=False)
    finally:
        BlockingResponseSubscriber.unregister()

    assert core.current is context_before
    spans = [span for trace in test_spans.pop_traces() for span in trace]
    assert spans, "The call should have created a span"
    for span in spans:
        assert span.duration is not None, f"{span.resource} span should be finished"
        assert span.error == 1
