import asyncio
import aiohttp_jinja2

from nose.tools import eq_, ok_
from aiohttp.test_utils import unittest_run_loop

from ddtrace.pin import Pin
from ddtrace.contrib.aiohttp.patch import patch, unpatch

from .utils import TraceTestCase
from .app.web import set_filesystem_loader, set_package_loader


class TestTraceTemplate(TraceTestCase):
    """
    Ensures that the aiohttp_jinja2 library is properly traced.
    """
    def enable_tracing(self):
        patch()
        Pin.override(aiohttp_jinja2, tracer=self.tracer)

    def disable_tracing(self):
        unpatch()

    @unittest_run_loop
    @asyncio.coroutine
    def test_template_rendering(self):
        # it should trace a template rendering
        request = yield from self.client.request('GET', '/template/')
        eq_(200, request.status)
        text = yield from request.text()
        eq_('OK', text)
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        # with the right fields
        eq_('aiohttp.template', span.name)
        eq_('template', span.span_type)
        eq_('/template.jinja2', span.get_tag('aiohttp.template'))
        eq_(0, span.error)

    @unittest_run_loop
    @asyncio.coroutine
    def test_template_rendering_filesystem(self):
        # it should trace a template rendering with a FileSystemLoader
        set_filesystem_loader(self.app)
        request = yield from self.client.request('GET', '/template/')
        eq_(200, request.status)
        text = yield from request.text()
        eq_('OK', text)
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        # with the right fields
        eq_('aiohttp.template', span.name)
        eq_('template', span.span_type)
        eq_('/template.jinja2', span.get_tag('aiohttp.template'))
        eq_(0, span.error)

    @unittest_run_loop
    @asyncio.coroutine
    def test_template_rendering_package(self):
        # it should trace a template rendering with a PackageLoader
        set_package_loader(self.app)
        request = yield from self.client.request('GET', '/template/')
        eq_(200, request.status)
        text = yield from request.text()
        eq_('OK', text)
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        # with the right fields
        eq_('aiohttp.template', span.name)
        eq_('template', span.span_type)
        eq_('templates/template.jinja2', span.get_tag('aiohttp.template'))
        eq_(0, span.error)

    @unittest_run_loop
    @asyncio.coroutine
    def test_template_decorator(self):
        # it should trace a template rendering
        request = yield from self.client.request('GET', '/template_decorator/')
        eq_(200, request.status)
        text = yield from request.text()
        eq_('OK', text)
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        # with the right fields
        eq_('aiohttp.template', span.name)
        eq_('template', span.span_type)
        eq_('/template.jinja2', span.get_tag('aiohttp.template'))
        eq_(0, span.error)

    @unittest_run_loop
    @asyncio.coroutine
    def test_template_error(self):
        # it should trace a template rendering
        request = yield from self.client.request('GET', '/template_error/')
        eq_(500, request.status)
        yield from request.text()
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        # with the right fields
        eq_('aiohttp.template', span.name)
        eq_('template', span.span_type)
        eq_('/error.jinja2', span.get_tag('aiohttp.template'))
        eq_(1, span.error)
        eq_('division by zero', span.get_tag('error.msg'))
        ok_('ZeroDivisionError: division by zero' in span.get_tag('error.stack'))
