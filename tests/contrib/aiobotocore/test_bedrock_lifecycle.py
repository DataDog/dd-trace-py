import asyncio
import contextlib
import io
import json

import mock
import pytest

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.aiobotocore import bedrock as aiobotocore_bedrock
from ddtrace.ext import SpanKind
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.llmobs._llmobs import LLMObs


MODEL_ID = "meta.llama2-13b-chat-v1"
RESPONSE_BODY = json.dumps({"generation": "partial response", "stop_reason": "max_tokens"}).encode()
STREAM_EVENT = {"chunk": {"bytes": RESPONSE_BODY}}


class _CounterStreamingBody:
    def __init__(self, body=RESPONSE_BODY):
        self._body = body
        self._amount_read = 0
        self._content_length = len(body)
        self.closed = False

    async def read(self, amt=None):
        end = len(self._body) if amt is None else self._amount_read + amt
        chunk = self._body[self._amount_read : end]
        self._amount_read += len(chunk)
        return chunk

    async def readinto(self, buffer):
        chunk = await self.read(len(buffer))
        buffer[: len(chunk)] = chunk
        return len(chunk)

    async def readlines(self):
        return [await self.read()]

    def close(self):
        self.closed = True

    async def aclose(self):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.aclose()
        return False


class _SelfCounterStreamingBody(_CounterStreamingBody):
    def __init__(self, body=RESPONSE_BODY):
        super().__init__(body)
        self._self_amount_read = 0
        self._self_content_length = len(body)
        del self._amount_read
        del self._content_length

    async def read(self, amt=None):
        end = len(self._body) if amt is None else self._self_amount_read + amt
        chunk = self._body[self._self_amount_read : end]
        self._self_amount_read += len(chunk)
        return chunk


class _CancelledStreamingBody(_CounterStreamingBody):
    async def read(self, amt=None):
        raise asyncio.CancelledError()


class _CancelledEventStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise asyncio.CancelledError()


class _ClosableEventStream:
    def __init__(self):
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration()

    def close(self):
        self.closed = True


class _SingleEventStream(_ClosableEventStream):
    def __init__(self):
        super().__init__()
        self._event = STREAM_EVENT

    async def __anext__(self):
        if self._event is None:
            raise StopAsyncIteration()
        event = self._event
        self._event = None
        return event


