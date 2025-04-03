import pytest
import tornado

from ddtrace import config
from ddtrace.constants import _ORIGIN_KEY
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import USER_KEEP
from ddtrace.ext import http
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from tests.opentracer.utils import init_tracer
from tests.tracer.utils_inferred_spans.test_helpers import assert_web_and_inferred_aws_api_gateway_span_data
from tests.utils import assert_is_measured
from tests.utils import assert_span_http_status_code

from .utils import TornadoTestCase
from .utils import TracerTestCase
from .web.app import CustomDefaultHandler


class TestTornadoWeb(TornadoTestCase):
    """
    Ensure that Tornado web handlers are properly traced.
    """

    def test_success_handler(self, query_string=""):
        # it should trace a handler that returns 200
        if query_string:
            fqs = "?" + query_string
        else:
            fqs = ""
        response = self.fetch("/success/" + fqs)
        assert 200 == response.code

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert_is_measured(request_span)
        assert "tornado-web" == request_span.service
        assert "tornado.request" == request_span.name
        assert "web" == request_span.span_type
        assert "tests.contrib.tornado.web.app.SuccessHandler" == request_span.resource
        assert "GET" == request_span.get_tag("http.method")
        assert "/success/" == request_span.get_tag("http.route")
        assert_span_http_status_code(request_span, 200)
        if config.tornado.trace_query_string:
            assert query_string == request_span.get_tag(http.QUERY_STRING)
        else:
            assert http.QUERY_STRING not in request_span.get_tags()

        if config.tornado.http_tag_query_string:
            assert self.get_url("/success/") + fqs == request_span.get_tag(http.URL)
        else:
            assert self.get_url("/success/") == request_span.get_tag(http.URL)

        assert 0 == request_span.error
        assert request_span.get_tag("component") == "tornado"
        assert request_span.get_tag("span.kind") == "server"

    def test_success_handler_query_string(self):
        self.test_success_handler("foo=bar")

    def test_success_handler_query_string_trace(self):
        with self.override_http_config("tornado", dict(trace_query_string=True)):
            self.test_success_handler("foo=bar")

    def test_status_code_500_handler(self):
        """
        Test an endpoint which sets the status code to 500 but doesn't raise an exception

        We expect the resulting span to be marked as an error
        """
        response = self.fetch("/status_code/500")
        assert 500 == response.code

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert_is_measured(request_span)
        assert "tests.contrib.tornado.web.app.ResponseStatusHandler" == request_span.resource
        assert "GET" == request_span.get_tag("http.method")
        assert_span_http_status_code(request_span, 500)
        assert self.get_url("/status_code/500") == request_span.get_tag(http.URL)
        assert 1 == request_span.error
        assert request_span.get_tag("http.route") == "/status_code/%s"
        assert request_span.get_tag("component") == "tornado"
        assert request_span.get_tag("span.kind") == "server"

    def test_nested_application(self):
        """
        Test an endpoint which sets the status code to 500 but doesn't raise an exception

        We expect the resulting span to be marked as an error
        """
        response1 = self.fetch("/nested_app/handler1/")
        response2 = self.fetch("/nested_app/handler2/")
        assert 200 == response1.code
        assert 200 == response2.code

        traces = self.pop_traces()
        assert 2 == len(traces)

        for i, trace in enumerate(traces, start=1):
            request_span = trace[0]
            assert_span_http_status_code(request_span, 200)
            assert request_span.get_tag("http.route") == f"/nested_app/handler{i}/"

    def test_nested_handler(self):
        # it should trace a handler that calls the tracer.trace() method
        # using the automatic Context retrieval
        response = self.fetch("/nested/")
        assert 200 == response.code
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 2 == len(traces[0])
        # check request span
        request_span = traces[0][0]
        assert_is_measured(request_span)
        assert "tornado-web" == request_span.service
        assert "tornado.request" == request_span.name
        assert "web" == request_span.span_type
        assert "tests.contrib.tornado.web.app.NestedHandler" == request_span.resource
        assert "GET" == request_span.get_tag("http.method")
        assert_span_http_status_code(request_span, 200)
        assert self.get_url("/nested/") == request_span.get_tag(http.URL)
        assert 0 == request_span.error
        # check nested span
        nested_span = traces[0][1]
        assert "tornado-web" == nested_span.service
        assert "tornado.sleep" == nested_span.name
        assert 0 == nested_span.error
        # check durations because of the yield sleep
        assert request_span.duration >= 0.05
        assert nested_span.duration >= 0.05
        assert request_span.get_tag("component") == "tornado"
        assert request_span.get_tag("span.kind") == "server"

    def test_exception_handler(self):
        # it should trace a handler that raises an exception
        response = self.fetch("/exception/")
        assert 500 == response.code

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert_is_measured(request_span)
        assert "tornado-web" == request_span.service
        assert "tornado.request" == request_span.name
        assert "web" == request_span.span_type
        assert "tests.contrib.tornado.web.app.ExceptionHandler" == request_span.resource
        assert "GET" == request_span.get_tag("http.method")
        assert_span_http_status_code(request_span, 500)
        assert self.get_url("/exception/") == request_span.get_tag(http.URL)
        assert 1 == request_span.error
        assert "Ouch!" == request_span.get_tag(ERROR_MSG)
        assert "Exception: Ouch!" in request_span.get_tag("error.stack")
        assert request_span.get_tag("component") == "tornado"
        assert request_span.get_tag("span.kind") == "server"

    def test_http_exception_handler(self):
        # it should trace a handler that raises a Tornado HTTPError
        response = self.fetch("/http_exception/")
        assert 501 == response.code

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert_is_measured(request_span)
        assert "tornado-web" == request_span.service
        assert "tornado.request" == request_span.name
        assert "web" == request_span.span_type
        assert "tests.contrib.tornado.web.app.HTTPExceptionHandler" == request_span.resource
        assert "GET" == request_span.get_tag("http.method")
        assert_span_http_status_code(request_span, 501)
        assert self.get_url("/http_exception/") == request_span.get_tag(http.URL)
        assert 1 == request_span.error
        assert "HTTP 501: Not Implemented (unavailable)" == request_span.get_tag(ERROR_MSG)
        assert "HTTP 501: Not Implemented (unavailable)" in request_span.get_tag("error.stack")
        assert request_span.get_tag("component") == "tornado"
        assert request_span.get_tag("span.kind") == "server"

    def test_http_exception_500_handler(self):
        # it should trace a handler that raises a Tornado HTTPError
        response = self.fetch("/http_exception_500/")
        assert 500 == response.code

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert_is_measured(request_span)
        assert "tornado-web" == request_span.service
        assert "tornado.request" == request_span.name
        assert "web" == request_span.span_type
        assert "tests.contrib.tornado.web.app.HTTPException500Handler" == request_span.resource
        assert "GET" == request_span.get_tag("http.method")
        assert_span_http_status_code(request_span, 500)
        assert self.get_url("/http_exception_500/") == request_span.get_tag(http.URL)
        assert 1 == request_span.error
        assert "HTTP 500: Server Error (server error)" == request_span.get_tag(ERROR_MSG)
        assert "HTTP 500: Server Error (server error)" in request_span.get_tag("error.stack")
        assert request_span.get_tag("component") == "tornado"
        assert request_span.get_tag("span.kind") == "server"

    def test_http_exception_500_handler_ignored_exception(self):
        # it should trace a handler that raises a Tornado HTTPError
        # The exception should NOT be set on the span
        prev_error_statuses = config._http_server.error_statuses
        try:
            config._http_server.error_statuses = "501-599"
            response = self.fetch("/http_exception_500/")
            assert 500 == response.code
        finally:
            config._http_server.error_statuses = prev_error_statuses

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        request_span = traces[0][0]
        assert "tornado.request" == request_span.name

        assert_span_http_status_code(request_span, 500)
        assert request_span.error == 0
        assert request_span.get_tag(ERROR_MSG) is None
        assert request_span.get_tag("error.stack") is None

    def test_sync_success_handler(self):
        # it should trace a synchronous handler that returns 200
        response = self.fetch("/sync_success/")
        assert 200 == response.code

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert_is_measured(request_span)
        assert "tornado-web" == request_span.service
        assert "tornado.request" == request_span.name
        assert "web" == request_span.span_type
        assert "tests.contrib.tornado.web.app.SyncSuccessHandler" == request_span.resource
        assert "GET" == request_span.get_tag("http.method")
        assert_span_http_status_code(request_span, 200)
        assert self.get_url("/sync_success/") == request_span.get_tag(http.URL)
        assert 0 == request_span.error
        assert request_span.get_tag("component") == "tornado"
        assert request_span.get_tag("span.kind") == "server"

    def test_sync_exception_handler(self):
        # it should trace a handler that raises an exception
        response = self.fetch("/sync_exception/")
        assert 500 == response.code

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert_is_measured(request_span)
        assert "tornado-web" == request_span.service
        assert "tornado.request" == request_span.name
        assert "web" == request_span.span_type
        assert "tests.contrib.tornado.web.app.SyncExceptionHandler" == request_span.resource
        assert "GET" == request_span.get_tag("http.method")
        assert_span_http_status_code(request_span, 500)
        assert self.get_url("/sync_exception/") == request_span.get_tag(http.URL)
        assert 1 == request_span.error
        assert "Ouch!" == request_span.get_tag(ERROR_MSG)
        assert "Exception: Ouch!" in request_span.get_tag("error.stack")
        assert request_span.get_tag("component") == "tornado"
        assert request_span.get_tag("span.kind") == "server"

    def test_404_handler(self):
        # it should trace 404
        response = self.fetch("/does_not_exist/")
        assert 404 == response.code

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert_is_measured(request_span)
        assert "tornado-web" == request_span.service
        assert "tornado.request" == request_span.name
        assert "web" == request_span.span_type
        assert "tornado.web.ErrorHandler" == request_span.resource
        assert "GET" == request_span.get_tag("http.method")
        assert_span_http_status_code(request_span, 404)
        assert self.get_url("/does_not_exist/") == request_span.get_tag(http.URL)
        assert 0 == request_span.error
        assert request_span.get_tag("component") == "tornado"
        assert request_span.get_tag("span.kind") == "server"

    def test_redirect_handler(self):
        # it should trace the built-in RedirectHandler
        response = self.fetch("/redirect/")
        assert 200 == response.code

        # we trace two different calls: the RedirectHandler and the SuccessHandler
        traces = self.pop_traces()
        assert 2 == len(traces)
        assert 1 == len(traces[0])
        assert 1 == len(traces[1])

        redirect_span = traces[0][0]
        assert_is_measured(redirect_span)
        assert "tornado-web" == redirect_span.service
        assert "tornado.request" == redirect_span.name
        assert "web" == redirect_span.span_type
        assert "tornado.web.RedirectHandler" == redirect_span.resource
        assert "GET" == redirect_span.get_tag("http.method")
        assert_span_http_status_code(redirect_span, 301)
        assert self.get_url("/redirect/") == redirect_span.get_tag(http.URL)
        assert 0 == redirect_span.error
        assert redirect_span.get_tag("component") == "tornado"
        assert redirect_span.get_tag("span.kind") == "server"

        success_span = traces[1][0]
        assert "tornado-web" == success_span.service
        assert "tornado.request" == success_span.name
        assert "web" == success_span.span_type
        assert "tests.contrib.tornado.web.app.SuccessHandler" == success_span.resource
        assert "GET" == success_span.get_tag("http.method")
        assert_span_http_status_code(success_span, 200)
        assert self.get_url("/success/") == success_span.get_tag(http.URL)
        assert 0 == success_span.error
        assert success_span.get_tag("component") == "tornado"
        assert success_span.get_tag("span.kind") == "server"

    def test_static_handler(self):
        # it should trace the access to static files
        response = self.fetch("/statics/empty.txt")
        assert 200 == response.code
        assert "Static file\n" == response.body.decode("utf-8")

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert_is_measured(request_span)
        assert "tornado-web" == request_span.service
        assert "tornado.request" == request_span.name
        assert "web" == request_span.span_type
        assert "tornado.web.StaticFileHandler" == request_span.resource
        assert "GET" == request_span.get_tag("http.method")
        assert_span_http_status_code(request_span, 200)
        assert self.get_url("/statics/empty.txt") == request_span.get_tag(http.URL)
        assert 0 == request_span.error
        assert request_span.get_tag("component") == "tornado"
        assert request_span.get_tag("span.kind") == "server"

    def test_propagation(self):
        # it should trace a handler that returns 200 with a propagated context
        headers = {"x-datadog-trace-id": "1234", "x-datadog-parent-id": "4567", "x-datadog-sampling-priority": "2"}
        response = self.fetch("/success/", headers=headers)
        assert 200 == response.code

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]

        # simple sanity check on the span
        assert "tornado.request" == request_span.name
        assert_span_http_status_code(request_span, 200)
        assert self.get_url("/success/") == request_span.get_tag(http.URL)
        assert 0 == request_span.error

        # check propagation
        assert 1234 == request_span.trace_id
        assert 4567 == request_span.parent_id
        assert 2 == request_span.get_metric(_SAMPLING_PRIORITY_KEY)
        assert request_span.get_tag("component") == "tornado"
        assert request_span.get_tag("span.kind") == "server"

    # Opentracing support depends on new AsyncioScopeManager
    # See: https://github.com/opentracing/opentracing-python/pull/118
    @pytest.mark.skipif(
        tornado.version_info >= (5, 0), reason="Opentracing ScopeManager not available for Tornado >= 5"
    )
    def test_success_handler_ot(self):
        """OpenTracing version of test_success_handler."""
        from opentracing.scope_managers.tornado import TornadoScopeManager

        ot_tracer = init_tracer("tornado_svc", self.tracer, scope_manager=TornadoScopeManager())

        with ot_tracer.start_active_span("tornado_op"):
            response = self.fetch("/success/")
            assert 200 == response.code

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 2 == len(traces[0])
        # dd_span will start and stop before the ot_span finishes
        ot_span, dd_span = traces[0]

        # confirm the parenting
        assert ot_span.parent_id is None
        assert dd_span.parent_id == ot_span.span_id

        assert ot_span.name == "tornado_op"
        assert ot_span.service == "tornado_svc"

        assert_is_measured(dd_span)
        assert "tornado-web" == dd_span.service
        assert "tornado.request" == dd_span.name
        assert "web" == dd_span.span_type
        assert "tests.contrib.tornado.web.app.SuccessHandler" == dd_span.resource
        assert "GET" == dd_span.get_tag("http.method")
        assert_span_http_status_code(dd_span, 200)
        assert self.get_url("/success/") == dd_span.get_tag(http.URL)
        assert 0 == dd_span.error
        assert dd_span.get_tag("component") == "tornado"
        assert dd_span.get_tag("span.kind") == "server"


