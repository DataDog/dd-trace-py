"""The anthropic.stream.created hook on the event source of the SDK streams."""

import json

import pytest

from ddtrace.contrib.internal._sdk_streams import ANTHROPIC_STREAM_CREATED
from ddtrace.internal import core


MODEL = "claude-3-opus-20240229"


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _message_body(text="Hello"):
    message = {
        "id": "msg_hook",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 0},
    }
    return b"".join(
        [
            _sse("message_start", {"type": "message_start", "message": message}),
            _sse(
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            ),
            _sse(
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
            ),
            _sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
            _sse(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 1},
                },
            ),
            _sse("message_stop", {"type": "message_stop"}),
        ]
    )


def _httpx(anthropic):
    """The httpx module of this SDK version (anthropic>=2 uses httpx2)."""
    return getattr(anthropic._base_client, "httpx2", None) or anthropic._base_client.httpx


def _client(anthropic, is_async=False):
    httpx = _httpx(anthropic)

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_message_body())

    transport = httpx.MockTransport(handler)
    if is_async:
        return anthropic.AsyncAnthropic(api_key="<not-a-real-key>", http_client=httpx.AsyncClient(transport=transport))
    return anthropic.Anthropic(api_key="<not-a-real-key>", http_client=httpx.Client(transport=transport))


def _without_text_deltas(events):
    for event in events:
        if event.type != "content_block_delta":
            yield event


async def _async_without_text_deltas(events):
    async for event in events:
        if event.type != "content_block_delta":
            yield event


@pytest.fixture
def stream_listener():
    """Record the stream.created events and drop the text deltas of every stream."""
    seen = []

    def listener(events):
        seen.append((events.response.request.url.path, events.is_async))
        replace = _async_without_text_deltas if events.is_async else _without_text_deltas
        events.iterator = replace(events.iterator)

    core.on(ANTHROPIC_STREAM_CREATED, listener)
    try:
        yield seen
    finally:
        core.reset_listeners(ANTHROPIC_STREAM_CREATED, listener)


def _messages():
    return [{"role": "user", "content": "Hi"}]


def test_create_stream_reads_the_replaced_events(anthropic, stream_listener):
    stream = _client(anthropic).messages.create(model=MODEL, max_tokens=8, messages=_messages(), stream=True)

    assert [event.type for event in stream].count("content_block_delta") == 0
    assert stream_listener == [("/v1/messages", False)]


def test_message_stream_accumulates_the_replaced_events(anthropic, stream_listener):
    """The SDK message accumulator reads the replaced events, so its final message has no text."""
    with _client(anthropic).messages.stream(model=MODEL, max_tokens=8, messages=_messages()) as stream:
        final = stream.get_final_message()

    assert [block.text for block in final.content] == [""]
    assert ("/v1/messages", False) in stream_listener


@pytest.mark.asyncio
async def test_async_message_stream_accumulates_the_replaced_events(anthropic, stream_listener):
    async with _client(anthropic, is_async=True).messages.stream(
        model=MODEL, max_tokens=8, messages=_messages()
    ) as stream:
        final = await stream.get_final_message()

    assert [block.text for block in final.content] == [""]
    assert ("/v1/messages", True) in stream_listener


def test_stream_unchanged_without_listener(anthropic):
    with _client(anthropic).messages.stream(model=MODEL, max_tokens=8, messages=_messages()) as stream:
        final = stream.get_final_message()

    assert [block.text for block in final.content] == ["Hello"]


def test_listener_that_keeps_the_events_leaves_the_stream_unchanged(anthropic):
    seen = []

    def listener(events):
        seen.append(events.response.request.url.path)

    core.on(ANTHROPIC_STREAM_CREATED, listener)
    try:
        with _client(anthropic).messages.stream(model=MODEL, max_tokens=8, messages=_messages()) as stream:
            final = stream.get_final_message()
    finally:
        core.reset_listeners(ANTHROPIC_STREAM_CREATED, listener)

    assert [block.text for block in final.content] == ["Hello"]
    assert "/v1/messages" in seen


def test_listener_sees_attributes_set_after_the_stream_constructor(anthropic, stream_listener):
    """A Stream subclass that sets attributes after super().__init__ (like BedrockStream) keeps them."""
    httpx = _httpx(anthropic)

    class _MarkedStream(anthropic.Stream):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.marker = "set after super().__init__"

        def __stream__(self):
            yield type("_Event", (), {"type": "marker", "value": getattr(self, "marker", None)})()

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    stream = _MarkedStream(cast_to=object, response=httpx.Response(200, request=request), client=_client(anthropic))

    assert [event.value for event in stream] == ["set after super().__init__"]
