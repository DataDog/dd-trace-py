from requests_mock import Adapter

from ddtrace.contrib.internal.urllib3.patch import patch as urllib3_patch
from ddtrace.contrib.internal.urllib3.patch import unpatch as urllib3_unpatch
from ddtrace.internal.settings.asm import config as asm_config
from tests.utils import TracerTestCase
from tests.utils import get_128_bit_trace_id_from_headers

from .test_requests import HOST_AND_PORT
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
        with self.override_config("requests", dict(distributed_tracing=True)):
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
        with self.override_config("requests", dict(distributed_tracing=False)):
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

            with self.override_config("requests", dict(distributed_tracing=True)):
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

    def test_propagation_with_urllib3_also_patched(self):
        # regression test: when urllib3 is also patched, requests' Session.send
        # internally calls the now-traced HTTPConnectionPool.urlopen, creating a
        # nested urllib3.request span around the same call. The propagation
        # headers injected onto the wire must still point at the outer
        # requests.request span (not get overwritten by the inner urllib3 span).
        urllib3_patch()
        try:
            with self.override_config("requests", dict(distributed_tracing=True)):
                with self.tracer.trace("root"):
                    resp = self.session.get("http://{}/headers".format(HOST_AND_PORT))
                    assert resp.status_code == 200
                    received_headers = resp.json()["headers"]

                spans = self.pop_spans()
                root, req, url3 = spans
                assert root.name == "root"
                assert req.name == "requests.request"
                assert url3.name == "urllib3.request"
                assert req.parent_id == root.span_id
                assert url3.parent_id == req.span_id

                assert received_headers["X-Datadog-Parent-Id"] == str(req.span_id)
        finally:
            urllib3_unpatch()

    def test_propagation_apm_opt_out_false(self):
        # ensure distributed tracing doesn't works when APM is disabled but not opted out
        with self.override_global_config(dict(_apm_tracing_enabled=True, _asm_enabled=True)):
            assert not asm_config._apm_opt_out
            self.tracer.enabled = False

            with self.override_config("requests", dict(distributed_tracing=True)):
                adapter = Adapter()
                self.session.mount("mock", adapter)

                with self.tracer.trace("root"):

                    def matcher(request):
                        return self.headers_not_here(self.tracer, request)

                    adapter.register_uri("GET", "mock://datadog/foo", additional_matcher=matcher, text="bar")
                    resp = self.session.get("mock://datadog/foo")
                    assert 200 == resp.status_code
                    assert "bar" == resp.text
