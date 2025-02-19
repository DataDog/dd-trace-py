from requests_mock import Adapter

from ddtrace import config
from ddtrace.settings.asm import config as asm_config
from tests.utils import TracerTestCase
from tests.utils import get_128_bit_trace_id_from_headers

from .test_requests import BaseRequestTestCase


class TestRequestsDistributed(BaseRequestTestCase, TracerTestCase):
    def headers_here(self, tracer, request, root_span):
        # Use an additional matcher to query the request headers.
        # This is because the parent_id can only been known within such a callback,
        # as it's defined on the requests span, which is not available when calling register_uri
        headers = request.headers
        assert "x-datadog-trace-id" in headers
        assert "x-datadog-parent-id" in headers
        assert root_span.trace_id == get_128_bit_trace_id_from_headers(headers)
        req_span = tracer.current_span()
        assert "requests.request" == req_span.name
        assert str(req_span.span_id) == headers["x-datadog-parent-id"]
        return True

    def headers_not_here(self, tracer, request):
        headers = request.headers
        assert "x-datadog-trace-id" not in headers
        assert "x-datadog-parent-id" not in headers
        return True

    def test_propagation_default(self):
        # ensure by default, distributed tracing is enabled
        adapter = Adapter()
        self.session.mount("mock", adapter)

        with self.tracer.trace("root") as root:

            def matcher(request):
                return self.headers_here(self.tracer, request, root)

            adapter.register_uri("GET", "mock://datadog/foo", additional_matcher=matcher, text="bar")
            resp = self.session.get("mock://datadog/foo")
            assert 200 == resp.status_code
            assert "bar" == resp.text

    def test_propagation_true_global(self):
        # distributed tracing can be enabled globally
        adapter = Adapter()
        self.session.mount("mock", adapter)

        with self.override_config("requests", dict(distributed_tracing=True)):
            with self.tracer.trace("root") as root:

                def matcher(request):
                    return self.headers_here(self.tracer, request, root)

                adapter.register_uri("GET", "mock://datadog/foo", additional_matcher=matcher, text="bar")
                resp = self.session.get("mock://datadog/foo")
                assert 200 == resp.status_code
                assert "bar" == resp.text

    def test_propagation_false_global(self):
        # distributed tracing can be disabled globally
        adapter = Adapter()
        self.session.mount("mock", adapter)

        with self.override_config("requests", dict(distributed_tracing=False)):
            with self.tracer.trace("root"):

                def matcher(request):
                    return self.headers_not_here(self.tracer, request)

                adapter.register_uri("GET", "mock://datadog/foo", additional_matcher=matcher, text="bar")
                resp = self.session.get("mock://datadog/foo")
                assert 200 == resp.status_code
                assert "bar" == resp.text

    def test_propagation_true(self):
        # ensure distributed tracing can be enabled manually
        cfg = config._get_from(self.session)
        cfg["distributed_tracing"] = True
        adapter = Adapter()
        self.session.mount("mock", adapter)

        with self.tracer.trace("root") as root:

            def matcher(request):
                return self.headers_here(self.tracer, request, root)

            adapter.register_uri("GET", "mock://datadog/foo", additional_matcher=matcher, text="bar")
            resp = self.session.get("mock://datadog/foo")
            assert 200 == resp.status_code
            assert "bar" == resp.text

        spans = self.pop_spans()
        root, req = spans
        assert "root" == root.name
        assert "requests.request" == req.name
        assert root.trace_id == req.trace_id
        assert root.span_id == req.parent_id

    def test_propagation_false(self):
        # ensure distributed tracing can be disabled manually
        cfg = config._get_from(self.session)
        cfg["distributed_tracing"] = False
        adapter = Adapter()
        self.session.mount("mock", adapter)

        with self.tracer.trace("root"):

            def matcher(request):
                return self.headers_not_here(self.tracer, request)

            adapter.register_uri("GET", "mock://datadog/foo", additional_matcher=matcher, text="bar")
            resp = self.session.get("mock://datadog/foo")
            assert 200 == resp.status_code
            assert "bar" == resp.text

    def test_propagation_apm_opt_out_true(self):
        # ensure distributed tracing works when APM is opted out
        with self.override_global_config(dict(_apm_tracing_enabled=False, _asm_enabled=True)):
            assert asm_config._apm_opt_out
            self.tracer.enabled = False
            cfg = config._get_from(self.session)
            cfg["distributed_tracing"] = True
            adapter = Adapter()
            self.session.mount("mock", adapter)

            with self.tracer.trace("root") as root:

                def matcher(request):
                    return self.headers_not_here(self.tracer, request)

                adapter.register_uri("GET", "mock://datadog/foo", additional_matcher=matcher, text="bar")
                resp = self.session.get("mock://datadog/foo")
                assert 200 == resp.status_code
                assert "bar" == resp.text

            spans = self.pop_spans()
            root, req = spans
            assert "root" == root.name
            assert "requests.request" == req.name
            assert root.trace_id == req.trace_id
            assert root.span_id == req.parent_id

    def test_propagation_apm_opt_out_false(self):
        # ensure distributed tracing doesn't works when APM is disabled but not opted out
        with self.override_global_config(dict(_apm_tracing_enabled=True, _asm_enabled=True)):
            assert not asm_config._apm_opt_out
            self.tracer.enabled = False

            cfg = config._get_from(self.session)
            cfg["distributed_tracing"] = True
            adapter = Adapter()
            self.session.mount("mock", adapter)

            with self.tracer.trace("root"):

                def matcher(request):
                    return self.headers_not_here(self.tracer, request)

                adapter.register_uri("GET", "mock://datadog/foo", additional_matcher=matcher, text="bar")
                resp = self.session.get("mock://datadog/foo")
                assert 200 == resp.status_code
                assert "bar" == resp.text