class TestNoPropagationTornadoWebViaSetting(TornadoTestCase):
    """
    Ensure that Tornado web handlers are properly traced and are ignoring propagated HTTP headers when disabled.
    """

    def get_settings(self):
        # distributed_tracing needs to be disabled manually
        return {
            "datadog_trace": {
                "distributed_tracing": False,
            },
        }

    def test_no_propagation(self):
        # it should not propagate the HTTP context
        headers = {
            "x-datadog-trace-id": "1234",
            "x-datadog-parent-id": "4567",
            "x-datadog-sampling-priority": "2",
            "x-datadog-origin": "synthetics",
        }
        response = self.fetch("/success/", headers=headers)
        assert 200 == response.code

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]

        # simple sanity check on the span
        assert "tornado.request" == request_span.name
        assert_span_http_status_code(request_span, 200)
        assert self.get_url("/success/") == request_span.get_tag(http.URL)
        assert 0 == request_span.error

        # check non-propagation
        assert request_span.trace_id != 1234
        assert request_span.parent_id != 4567
        assert request_span.get_metric(_SAMPLING_PRIORITY_KEY) != 2
        assert request_span.get_tag(_ORIGIN_KEY) != "synthetics"
        assert request_span.get_tag("component") == "tornado"
        assert request_span.get_tag("span.kind") == "server"


