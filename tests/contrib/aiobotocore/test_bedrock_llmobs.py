import json

import botocore.session
import mock
import pytest

from ddtrace.contrib.internal.aiobotocore.patch import patch
from ddtrace.contrib.internal.aiobotocore.patch import unpatch
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.contrib.aiobotocore.utils import aiobotocore_client
from tests.llmobs._processors import install_mock_llmobs_writer
from tests.llmobs._utils import assert_llmobs_span_data
from tests.utils import override_global_config


MODEL_ID = "meta.llama2-13b-chat-v1"
REQUEST_BODY = {
    "prompt": "What does 'lorem ipsum' mean?",
    "temperature": 0.9,
    "top_p": 1.0,
    "max_gen_len": 60,
}
RESPONSE_BODY = json.dumps(
    {
        "generation": "Lorem ipsum is placeholder text used in publishing and design.",
        "stop_reason": "max_tokens",
    }
).encode()
CONVERSE_REQUEST = {
    "modelId": "anthropic.claude-3-sonnet-20240229-v1:0",
    "messages": [{"role": "user", "content": [{"text": "Explain distributed tracing."}]}],
    "inferenceConfig": {"temperature": 0.7, "maxTokens": 100},
}

_botocore_session = botocore.session.get_session()
_available_services = _botocore_session.get_available_services()
HAS_BEDROCK_RUNTIME = "bedrock-runtime" in _available_services
if HAS_BEDROCK_RUNTIME:
    _bedrock_operations = set(_botocore_session.get_service_model("bedrock-runtime").operation_names)
else:
    _bedrock_operations = set()
HAS_CONVERSE = {"Converse", "ConverseStream"}.issubset(_bedrock_operations)

pytestmark = pytest.mark.skipif(not HAS_BEDROCK_RUNTIME, reason="Bedrock Runtime is unavailable in this botocore")


class AsyncStreamingBody:
    def __init__(self, body):
        self._body = body
        self._content_length = len(body)
        self._position = 0

    async def read(self, amt=None):
        end = len(self._body) if amt is None else self._position + amt
        chunk = self._body[self._position : end]
        self._position += len(chunk)
        return chunk

    def tell(self):
        return self._position

    async def readinto(self, buffer):
        chunk = await self.read(len(buffer))
        buffer[: len(chunk)] = chunk
        return len(chunk)

    async def readlines(self):
        return [line async for line in self.iter_lines()]

    async def iter_chunks(self, chunk_size=1024):
        while True:
            chunk = await self.read(chunk_size)
            if not chunk:
                break
            yield chunk

    async def iter_lines(self, chunk_size=1024, keepends=False):
        pending = b""
        async for chunk in self.iter_chunks(chunk_size):
            lines = (pending + chunk).splitlines(True)
            for line in lines[:-1]:
                yield line.splitlines(keepends)[0]
            pending = lines[-1]
        if pending:
            yield pending.splitlines(keepends)[0]

    def __aiter__(self):
        return self

    async def __anext__(self):
        chunk = await self.read(16)
        if chunk:
            return chunk
        raise StopAsyncIteration

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class AsyncEventStream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration


@pytest.fixture(autouse=True)
def patch_aiobotocore():
    patch()
    yield
    unpatch()


@pytest.fixture
def bedrock_llmobs(tracer):
    LLMObs.disable()
    with override_global_config(
        {
            "_llmobs_ml_app": "<ml-app-name>",
            "_dd_api_key": "<not-a-real-key>",
        }
    ):
        LLMObs.enable(_tracer=tracer, integrations_enabled=False, agentless_enabled=False)
        install_mock_llmobs_writer(tracer)
        yield LLMObs
    LLMObs.disable()


