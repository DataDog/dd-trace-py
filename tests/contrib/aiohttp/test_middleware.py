import asyncio

from nose.tools import eq_, ok_
from aiohttp.test_utils import unittest_run_loop

from ddtrace.contrib.aiohttp.middlewares import trace_app, trace_middleware
from ddtrace.sampler import RateSampler

from .utils import TraceTestCase
from .app.web import setup_app, noop_middleware


class TestTraceMiddleware(TraceTestCase):
    """
    Ensures that the trace Middleware creates root spans at
    the beginning of a request.
    """
    def enable_tracing(self):
        trace_app(self.app, self.tracer)

    @unittest_run_loop
    @asyncio.coroutine
    def test_tracing_service(self):
        # it should configure the aiohttp service
        eq_(1, len(self.tracer._services))
        service = self.tracer._services.get('aiohttp-web')
        eq_('aiohttp-web', service[0])
        eq_('aiohttp', service[1])
        eq_('web', service[2])

    @unittest_run_loop
    @asyncio.coroutine
    def test_handler(self):
        # it should create a root span when there is a handler hit
        # with the proper tags
        request = yield from self.client.request('GET', '/')
        eq_(200, request.status)
        text = yield from request.text()
        eq_("What's tracing?", text)
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        # with the right fields
        eq_('aiohttp.request', span.name)
        eq_('aiohttp-web', span.service)
        eq_('http', span.span_type)
        eq_('/', span.resource)
        eq_('/', span.get_tag('http.url'))
        eq_('GET', span.get_tag('http.method'))
        eq_('200', span.get_tag('http.status_code'))
        eq_(0, span.error)

    @unittest_run_loop
    @asyncio.coroutine
    def test_param_handler(self):
        # it should manage properly handlers with params
        request = yield from self.client.request('GET', '/echo/team')
        eq_(200, request.status)
        text = yield from request.text()
        eq_('Hello team', text)
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        # with the right fields
        eq_('/echo/{name}', span.resource)
        eq_('/echo/team', span.get_tag('http.url'))
        eq_('200', span.get_tag('http.status_code'))

    @unittest_run_loop
    @asyncio.coroutine
    def test_404_handler(self):
        # it should not pollute the resource space
        request = yield from self.client.request('GET', '/404/not_found')
        eq_(404, request.status)
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        # with the right fields
        eq_('404', span.resource)
        eq_('/404/not_found', span.get_tag('http.url'))
        eq_('GET', span.get_tag('http.method'))
        eq_('404', span.get_tag('http.status_code'))

    @unittest_run_loop
    @asyncio.coroutine
    def test_coroutine_chaining(self):
        # it should create a trace with multiple spans
        request = yield from self.client.request('GET', '/chaining/')
        eq_(200, request.status)
        text = yield from request.text()
        eq_('OK', text)
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(3, len(traces[0]))
        root = traces[0][0]
        handler = traces[0][1]
        coroutine = traces[0][2]
        # root span created in the middleware
        eq_('aiohttp.request', root.name)
        eq_('/chaining/', root.resource)
        eq_('/chaining/', root.get_tag('http.url'))
        eq_('GET', root.get_tag('http.method'))
        eq_('200', root.get_tag('http.status_code'))
        # span created in the coroutine_chaining handler
        eq_('aiohttp.coro_1', handler.name)
        eq_(root.span_id, handler.parent_id)
        eq_(root.trace_id, handler.trace_id)
        # span created in the coro_2 handler
        eq_('aiohttp.coro_2', coroutine.name)
        eq_(handler.span_id, coroutine.parent_id)
        eq_(root.trace_id, coroutine.trace_id)

    @unittest_run_loop
    @asyncio.coroutine
    def test_static_handler(self):
        # it should create a trace with multiple spans
        request = yield from self.client.request('GET', '/statics/empty.txt')
        eq_(200, request.status)
        text = yield from request.text()
        eq_('Static file\n', text)
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        # root span created in the middleware
        eq_('aiohttp.request', span.name)
        eq_('/statics', span.resource)
        eq_('/statics/empty.txt', span.get_tag('http.url'))
        eq_('GET', span.get_tag('http.method'))
        eq_('200', span.get_tag('http.status_code'))

    @unittest_run_loop
    @asyncio.coroutine
    def test_middleware_applied_twice(self):
        # it should be idempotent
        app = setup_app(self.app.loop)
        # the middleware is not present
        eq_(1, len(app.middlewares))
        eq_(noop_middleware, app.middlewares[0])
        # the middleware is present (with the noop middleware)
        trace_app(app, self.tracer)
        eq_(2, len(app.middlewares))
        # applying the middleware twice doesn't add it again
        trace_app(app, self.tracer)
        eq_(2, len(app.middlewares))
        # and the middleware is always the first
        eq_(trace_middleware, app.middlewares[0])
        eq_(noop_middleware, app.middlewares[1])

    @unittest_run_loop
    @asyncio.coroutine
    def test_exception(self):
        request = yield from self.client.request('GET', '/exception')
        eq_(500, request.status)
        yield from request.text()

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        spans = traces[0]
        eq_(1, len(spans))
        span = spans[0]
        eq_(1, span.error)
        eq_('/exception', span.resource)
        eq_('error', span.get_tag('error.msg'))
        ok_('Exception: error' in span.get_tag('error.stack'))

    @unittest_run_loop
    @asyncio.coroutine
    def test_async_exception(self):
        request = yield from self.client.request('GET', '/async_exception')
        eq_(500, request.status)
        yield from request.text()

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        spans = traces[0]
        eq_(1, len(spans))
        span = spans[0]
        eq_(1, span.error)
        eq_('/async_exception', span.resource)
        eq_('error', span.get_tag('error.msg'))
        ok_('Exception: error' in span.get_tag('error.stack'))

    @unittest_run_loop
    @asyncio.coroutine
    def test_wrapped_coroutine(self):
        request = yield from self.client.request('GET', '/wrapped_coroutine')
        eq_(200, request.status)
        text = yield from request.text()
        eq_('OK', text)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        spans = traces[0]
        eq_(2, len(spans))
        span = spans[0]
        eq_('/wrapped_coroutine', span.resource)
        span = spans[1]
        eq_('nested', span.name)
        ok_(span.duration > 0.25,
            msg="span.duration={0}".format(span.duration))

    @unittest_run_loop
    @asyncio.coroutine
    def test_distributed_tracing(self):
        # activate distributed tracing
        self.app['datadog_trace']['distributed_tracing_enabled'] = True
        tracing_headers = {
            'x-datadog-trace-id': '100',
            'x-datadog-parent-id': '42',
        }

        request = yield from self.client.request('GET', '/', headers=tracing_headers)
        eq_(200, request.status)
        text = yield from request.text()
        eq_("What's tracing?", text)
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        # with the right trace_id and parent_id
        eq_(span.trace_id, 100)
        eq_(span.parent_id, 42)
        eq_(span.distributed.sampled, True)

    @unittest_run_loop
    @asyncio.coroutine
    def test_distributed_tracing_with_sampling_true(self):
        old_sampler = self.tracer.distributed_sampler
        self.tracer.distributed_sampler = RateSampler(0.1)

        # activate distributed tracing
        self.app['datadog_trace']['distributed_tracing_enabled'] = True
        tracing_headers = {
            'x-datadog-trace-id': '100',
            'x-datadog-parent-id': '42',
            'x-datadog-is-sampled': '1',
        }

        request = yield from self.client.request('GET', '/', headers=tracing_headers)
        eq_(200, request.status)
        text = yield from request.text()
        eq_("What's tracing?", text)
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        # with the right trace_id and parent_id
        eq_(span.trace_id, 100)
        eq_(span.parent_id, 42)
        eq_(span.distributed.sampled, True)

        self.tracer.distributed_sampler = old_sampler

    @unittest_run_loop
    @asyncio.coroutine
    def test_distributed_tracing_with_sampling_false(self):
        old_sampler = self.tracer.distributed_sampler
        self.tracer.distributed_sampler = RateSampler(0.9)

        # activate distributed tracing
        self.app['datadog_trace']['distributed_tracing_enabled'] = True
        tracing_headers = {
            'x-datadog-trace-id': '100',
            'x-datadog-parent-id': '42',
            'x-datadog-is-sampled': '0',
        }

        request = yield from self.client.request('GET', '/', headers=tracing_headers)
        eq_(200, request.status)
        text = yield from request.text()
        eq_("What's tracing?", text)
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        # with the right trace_id and parent_id
        eq_(span.trace_id, 100)
        eq_(span.parent_id, 42)
        eq_(span.distributed.sampled, False)

        self.tracer.distributed_sampler = old_sampler

    @unittest_run_loop
    @asyncio.coroutine
    def test_distributed_tracing_disabled_default(self):
        # pass headers for distributed tracing
        tracing_headers = {
            'x-datadog-trace-id': '100',
            'x-datadog-parent-id': '42',
        }

        request = yield from self.client.request('GET', '/', headers=tracing_headers)
        eq_(200, request.status)
        text = yield from request.text()
        eq_("What's tracing?", text)
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        # distributed tracing must be ignored by default
        ok_(span.trace_id is not 100)
        ok_(span.parent_id is not 42)