def _execution_ctx():
    return core.ExecutionContext(
        "test.aiobotocore.bedrock",
        model_name=MODEL_ID,
        model_provider="meta",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("body_cls", [_CounterStreamingBody, _SelfCounterStreamingBody])
async def test_sized_read_finishes_when_content_length_is_consumed(body_cls):
    body = body_cls()
    wrapped = aiobotocore_bedrock.AiobotocoreStreamingBody(body, _execution_ctx())

    with mock.patch.object(aiobotocore_bedrock.core, "dispatch") as dispatch:
        assert await wrapped.read(len(RESPONSE_BODY)) == RESPONSE_BODY

    assert dispatch.call_count == 1
    assert dispatch.call_args.args[0] == "botocore.bedrock.process_response"


@pytest.mark.asyncio
async def test_cancelled_body_read_dispatches_exception_and_finishes_once():
    wrapped = aiobotocore_bedrock.AiobotocoreStreamingBody(_CancelledStreamingBody(), _execution_ctx())

    with mock.patch.object(aiobotocore_bedrock.core, "dispatch") as dispatch:
        with pytest.raises(asyncio.CancelledError):
            await wrapped.read()
        wrapped._finish(allow_partial=True)

    assert dispatch.call_count == 1
    assert dispatch.call_args.args[0] == "botocore.patched_bedrock_api_call.exception"
    assert dispatch.call_args.args[1][1][0] is asyncio.CancelledError


@pytest.mark.asyncio
async def test_cancelled_event_stream_dispatches_exception_instead_of_success():
    stream = aiobotocore_bedrock.make_aiobotocore_streaming_body_traced_event_stream(
        _CancelledEventStream(), _execution_ctx()
    )

    with mock.patch.object(aiobotocore_bedrock.core, "dispatch") as dispatch:
        with pytest.raises(asyncio.CancelledError):
            await stream.__anext__()

    assert dispatch.call_count == 1
    assert dispatch.call_args.args[0] == "botocore.patched_bedrock_api_call.exception"
    assert dispatch.call_args.args[1][1][0] is asyncio.CancelledError


@pytest.mark.asyncio
async def test_event_stream_yields_processed_chunk():
    body = _SingleEventStream()
    stream = aiobotocore_bedrock.make_aiobotocore_streaming_body_traced_event_stream(body, _execution_ctx())

    with mock.patch.object(aiobotocore_bedrock.core, "dispatch") as dispatch:
        assert await stream.__anext__() == STREAM_EVENT
        stream.close()

    assert dispatch.call_count == 1
    assert dispatch.call_args.args[0] == "botocore.bedrock.process_response"


def test_early_event_stream_close_finalizes_once():
    body = _ClosableEventStream()
    stream = aiobotocore_bedrock.make_aiobotocore_streaming_body_traced_event_stream(body, _execution_ctx())

    with mock.patch.object(aiobotocore_bedrock.core, "dispatch") as dispatch:
        stream.close()
        stream.close()

    assert body.closed
    assert dispatch.call_count == 1
    assert dispatch.call_args.args[0] == "botocore.bedrock.process_response"


@pytest.mark.asyncio
async def test_early_aclose_finishes_partial_body_without_waiting_for_eof():
    body = _CounterStreamingBody()
    wrapped = aiobotocore_bedrock.AiobotocoreStreamingBody(body, _execution_ctx())

    with mock.patch.object(aiobotocore_bedrock.core, "dispatch") as dispatch:
        assert await wrapped.read(4) == RESPONSE_BODY[:4]
        await wrapped.aclose()

    assert body.closed
    assert dispatch.call_count == 1
    assert dispatch.call_args.args[0] == "botocore.bedrock.process_response"


@pytest.mark.asyncio
async def test_early_context_exit_finishes_partial_body():
    body = _CounterStreamingBody()
    wrapped = aiobotocore_bedrock.AiobotocoreStreamingBody(body, _execution_ctx())

    with mock.patch.object(aiobotocore_bedrock.core, "dispatch") as dispatch:
        async with wrapped:
            pass

    assert body.closed
    assert dispatch.call_count == 1
    assert dispatch.call_args.args[0] == "botocore.bedrock.process_response"


def test_file_like_invoke_body_is_not_consumed_during_request_tagging():
    body = io.BytesIO(b'{"prompt":"hello"}')
    integration = mock.MagicMock(llmobs_enabled=True)
    ctx = core.ExecutionContext(
        "test.aiobotocore.bedrock.request",
        resource="InvokeModel",
        params={"modelId": MODEL_ID, "body": body},
        model_provider="meta",
        bedrock_integration=integration,
    )

    with mock.patch.object(aiobotocore_bedrock.core, "dispatch") as dispatch:
        aiobotocore_bedrock._handle_aiobotocore_bedrock_request(ctx)

    assert body.tell() == 0
    assert body.read() == b'{"prompt":"hello"}'
    dispatch.assert_called_once_with("botocore.patched_bedrock_api_call.started", (ctx, {}))
    assert ctx["llmobs.request_params"] == {}


@pytest.mark.asyncio
async def test_initial_request_cancellation_dispatches_bedrock_exception():
    integration = mock.MagicMock(llmobs_enabled=True)
    ctx = mock.MagicMock()
    original = mock.AsyncMock(side_effect=asyncio.CancelledError())
    function_vars = {
        "pin": mock.MagicMock(),
        "trace_operation": "bedrock-runtime.command",
        "service": "aws.bedrock-runtime",
        "operation": "InvokeModel",
        "endpoint_name": "bedrock-runtime",
        "params": {"modelId": MODEL_ID, "body": "{}"},
        "integration": integration,
    }

    with (
        mock.patch.object(aiobotocore_bedrock.core, "context_with_data", return_value=contextlib.nullcontext(ctx)),
        mock.patch.object(aiobotocore_bedrock, "_set_apm_request_tags"),
        mock.patch.object(aiobotocore_bedrock, "_handle_aiobotocore_bedrock_request"),
        mock.patch.object(aiobotocore_bedrock.core, "dispatch") as dispatch,
    ):
        with pytest.raises(asyncio.CancelledError):
            await aiobotocore_bedrock.patched_aiobotocore_bedrock_api_call(
                original,
                mock.MagicMock(),
                (),
                {},
                function_vars,
            )

    assert dispatch.call_count == 1
    assert dispatch.call_args.args[0] == "botocore.patched_bedrock_api_call.exception"
    assert dispatch.call_args.args[1][1][0] is asyncio.CancelledError


def test_bedrock_specialized_path_preserves_aiobotocore_apm_tags():
    span = mock.MagicMock()
    instance = mock.MagicMock()
    instance.meta.region_name = "us-east-1"
    integration_config = mock.MagicMock()
    integration_config.integration_name = "aiobotocore"
    integration_config.__getitem__.return_value = False
    config = mock.MagicMock()
    config.aiobotocore = integration_config
    function_vars = {
        "instance": instance,
        "endpoint_name": "bedrock-runtime",
        "operation": "InvokeModel",
        "params": {"modelId": MODEL_ID},
        "service": "aws.bedrock-runtime",
    }
    result = {
        "ResponseMetadata": {
            "HTTPStatusCode": 200,
            "HTTPHeaders": {"x-amz-id-2": "secondary-request-id"},
            "RetryAttempts": 1,
            "RequestId": "request-id",
        }
    }

    with (
        mock.patch.object(aiobotocore_bedrock, "span_from_context", return_value=span),
        mock.patch.object(aiobotocore_bedrock, "config", config),
        mock.patch.object(aiobotocore_bedrock, "set_service_and_source") as set_service,
        mock.patch.object(aiobotocore_bedrock, "in_aws_lambda", return_value=False),
    ):
        aiobotocore_bedrock._set_apm_request_tags(mock.MagicMock(), function_vars)
        aiobotocore_bedrock._set_apm_response_tags(mock.MagicMock(), result)

    set_service.assert_called_once_with(span, "aws.bedrock-runtime", integration_config)
    span._set_attribute.assert_any_call(COMPONENT, "aiobotocore")
    span._set_attribute.assert_any_call(SPAN_KIND, SpanKind.CLIENT)
    span._set_attribute.assert_any_call(_SPAN_MEASURED_KEY, 1)
    span._set_attribute.assert_any_call(http.STATUS_CODE, 200)
    span._set_attribute.assert_any_call("aws.requestid", "request-id")
    span._set_attribute.assert_any_call("aws.requestid2", "secondary-request-id")
    span.set_tag.assert_called_with("retry_attempts", 1)
    assert span.resource == "bedrock-runtime.invokemodel"


def test_llmobs_auto_patch_includes_aiobotocore():
    with mock.patch("ddtrace.llmobs._llmobs.patch") as patch:
        LLMObs._patch_integrations()

    assert patch.call_count == 1
    assert patch.call_args.kwargs["aiobotocore"] is True
