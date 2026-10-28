import asyncio
import json

import httpx
import pytest

from ddtrace.llmobs._utils import _get_llmobs_data_metastruct


MODEL = "claude-3-opus-20240229"
CREATE_KWARGS = dict(model=MODEL, max_tokens=15, messages=[{"role": "user", "content": "Hello"}])
EXPECTED_OUTPUT = [{"content": "hi there", "role": "assistant"}]
EXPECTED_METRICS = {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}


def _messages_response(request):
    body = {
        "id": "msg_raw",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [{"type": "text", "text": "hi there"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 7, "output_tokens": 3},
    }
    return httpx.Response(200, headers={"content-type": "application/json"}, content=json.dumps(body).encode())


async def _async_messages_response(request):
    return _messages_response(request)


def _sync_client(anthropic):
    return anthropic.Anthropic(
        api_key="<not-a-real-key>", http_client=httpx.Client(transport=httpx.MockTransport(_messages_response))
    )


def _async_client(anthropic):
    return anthropic.AsyncAnthropic(
        api_key="<not-a-real-key>",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_async_messages_response)),
    )


def _assert_recorded(test_spans):
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    data = _get_llmobs_data_metastruct(spans[0])
    assert data["meta"]["output"]["messages"] == EXPECTED_OUTPUT
    for key, value in EXPECTED_METRICS.items():
        assert data["metrics"][key] == value


def test_with_raw_response_records_output(anthropic, anthropic_llmobs, test_spans):
    raw = _sync_client(anthropic).messages.with_raw_response.create(**CREATE_KWARGS)
    assert raw.parse().content[0].text == "hi there"
    _assert_recorded(test_spans)


def test_with_streaming_response_records_output(anthropic, anthropic_llmobs, test_spans):
    with _sync_client(anthropic).messages.with_streaming_response.create(**CREATE_KWARGS) as raw:
        assert raw.parse().content[0].text == "hi there"
    _assert_recorded(test_spans)


async def test_async_with_raw_response_records_output(anthropic, anthropic_llmobs, test_spans):
    raw = await _async_client(anthropic).messages.with_raw_response.create(**CREATE_KWARGS)
    parsed = raw.parse()
    if hasattr(parsed, "__await__"):
        parsed = await parsed
    assert parsed.content[0].text == "hi there"
    _assert_recorded(test_spans)


async def test_async_with_streaming_response_records_output(anthropic, anthropic_llmobs, test_spans):
    async with _async_client(anthropic).messages.with_streaming_response.create(**CREATE_KWARGS) as raw:
        parsed = await raw.parse()
        assert parsed.content[0].text == "hi there"
    _assert_recorded(test_spans)


class _FailingBody(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __init__(self, exc):
        self._exc = exc

    def __iter__(self):
        raise self._exc
        yield b""

    async def __aiter__(self):
        raise self._exc
        yield b""


def _failing_body_response(exc):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=_FailingBody(exc))

    return handler


def _assert_errored(test_spans, exc_type):
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1
    assert spans[0].error == 1
    assert spans[0].get_tag("error.type").endswith(exc_type.__name__)


def test_with_streaming_response_body_read_error_propagates(anthropic, anthropic_llmobs, test_spans):
    timeout = httpx.ReadTimeout("body read timed out")
    client = anthropic.Anthropic(
        api_key="<not-a-real-key>",
        http_client=httpx.Client(transport=httpx.MockTransport(_failing_body_response(timeout))),
        max_retries=0,
    )
    with pytest.raises(httpx.ReadTimeout):
        with client.messages.with_streaming_response.create(**CREATE_KWARGS) as raw:
            raw.parse()
    _assert_errored(test_spans, httpx.ReadTimeout)


async def test_async_with_streaming_response_cancelled_body_read_finishes_span(anthropic, anthropic_llmobs, test_spans):
    async def handler(request):
        return _failing_body_response(asyncio.CancelledError())(request)

    client = anthropic.AsyncAnthropic(
        api_key="<not-a-real-key>",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    with pytest.raises(asyncio.CancelledError):
        async with client.messages.with_streaming_response.create(**CREATE_KWARGS) as raw:
            await raw.parse()
    _assert_errored(test_spans, asyncio.CancelledError)


def test_with_streaming_response_body_stays_readable(anthropic, anthropic_llmobs, test_spans):
    with _sync_client(anthropic).messages.with_streaming_response.create(**CREATE_KWARGS) as raw:
        assert json.loads(b"".join(raw.iter_bytes()))["content"][0]["text"] == "hi there"
        assert raw.json()["id"] == "msg_raw"
    _assert_recorded(test_spans)


def test_wrapper_parse_failure_returns_wrapper_unchanged():
    from ddtrace.contrib.internal.anthropic.patch import _parse_wrapped_response

    class _BrokenWrapper:
        http_response = None

        def parse(self):
            raise ValueError("boom")

    wrapper = _BrokenWrapper()
    assert _parse_wrapped_response(wrapper) is wrapper


@pytest.mark.parametrize("resp", [None, object(), {"content": []}])
def test_non_wrapper_passes_through(resp):
    from ddtrace.contrib.internal.anthropic.patch import _parse_wrapped_response

    assert _parse_wrapped_response(resp) is resp
