import subprocess
import sys

import pytest
import requests
from requests import Session
from requests.exceptions import InvalidURL
from requests.exceptions import MissingSchema
import six

from ddtrace import Pin
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib.requests import patch
from ddtrace.contrib.requests import unpatch
from ddtrace.contrib.requests.connection import _extract_hostname
from ddtrace.contrib.requests.connection import _extract_query_string
from ddtrace.ext import http
from tests.opentracer.utils import init_tracer
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured
from tests.utils import assert_span_http_status_code
from tests.utils import override_global_tracer


# socket name comes from https://english.stackexchange.com/a/44048
SOCKET = "httpbin.org"
URL_200 = "http://{}/status/200".format(SOCKET)
URL_500 = "http://{}/status/500".format(SOCKET)


class BaseRequestTestCase(object):
    """Create a traced Session, patching during the setUp and
    unpatching after the tearDown
    """

    def setUp(self):
        super(BaseRequestTestCase, self).setUp()

        patch()
        self.session = Session()
        setattr(self.session, "datadog_tracer", self.tracer)

    def tearDown(self):
        unpatch()

        super(BaseRequestTestCase, self).tearDown()


class TestRequests(BaseRequestTestCase, TracerTestCase):
    def test_resource_path(self):
        out = self.session.get(URL_200)
        assert out.status_code == 200
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.get_tag("http.url") == URL_200

    def test_tracer_disabled(self):
        # ensure all valid combinations of args / kwargs work
        self.tracer.enabled = False
        out = self.session.get(URL_200)
        assert out.status_code == 200
        spans = self.pop_spans()
        assert len(spans) == 0

    def test_args_kwargs(self):
        # ensure all valid combinations of args / kwargs work
        url = URL_200
        method = "GET"
        inputs = [
            ([], {"method": method, "url": url}),
            ([method], {"url": url}),
            ([method, url], {}),
        ]

        for args, kwargs in inputs:
            # ensure a traced request works with these args
            out = self.session.request(*args, **kwargs)
            assert out.status_code == 200
            # validation
            spans = self.pop_spans()
            assert len(spans) == 1
            s = spans[0]
            assert s.get_tag(http.METHOD) == "GET"
            assert_span_http_status_code(s, 200)

    def test_untraced_request(self):
        # ensure the unpatch removes tracing
        unpatch()
        untraced = Session()

        out = untraced.get(URL_200)
        assert out.status_code == 200
        # validation
        spans = self.pop_spans()
        assert len(spans) == 0

    def test_double_patch(self):
        # ensure that double patch doesn't duplicate instrumentation
        patch()
        session = Session()
        setattr(session, "datadog_tracer", self.tracer)

        out = session.get(URL_200)
        assert out.status_code == 200
        spans = self.pop_spans()
        assert len(spans) == 1

    def test_200(self):
        out = self.session.get(URL_200)
        assert out.status_code == 200
        # validation
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert_is_measured(s)
        assert s.get_tag(http.METHOD) == "GET"
        assert_span_http_status_code(s, 200)
        assert s.error == 0
        assert s.span_type == "http"
        assert http.QUERY_STRING not in s.get_tags()

    def test_200_send(self):
        # when calling send directly
        req = requests.Request(url=URL_200, method="GET")
        req = self.session.prepare_request(req)

        out = self.session.send(req)
        assert out.status_code == 200
        # validation
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert_is_measured(s)
        assert s.get_tag(http.METHOD) == "GET"
        assert_span_http_status_code(s, 200)
        assert s.error == 0
        assert s.span_type == "http"

    def test_200_query_string(self):
        # ensure query string is removed before adding url to metadata
        query_string = "key=value&key2=value2"
        with self.override_http_config("requests", dict(trace_query_string=True)):
            out = self.session.get(URL_200 + "?" + query_string)
        assert out.status_code == 200
        # validation
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert_is_measured(s)
        assert s.get_tag(http.METHOD) == "GET"
        assert_span_http_status_code(s, 200)
        assert s.get_tag(http.URL) == URL_200 + "?" + query_string
        assert s.error == 0
        assert s.span_type == "http"
        assert s.get_tag(http.QUERY_STRING) == query_string

    def test_requests_module_200(self):
        # ensure the requests API is instrumented even without
        # using a `Session` directly
        with override_global_tracer(self.tracer):
            out = requests.get(URL_200)
            assert out.status_code == 200
            # validation
            spans = self.pop_spans()
            assert len(spans) == 1
            s = spans[0]

            assert_is_measured(s)
            assert s.get_tag(http.METHOD) == "GET"
            assert_span_http_status_code(s, 200)
            assert s.error == 0
            assert s.span_type == "http"

    def test_post_500(self):
        out = self.session.post(URL_500)
        # validation
        assert out.status_code == 500
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert_is_measured(s)
        assert s.get_tag(http.METHOD) == "POST"
        assert_span_http_status_code(s, 500)
        assert s.error == 1

    def test_non_existant_url(self):
        try:
            self.session.get("http://doesnotexist.google.com")
        except Exception:
            pass
        else:
            assert 0, "expected error"

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert_is_measured(s)
        assert s.get_tag(http.METHOD) == "GET"
        assert s.error == 1
        assert "Failed to establish a new connection" in s.get_tag(ERROR_MSG)
        assert "Failed to establish a new connection" in s.get_tag(ERROR_STACK)
        assert "Traceback (most recent call last)" in s.get_tag(ERROR_STACK)
        assert "requests.exception" in s.get_tag(ERROR_TYPE)

    def test_500(self):
        out = self.session.get(URL_500)
        assert out.status_code == 500

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert_is_measured(s)
        assert s.get_tag(http.METHOD) == "GET"
        assert_span_http_status_code(s, 500)
        assert s.error == 1

    def test_default_service_name(self):
        # ensure a default service name is set
        out = self.session.get(URL_200)
        assert out.status_code == 200

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert s.service == "requests"

    def test_user_set_service_name(self):
        # ensure a service name set by the user has precedence
        cfg = config.get_from(self.session)
        cfg["service_name"] = "clients"
        out = self.session.get(URL_200)
        assert out.status_code == 200
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "clients"

    def test_user_set_service(self):
        # ensure a service name set by the user has precedence
        cfg = config.get_from(self.session)
        cfg["service"] = "clients"
        out = self.session.get(URL_200)
        assert out.status_code == 200
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "clients"

    def test_parent_service_name_precedence(self):
        # span should not inherit the parent's service name
        # as all outbound requests should go to a difference service
        with self.tracer.trace("parent.span", service="web"):
            out = self.session.get(URL_200)
            assert out.status_code == 200

        spans = self.pop_spans()
        assert len(spans) == 2
        s = spans[1]

        assert s.name == "requests.request"
        assert s.service == "requests"

    def test_parent_without_service_name(self):
        # ensure the default value is used if the parent
        # doesn't have a service
        with self.tracer.trace("parent.span"):
            out = self.session.get(URL_200)
            assert out.status_code == 200

        spans = self.pop_spans()
        assert len(spans) == 2
        s = spans[1]

        assert s.name == "requests.request"
        assert s.service == "requests"

    def test_user_service_name_precedence(self):
        # ensure the user service name takes precedence over
        # the parent Span
        cfg = config.get_from(self.session)
        cfg["service_name"] = "clients"
        with self.tracer.trace("parent.span", service="web"):
            out = self.session.get(URL_200)
            assert out.status_code == 200

        spans = self.pop_spans()
        assert len(spans) == 2
        s = spans[1]

        assert s.name == "requests.request"
        assert s.service == "clients"

    def test_split_by_domain(self):
        # ensure a service name is generated by the domain name
        # of the ongoing call
        cfg = config.get_from(self.session)
        cfg["split_by_domain"] = True
        out = self.session.get(URL_200)
        assert out.status_code == 200

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert s.service == "httpbin.org"

    def test_split_by_domain_precedence(self):
        # ensure the split by domain has precedence all the time
        cfg = config.get_from(self.session)
        cfg["split_by_domain"] = True
        cfg["service_name"] = "intake"
        out = self.session.get(URL_200)
        assert out.status_code == 200

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert s.service == "httpbin.org"

    def test_split_by_domain_wrong(self):
        # ensure the split by domain doesn't crash in case of a wrong URL;
        # in that case, no spans are created
        cfg = config.get_from(self.session)
        cfg["split_by_domain"] = True
        with pytest.raises((MissingSchema, InvalidURL)):
            self.session.get("http:/some>thing")

        # We are wrapping `requests.Session.send` and this error gets thrown before that function
        spans = self.pop_spans()
        assert len(spans) == 0

    def test_split_by_domain_remove_auth_in_url(self):
        # ensure that auth details are stripped from URL
        cfg = config.get_from(self.session)
        cfg["split_by_domain"] = True
        out = self.session.get("http://user:pass@httpbin.org")
        assert out.status_code == 200

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert s.service == "httpbin.org"

    def test_split_by_domain_includes_port(self):
        # ensure that port is included if present in URL
        cfg = config.get_from(self.session)
        cfg["split_by_domain"] = True
        out = self.session.get("http://httpbin.org:80")
        assert out.status_code == 200

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert s.service == "httpbin.org:80"

    def test_split_by_domain_includes_port_path(self):
        # ensure that port is included if present in URL but not path
        cfg = config.get_from(self.session)
        cfg["split_by_domain"] = True
        out = self.session.get("http://httpbin.org:80/anything/v1/foo")
        assert out.status_code == 200

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert s.service == "httpbin.org:80"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_REQUESTS_SERVICE="override"))
    def test_global_config_service_env_precedence(self):
        out = self.session.get(URL_200)
        assert out.status_code == 200
        spans = self.pop_spans()
        assert spans[0].service == "override"

        cfg = config.get_from(self.session)
        cfg["service"] = "override2"
        out = self.session.get(URL_200)
        assert out.status_code == 200
        spans = self.pop_spans()
        assert spans[0].service == "override2"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_REQUESTS_SERVICE="override"))
    def test_global_config_service_env(self):
        out = self.session.get(URL_200)
        assert out.status_code == 200
        spans = self.pop_spans()
        assert spans[0].service == "override"

    def test_global_config_service(self):
        with self.override_config("requests", dict(service="override")):
            out = self.session.get(URL_200)
        assert out.status_code == 200
        spans = self.pop_spans()
        assert spans[0].service == "override"

    def test_200_ot(self):
        """OpenTracing version of test_200."""

        ot_tracer = init_tracer("requests_svc", self.tracer)

        with ot_tracer.start_active_span("requests_get"):
            out = self.session.get(URL_200)
            assert out.status_code == 200

        # validation
        spans = self.pop_spans()
        assert len(spans) == 2

        ot_span, dd_span = spans

        # confirm the parenting
        assert ot_span.parent_id is None
        assert dd_span.parent_id == ot_span.span_id

        assert ot_span.name == "requests_get"
        assert ot_span.service == "requests_svc"

        assert_is_measured(dd_span)
        assert dd_span.get_tag(http.METHOD) == "GET"
        assert_span_http_status_code(dd_span, 200)
        assert dd_span.error == 0
        assert dd_span.span_type == "http"

    def test_request_and_response_headers(self):
        # Disabled when not configured
        self.session.get(URL_200, headers={"my-header": "my_value"})
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.get_tag("http.request.headers.my-header") is None
        assert s.get_tag("http.response.headers.access-control-allow-origin") is None

        # Enabled when explicitly configured
        with self.override_config("requests", {}):
            config.requests.http.trace_headers(["my-header", "access-control-allow-origin"])
            self.session.get(URL_200, headers={"my-header": "my_value"})
            spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.get_tag("http.request.headers.my-header") == "my_value"
        assert s.get_tag("http.response.headers.access-control-allow-origin") == "*"

    def test_analytics_integration_default(self):
        """
        When making a request
            When an integration trace search is enabled and sample rate is set
                We expect the root span to have the appropriate tag
        """
        self.session.get(URL_200)

        spans = self.pop_spans()
        self.assertEqual(len(spans), 1)
        s = spans[0]
        self.assertIsNone(s.get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_integration_disabled(self):
        """
        When making a request
            When an integration trace search is enabled and sample rate is set
                We expect the root span to have the appropriate tag
        """
        with self.override_config("requests", dict(analytics_enabled=False, analytics_sample_rate=0.5)):
            self.session.get(URL_200)

        spans = self.pop_spans()
        self.assertEqual(len(spans), 1)
        s = spans[0]
        self.assertIsNone(s.get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_integration_on(self):
        """
        When making a request
            When an integration trace search is enabled and sample rate is set
                We expect the root span to have the appropriate tag
        """
        with self.override_config("requests", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            self.session.get(URL_200)

        spans = self.pop_spans()
        self.assertEqual(len(spans), 1)
        s = spans[0]
        self.assertEqual(s.get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_analytics_integration_on_using_pin(self):
        """
        When making a request
            When an integration trace search is enabled and sample rate is set
                We expect the root span to have the appropriate tag
        """
        pin = Pin(
            service=__name__,
            app="requests",
            _config={
                "service_name": __name__,
                "distributed_tracing": False,
                "split_by_domain": False,
                "analytics_enabled": True,
                "analytics_sample_rate": 0.5,
            },
        )
        pin.onto(self.session)
        self.session.get(URL_200)

        spans = self.pop_spans()
        self.assertEqual(len(spans), 1)
        s = spans[0]
        self.assertEqual(s.get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_analytics_integration_on_using_pin_default(self):
        """
        When making a request
            When an integration trace search is enabled and sample rate is set
                We expect the root span to have the appropriate tag
        """
        pin = Pin(
            service=__name__,
            app="requests",
            _config={
                "service_name": __name__,
                "distributed_tracing": False,
                "split_by_domain": False,
                "analytics_enabled": True,
            },
        )
        pin.onto(self.session)
        self.session.get(URL_200)

        spans = self.pop_spans()
        self.assertEqual(len(spans), 1)
        s = spans[0]
        self.assertEqual(s.get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)


def test_traced_session_no_patch_all(tmpdir):
    f = tmpdir.join("test.py")
    f.write(
        """
import mock
import ddtrace
from ddtrace.contrib.requests import TracedSession

# disable tracer writing to agent
# FIXME: Remove use of this internal attribute of Tracer to disable writer
ddtrace.tracer._writer.flush_queue = mock.Mock(return_value=None)

session = TracedSession()
session.get("http://httpbin.org/status/200")
""".lstrip()
    )
    p = subprocess.Popen(
        [sys.executable, "test.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(tmpdir),
    )
    p.wait()
    assert p.stderr.read() == six.b("")
    assert p.stdout.read() == six.b("")
    assert p.returncode == 0


@pytest.mark.parametrize(
    "uri,hostname",
    [
        ("http://localhost:8080", "localhost:8080"),
        ("http://localhost:8080/", "localhost:8080"),
        ("http://localhost", "localhost"),
        ("http://localhost/", "localhost"),
        ("http://asd:wwefwf@localhost:8080", "localhost:8080"),
        ("http://asd:wwefwf@localhost:8080/", "localhost:8080"),
        ("http://asd:wwefwf@localhost:8080/path", "localhost:8080"),
        ("http://asd:wwefwf@localhost:8080/path?query", "localhost:8080"),
        ("http://asd:wwefwf@localhost:8080/path?query#fragment", "localhost:8080"),
        ("http://asd:wwefwf@localhost:8080/path#frag?ment", "localhost:8080"),
        ("http://localhost:8080/path#frag?ment", "localhost:8080"),
        ("http://localhost/path#frag?ment", "localhost"),
    ],
)
def test_extract_hostname(uri, hostname):
    assert _extract_hostname(uri) == hostname


@pytest.mark.parametrize(
    "uri,qs",
    [
        ("http://localhost:8080", None),
        ("http://localhost", None),
        ("http://asd:wwefwf@localhost:8080", None),
        ("http://asd:wwefwf@localhost:8080/path", None),
        ("http://asd:wwefwf@localhost:8080/path?query", "query"),
        ("http://asd:wwefwf@localhost:8080/path?query#fragment", "query"),
        ("http://asd:wwefwf@localhost:8080/path#frag?ment", None),
        ("http://localhost:8080/path#frag?ment", None),
        ("http://localhost/path#frag?ment", None),
        ("http://localhost?query", "query"),
    ],
)
def test_extract_query_string(uri, qs):
    assert _extract_query_string(uri) == qs
