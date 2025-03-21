# -*- coding: utf-8 -*-
import logging
import os
import re
import time
from urllib.parse import quote as url_quote

import cherrypy
from cherrypy.test import helper
import pytest

import ddtrace
from ddtrace import config
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import USER_KEEP
from ddtrace.contrib.internal.cherrypy.patch import TraceMiddleware
from ddtrace.ext import http
from tests.contrib.patch import emit_integration_and_version_to_test_agent
from tests.tracer.utils_inferred_spans.test_helpers import assert_web_and_inferred_aws_api_gateway_span_data
from tests.utils import TracerTestCase
from tests.utils import assert_span_http_status_code
from tests.utils import override_global_config
from tests.utils import snapshot

from .web import StubApp


logger = logging.getLogger()
logger.level = logging.DEBUG


class TestCherrypy(TracerTestCase, helper.CPWebCase):
    """
    FIXME: the tests using getPage() are not synchronous and so require a
           delay afterwards.
    """

    @staticmethod
    def setup_server():
        cherrypy.tree.mount(
            StubApp(),
            "/",
            {
                "/": {"tools.tracer.on": True},
            },
        )

    def setUp(self):
        super(TestCherrypy, self).setUp()
        self.traced_app = TraceMiddleware(
            cherrypy,
            self.tracer,
            service="test.cherrypy.service",
            distributed_tracing=True,
        )

    def test_and_emit_get_version(self):
        from ddtrace.contrib.internal.cherrypy.patch import get_version

        version = get_version()
        assert type(version) == str
        assert version != ""

        emit_integration_and_version_to_test_agent("cherrypy", version)

    def test_double_instrumentation(self):
        # ensure CherryPy is never instrumented twice when `ddtrace-run`
        # and `TraceMiddleware` are used together. `traced_app` MUST
        # be assigned otherwise it's not possible to reproduce the
        # problem (the test scope must keep a strong reference)
        traced_app = TraceMiddleware(cherrypy, self.tracer)  # noqa: F841
        self.getPage("/")
        time.sleep(0.1)
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertStatus("200 OK")

        spans = self.pop_spans()
        assert len(spans) == 1

    def test_double_instrumentation_config(self):
        # ensure CherryPy uses the last set configuration to be sure
        # there are no breaking changes for who uses `ddtrace-run`
        # with the `TraceMiddleware`
        assert cherrypy.tools.tracer.service == "test.cherrypy.service"
        TraceMiddleware(
            cherrypy,
            self.tracer,
            service="new-intake",
        )
        self.getPage("/")
        time.sleep(0.1)
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertStatus("200 OK")

        spans = self.pop_spans()
        assert len(spans) == 1

        assert cherrypy.tools.tracer.service == "new-intake"

    def test_child(self):
        self.getPage("/child")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("child")

        # ensure trace worked
        spans = self.pop_spans()
        assert len(spans) == 2

        spans_by_name = {s.name: s for s in spans}

        s = spans_by_name["cherrypy.request"]
        assert s.span_id
        assert s.trace_id
        assert not s.parent_id
        assert s.service == "test.cherrypy.service"
        assert s.resource == "GET /child"
        assert s.error == 0

        c = spans_by_name["child"]
        assert c.span_id
        assert c.trace_id == s.trace_id
        assert c.parent_id == s.span_id
        assert c.service == "test.cherrypy.service"
        assert c.resource == "child"
        assert c.error == 0

    def test_success(self):
        self.getPage("/")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "GET /"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.get_tag(http.METHOD) == "GET"
        assert s.get_tag("component") == "cherrypy"
        assert s.get_tag("span.kind") == "server"

    def test_alias(self):
        self.getPage("/aliases")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("alias")

        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "GET /aliases"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.get_tag(http.METHOD) == "GET"
        assert s.get_tag("component") == "cherrypy"
        assert s.get_tag("span.kind") == "server"

    def test_handleme(self):
        self.getPage("/handleme")
        time.sleep(0.1)
        self.assertErrorPage(418, message="handled")

        # ensure trace worked
        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "GET /handleme"
        assert s.error == 0
        assert_span_http_status_code(s, 418)
        assert s.get_tag(http.METHOD) == "GET"
        assert s.get_tag("component") == "cherrypy"
        assert s.get_tag("span.kind") == "server"

    def test_error(self):
        self.getPage("/error")
        time.sleep(0.1)
        self.assertErrorPage(500)

        # ensure the request was traced.
        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "GET /error"
        assert_span_http_status_code(s, 500)
        assert s.error == 1
        assert s.get_tag(http.METHOD) == "GET"
        assert s.get_tag("component") == "cherrypy"
        assert s.get_tag("span.kind") == "server"

    def test_fatal(self):
        self.getPage("/fatal")
        time.sleep(0.1)

        self.assertErrorPage(500)

        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "GET /fatal"
        assert_span_http_status_code(s, 500)
        assert s.error == 1
        assert s.get_tag(http.METHOD) == "GET"
        assert "ZeroDivisionError" in s.get_tag(ERROR_TYPE), s.get_tags()
        assert "by zero" in s.get_tag(ERROR_MSG)
        assert s.get_tag("component") == "cherrypy"
        assert s.get_tag("span.kind") == "server"
        assert re.search('File ".*/contrib/cherrypy/web.py", line [0-9]+, in fatal', s.get_tag(ERROR_STACK))

    def test_unicode(self):
        # Encoded utf8 query strings MUST be parsed correctly.
        # Here, the URL is encoded in utf8 and then %HEX
        # See https://docs.cherrypy.org/en/latest/_modules/cherrypy/test/test_encoding.html for more
        self.getPage(url_quote("/üŋïĉóđē".encode("utf-8")))
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody(b"\xc3\xbc\xc5\x8b\xc3\xaf\xc4\x89\xc3\xb3\xc4\x91\xc4\x93")

        # ensure trace worked
        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "GET /üŋïĉóđē"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.get_tag(http.METHOD) == "GET"
        assert s.get_tag(http.URL) == "http://127.0.0.1:54583/üŋïĉóđē"
        assert s.get_tag("component") == "cherrypy"
        assert s.get_tag("span.kind") == "server"

    def test_404(self):
        self.getPage("/404/test")
        time.sleep(0.1)
        self.assertStatus("404 Not Found")

        # ensure trace worked
        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "GET /404/test"
        assert s.error == 0
        assert_span_http_status_code(s, 404)
        assert s.get_tag(http.METHOD) == "GET"
        assert s.get_tag(http.URL) == "http://127.0.0.1:54583/404/test"
        assert s.get_tag("component") == "cherrypy"
        assert s.get_tag("span.kind") == "server"

    def test_propagation(self):
        self.getPage(
            "/",
            headers=[
                ("x-datadog-trace-id", "1234"),
                ("x-datadog-parent-id", "4567"),
                ("x-datadog-sampling-priority", "2"),
            ],
        )
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        # ensure the propagation worked well
        assert s.trace_id == 1234
        assert s.parent_id == 4567
        assert s.get_metric(_SAMPLING_PRIORITY_KEY) == 2

    def test_disabled_distributed_tracing_config(self):
        previous_distributed_tracing = config.cherrypy["distributed_tracing"]
        config.cherrypy["distributed_tracing"] = False
        self.getPage(
            "/",
            headers=[
                ("x-datadog-trace-id", "1234"),
                ("x-datadog-parent-id", "4567"),
                ("x-datadog-sampling-priority", "2"),
            ],
        )
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

        # ensure trace worked
        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        # ensure the propagation worked well
        assert s.trace_id != 1234
        assert s.parent_id != 4567
        assert s.get_metric(_SAMPLING_PRIORITY_KEY) != 2

        config.cherrypy["distributed_tracing"] = previous_distributed_tracing

    def test_disabled_distributed_tracing_middleware(self):
        previous_distributed_tracing = cherrypy.tools.tracer.use_distributed_tracing
        cherrypy.tools.tracer.use_distributed_tracing = False
        self.getPage(
            "/",
            headers=[
                ("x-datadog-trace-id", "1234"),
                ("x-datadog-parent-id", "4567"),
                ("x-datadog-sampling-priority", "2"),
            ],
        )
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

        # ensure trace worked
        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        # ensure the propagation worked well
        assert s.trace_id != 1234
        assert s.parent_id != 4567
        assert s.get_metric(_SAMPLING_PRIORITY_KEY) != 2

        cherrypy.tools.tracer.use_distributed_tracing = previous_distributed_tracing

    def test_custom_span(self):
        self.getPage("/custom_span")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertBody("hiya")

        # ensure trace worked
        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "overridden"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.get_tag(http.METHOD) == "GET"
        assert s.get_tag("component") == "cherrypy"
        assert s.get_tag("span.kind") == "server"

    def test_http_request_header_tracing(self):
        config.cherrypy.http.trace_headers(["Host", "my-header"])
        self.getPage(
            "/",
            headers=[
                ("my-header", "my_value"),
                ("x-datadog-parent-id", "4567"),
                ("x-datadog-sampling-priority", "2"),
            ],
        )
        time.sleep(0.1)

        traces = self.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]
        assert span.get_tag("http.request.headers.my-header") == "my_value"
        assert span.get_tag("http.request.headers.host") == "127.0.0.1:54583"

    def test_http_response_header_tracing(self):
        config.cherrypy.http.trace_headers(["my-response-header"])
        self.getPage("/response_headers")
        time.sleep(0.1)

        traces = self.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]

        assert span.get_tag("http.response.headers.my-response-header") == "my_response_value"

    def test_variable_resource(self):
        self.getPage("/dispatch/abc123/")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("dispatch with abc123")

        # ensure trace worked
        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"

        # Once CherryPy returns sensible results for virtual path components, this
        # can be: "GET /dispatch/{{test_value}}/"
        assert s.resource == "GET /dispatch/abc123/"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.get_tag(http.METHOD) == "GET"

    def test_post(self):
        self.getPage("/", method="POST")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

        # ensure trace worked
        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "POST /"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.get_tag(http.METHOD) == "POST"

    def test_service_configuration_config(self):
        previous_service = config.cherrypy.get("service", "test.cherrypy.service")
        config.cherrypy["service"] = "my_cherrypy_service"
        self.getPage("/")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

        # ensure trace worked
        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "my_cherrypy_service"
        assert s.resource == "GET /"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.get_tag(http.METHOD) == "GET"

        config.cherrypy["service"] = previous_service

    def test_service_configuration_middleware(self):
        previous_service = cherrypy.tools.tracer.service
        cherrypy.tools.tracer.service = "my_cherrypy_service2"
        self.getPage("/")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

        # ensure trace worked
        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "my_cherrypy_service2"
        assert s.resource == "GET /"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.get_tag(http.METHOD) == "GET"

        cherrypy.tools.tracer.service = previous_service

    def test_inferred_spans_api_gateway_default(self):
        default_headers = [
            ("x-dd-proxy", "aws-apigateway"),
            ("x-dd-proxy-request-time-ms", "1736973768000"),
            ("x-dd-proxy-path", "/"),
            ("x-dd-proxy-httpmethod", "GET"),
            ("x-dd-proxy-domain-name", "local"),
            ("x-dd-proxy-stage", "stage"),
        ]

        distributed_headers = [
            ("x-dd-proxy", "aws-apigateway"),
            ("x-dd-proxy-request-time-ms", "1736973768000"),
            ("x-dd-proxy-path", "/"),
            ("x-dd-proxy-httpmethod", "GET"),
            ("x-dd-proxy-domain-name", "local"),
            ("x-dd-proxy-stage", "stage"),
            ("x-datadog-trace-id", "1"),
            ("x-datadog-parent-id", "2"),
            ("x-datadog-origin", "rum"),
            ("x-datadog-sampling-priority", "2"),
        ]
        for setting_enabled in [True, False]:
            for test_endpoint in [
                {
                    "endpoint": "/",
                    "status": 200,
                    "resource_name": "GET /",
                },
                {
                    "endpoint": "/fatal",
                    "status": 500,
                    "resource_name": "GET /fatal",
                },
                {
                    "endpoint": "/error",
                    "status": 500,
                    "resource_name": "GET /error",
                },
            ]:
                with override_global_config(dict(_inferred_proxy_services_enabled=setting_enabled)):
                    for test_headers in [default_headers, distributed_headers]:
                        self.getPage(test_endpoint["endpoint"], headers=test_headers)
                        time.sleep(0.1)
                        traces = self.pop_traces()
                        if setting_enabled:
                            aws_gateway_span = traces[0][0]
                            web_span = traces[0][1]

                            assert_web_and_inferred_aws_api_gateway_span_data(
                                aws_gateway_span,
                                web_span,
                                web_span_name="cherrypy.request",
                                web_span_component="cherrypy",
                                web_span_service_name="test.cherrypy.service",
                                web_span_resource="GET " + test_endpoint["endpoint"],
                                api_gateway_service_name="local",
                                api_gateway_resource="GET /",
                                method="GET",
                                status_code=test_endpoint["status"],
                                url="local/",
                                start=1736973768,
                                is_distributed=test_headers == distributed_headers,
                                distributed_trace_id=1,
                                distributed_parent_id=2,
                                distributed_sampling_priority=USER_KEEP,
                            )

                        else:
                            web_span = traces[0][0]
                            assert web_span._parent is None