class TestNoPropagationTornadoWebViaConfig(TornadoTestCase):
    """
    Ensure that Tornado web handlers are properly traced and are ignoring propagated HTTP headers when disabled.
    """

    def test_no_propagation_via_int_config(self):
        original = config.tornado.distributed_tracing
        config.tornado.distributed_tracing = False
        # it should not propagate the HTTP context
        headers = {
            "x-datadog-trace-id": "1234",
            "x-datadog-parent-id": "4567",
            "x-datadog-sampling-priority": "2",
            "x-datadog-origin": "synthetics",
        }
        response = self.fetch("/success/", headers=headers)
        assert 200 == response.code

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]

        # simple sanity check on the span
        assert "tornado.request" == request_span.name
        assert_span_http_status_code(request_span, 200)
        assert self.get_url("/success/") == request_span.get_tag(http.URL)
        assert 0 == request_span.error

        # check non-propagation
        assert request_span.trace_id != 1234
        assert request_span.parent_id != 4567
        assert request_span.get_metric(_SAMPLING_PRIORITY_KEY) != 2
        assert request_span.get_tag(_ORIGIN_KEY) != "synthetics"
        assert request_span.get_tag("component") == "tornado"
        assert request_span.get_tag("span.kind") == "server"

        config.tornado.distributed_tracing = original

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TORNADO_DISTRIBUTED_TRACING="False"))
    def test_no_propagation_via_env_var(self):
        # it should not propagate the HTTP context
        headers = {
            "x-datadog-trace-id": "1234",
            "x-datadog-parent-id": "4567",
            "x-datadog-sampling-priority": "2",
            "x-datadog-origin": "synthetics",
        }
        response = self.fetch("/success/", headers=headers)
        assert 200 == response.code

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]

        # simple sanity check on the span
        assert "tornado.request" == request_span.name
        assert_span_http_status_code(request_span, 200)
        assert self.get_url("/success/") == request_span.get_tag(http.URL)
        assert 0 == request_span.error

        # check non-propagation
        assert request_span.trace_id != 1234
        assert request_span.parent_id != 4567
        assert request_span.get_metric(_SAMPLING_PRIORITY_KEY) != 2
        assert request_span.get_tag(_ORIGIN_KEY) != "synthetics"
        assert request_span.get_tag("component") == "tornado"
        assert request_span.get_tag("span.kind") == "server"


