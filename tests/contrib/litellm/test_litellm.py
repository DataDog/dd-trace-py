import pytest

from ddtrace.contrib.internal.litellm.patch import get_version
from ddtrace.internal.utils.version import parse_version
from tests.contrib.litellm.utils import get_cassette_name
from tests.utils import override_global_config


def test_global_tags(litellm, request_vcr, mock_tracer):
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

    span = mock_tracer.pop_traces()[0][0]
    assert span.resource == "completion"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("litellm.request.model") == "gpt-3.5-turbo"


@pytest.mark.parametrize("stream,n", [(True, 1), (True, 2), (False, 1), (False, 2)])
def test_litellm_completion(litellm, snapshot_context, request_vcr, stream, n):
    with snapshot_context(token="tests.contrib.litellm.test_litellm.test_litellm_completion"):
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
    with snapshot_context(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=["resource"]):
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
    with snapshot_context(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=["resource"]):
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
    with snapshot_context(token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=["resource"]):
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


@pytest.mark.parametrize("model", ["command-r", "anthropic/claude-sonnet-4-5-20250929"])
def test_litellm_completion_different_models(litellm, snapshot_context, request_vcr, model):
    model_base = model.split("/")[0]
    is_new_litellm = parse_version(get_version()) >= (1, 74, 15)

    if model == "command-r" and is_new_litellm:
        pytest.skip("Cassette not yet generated for command-r on litellm >= 1.74.15")

    if is_new_litellm:
        cassette_name = f"completion_{model_base}_v1_74_15.yaml"
    else:
        cassette_name = f"completion_{model_base}.yaml"

    with snapshot_context(
        token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=["meta.litellm.request.model"]
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
    with snapshot_context(token="tests.contrib.litellm.test_litellm.test_litellm_router_completion"):
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
        token="tests.contrib.litellm.test_litellm.test_litellm_router_completion", ignores=["resource"]
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
        token="tests.contrib.litellm.test_litellm.test_litellm_router_completion", ignores=["resource"]
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
        token="tests.contrib.litellm.test_litellm.test_litellm_router_completion", ignores=["resource"]
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
