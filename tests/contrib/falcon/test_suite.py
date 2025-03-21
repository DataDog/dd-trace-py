from ddtrace import config
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import USER_KEEP
from ddtrace.contrib.internal.falcon.patch import FALCON_VERSION
from ddtrace.ext import http as httpx
from tests.opentracer.utils import init_tracer
from tests.tracer.utils_inferred_spans.test_helpers import assert_web_and_inferred_aws_api_gateway_span_data
from tests.utils import assert_is_measured
from tests.utils import assert_span_http_status_code
from tests.utils import override_global_config


class FalconTestMixin(object):
    def make_test_call(self, url, method="get", expected_status_code=None, **kwargs):
        func = getattr(self.client, "simulate_%s" % (method,))
        out = func(url, **kwargs)
        if FALCON_VERSION < (2, 0, 0):
            if expected_status_code is not None:
                assert out.status_code == expected_status_code
        else:
            if expected_status_code is not None:
                assert out.status[:3] == str(expected_status_code)
        return out


class FalconTestCase(FalconTestMixin):
    """Falcon mixin test case that includes all possible tests. If you need
    to add new tests, add them here so that they're shared across manual
    and automatic instrumentation.
    """

    def test_404(self):
        self.make_test_call("/fake_endpoint", expected_status_code=404)
        traces = self.tracer.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]

        assert_is_measured(span)
        assert span.name == "falcon.request"
        assert span.service == self._service
        assert span.resource == "GET 404"
        assert_span_http_status_code(span, 404)
        assert span.get_tag(httpx.URL) == "http://falconframework.org/fake_endpoint"
        assert span.get_tag("component") == "falcon"
        assert span.get_tag("span.kind") == "server"
        assert httpx.QUERY_STRING not in span.get_tags()
        assert span.parent_id is None
        assert span.error == 0

    def test_exception(self):
        try:
            self.make_test_call("/exception")
        except Exception:
            pass
        else:
            if FALCON_VERSION < (3, 0, 0):
                assert 0

        traces = self.tracer.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]

        assert_is_measured(span)
        assert span.name == "falcon.request"
        assert span.service == self._service
        assert span.resource == "GET tests.contrib.falcon.app.resources.ResourceException"
        assert_span_http_status_code(span, 500)
        assert span.get_tag(httpx.URL) == "http://falconframework.org/exception"
        assert span.parent_id is None
        assert span.error == 1
        assert span.get_tag("component") == "falcon"
        assert span.get_tag("span.kind") == "server"

    def test_200(self, query_string="", trace_query_string=False):
        out = self.make_test_call("/200", expected_status_code=200, query_string=query_string)
        assert out.content.decode("utf-8") == "Success"

        traces = self.tracer.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]

        assert_is_measured(span)
        assert span.name == "falcon.request"
        assert span.service == self._service
        assert span.resource == "GET tests.contrib.falcon.app.resources.Resource200"
        assert_span_http_status_code(span, 200)
        fqs = ("?" + query_string) if query_string else ""
        assert span.get_tag(httpx.URL) == "http://falconframework.org/200" + fqs
        assert span.get_tag("component") == "falcon"
        assert span.get_tag("span.kind") == "server"
        if config.falcon.trace_query_string:
            assert span.get_tag(httpx.QUERY_STRING) == query_string
        else:
            assert httpx.QUERY_STRING not in span.get_tags()
        assert span.parent_id is None
        assert span.span_type == "web"
        assert span.error == 0

    def test_200_qs(self):
        return self.test_200("foo=bar")

    def test_200_multi_qs(self):
        return self.test_200("foo=bar&foo=baz&x=y")

    def test_200_qs_trace(self):
        with self.override_http_config("falcon", dict(trace_query_string=True)):
            return self.test_200("foo=bar", trace_query_string=True)

    def test_200_multi_qs_trace(self):
        with self.override_http_config("falcon", dict(trace_query_string=True)):
            return self.test_200("foo=bar&foo=baz&x=y", trace_query_string=True)

    def make_route_reporting_test(self, endpoint, status, expected_route):
        _ = self.make_test_call(endpoint)

        traces = self.tracer.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]

        assert_is_measured(span)
        assert span.name == "falcon.request"
        assert span.service == self._service
        assert_span_http_status_code(span, status)
        assert span.get_tag("component") == "falcon"
        assert span.get_tag("span.kind") == "server"
        assert span.get_tag("http.route") == expected_route
        assert span.parent_id is None
        assert span.span_type == "web"

    def test_route_reporting_200(self):
        return self.make_route_reporting_test("/200", 200, "/200")

    def test_route_reporting_dynamic_match(self):
        return self.make_route_reporting_test("/hello/foo", 200, "/hello/{name}")

    def test_route_reporting_404_no_match(self):
        return self.make_route_reporting_test("/nothing/here", 404, None)

    def test_route_reporting_404_match(self):
        return self.make_route_reporting_test("/not_found", 404, "/not_found")

    def test_route_reporting_500_match(self):
        return self.make_route_reporting_test("/exception", 500, "/exception")

    def test_201(self):
        out = self.make_test_call("/201", method="post", expected_status_code=201)
        assert out.status_code == 201
        assert out.content.decode("utf-8") == "Success"

        traces = self.tracer.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]

        assert_is_measured(span)
        assert span.name == "falcon.request"
        assert span.service == self._service
        assert span.resource == "POST tests.contrib.falcon.app.resources.Resource201"
        assert_span_http_status_code(span, 201)
        assert span.get_tag(httpx.URL) == "http://falconframework.org/201"
        assert span.parent_id is None
        assert span.error == 0
        assert span.get_tag("component") == "falcon"
        assert span.get_tag("span.kind") == "server"

    def test_500(self):
        out = self.make_test_call("/500", expected_status_code=500)
        assert out.content.decode("utf-8") == "Failure"

        traces = self.tracer.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]

        assert_is_measured(span)
        assert span.name == "falcon.request"
        assert span.service == self._service
        assert span.resource == "GET tests.contrib.falcon.app.resources.Resource500"
        assert_span_http_status_code(span, 500)
        assert span.get_tag(httpx.URL) == "http://falconframework.org/500"
        assert span.parent_id is None
        assert span.error == 1
        assert span.get_tag("component") == "falcon"
        assert span.get_tag("span.kind") == "server"

    def test_404_exception(self):
        self.make_test_call("/not_found", expected_status_code=404)

        traces = self.tracer.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]

        assert_is_measured(span)
        assert span.name == "falcon.request"
        assert span.service == self._service
        assert span.resource == "GET tests.contrib.falcon.app.resources.ResourceNotFound"
        assert_span_http_status_code(span, 404)
        assert span.get_tag(httpx.URL) == "http://falconframework.org/not_found"
        assert span.parent_id is None
        assert span.error == 0
        assert span.get_tag("component") == "falcon"
        assert span.get_tag("span.kind") == "server"

    def test_404_exception_no_stacktracer(self):
        # it should not have the stacktrace when a 404 exception is raised
        self.make_test_call("/not_found", expected_status_code=404)

        traces = self.tracer.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]

        assert_is_measured(span)
        assert span.name == "falcon.request"
        assert span.service == self._service
        assert_span_http_status_code(span, 404)
        assert span.get_tag(ERROR_TYPE) is None
        assert span.parent_id is None
        assert span.error == 0
        assert span.get_tag("component") == "falcon"
        assert span.get_tag("span.kind") == "server"

    def test_200_ot(self):
        """OpenTracing version of test_200."""
        writer = self.tracer._writer
        ot_tracer = init_tracer("my_svc", self.tracer)
        ot_tracer._dd_tracer._writer = writer
        ot_tracer._dd_tracer._recreate()

        with ot_tracer.start_active_span("ot_span"):
            out = self.make_test_call("/200", expected_status_code=200)
        assert out.content.decode("utf-8") == "Success"

        traces = self.tracer.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 2
        ot_span, dd_span = traces[0]

        # confirm the parenting
        assert ot_span.parent_id is None
        assert dd_span.parent_id == ot_span.span_id

        assert ot_span.service == "my_svc"
        assert ot_span.resource == "ot_span"

        assert_is_measured(dd_span)
        assert dd_span.name == "falcon.request"
        assert dd_span.service == self._service
        assert dd_span.resource == "GET tests.contrib.falcon.app.resources.Resource200"
        assert_span_http_status_code(dd_span, 200)
        assert dd_span.get_tag(httpx.URL) == "http://falconframework.org/200"
        assert dd_span.error == 0

    def test_falcon_request_hook(self):
        @config.falcon.hooks.on("request")
        def on_falcon_request(span, request, response):
            span.set_tag("my.custom", "tag")

        out = self.make_test_call("/200", expected_status_code=200)
        assert out.content.decode("utf-8") == "Success"

        traces = self.tracer.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]
        assert span.get_tag("http.request.headers.my_header") is None
        assert span.get_tag("http.response.headers.my_response_header") is None

        assert span.name == "falcon.request"

        assert span.get_tag("my.custom") == "tag"

        assert span.error == 0

    def test_http_header_tracing(self):
        with self.override_config("falcon", {}):
            config.falcon.http.trace_headers(["my-header", "my-response-header"])
            self.make_test_call("/200", headers={"my-header": "my_value"})
            traces = self.tracer.pop_traces()

        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]
        assert span.get_tag("http.request.headers.my-header") == "my_value"
        assert span.get_tag("http.response.headers.my-response-header") == "my_response_value"

    def test_inferred_spans_api_gateway_default(self):
        headers = {
            "x-dd-proxy": "aws-apigateway",
            "x-dd-proxy-request-time-ms": "1736973768000",
            "x-dd-proxy-path": "/",
            "x-dd-proxy-httpmethod": "GET",
            "x-dd-proxy-domain-name": "local",
            "x-dd-proxy-stage": "stage",
        }
        for setting_enabled in [False, True]:
            for test_headers in [headers]:
                for test_endpoint in [
                    {
                        "endpoint": "/200",
                        "status": 200,
                        "resource_name": "GET tests.contrib.falcon.app.resources.Resource200",
                    },
                    {
                        "endpoint": "/exception",
                        "status": 500,
                        "resource_name": "GET tests.contrib.falcon.app.resources.ResourceException",
                    },
                ]:
                    with override_global_config(dict(_inferred_proxy_services_enabled=setting_enabled)):
                        self.make_test_call(test_endpoint["endpoint"], headers=test_headers)
                        traces = self.tracer.pop_traces()
                        if setting_enabled:
                            aws_gateway_span = traces[0][0]
                            web_span = traces[0][1]

                            assert len(traces) == 1
                            assert len(traces[0]) == 2

                            assert_web_and_inferred_aws_api_gateway_span_data(
                                aws_gateway_span,
                                web_span,
                                web_span_name="falcon.request",
                                web_span_component="falcon",
                                web_span_service_name="falcon",
                                web_span_resource=test_endpoint["resource_name"],
                                api_gateway_service_name="local",
                                api_gateway_resource="GET /",
                                method="GET",
                                status_code=test_endpoint["status"],
                                url="local/",
                                start=1736973768.0,
                                is_distributed=False,
                                distributed_trace_id=1,
                                distributed_parent_id=2,
                                distributed_sampling_priority=USER_KEEP,
                            )

                        else:
                            web_span = traces[0][0]
                            assert web_span._parent is None