class TestCustomTornadoWeb(TornadoTestCase):
    """
    Ensure that Tornado web handlers are properly traced when using
    a custom default handler.
    """

    def get_settings(self):
        return {
            "default_handler_class": CustomDefaultHandler,
            "default_handler_args": dict(status_code=400),
        }

    def test_custom_default_handler(self):
        # it should trace any call that uses a custom default handler
        response = self.fetch("/custom_handler/")
        assert 400 == response.code

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert "tornado-web" == request_span.service
        assert "tornado.request" == request_span.name
        assert "web" == request_span.span_type
        assert "tests.contrib.tornado.web.app.CustomDefaultHandler" == request_span.resource
        assert "GET" == request_span.get_tag("http.method")
        assert_span_http_status_code(request_span, 400)
        assert self.get_url("/custom_handler/") == request_span.get_tag(http.URL)
        assert 0 == request_span.error
        assert request_span.get_tag("component") == "tornado"
        assert request_span.get_tag("span.kind") == "server"


class TestSchematization(TornadoTestCase):
    """
    Ensure that schematization works for both service name and operations.
    """

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_service_name_schematization_default(self):
        self.fetch("/success/")
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert "mysvc" == request_span.service, "Expected 'mysvc' but got {}".format(request_span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_service_name_schematization_v0(self):
        self.fetch("/success/")
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert "mysvc" == request_span.service, "Expected 'mysvc' but got {}".format(request_span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_service_name_schematization_v1(self):
        self.fetch("/success/")
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert "mysvc" == request_span.service, "Expected 'mysvc' but got {}".format(request_span.service)

    @TracerTestCase.run_in_subprocess()
    def test_unspecified_service_name_schematization_default(self):
        self.fetch("/success/")
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert "tornado-web" == request_span.service, "Expected 'tornado-web' but got {}".format(request_span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_unspecified_service_name_schematization_v0(self):
        self.fetch("/success/")
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert "tornado-web" == request_span.service, "Expected 'tornado-web' but got {}".format(request_span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_unspecified_service_name_schematization_v1(self):
        self.fetch("/success/")
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert DEFAULT_SPAN_SERVICE_NAME == request_span.service, "Expected '{}' but got {}".format(
            DEFAULT_SPAN_SERVICE_NAME, request_span.service
        )

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_unspecified_operation_name_schematization_v0(self):
        self.fetch("/success/")
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert "tornado.request" == request_span.name, "Expected 'tornado.request' but got {}".format(request_span.name)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_unspecified_operation_name_schematization_v1(self):
        self.fetch("/success/")
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert "http.server.request" == request_span.name, "Expected 'http.server.request' but got {}".format(
            request_span.name
        )


class TestAPIGatewayTracing(TornadoTestCase):
    """
    Ensure that Tornado web handlers are properly traced when API Gateway is involved
    """

    def test_inferred_spans_api_gateway(self):
        headers = {
            "x-dd-proxy": "aws-apigateway",
            "x-dd-proxy-request-time-ms": "1736973768000",
            "x-dd-proxy-path": "/",
            "x-dd-proxy-httpmethod": "GET",
            "x-dd-proxy-domain-name": "local",
            "x-dd-proxy-stage": "stage",
        }

        distributed_headers = {
            "x-dd-proxy": "aws-apigateway",
            "x-dd-proxy-request-time-ms": "1736973768000",
            "x-dd-proxy-path": "/",
            "x-dd-proxy-httpmethod": "GET",
            "x-dd-proxy-domain-name": "local",
            "x-dd-proxy-stage": "stage",
            "x-datadog-trace-id": "1",
            "x-datadog-parent-id": "2",
            "x-datadog-origin": "rum",
            "x-datadog-sampling-priority": "2",
        }

        for setting_enabled in [False, True]:
            config._inferred_proxy_services_enabled = setting_enabled
            for test_headers in [distributed_headers, headers]:
                for test_endpoint in [
                    {
                        "endpoint": "/success/",
                        "status": 200,
                        "resource_name": "tests.contrib.tornado.web.app.SuccessHandler",
                    },
                    {
                        "endpoint": "/exception/",
                        "status": 500,
                        "resource_name": "tests.contrib.tornado.web.app.ExceptionHandler",
                    },
                    {
                        "endpoint": "/status_code/500",
                        "status": 500,
                        "resource_name": "tests.contrib.tornado.web.app.ResponseStatusHandler",
                    },
                ]:
                    self.fetch(test_endpoint["endpoint"], headers=test_headers)
                    traces = self.pop_traces()
                    if setting_enabled:
                        aws_gateway_span = traces[0][0]
                        web_span = traces[0][1]

                        assert_web_and_inferred_aws_api_gateway_span_data(
                            aws_gateway_span,
                            web_span,
                            web_span_name="tornado.request",
                            web_span_component="tornado",
                            web_span_service_name="tornado-web",
                            web_span_resource=test_endpoint["resource_name"],
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