class TestCherrypySnapshot(helper.CPWebCase):
    @staticmethod
    def setup_server():
        cherrypy.tree.mount(
            StubApp(),
            "/",
            {
                "/": {"tools.tracer.on": True},
            },
        )

    def setUp(self):
        config.cherrypy.http.trace_headers(["Host", "my-header"])
        self.traced_app = TraceMiddleware(
            cherrypy,
            tracer=ddtrace.tracer,
            service="test.cherrypy.service",
            distributed_tracing=True,
        )

    @snapshot()
    def test_child(self):
        self.getPage("/child")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("child")

    @snapshot(ignores=["meta.error.stack", "meta.error.type", "meta.error.message"])
    def test_success(self):
        self.getPage("/")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

    @snapshot(ignores=["meta.error.stack", "meta.error.type", "meta.error.message"])
    def test_error(self):
        self.getPage("/error")
        time.sleep(0.1)
        self.assertErrorPage(500)

    @snapshot(ignores=["meta.error.stack", "meta.error.type", "meta.error.message"])
    def test_fatal(self):
        self.getPage("/fatal")
        time.sleep(0.1)
        self.assertErrorPage(500)


@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
def test_service_name_schema(ddtrace_run_python_code_in_subprocess, schema_version):
    expected_service_name = {
        None: "cherrypy",
        "v0": "cherrypy",
        "v1": "mysvc",
    }[schema_version]
    code = """
import pytest
import cherrypy
import time
from cherrypy.test import helper
from tests.utils import TracerTestCase
from tests.contrib.cherrypy.web import StubApp
from ddtrace.contrib.internal.cherrypy.patch import TraceMiddleware
class TestCherrypy(TracerTestCase, helper.CPWebCase):
    @staticmethod
    def setup_server():
        cherrypy.tree.mount(
            StubApp(),
            "/",
            {{
                "/": {{"tools.tracer.on": True}},
            }},
        )

    def setUp(self):
        super(TestCherrypy, self).setUp()
        self.traced_app = TraceMiddleware(
            cherrypy,
            self.tracer,
            distributed_tracing=True,
        )

    def test(self):
        import os

        self.getPage("/")
        time.sleep(0.1)
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "{}", "Schema Version: {{}}".format(os.environ.get("DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"))

if __name__ == "__main__":
    import sys
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_service_name
    )
    env = os.environ.copy()
    if schema_version:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    env["DD_SERVICE"] = "mysvc"
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err, out)
    assert b"2 passed" in out


@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
def test_operation_name_schema(ddtrace_run_python_code_in_subprocess, schema_version):
    expected_operation_name = {
        None: "cherrypy.request",
        "v0": "cherrypy.request",
        "v1": "http.server.request",
    }[schema_version]
    code = """
import pytest
import cherrypy
import time
from cherrypy.test import helper
from tests.utils import TracerTestCase
from tests.contrib.cherrypy.web import StubApp
from ddtrace.contrib.internal.cherrypy.patch import TraceMiddleware
class TestCherrypy(TracerTestCase, helper.CPWebCase):
    @staticmethod
    def setup_server():
        cherrypy.tree.mount(
            StubApp(),
            "/",
            {{
                "/": {{"tools.tracer.on": True}},
            }},
        )

    def setUp(self):
        super(TestCherrypy, self).setUp()
        self.traced_app = TraceMiddleware(
            cherrypy,
            self.tracer,
            distributed_tracing=True,
        )

    def test(self):
        import os

        self.getPage("/")
        time.sleep(0.1)
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == "{}", "Schema Version: {{}}".format(os.environ.get("DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"))

if __name__ == "__main__":
    import sys
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_operation_name
    )
    env = os.environ.copy()
    if schema_version:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    env["DD_SERVICE"] = "mysvc"
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err, out)
    assert b"2 passed" in out
