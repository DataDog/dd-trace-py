import pytest


# aiohttp logs the intentionally unhandled handler error.
@pytest.mark.subprocess(err=None)
def test_requests_after_tracing_product_stop() -> None:
    import asyncio
    from unittest.mock import patch
    from urllib.parse import urlsplit

    from aiohttp import web
    from aiohttp.test_utils import TestClient
    from aiohttp.test_utils import TestServer

    from ddtrace._trace import product
    from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
    from ddtrace.internal import core
    from ddtrace.trace import tracer

    contexts = []
    statuses = []

    def started(ctx):
        assert ctx.get_item("_inner_span") is None
        contexts.append(ctx)

    def ended(ctx, exc_info):
        statuses.append((urlsplit(ctx.event.request_url).path, ctx.event.response_status_code))

    async def handler(request):
        if request.path == "/error":
            raise RuntimeError("handler failed")
        if request.path == "/stream":
            response = web.StreamResponse()
            await response.prepare(request)
            await response.write(b"ok")
            await response.write_eof()
            return response
        return web.Response(text="ok")

    async def main():
        app = web.Application()
        for path in ("/ok", "/error", "/stream"):
            app.router.add_get(path, handler)
        trace_app(app)
        product.stop()
        assert not core.has_listeners("context.started.web.request")
        assert not core.has_listeners("context.ended.web.request")
        core.on("context.started.web.request", started)
        core.on("context.ended.web.request", ended)
        with patch.object(tracer, "start_span", wraps=tracer.start_span) as start_span:
            async with TestClient(TestServer(app)) as client:
                for _ in range(2):
                    for path, status in (("/ok", 200), ("/error", 500), ("/missing", 404), ("/stream", 200)):
                        response = await client.get(path)
                        assert response.status == status
                        await response.read()
            await asyncio.sleep(0)
        start_span.assert_not_called()
        assert len(contexts) == len(statuses) == 8
        # Stream completion can follow a later response on a reused connection.
        assert sorted(statuses) == sorted([("/ok", 200), ("/error", 500), ("/missing", 404), ("/stream", 200)] * 2)
        assert all(ctx._end_event_dispatched and ctx._event is None for ctx in contexts)
        assert not tracer._span_aggregator._traces
        assert tracer.current_span() is None

    asyncio.run(main())
