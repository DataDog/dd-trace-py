import pytest


@pytest.mark.parametrize("tracing_enabled", [True, False])
@pytest.mark.parametrize("prepared", [True, False])
@pytest.mark.parametrize("finish_on_prepare", [True, False])
@pytest.mark.parametrize("cancelled", [True, False])
async def test_failed_request_releases_frames_and_completes(
    test_spans, monkeypatch, tracing_enabled, prepared, finish_on_prepare, cancelled
):
    import asyncio
    import gc
    from unittest.mock import Mock
    import weakref

    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from ddtrace import config
    from ddtrace.contrib.internal.aiohttp import middlewares
    from ddtrace.internal import core
    from ddtrace.trace import tracer

    class RequestPayload:
        pass

    class RequestFailure(Exception):
        pass

    monkeypatch.setattr(tracer, "enabled", tracing_enabled)
    monkeypatch.setitem(config.aiohttp, "disable_stream_timing_for_mem_leak", finish_on_prepare)
    app = web.Application()
    middlewares.trace_app(app)
    request = make_mocked_request("GET", "/", app=app)
    request.match_info.route.resource.get_info.return_value = {"path": "/"}
    request.match_info.route.resource.canonical = "/"
    task = asyncio.get_running_loop().create_future()
    request._task = task
    response = web.StreamResponse()
    response._req = request
    contexts = []
    payload_refs = []
    completions = []

    def ended(ctx, exc_info):
        completions.append(ctx)

    core.on("context.ended.web.request", ended)

    async def fail(ctx, request, handler):
        contexts.append(ctx)
        payload = RequestPayload()
        payload_refs.append(weakref.ref(payload))
        if prepared:
            await middlewares.on_prepare(request, response)
        if cancelled:
            raise asyncio.CancelledError()
        raise RequestFailure("request failed")

    monkeypatch.setattr(middlewares, "_handle_request", fail)
    wrapped = await middlewares.trace_middleware(app, Mock())
    was_gc_enabled = gc.isenabled()
    gc.disable()
    try:
        try:
            await wrapped(request)
        except (RequestFailure, asyncio.CancelledError):
            pass
        else:
            raise AssertionError("The request failure must propagate")
        ctx = contexts[0]
        assert ctx.get_item("aiohttp.request.exc_info") is None
        assert payload_refs[0]() is None, "The context must not retain the failing handler frame"
        span = ctx.get_item("_inner_span")
        # Disabling trace export still creates request spans that must be completed.
        assert span is not None
        assert span.error == (not cancelled)
        if not cancelled:
            assert span.get_tag("error.type").endswith("RequestFailure")
            assert "request failed" in span.get_tag("error.stack")
        # Real aiohttp teardown may release the task before its done callbacks run.
        request._task = None
        task.set_result(None)
        await asyncio.sleep(0)
        assert completions == [ctx]
        assert ctx._event is None
        assert span.finished
        middlewares.finish_request_span(request, response)
        assert completions == [ctx]
    finally:
        if was_gc_enabled:
            gc.enable()
        core.reset_listeners("context.ended.web.request", ended)


@pytest.mark.parametrize("finish_on_prepare", [True, False])
async def test_stream_failure_records_error_and_completes(test_spans, monkeypatch, aiohttp_client, finish_on_prepare):
    import asyncio

    from aiohttp import ClientPayloadError
    from aiohttp import web

    from ddtrace import config
    from ddtrace.contrib.internal.aiohttp import middlewares

    monkeypatch.setitem(config.aiohttp, "disable_stream_timing_for_mem_leak", finish_on_prepare)
    contexts = []
    tasks = []

    async def fail_stream(request):
        contexts.append(request[middlewares.REQUEST_EXECUTION_CONTEXT_KEY])
        tasks.append(request.task)
        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(b"partial response")
        raise ValueError("stream failed after prepare")

    app = web.Application()
    app.router.add_get("/", fail_stream)
    middlewares.trace_app(app)
    client = await aiohttp_client(app)
    response = await client.get("/")
    assert response.status == 200
    with pytest.raises(ClientPayloadError):
        await response.read()
    await client.close()
    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)
    ctx = contexts[0]
    span = ctx.get_item("_inner_span")
    assert span.finished
    assert span.error == 1
    assert span.get_tag("error.message") == "stream failed after prepare"
    assert "fail_stream" in span.get_tag("error.stack")
    assert ctx._event is None
    assert ctx.get_item("aiohttp.request.exc_info") is None


async def test_error_response_releases_completion_callback(test_spans, monkeypatch):
    import asyncio
    from unittest.mock import Mock
    import weakref

    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from ddtrace.contrib.internal.aiohttp import middlewares

    app = web.Application()
    middlewares.trace_app(app)
    request = make_mocked_request("GET", "/", app=app)
    request.match_info.route.resource.get_info.return_value = {"path": "/"}
    task = asyncio.get_running_loop().create_future()
    request._task = task

    async def fail(ctx, request, handler):
        raise web.HTTPInternalServerError()

    monkeypatch.setattr(middlewares, "_handle_request", fail)
    wrapped = await middlewares.trace_middleware(app, Mock())
    try:
        await wrapped(request)
    except web.HTTPInternalServerError:
        pass
    callback_ref = weakref.ref(request[middlewares.REQUEST_COMPLETION_CALLBACK_KEY])
    await middlewares.on_prepare(request, web.Response(status=500))
    assert not task.done(), "The keep-alive request task can outlive this response"
    assert callback_ref() is None, "The completed request must release its pending task callback"
    ctx = request[middlewares.REQUEST_EXECUTION_CONTEXT_KEY]
    assert ctx._event is None
    assert ctx.get_item("_inner_span").finished
    task.set_result(None)
    await asyncio.sleep(0)
