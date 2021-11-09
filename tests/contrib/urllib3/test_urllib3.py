import mock
import pytest
import urllib3

from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib.urllib3 import patch
from ddtrace.contrib.urllib3 import unpatch
from ddtrace.ext import http
from ddtrace.pin import Pin
from tests.opentracer.utils import init_tracer
from tests.utils import TracerTestCase
from tests.utils import snapshot


# host:port of httpbin_local container
HOST = "localhost"
PORT = 8001
SOCKET = "{}:{}".format(HOST, PORT)
URL_200 = "http://{}/status/200".format(SOCKET)
URL_500 = "http://{}/status/500".format(SOCKET)


class BaseUrllib3TestCase(TracerTestCase):
    """Provides the setup and teardown for patching/unpatching the urllib3 integration"""

    def setUp(self):
        super(BaseUrllib3TestCase, self).setUp()

        patch()
        self.http = urllib3.PoolManager()
        Pin.override(urllib3.connectionpool.HTTPConnectionPool, tracer=self.tracer)

    def tearDown(self):
        super(BaseUrllib3TestCase, self).tearDown()
        unpatch()


class TestUrllib3(BaseUrllib3TestCase):
    def test_HTTPConnectionPool_traced(self):
        """Tests that requests made from the HTTPConnectionPool are traced"""
        pool = urllib3.connectionpool.HTTPConnectionPool(HOST, PORT)
        # Test a relative URL
        r = pool.request("GET", "/status/200")
        assert r.status == 200
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.get_tag(http.URL) == URL_200

        # Test an absolute URL
        r = pool.request("GET", URL_200)
        assert r.status == 200
        assert len(self.pop_spans()) == 1

    def test_traced_connection_from_url(self):
        """Tests tracing from ``connection_from_url`` is set up"""
        conn = urllib3.connectionpool.connection_from_url(URL_200)
        resp = conn.request("GET", "/")
        assert resp.status == 200
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.get_tag(http.URL) == "http://" + SOCKET + "/"

    def test_resource_path(self):
        """Tests that a successful request tags a single span with the URL"""
        resp = self.http.request("GET", URL_200)
        assert resp.status == 200
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.get_tag("http.url") == URL_200

    def test_tracer_disabled(self):
        """Tests a disabled tracer produces no spans on request"""
        self.tracer.enabled = False
        out = self.http.request("GET", URL_200)
        assert out.status == 200
        spans = self.pop_spans()
        assert len(spans) == 0

    def test_args_kwargs(self):
        """
        Test that args are kwargs are correctly inferred from the target function's
        signature.

        The args/kwargs used in the integration are:
            - method (idx 0)
            - url (idx 1)
            - headers (idx 3)
        """
        inputs = [
            (("POST", URL_200, b"payload", {"accept": "*"}), {}),
            (("POST", URL_200, b"payload"), {"headers": {"accept": "*"}}),
            (("POST", URL_200), {"headers": {"accept": "*"}}),
            (("POST",), {"url": URL_200, "headers": {"accept": "*"}}),
            ((), {"method": "POST", "url": URL_200, "headers": {"accept": "*"}}),
        ]

        for args, kwargs in inputs:

            with self.override_http_config("urllib3", {"_header_tags": dict()}):
                config.urllib3.http.trace_headers(["accept"])
                pool = urllib3.connectionpool.HTTPConnectionPool(HOST, PORT)
                out = pool.urlopen(*args, **kwargs)
            assert out.status == 200
            spans = self.pop_spans()
            assert len(spans) == 1
            s = spans[0]
            assert s.get_tag(http.METHOD) == "POST"
            assert s.get_tag(http.STATUS_CODE) == "200"
            assert s.get_tag(http.URL) == URL_200
            assert s.get_tag("http.request.headers.accept") == "*"

    def test_untraced_request(self):
        """Disabling tracing with unpatch should submit no spans"""
        # Assumes patching is done in the setUp of the test
        unpatch()

        out = self.http.request("GET", URL_200)
        assert out.status == 200
        spans = self.pop_spans()
        assert len(spans) == 0

    def test_double_patch(self):
        """Ensure that double patch doesn't duplicate instrumentation"""
        patch()
        connpool = urllib3.connectionpool.HTTPConnectionPool(HOST, PORT)
        setattr(connpool, "datadog_tracer", self.tracer)

        out = connpool.urlopen("GET", URL_200)
        assert out.status == 200
        spans = self.pop_spans()
        assert len(spans) == 1

    def test_200(self):
        """Test 200 span tags"""
        out = self.http.request("GET", URL_200)
        assert out.status == 200
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.get_tag(http.METHOD) == "GET"
        assert s.get_tag(http.URL) == URL_200
        assert s.get_tag(http.STATUS_CODE) == "200"
        assert s.error == 0
        assert s.span_type == "http"
        assert http.QUERY_STRING not in s.meta

    def test_200_query_string(self):
        """Tests query string tag is added when trace_query_string config is set"""
        query_string = "key=value&key2=value2"
        URL_200_QS = URL_200 + "?" + query_string
        with self.override_http_config("urllib3", dict(trace_query_string=True)):
            out = self.http.request("GET", URL_200_QS)
            assert out.status == 200

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.get_tag(http.METHOD) == "GET"
        assert s.get_tag(http.STATUS_CODE) == "200"
        assert s.get_tag(http.URL) == URL_200_QS
        assert s.error == 0
        assert s.span_type == "http"
        assert s.get_tag(http.QUERY_STRING) == query_string

    def test_post_500(self):
        """Test a request with method POST and expected status 500"""
        out = self.http.request("POST", URL_500)
        assert out.status == 500
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.get_tag(http.METHOD) == "POST"
        assert s.get_tag(http.STATUS_CODE) == "500"
        assert s.get_tag(http.URL) == URL_500
        assert s.error == 1

    def test_connection_retries(self):
        """Tests a connection error results in error spans with proper exc info"""
        retries = 3
        try:
            self.http.request("GET", "http://localhost:9999", retries=retries)
        except Exception:
            pass
        else:
            assert 0, "expected error"

        spans = self.pop_spans()
        assert len(spans) == 4  # Default retry behavior is 3 retries + original request
        for i, s in enumerate(spans):
            assert s.get_tag(http.METHOD) == "GET"
            if i > 0:
                assert s.get_tag(http.RETRIES_REMAIN) == str(retries - i)
            assert s.error == 1
            assert "Failed to establish a new connection" in s.get_tag(ERROR_MSG)
            assert "Failed to establish a new connection" in s.get_tag(ERROR_STACK)
            assert "Traceback (most recent call last)" in s.get_tag(ERROR_STACK)
            assert "urllib3.exceptions.MaxRetryError" in s.get_tag(ERROR_TYPE)

    def test_default_service_name(self):
        """Test the default service name is set"""
        out = self.http.request("GET", URL_200)
        assert out.status == 200
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "urllib3"

    def test_user_set_service_name(self):
        """Test the user-set service name is set on the span"""
        with self.override_config("urllib3", dict(split_by_domain=False, service_name="clients")):
            out = self.http.request("GET", URL_200)
        assert out.status == 200
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert s.service == "clients"

    def test_parent_service_name_split_by_domain(self):
        """
        Tests the request span does not inherit the service name when
        split_by_domain is set to True
        """
        with self.override_config("urllib3", dict(split_by_domain=True)):
            with self.tracer.trace("parent.span", service="web"):
                out = self.http.request("GET", URL_200)
                assert out.status == 200

        spans = self.pop_spans()
        assert len(spans) == 2
        s = spans[1]

        assert s.name == "urllib3.request"
        assert s.service == SOCKET

    def test_parent_without_service_name(self):
        """Test that span with a parent with no service defaults to the hostname"""
        with self.override_config("urllib3", dict(split_by_domain=True)):
            with self.tracer.trace("parent.span"):
                out = self.http.request("GET", URL_200)
                assert out.status == 200

            spans = self.pop_spans()
            assert len(spans) == 2
            s = spans[1]

            assert s.name == "urllib3.request"
            assert s.service == SOCKET

    def test_split_by_domain_remove_auth_in_url(self):
        """Tests that only the hostname is used as the default service name"""
        with self.override_config("urllib3", dict(split_by_domain=True)):
            out = self.http.request("GET", "http://user:pass@{}".format(SOCKET))
            assert out.status == 200

            spans = self.pop_spans()
            assert len(spans) == 1
            s = spans[0]

            assert s.service == SOCKET

    def test_split_by_domain_includes_port(self):
        """Test the port is included if not 80 or 443"""
        with self.override_config("urllib3", dict(split_by_domain=True)):
            with pytest.raises(Exception):
                # Using a port the service is not listening on will throw an error, which is fine
                self.http.request("GET", "http://httpbin.org:8000/hello", timeout=0.0001, retries=0)

            spans = self.pop_spans()
            assert len(spans) == 1
            s = spans[0]

            assert s.error == 1
            assert s.service == "httpbin.org:8000"

    def test_200_ot(self):
        """OpenTracing version of test_200."""

        ot_tracer = init_tracer("urllib3_svc", self.tracer)

        with ot_tracer.start_active_span("urllib3_get"):
            out = self.http.request("GET", URL_200)
            assert out.status == 200

        spans = self.pop_spans()
        assert len(spans) == 2

        ot_span, dd_span = spans

        # confirm the parenting
        assert ot_span.parent_id is None
        assert dd_span.parent_id == ot_span.span_id

        assert ot_span.name == "urllib3_get"
        assert ot_span.service == "urllib3_svc"

        assert dd_span.get_tag(http.METHOD) == "GET"
        assert dd_span.get_tag(http.STATUS_CODE) == "200"
        assert dd_span.error == 0
        assert dd_span.span_type == "http"

    def test_request_and_response_headers(self):
        """Tests the headers are added as tag when the headers are whitelisted"""
        self.http.request("GET", URL_200, headers={"my-header": "my_value"})
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.get_tag("http.request.headers.my-header") is None
        assert s.get_tag("http.response.headers.access-control-allow-origin") is None

        # Enabled when explicitly configured
        with self.override_http_config("urllib3", {"_header_tags": dict()}):
            config.urllib3.http.trace_headers(["my-header", "access-control-allow-origin"])
            self.http.request("GET", URL_200, headers={"my-header": "my_value"})
            spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.get_tag("http.request.headers.my-header") == "my_value"
        assert s.get_tag("http.response.headers.access-control-allow-origin") == "*"

    def test_analytics_integration_default(self):
        """Tests the default behavior of analytics integration is disabled"""
        r = self.http.request("GET", URL_200)
        assert r.status == 200
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None

    def test_analytics_integration_disabled(self):
        """Test disabling the analytics integration"""
        with self.override_config("urllib3", dict(analytics_enabled=False, analytics_sample_rate=0.5)):
            self.http.request("GET", URL_200)

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None

    def test_analytics_integration_enabled(self):
        """Tests enabline the analytics integration"""
        with self.override_config("urllib3", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            self.http.request("GET", URL_200)

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 0.5

    def test_distributed_tracing_enabled(self):
        """Tests distributed tracing headers are passed by default"""
        # Check that distributed tracing headers are passed down; raise an error rather than make the
        # request since we don't care about the response at all
        config.urllib3["distributed_tracing"] = True
        with mock.patch(
            "urllib3.connectionpool.HTTPConnectionPool._make_request", side_effect=ValueError
        ) as m_make_request:
            with pytest.raises(ValueError):
                self.http.request("GET", URL_200)

            spans = self.pop_spans()
            s = spans[0]
            expected_headers = {
                "x-datadog-trace-id": str(s.trace_id),
                "x-datadog-parent-id": str(s.span_id),
                "x-datadog-sampling-priority": "1",
            }
            m_make_request.assert_called_with(
                mock.ANY, "GET", "/status/200", body=None, chunked=mock.ANY, headers=expected_headers, timeout=mock.ANY
            )

    def test_distributed_tracing_disabled(self):
        """Test with distributed tracing disabled does not propagate the headers"""
        config.urllib3["distributed_tracing"] = False
        with mock.patch(
            "urllib3.connectionpool.HTTPConnectionPool._make_request", side_effect=ValueError
        ) as m_make_request:
            with pytest.raises(ValueError):
                self.http.request("GET", URL_200)

            m_make_request.assert_called_with(
                mock.ANY, "GET", "/status/200", body=None, chunked=mock.ANY, headers={}, timeout=mock.ANY
            )


@pytest.fixture()
def patch_urllib3():
    patch()
    try:
        yield
    finally:
        unpatch()


@snapshot()
def test_urllib3_poolmanager_snapshot(patch_urllib3):
    pool = urllib3.PoolManager()
    pool.request("GET", URL_200)


@snapshot()
def test_urllib3_connectionpool_snapshot(patch_urllib3):
    pool = urllib3.connectionpool.HTTPConnectionPool(HOST, PORT)
    pool.request("GET", "/status/200")
