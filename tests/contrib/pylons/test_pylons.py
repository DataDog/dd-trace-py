import os

from paste import fixture
from paste.deploy import loadapp
import pylons
import pytest
from routes import url_for

from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.contrib.pylons import PylonsTraceMiddleware
from ddtrace.ext import http
from tests.opentracer.utils import init_tracer
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured
from tests.utils import assert_span_http_status_code


class PylonsTestCase(TracerTestCase):
    """Pylons Test Controller that is used to test specific
    cases defined in the Pylons controller. To test a new behavior,
    add a new action in the `app.controllers.root` module.
    """

    conf_dir = os.path.dirname(os.path.abspath(__file__))

    def setUp(self):
        super(PylonsTestCase, self).setUp()
        # initialize a real traced Pylons app
        wsgiapp = loadapp("config:test.ini", relative_to=PylonsTestCase.conf_dir)
        self._wsgiapp = wsgiapp
        app = PylonsTraceMiddleware(wsgiapp, self.tracer, service="web")
        self.app = fixture.TestApp(app)

    def test_controller_exception(self):
        """Ensure exceptions thrown in controllers can be handled.

        No error tags should be set in the span.
        """
        from .app.middleware import ExceptionToSuccessMiddleware

        wsgiapp = ExceptionToSuccessMiddleware(self._wsgiapp)
        app = PylonsTraceMiddleware(wsgiapp, self.tracer, service="web")

        app = fixture.TestApp(app)
        app.get(url_for(controller="root", action="raise_exception"))

        spans = self.pop_spans()

        assert spans, spans
        assert len(spans) == 1
        span = spans[0]

        assert_is_measured(span)
        assert span.service == "web"
        assert span.resource == "root.raise_exception"
        assert span.error == 0
        assert span.get_tag(http.URL) == "http://localhost:80/raise_exception"
        assert_span_http_status_code(span, 200)
        assert http.QUERY_STRING not in span.get_tags()
        assert span.get_tag(ERROR_MSG) is None
        assert span.get_tag(ERROR_TYPE) is None
        assert span.get_tag(ERROR_STACK) is None
        assert span.span_type == "web"

    def test_mw_exc_success(self):
        """Ensure exceptions can be properly handled by other middleware.

        No error should be reported in the span.
        """
        from .app.middleware import ExceptionMiddleware
        from .app.middleware import ExceptionToSuccessMiddleware

        wsgiapp = ExceptionMiddleware(self._wsgiapp)
        wsgiapp = ExceptionToSuccessMiddleware(wsgiapp)
        app = PylonsTraceMiddleware(wsgiapp, self.tracer, service="web")
        app = fixture.TestApp(app)

        app.get(url_for(controller="root", action="index"))

        spans = self.pop_spans()

        assert spans, spans
        assert len(spans) == 1
        span = spans[0]

        assert_is_measured(span)
        assert span.service == "web"
        assert span.resource == "None.None"
        assert span.error == 0
        assert span.get_tag(http.URL) == "http://localhost:80/"
        assert_span_http_status_code(span, 200)
        assert span.get_tag(ERROR_MSG) is None
        assert span.get_tag(ERROR_TYPE) is None
        assert span.get_tag(ERROR_STACK) is None

    def test_middleware_exception(self):
        """Ensure exceptions raised in middleware are properly handled.

        Uncaught exceptions should result in error tagged spans.
        """
        from .app.middleware import ExceptionMiddleware

        wsgiapp = ExceptionMiddleware(self._wsgiapp)
        app = PylonsTraceMiddleware(wsgiapp, self.tracer, service="web")
        app = fixture.TestApp(app)

        with pytest.raises(Exception):
            app.get(url_for(controller="root", action="index"))

        spans = self.pop_spans()

        assert spans, spans
        assert len(spans) == 1
        span = spans[0]

        assert_is_measured(span)
        assert span.service == "web"
        assert span.resource == "None.None"
        assert span.error == 1
        assert span.get_tag(http.URL) == "http://localhost:80/"
        assert_span_http_status_code(span, 500)
        assert span.get_tag(ERROR_MSG) == "Middleware exception"
        assert span.get_tag(ERROR_TYPE) == "exceptions.Exception"
        assert span.get_tag(ERROR_STACK)

    def test_exc_success(self):
        from .app.middleware import ExceptionToSuccessMiddleware

        wsgiapp = ExceptionToSuccessMiddleware(self._wsgiapp)
        app = PylonsTraceMiddleware(wsgiapp, self.tracer, service="web")
        app = fixture.TestApp(app)

        app.get(url_for(controller="root", action="raise_exception"))

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]

        assert_is_measured(span)
        assert span.service == "web"
        assert span.resource == "root.raise_exception"
        assert span.error == 0
        assert span.get_tag(http.URL) == "http://localhost:80/raise_exception"
        assert_span_http_status_code(span, 200)
        assert span.get_tag(ERROR_MSG) is None
        assert span.get_tag(ERROR_TYPE) is None
        assert span.get_tag(ERROR_STACK) is None

    def test_exc_client_failure(self):
        from .app.middleware import ExceptionToClientErrorMiddleware

        wsgiapp = ExceptionToClientErrorMiddleware(self._wsgiapp)
        app = PylonsTraceMiddleware(wsgiapp, self.tracer, service="web")
        app = fixture.TestApp(app)

        app.get(url_for(controller="root", action="raise_exception"), status=404)

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]

        assert_is_measured(span)
        assert span.service == "web"
        assert span.resource == "root.raise_exception"
        assert span.error == 0
        assert span.get_tag(http.URL) == "http://localhost:80/raise_exception"
        assert_span_http_status_code(span, 404)
        assert span.get_tag(ERROR_MSG) is None
        assert span.get_tag(ERROR_TYPE) is None
        assert span.get_tag(ERROR_STACK) is None

    def test_success_200(self, query_string=""):
        if query_string:
            fqs = "?" + query_string
        else:
            fqs = ""
        res = self.app.get(url_for(controller="root", action="index") + fqs)
        assert res.status == 200

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]

        assert_is_measured(span)
        assert span.service == "web"
        assert span.resource == "root.index"
        assert_span_http_status_code(span, 200)
        if config.pylons.trace_query_string:
            assert span.get_tag(http.QUERY_STRING) == query_string
        else:
            assert http.QUERY_STRING not in span.get_tags()
        assert span.error == 0

    def test_query_string(self):
        return self.test_success_200("foo=bar")

    def test_multi_query_string(self):
        return self.test_success_200("foo=bar&foo=baz&x=y")

    def test_query_string_trace(self):
        with self.override_http_config("pylons", dict(trace_query_string=True)):
            return self.test_success_200("foo=bar")

    def test_multi_query_string_trace(self):
        with self.override_http_config("pylons", dict(trace_query_string=True)):
            return self.test_success_200("foo=bar&foo=baz&x=y")

    def test_analytics_global_on_integration_default(self):
        """
        When making a request
            When an integration trace search is not event sample rate is not set and globally trace search is enabled
                We expect the root span to have the appropriate tag
        """
        with self.override_global_config(dict(analytics_enabled=True)):
            res = self.app.get(url_for(controller="root", action="index"))
            self.assertEqual(res.status, 200)

        self.assert_structure(dict(name="pylons.request", metrics={ANALYTICS_SAMPLE_RATE_KEY: 1.0}))

    def test_analytics_global_on_integration_on(self):
        """
        When making a request
            When an integration trace search is enabled and sample rate is set and globally trace search is enabled
                We expect the root span to have the appropriate tag
        """
        with self.override_global_config(dict(analytics_enabled=True)):
            with self.override_config("pylons", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
                res = self.app.get(url_for(controller="root", action="index"))
                self.assertEqual(res.status, 200)

        self.assert_structure(dict(name="pylons.request", metrics={ANALYTICS_SAMPLE_RATE_KEY: 0.5}))

    def test_analytics_global_off_integration_default(self):
        """
        When making a request
            When an integration trace search is not set and sample rate is set and globally trace search is disabled
                We expect the root span to not include tag
        """
        with self.override_global_config(dict(analytics_enabled=False)):
            res = self.app.get(url_for(controller="root", action="index"))
            self.assertEqual(res.status, 200)

        root = self.get_root_span()
        self.assertIsNone(root.get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_global_off_integration_on(self):
        """
        When making a request
            When an integration trace search is enabled and sample rate is set and globally trace search is disabled
                We expect the root span to have the appropriate tag
        """
        with self.override_global_config(dict(analytics_enabled=False)):
            with self.override_config("pylons", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
                res = self.app.get(url_for(controller="root", action="index"))
                self.assertEqual(res.status, 200)

        self.assert_structure(dict(name="pylons.request", metrics={ANALYTICS_SAMPLE_RATE_KEY: 0.5}))

    def test_template_render(self):
        res = self.app.get(url_for(controller="root", action="render"))
        assert res.status == 200

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 2
        request = spans[0]
        template = spans[1]

        assert request.service == "web"
        assert request.resource == "root.render"
        assert_span_http_status_code(request, 200)
        assert request.error == 0

        assert template.service == "web"
        assert template.resource == "pylons.render"
        assert template.get_tag("template.name") == "/template.mako"
        assert template.error == 0

    def test_template_render_exception(self):
        with pytest.raises(Exception):
            self.app.get(url_for(controller="root", action="render_exception"))

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 2
        request = spans[0]
        template = spans[1]

        assert request.service == "web"
        assert request.resource == "root.render_exception"
        assert_span_http_status_code(request, 500)
        assert request.error == 1

        assert template.service == "web"
        assert template.resource == "pylons.render"
        assert template.get_tag("template.name") == "/exception.mako"
        assert template.error == 1
        assert template.get_tag("error.msg") == "integer division or modulo by zero"
        assert "ZeroDivisionError: integer division or modulo by zero" in template.get_tag("error.stack")

    def test_failure_500(self):
        with pytest.raises(Exception):
            self.app.get(url_for(controller="root", action="raise_exception"))

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]

        assert span.service == "web"
        assert span.resource == "root.raise_exception"
        assert span.error == 1
        assert_span_http_status_code(span, 500)
        assert span.get_tag("error.msg") == "Ouch!"
        assert span.get_tag(http.URL) == "http://localhost:80/raise_exception"
        assert "Exception: Ouch!" in span.get_tag("error.stack")

    def test_failure_500_with_wrong_code(self):
        with pytest.raises(Exception):
            self.app.get(url_for(controller="root", action="raise_wrong_code"))

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]

        assert span.service == "web"
        assert span.resource == "root.raise_wrong_code"
        assert span.error == 1
        assert_span_http_status_code(span, 500)
        assert span.get_tag(http.URL) == "http://localhost:80/raise_wrong_code"
        assert span.get_tag("error.msg") == "Ouch!"
        assert "Exception: Ouch!" in span.get_tag("error.stack")

    def test_failure_500_with_custom_code(self):
        with pytest.raises(Exception):
            self.app.get(url_for(controller="root", action="raise_custom_code"))

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]

        assert span.service == "web"
        assert span.resource == "root.raise_custom_code"
        assert span.error == 1
        assert_span_http_status_code(span, 512)
        assert span.get_tag(http.URL) == "http://localhost:80/raise_custom_code"
        assert span.get_tag("error.msg") == "Ouch!"
        assert "Exception: Ouch!" in span.get_tag("error.stack")

    def test_failure_500_with_code_method(self):
        with pytest.raises(Exception):
            self.app.get(url_for(controller="root", action="raise_code_method"))

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]

        assert span.service == "web"
        assert span.resource == "root.raise_code_method"
        assert span.error == 1
        assert_span_http_status_code(span, 500)
        assert span.get_tag(http.URL) == "http://localhost:80/raise_code_method"
        assert span.get_tag("error.msg") == "Ouch!"

    def test_distributed_tracing_default(self):
        # ensure by default, distributed tracing is enabled
        headers = {
            "x-datadog-trace-id": "100",
            "x-datadog-parent-id": "42",
            "x-datadog-sampling-priority": "2",
        }
        res = self.app.get(url_for(controller="root", action="index"), headers=headers)
        assert res.status == 200

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]

        assert span.trace_id == 100
        assert span.parent_id == 42
        assert span.get_metric(SAMPLING_PRIORITY_KEY) == 2

    def test_distributed_tracing_disabled_via_int_config(self):
        config.pylons["distributed_tracing"] = False
        headers = {
            "x-datadog-trace-id": "100",
            "x-datadog-parent-id": "42",
            "x-datadog-sampling-priority": "2",
        }

        res = self.app.get(url_for(controller="root", action="index"), headers=headers)
        assert res.status == 200

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]

        assert span.trace_id != 100
        assert span.parent_id != 42
        assert span.get_metric(SAMPLING_PRIORITY_KEY) != 2

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_PYLONS_DISTRIBUTED_TRACING="False"))
    def test_distributed_tracing_disabled_via_env_var(self):
        headers = {
            "x-datadog-trace-id": "100",
            "x-datadog-parent-id": "42",
            "x-datadog-sampling-priority": "2",
        }

        res = self.app.get(url_for(controller="root", action="index"), headers=headers)
        assert res.status == 200

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]

        assert span.trace_id != 100
        assert span.parent_id != 42
        assert span.get_metric(SAMPLING_PRIORITY_KEY) != 2

    def test_success_200_ot(self):
        """OpenTracing version of test_success_200."""
        ot_tracer = init_tracer("pylons_svc", self.tracer)

        with ot_tracer.start_active_span("pylons_get"):
            res = self.app.get(url_for(controller="root", action="index"))
            assert res.status == 200

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 2
        ot_span, dd_span = spans

        # confirm the parenting
        assert ot_span.parent_id is None
        assert dd_span.parent_id == ot_span.span_id

        assert ot_span.name == "pylons_get"
        assert ot_span.service == "pylons_svc"

        assert dd_span.service == "web"
        assert dd_span.resource == "root.index"
        assert_span_http_status_code(dd_span, 200)
        assert dd_span.get_tag(http.URL) == "http://localhost:80/"
        assert dd_span.error == 0

    def test_request_headers(self):
        headers = {
            "my-header": "value",
        }
        config.pylons.http.trace_headers(["my-header"])
        res = self.app.get(url_for(controller="root", action="index"), headers=headers)
        assert res.status == 200
        spans = self.pop_spans()
        assert spans[0].get_tag("http.request.headers.my-header") == "value"

    def test_response_headers(self):
        config.pylons.http.trace_headers(["content-length", "custom-header"])
        res = self.app.get(url_for(controller="root", action="response_headers"))
        assert res.status == 200
        spans = self.pop_spans()
        # Pylons 0.96 and below don't report the content length
        if pylons.__version__ > (0, 9, 6):
            assert spans[0].get_tag("http.response.headers.content-length") == "2"
        assert spans[0].get_tag("http.response.headers.custom-header") == "value"
