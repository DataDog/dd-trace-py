import asyncio

import anyio
from mcp.client.session import ClientSession
import pytest

from ddtrace.contrib.internal.mcp.patch import traced_call_tool


@pytest.mark.parametrize("llmobs_enabled", [False, True])
def test_cancel_client_tool_call(anyio_backend_name, llmobs_enabled, request, test_spans):
    llmobs = request.getfixturevalue("mcp_llmobs") if llmobs_enabled else None

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
                with pytest.raises(anyio.get_cancelled_exc_class()) as cancelled:
                    await client.call_tool("slow_tool")
        return cancelled.type

    cancel_type = anyio.run(run, backend=anyio_backend_name)

    (span,) = [span for span in test_spans.pop() if span.resource == "client_tool_call"]
    assert span.error
    assert span.get_tag("error.type") == f"{cancel_type.__module__}.{cancel_type.__name__}"
    if llmobs_enabled:
        events = (call.args[0] for call in llmobs._instance._llmobs_span_writer.enqueue.call_args_list)
        (event,) = [event for event in events if event["span_id"] == str(span.span_id)]
        assert event["name"] == "MCP Client Tool Call: slow_tool"
        assert event["status"] == "error"


def test_close_client_tool_coroutine_without_event_loop(test_spans):
    call = traced_call_tool(asyncio.sleep, None, (0,), {})
    call.send(None)
    call.close()

    (span,) = test_spans.pop()
    assert not span.error