@pytest.mark.asyncio
async def test_llmobs_invoke_model_finishes_after_async_body_read(bedrock_llmobs, tracer, test_spans):
    response_body = AsyncStreamingBody(RESPONSE_BODY)
    parsed_response = {
        "ResponseMetadata": {
            "RequestId": "fddf10b3-c895-4e5d-9b21-3ca963708b03",
            "HTTPStatusCode": 200,
            "HTTPHeaders": {
                "x-amzn-bedrock-invocation-latency": "2823",
                "x-amzn-bedrock-output-token-count": "91",
                "x-amzn-bedrock-input-token-count": "10",
            },
            "RetryAttempts": 0,
        },
        "contentType": "application/json",
        "body": response_body,
    }
    http_response = mock.MagicMock(status_code=200)

    async with aiobotocore_client("bedrock-runtime", tracer) as client:
        with mock.patch.object(client, "_make_request", new_callable=mock.AsyncMock) as make_request:
            make_request.return_value = http_response, parsed_response
            response = await client.invoke_model(body=json.dumps(REQUEST_BODY), modelId=MODEL_ID)

        assert test_spans.pop_traces() == []
        assert await response["body"].read() == RESPONSE_BODY

    spans = [span for trace in test_spans.pop_traces() for span in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_name=MODEL_ID,
        model_provider="amazon_bedrock",
        input_messages=[{"content": REQUEST_BODY["prompt"]}],
        output_messages=[{"content": "Lorem ipsum is placeholder text used in publishing and design."}],
        metadata={"temperature": 0.9, "max_tokens": 60},
        metrics={"input_tokens": 10, "output_tokens": 91, "total_tokens": 101},
        tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>", "integration": "bedrock"},
    )


@pytest.mark.asyncio
async def test_llmobs_invoke_model_read_zero_does_not_finish_body(bedrock_llmobs, tracer, test_spans):
    parsed_response = {
        "ResponseMetadata": {
            "RequestId": "fddf10b3-c895-4e5d-9b21-3ca963708b03",
            "HTTPStatusCode": 200,
            "HTTPHeaders": {
                "x-amzn-bedrock-output-token-count": "91",
                "x-amzn-bedrock-input-token-count": "10",
            },
            "RetryAttempts": 0,
        },
        "contentType": "application/json",
        "body": AsyncStreamingBody(RESPONSE_BODY),
    }
    http_response = mock.MagicMock(status_code=200)

    async with aiobotocore_client("bedrock-runtime", tracer) as client:
        with mock.patch.object(client, "_make_request", new_callable=mock.AsyncMock) as make_request:
            make_request.return_value = http_response, parsed_response
            response = await client.invoke_model(body=json.dumps(REQUEST_BODY), modelId=MODEL_ID)

        assert await response["body"].read(0) == b""
        assert test_spans.pop_traces() == []
        assert await response["body"].read() == RESPONSE_BODY

    spans = [span for trace in test_spans.pop_traces() for span in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_name=MODEL_ID,
        model_provider="amazon_bedrock",
        output_messages=[{"content": "Lorem ipsum is placeholder text used in publishing and design."}],
        metrics={"input_tokens": 10, "output_tokens": 91, "total_tokens": 101},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["readinto", "readlines", "iter_chunks", "iter_lines", "anext", "context"])
async def test_llmobs_invoke_model_preserves_async_body_consumers(consumer, bedrock_llmobs, tracer, test_spans):
    parsed_response = {
        "ResponseMetadata": {
            "RequestId": "fddf10b3-c895-4e5d-9b21-3ca963708b03",
            "HTTPStatusCode": 200,
            "HTTPHeaders": {
                "x-amzn-bedrock-output-token-count": "91",
                "x-amzn-bedrock-input-token-count": "10",
            },
            "RetryAttempts": 0,
        },
        "contentType": "application/json",
        "body": AsyncStreamingBody(RESPONSE_BODY),
    }
    http_response = mock.MagicMock(status_code=200)

    async with aiobotocore_client("bedrock-runtime", tracer) as client:
        with mock.patch.object(client, "_make_request", new_callable=mock.AsyncMock) as make_request:
            make_request.return_value = http_response, parsed_response
            response = await client.invoke_model(body=json.dumps(REQUEST_BODY), modelId=MODEL_ID)

        body = response["body"]
        if consumer == "readinto":
            chunks = []
            while True:
                buffer = bytearray(17)
                amount_read = await body.readinto(buffer)
                if amount_read == 0:
                    break
                chunks.append(bytes(buffer[:amount_read]))
            consumed = b"".join(chunks)
        elif consumer == "readlines":
            consumed = b"".join(await body.readlines())
        elif consumer == "iter_chunks":
            consumed = b"".join([chunk async for chunk in body.iter_chunks(17)])
        elif consumer == "iter_lines":
            consumed = b"\n".join([line async for line in body.iter_lines(17)])
        elif consumer == "anext":
            chunks = []
            while True:
                try:
                    chunks.append(await body.__anext__())
                except StopAsyncIteration:
                    break
            consumed = b"".join(chunks)
        else:
            async with body as entered_body:
                assert entered_body is body
                consumed = await entered_body.read()

    assert consumed == RESPONSE_BODY
    spans = [span for trace in test_spans.pop_traces() for span in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_name=MODEL_ID,
        model_provider="amazon_bedrock",
        output_messages=[{"content": "Lorem ipsum is placeholder text used in publishing and design."}],
        metrics={"input_tokens": 10, "output_tokens": 91, "total_tokens": 101},
    )


