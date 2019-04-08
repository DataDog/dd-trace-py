from requests_mock import Adapter
from nose.tools import eq_, assert_in, assert_not_in

from ddtrace import config

from ...base import BaseTracerTestCase
from .test_requests import BaseRequestTestCase


class TestRequestsDistributed(BaseRequestTestCase, BaseTracerTestCase):
    def headers_here(self, tracer, request, root_span):
        # Use an additional matcher to query the request headers.
        # This is because the parent_id can only been known within such a callback,
        # as it's defined on the requests span, which is not available when calling register_uri
        headers = request.headers
        assert_in('x-datadog-trace-id', headers)
        assert_in('x-datadog-parent-id', headers)
        eq_(str(root_span.trace_id), headers['x-datadog-trace-id'])
        req_span = root_span.context.get_current_span()
        eq_('requests.request', req_span.name)
        eq_(str(req_span.span_id), headers['x-datadog-parent-id'])
        return True

    def headers_not_here(self, tracer, request):
        headers = request.headers
        assert_not_in('x-datadog-trace-id', headers)
        assert_not_in('x-datadog-parent-id', headers)
        return True

    def test_propagation_default(self):
        # ensure by default, distributed tracing is enabled
        adapter = Adapter()
        self.session.mount('mock', adapter)

        with self.tracer.trace('root') as root:
            def matcher(request):
                return self.headers_here(self.tracer, request, root)
            adapter.register_uri('GET', 'mock://datadog/foo', additional_matcher=matcher, text='bar')
            resp = self.session.get('mock://datadog/foo')
            eq_(200, resp.status_code)
            eq_('bar', resp.text)

    def test_propagation_true_global(self):
        # distributed tracing can be enabled globally
        adapter = Adapter()
        self.session.mount('mock', adapter)

        with self.override_config('requests', dict(distributed_tracing=True)):
            with self.tracer.trace('root') as root:
                def matcher(request):
                    return self.headers_here(self.tracer, request, root)
                adapter.register_uri('GET', 'mock://datadog/foo', additional_matcher=matcher, text='bar')
                resp = self.session.get('mock://datadog/foo')
                eq_(200, resp.status_code)
                eq_('bar', resp.text)

    def test_propagation_false_global(self):
        # distributed tracing can be disabled globally
        adapter = Adapter()
        self.session.mount('mock', adapter)

        with self.override_config('requests', dict(distributed_tracing=False)):
            with self.tracer.trace('root'):
                def matcher(request):
                    return self.headers_not_here(self.tracer, request)
                adapter.register_uri('GET', 'mock://datadog/foo', additional_matcher=matcher, text='bar')
                resp = self.session.get('mock://datadog/foo')
                eq_(200, resp.status_code)
                eq_('bar', resp.text)

    def test_propagation_true(self):
        # ensure distributed tracing can be enabled manually
        cfg = config.get_from(self.session)
        cfg['distributed_tracing'] = True
        adapter = Adapter()
        self.session.mount('mock', adapter)

        with self.tracer.trace('root') as root:
            def matcher(request):
                return self.headers_here(self.tracer, request, root)
            adapter.register_uri('GET', 'mock://datadog/foo', additional_matcher=matcher, text='bar')
            resp = self.session.get('mock://datadog/foo')
            eq_(200, resp.status_code)
            eq_('bar', resp.text)

        spans = self.tracer.writer.spans
        root, req = spans
        eq_('root', root.name)
        eq_('requests.request', req.name)
        eq_(root.trace_id, req.trace_id)
        eq_(root.span_id, req.parent_id)

    def test_propagation_false(self):
        # ensure distributed tracing can be disabled manually
        cfg = config.get_from(self.session)
        cfg['distributed_tracing'] = False
        adapter = Adapter()
        self.session.mount('mock', adapter)

        with self.tracer.trace('root'):
            def matcher(request):
                return self.headers_not_here(self.tracer, request)
            adapter.register_uri('GET', 'mock://datadog/foo', additional_matcher=matcher, text='bar')
            resp = self.session.get('mock://datadog/foo')
            eq_(200, resp.status_code)
            eq_('bar', resp.text)

    def test_propagation_true_legacy_default(self):
        # [Backward compatibility]: ensure users can switch the distributed
        # tracing flag using the `Session` attribute
        adapter = Adapter()
        self.session.mount('mock', adapter)

        with self.tracer.trace('root') as root:
            def matcher(request):
                return self.headers_here(self.tracer, request, root)
            adapter.register_uri('GET', 'mock://datadog/foo', additional_matcher=matcher, text='bar')
            resp = self.session.get('mock://datadog/foo')
            eq_(200, resp.status_code)
            eq_('bar', resp.text)

        spans = self.tracer.writer.spans
        root, req = spans
        eq_('root', root.name)
        eq_('requests.request', req.name)
        eq_(root.trace_id, req.trace_id)
        eq_(root.span_id, req.parent_id)

    def test_propagation_true_legacy(self):
        # [Backward compatibility]: ensure users can switch the distributed
        # tracing flag using the `Session` attribute
        adapter = Adapter()
        self.session.mount('mock', adapter)
        self.session.distributed_tracing = True

        with self.tracer.trace('root') as root:
            def matcher(request):
                return self.headers_here(self.tracer, request, root)
            adapter.register_uri('GET', 'mock://datadog/foo', additional_matcher=matcher, text='bar')
            resp = self.session.get('mock://datadog/foo')
            eq_(200, resp.status_code)
            eq_('bar', resp.text)

        spans = self.tracer.writer.spans
        root, req = spans
        eq_('root', root.name)
        eq_('requests.request', req.name)
        eq_(root.trace_id, req.trace_id)
        eq_(root.span_id, req.parent_id)

    def test_propagation_false_legacy(self):
        # [Backward compatibility]: ensure users can switch the distributed
        # tracing flag using the `Session` attribute
        adapter = Adapter()
        self.session.mount('mock', adapter)
        self.session.distributed_tracing = False

        with self.tracer.trace('root'):
            def matcher(request):
                return self.headers_not_here(self.tracer, request)
            adapter.register_uri('GET', 'mock://datadog/foo', additional_matcher=matcher, text='bar')
            resp = self.session.get('mock://datadog/foo')
            eq_(200, resp.status_code)
            eq_('bar', resp.text)
