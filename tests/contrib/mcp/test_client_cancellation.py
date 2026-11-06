import asyncio
import json

import anyio
from mcp.client.session import ClientSession
import pytest

from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib.internal.mcp.patch import traced_call_tool


@pytest.mark.parametrize("backend", ["asyncio", "trio"])
@pytest.mark.parametrize("llmobs_enabled", [True, False])
def test_client_tool_cancellation(mcp_setup, request, test_spans, backend, llmobs_enabled):
    if backend == "trio":
        pytest.importorskip("trio")
    if llmobs_enabled:
        llmobs = request.getfixturevalue("mcp_llmobs")
    assert mcp_setup._datadog_integration.llmobs_enabled == llmobs_enabled

    async def run():
        send, requests = anyio.create_memory_object_stream(1)
        responses, receive = anyio.create_memory_object_stream(1)

        async def cancel_after_request(scope):
            with anyio.fail_after(10):
                await requests.receive()
            scope.cancel()

        async with responses, requests, ClientSession(receive, send) as client:
            async with anyio.create_task_group() as group:
                group.start_soon(cancel_after_request, group.cancel_scope)
                with pytest.raises(anyio.get_cancelled_exc_class()):
                    await client.call_tool("slow_tool", {"argument": "value"})

    anyio.run(run, backend=backend)

    (span,) = [span for span in test_spans.pop() if span.resource == "client_tool_call"]
    assert span.finished
    assert span.error
    expected_type = "asyncio.exceptions.CancelledError" if backend == "asyncio" else "trio.Cancelled"
    assert span.get_tag(ERROR_TYPE) == expected_type
    if llmobs_enabled:
        (event,) = [
            call.args[0]
            for call in llmobs._instance._llmobs_span_writer.enqueue.call_args_list
            if call.args[0]["span_id"] == str(span.span_id)
        ]
        assert event["name"] == "MCP Client Tool Call: slow_tool"
        assert event["meta"]["span"]["kind"] == "tool"
        assert json.loads(event["meta"]["input"]["value"]) == {"argument": "value"}
        assert event["status"] == "error"


def test_close_suspended_client_tool_without_event_loop(mcp_llmobs, test_spans):
    async def tool(name):
        await asyncio.sleep(0)

    call = traced_call_tool(tool, None, ("slow_tool",), {})
    call.send(None)
    call.close()

    (span,) = test_spans.pop()
    assert span.finished
    assert not span.error
    assert not mcp_llmobs._instance._llmobs_span_writer.enqueue.called