@pytest.mark.asyncio
async def test_llmobs_invoke_model_supports_async_body_iteration(bedrock_llmobs, tracer, test_spans):
    parsed_response = {
        "ResponseMetadata": {
            "RequestId": "fddf10b3-c895-4e5d-9b21-3ca963708b03",
            "HTTPStatusCode": 200,
            "HTTPHeaders": {
                "x-amzn-bedrock-output-token-count": "91",
                "x-amzn-bedrock-input-token-count": "10",
            },
            "RetryAttempts": 0,
        },
        "contentType": "application/json",
        "body": AsyncStreamingBody(RESPONSE_BODY),
    }
    http_response = mock.MagicMock(status_code=200)

    async with aiobotocore_client("bedrock-runtime", tracer) as client:
        with mock.patch.object(client, "_make_request", new_callable=mock.AsyncMock) as make_request:
            make_request.return_value = http_response, parsed_response
            response = await client.invoke_model(body=json.dumps(REQUEST_BODY), modelId=MODEL_ID)

        assert b"".join([chunk async for chunk in response["body"]]) == RESPONSE_BODY

    spans = [span for trace in test_spans.pop_traces() for span in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_name=MODEL_ID,
        model_provider="amazon_bedrock",
        output_messages=[{"content": "Lorem ipsum is placeholder text used in publishing and design."}],
        metrics={"input_tokens": 10, "output_tokens": 91, "total_tokens": 101},
    )


