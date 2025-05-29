import pytest
from starlette.requests import Request
from starlette.datastructures import Headers
from fastapi.responses import Response

from tests.contrib.litellm.utils import get_cassette_name
from tests.utils import override_global_config
from litellm.proxy.common_request_processing import ProxyBaseLLMRequestProcessing
from litellm.proxy._types import UserAPIKeyAuth


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


@pytest.mark.parametrize("model", ["command-r", "anthropic/claude-3-5-sonnet-20240620"])
def test_litellm_completion_different_models(litellm, snapshot_context, request_vcr, model):
    with snapshot_context(
        token="tests.contrib.litellm.test_litellm.test_litellm_completion", ignores=["meta.litellm.request.model"]
    ):
        with request_vcr.use_cassette(f"completion_{model.split('/')[0]}.yaml"):
            messages = [{"content": "Hey, what is up?", "role": "user"}]
            litellm.completion(
                model=model,
                messages=messages,
                stream=False,
                n=1,
            )

async def test_litellm_base_process_llm_request(snapshot_context):
    with snapshot_context(token="tests.contrib.litellm.test_litellm.test_litellm_base_process_llm_request"):
        headers = Headers({"host": "0.0.0.0:4000"})
        request = Request({"type": "http", "headers": headers})
        request._json = {"model": "gpt-3.5-turbo"}
        response = Response()
        user_api_key_dict = UserAPIKeyAuth()
        request_processor = ProxyBaseLLMRequestProcessing(data = {})
        await request_processor.base_process_llm_request(
            request=request,
            fastapi_response=response,
            user_api_key_dict=user_api_key_dict,
            route_type="acompletion",
            proxy_logging_obj=None,
            general_settings=None,
            proxy_config=None,
            select_data_generator=None,
        )