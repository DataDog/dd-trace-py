import asyncio

import anyio
from mcp.client.session import ClientSession
import pytest


def test_cancel_client_tool_call(mcp_llmobs, test_spans):
    async def run():
        send, requests = anyio.create_memory_object_stream(1)
        responses, receive = anyio.create_memory_object_stream(1)

        async with responses, requests, ClientSession(receive, send) as client:
            call = asyncio.create_task(client.call_tool("slow_tool"))
            # Cancel only once the request is in flight
            await asyncio.wait_for(requests.receive(), timeout=10)
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call

    asyncio.run(run())

    (span,) = [span for span in test_spans.pop() if span.resource == "client_tool_call"]
    assert span.error
    assert span.get_tag("error.type") == "asyncio.exceptions.CancelledError"

    events = [call.args[0] for call in mcp_llmobs._instance._llmobs_span_writer.enqueue.call_args_list]
    (event,) = [event for event in events if event["span_id"] == str(span.span_id)]
    assert event["name"] == "MCP Client Tool Call: slow_tool"
    assert event["status"] == "error"
    assert event["meta"]["error"]["type"] == "asyncio.exceptions.CancelledError"
