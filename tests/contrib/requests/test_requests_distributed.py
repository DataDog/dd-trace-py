
# 3p
from nose.tools import eq_, assert_in, assert_not_in
from requests_mock import Adapter

# project
from .test_requests import get_traced_session

class TestRequestsDistributed(object):

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

    def test_propagation_true(self):
        adapter = Adapter()
        tracer, session = get_traced_session()
        session.mount('mock', adapter)
        session.distributed_tracing_enabled = True

        with tracer.trace('root') as root:
            def matcher(request):
                return self.headers_here(tracer, request, root)
            adapter.register_uri('GET', 'mock://datadog/foo', additional_matcher=matcher, text='bar')
            resp = session.get('mock://datadog/foo')
            eq_(200, resp.status_code)
            eq_('bar', resp.text)

        spans = tracer.writer.spans
        root, req = spans
        eq_('root', root.name)
        eq_('requests.request', req.name)
        eq_(root.trace_id, req.trace_id)
        eq_(root.span_id, req.parent_id)

    def test_propagation_false(self):
        adapter = Adapter()
        tracer, session = get_traced_session()
        session.mount('mock', adapter)
        session.distributed_tracing_enabled = False

        with tracer.trace('root'):
            def matcher(request):
                return self.headers_not_here(tracer, request)
            adapter.register_uri('GET', 'mock://datadog/foo', additional_matcher=matcher, text='bar')
            resp = session.get('mock://datadog/foo')
            eq_(200, resp.status_code)
            eq_('bar', resp.text)
