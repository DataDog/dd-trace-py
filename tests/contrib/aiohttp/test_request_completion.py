import asyncio
import gc
from typing import Any
import weakref

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient
from aiohttp.test_utils import TestServer
import pytest

from ddtrace.contrib.internal.aiohttp.middlewares import REQUEST_EXECUTION_CONTEXT_KEY
from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
from tests.utils import override_config


@pytest.mark.parametrize("prepare_in_handler", [False, True])
@pytest.mark.parametrize("disable_stream_timing", [False, True])
async def test_completed_requests_are_released_on_keepalive(
    test_spans: Any, prepare_in_handler: bool, disable_stream_timing: bool
) -> None:
    requests = []
    responses = []
    tasks = []
    contexts = []

    async def handler(request: web.Request) -> web.Response:
        response = web.Response(text="ok")
        requests.append(weakref.ref(request))
        responses.append(weakref.ref(response))
        tasks.append(request.task)
        contexts.append(request[REQUEST_EXECUTION_CONTEXT_KEY])
        if prepare_in_handler:
            await response.prepare(request)
        return response

    app = web.Application()
    app.router.add_get("/", handler)
    trace_app(app)
    with override_config("aiohttp", {"disable_stream_timing_for_mem_leak": disable_stream_timing}):
        async with TestClient(TestServer(app), headers={"Host": "localhost"}) as client:
            for _ in range(3):
                response = await client.get("/")
                assert await response.text() == "ok"
            assert tasks[0] is tasks[1] is tasks[2]
            assert not tasks[0].done()
            await asyncio.sleep(0)
            gc.collect()
            # aiohttp can retain the most recent response while awaiting the next request.
            assert all(request() is None for request in requests[:-1])
            assert all(response() is None for response in responses[:-1])
            assert all(ctx._event is None for ctx in contexts)
            assert len(test_spans.pop_traces()) == 3


@pytest.mark.parametrize("disable_stream_timing", [False, True])
async def test_prepared_stream_failure_completes_request(test_spans: Any, disable_stream_timing: bool) -> None:
    contexts = []
    tasks = []
    prepared_statuses = []

    async def handler(request: web.Request) -> web.StreamResponse:
        contexts.append(request[REQUEST_EXECUTION_CONTEXT_KEY])
        tasks.append(request.task)
        response = web.StreamResponse()
        await response.prepare(request)
        raise RuntimeError("stream failed after preparation")

    async def record_prepared_status(request: web.Request, response: web.StreamResponse) -> None:
        prepared_statuses.append(response.status)

    app = web.Application()
    app.router.add_post("/stream", handler)
    trace_app(app)
    app.on_response_prepare.append(record_prepared_status)
    with override_config("aiohttp", {"disable_stream_timing_for_mem_leak": disable_stream_timing}):
        async with TestClient(TestServer(app), headers={"Host": "localhost"}) as client:
            with pytest.raises(aiohttp.ClientError):
                response = await client.post("/stream")
                await response.read()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
            ctx = contexts[0]
            span = ctx.get_item("_inner_span")
            try:
                assert span.finished
                assert ctx._end_event_dispatched
                assert ctx._event is None
                assert span.error == 1
                assert span.get_tag("error.message") == "stream failed after preparation"
                assert prepared_statuses[0] == 200
                # Older aiohttp versions prepare a second error response after the handler fails.
                expected_status = prepared_statuses[0 if disable_stream_timing else -1]
                assert span.get_tag("http.status_code") == str(expected_status)
            finally:
                ctx.dispatch_ended_event()


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("disable_stream_timing", [False, True])
async def test_cancelled_handler_completes_request(
    test_spans: Any, prepared: bool, disable_stream_timing: bool
) -> None:
    started = asyncio.get_running_loop().create_future()
    contexts = []
    tasks = []

    async def handler(request: web.Request) -> web.StreamResponse:
        contexts.append(request[REQUEST_EXECUTION_CONTEXT_KEY])
        tasks.append(request.task)
        response = web.StreamResponse()
        if prepared:
            await response.prepare(request)
        started.set_result(asyncio.current_task())
        await asyncio.Event().wait()
        return response

    app = web.Application()
    app.router.add_post("/cancel", handler)
    trace_app(app)
    with override_config("aiohttp", {"disable_stream_timing_for_mem_leak": disable_stream_timing}):
        async with TestClient(TestServer(app), headers={"Host": "localhost"}) as client:
            client_request = asyncio.ensure_future(client.post("/cancel"))
            handler_task = await asyncio.wait_for(started, 5)
            handler_task.cancel()
            with pytest.raises(aiohttp.ClientError):
                response = await asyncio.wait_for(client_request, 5)
                await response.read()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
            ctx = contexts[0]
            span = ctx.get_item("_inner_span")
            try:
                assert handler_task.cancelled()
                assert span.finished
                assert ctx._end_event_dispatched
                assert ctx._event is None
                assert span.error == 0
                assert span.get_tag("http.status_code") == ("200" if prepared else None)
            finally:
                ctx.dispatch_ended_event()
