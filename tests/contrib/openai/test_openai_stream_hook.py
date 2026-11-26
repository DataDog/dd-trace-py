"""The openai.stream.created hook on the event source of the SDK streams."""

import json

import pytest

from ddtrace.contrib.internal._sdk_streams import OPENAI_STREAM_CREATED
from ddtrace.internal import core


MODEL = "gpt-4o-mini"


def _chunk(content=None, finish_reason=None):
    delta = {"role": "assistant", "content": content} if content is not None else {}
    return {
        "id": "chatcmpl-hook",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def _chat_body():
    chunks = [_chunk("Hello"), _chunk(finish_reason="stop")]
    return b"".join(f"data: {json.dumps(chunk)}\n\n".encode() for chunk in chunks) + b"data: [DONE]\n\n"


def _client(openai, is_async=False):
    # openai>=3 uses httpx2.
    httpx = getattr(openai._base_client, "httpx2", None) or openai._base_client.httpx

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_chat_body())

    transport = httpx.MockTransport(handler)
    if is_async:
        return openai.AsyncOpenAI(api_key="<not-a-real-key>", http_client=httpx.AsyncClient(transport=transport))
    return openai.OpenAI(api_key="<not-a-real-key>", http_client=httpx.Client(transport=transport))


def _without_content(events):
    for event in events:
        if not (event.choices and event.choices[0].delta.content):
            yield event


async def _async_without_content(events):
    async for event in events:
        if not (event.choices and event.choices[0].delta.content):
            yield event


@pytest.fixture
def stream_listener():
    """Record the stream.created events and drop the content chunks of every stream."""
    seen = []

    def listener(events):
        seen.append((events.response.request.url.path, events.is_async))
        replace = _async_without_content if events.is_async else _without_content
        events.iterator = replace(events.iterator)

    core.on(OPENAI_STREAM_CREATED, listener)
    try:
        yield seen
    finally:
        core.reset_listeners(OPENAI_STREAM_CREATED, listener)


def _messages():
    return [{"role": "user", "content": "Hi"}]


def _content(chunks):
    return [chunk.choices[0].delta.content for chunk in chunks if chunk.choices and chunk.choices[0].delta.content]


def test_create_stream_reads_the_replaced_events(openai, stream_listener):
    stream = _client(openai).chat.completions.create(model=MODEL, messages=_messages(), stream=True)

    assert _content(stream) == []
    assert stream_listener == [("/v1/chat/completions", False)]


@pytest.mark.asyncio
async def test_async_create_stream_reads_the_replaced_events(openai, stream_listener):
    stream = await _client(openai, is_async=True).chat.completions.create(
        model=MODEL, messages=_messages(), stream=True
    )

    assert _content([chunk async for chunk in stream]) == []
    assert stream_listener == [("/v1/chat/completions", True)]


def test_streaming_response_parse_reads_the_replaced_events(openai, stream_listener):
    """with_streaming_response builds its stream outside create, and the hook still sees it."""
    completions = _client(openai).chat.completions
    if not hasattr(completions, "with_streaming_response"):
        pytest.skip("with_streaming_response needs a newer OpenAI SDK")
    with completions.with_streaming_response.create(model=MODEL, messages=_messages(), stream=True) as response:
        assert _content(response.parse()) == []

    assert ("/v1/chat/completions", False) in stream_listener


def test_stream_unchanged_without_listener(openai):
    stream = _client(openai).chat.completions.create(model=MODEL, messages=_messages(), stream=True)

    assert _content(stream) == ["Hello"]