@pytest.mark.asyncio
async def test_llmobs_invoke_model_stream_finishes_after_async_iteration(bedrock_llmobs, tracer, test_spans):
    event = {
        "chunk": {
            "bytes": json.dumps(
                {
                    "generation": "Lorem ipsum is placeholder text used in publishing and design.",
                    "stop_reason": "max_tokens",
                    "amazon-bedrock-invocationMetrics": {
                        "inputTokenCount": 10,
                        "outputTokenCount": 91,
                        "invocationLatency": 2823,
                    },
                }
            ).encode()
        }
    }
    parsed_response = {
        "ResponseMetadata": {
            "RequestId": "fddf10b3-c895-4e5d-9b21-3ca963708b03",
            "HTTPStatusCode": 200,
            "HTTPHeaders": {},
            "RetryAttempts": 0,
        },
        "contentType": "application/json",
        "body": AsyncEventStream([event]),
    }
    http_response = mock.MagicMock(status_code=200)

    async with aiobotocore_client("bedrock-runtime", tracer) as client:
        with mock.patch.object(client, "_make_request", new_callable=mock.AsyncMock) as make_request:
            make_request.return_value = http_response, parsed_response
            response = await client.invoke_model_with_response_stream(body=json.dumps(REQUEST_BODY), modelId=MODEL_ID)

        assert test_spans.pop_traces() == []
        assert [chunk async for chunk in response["body"]] == [event]

    spans = [span for trace in test_spans.pop_traces() for span in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_name=MODEL_ID,
        model_provider="amazon_bedrock",
        input_messages=[{"content": REQUEST_BODY["prompt"]}],
        output_messages=[{"content": "Lorem ipsum is placeholder text used in publishing and design."}],
        metadata={"temperature": 0.9, "max_tokens": 60},
        metrics={"input_tokens": 10, "output_tokens": 91, "total_tokens": 101},
        tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>", "integration": "bedrock"},
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not HAS_CONVERSE, reason="Converse APIs are unavailable in this botocore")
async def test_llmobs_converse_records_async_response(bedrock_llmobs, tracer, test_spans):
    parsed_response = {
        "ResponseMetadata": {
            "RequestId": "fddf10b3-c895-4e5d-9b21-3ca963708b03",
            "HTTPStatusCode": 200,
            "HTTPHeaders": {},
            "RetryAttempts": 0,
        },
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": "Distributed tracing follows a request across services."}],
            }
        },
        "stopReason": "end_turn",
        "usage": {"inputTokens": 8, "outputTokens": 9, "totalTokens": 17},
        "metrics": {"latencyMs": 125},
    }
    http_response = mock.MagicMock(status_code=200)

    async with aiobotocore_client("bedrock-runtime", tracer) as client:
        with mock.patch.object(client, "_make_request", new_callable=mock.AsyncMock) as make_request:
            make_request.return_value = http_response, parsed_response
            response = await client.converse(**CONVERSE_REQUEST)

    assert response is parsed_response
    spans = [span for trace in test_spans.pop_traces() for span in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_name=CONVERSE_REQUEST["modelId"],
        model_provider="amazon_bedrock",
        input_messages=[{"role": "user", "content": "Explain distributed tracing."}],
        output_messages=[{"role": "assistant", "content": "Distributed tracing follows a request across services."}],
        metadata={"temperature": 0.7, "max_tokens": 100, "stop_reason": "end_turn"},
        metrics={"input_tokens": 8, "output_tokens": 9, "total_tokens": 17},
        tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>", "integration": "bedrock"},
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not HAS_CONVERSE, reason="Converse APIs are unavailable in this botocore")
async def test_llmobs_converse_stream_finishes_after_async_iteration(bedrock_llmobs, tracer, test_spans):
    events = [
        {"messageStart": {"role": "assistant"}},
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"text": "Distributed tracing follows a request across services."},
            }
        },
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 8, "outputTokens": 9, "totalTokens": 17}}},
    ]
    parsed_response = {
        "ResponseMetadata": {
            "RequestId": "fddf10b3-c895-4e5d-9b21-3ca963708b03",
            "HTTPStatusCode": 200,
            "HTTPHeaders": {},
            "RetryAttempts": 0,
        },
        "stream": AsyncEventStream(events),
    }
    http_response = mock.MagicMock(status_code=200)

    async with aiobotocore_client("bedrock-runtime", tracer) as client:
        with mock.patch.object(client, "_make_request", new_callable=mock.AsyncMock) as make_request:
            make_request.return_value = http_response, parsed_response
            response = await client.converse_stream(**CONVERSE_REQUEST)

        assert test_spans.pop_traces() == []
        assert [event async for event in response["stream"]] == events

    spans = [span for trace in test_spans.pop_traces() for span in trace]
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_name=CONVERSE_REQUEST["modelId"],
        model_provider="amazon_bedrock",
        input_messages=[{"role": "user", "content": "Explain distributed tracing."}],
        output_messages=[{"role": "assistant", "content": "Distributed tracing follows a request across services."}],
        metadata={"temperature": 0.7, "max_tokens": 100},
        metrics={"input_tokens": 8, "output_tokens": 9, "total_tokens": 17},
        tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>", "integration": "bedrock"},
    )


@pytest.mark.asyncio
async def test_llmobs_async_request_error_finishes_span(bedrock_llmobs, tracer, test_spans):
    async with aiobotocore_client("bedrock-runtime", tracer) as client:
        with mock.patch.object(client, "_make_request", new_callable=mock.AsyncMock) as make_request:
            make_request.side_effect = RuntimeError("request failed")
            with pytest.raises(RuntimeError, match="request failed"):
                await client.invoke_model(body=json.dumps(REQUEST_BODY), modelId=MODEL_ID)

    spans = [span for trace in test_spans.pop_traces() for span in trace]
    assert len(spans) == 1
    span = spans[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name=MODEL_ID,
        model_provider="amazon_bedrock",
        input_messages=[{"content": REQUEST_BODY["prompt"]}],
        output_messages=[{"content": ""}],
        error={
            "type": span.get_tag("error.type"),
            "message": span.get_tag("error.message"),
            "stack": span.get_tag("error.stack"),
        },
        tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>", "integration": "bedrock"},
    )
