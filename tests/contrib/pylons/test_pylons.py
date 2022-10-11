import json
import logging
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
from ddtrace.ext import user
from ddtrace.internal import _context
from ddtrace.internal.compat import urlencode
from tests.appsec.test_processor import RULES_GOOD_PATH
from tests.opentracer.utils import init_tracer
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured
from tests.utils import assert_span_http_status_code
from tests.utils import override_env
from tests.utils import override_global_config


class PylonsTestCase(TracerTestCase):
    """Pylons Test Controller that is used to test specific
    cases defined in the Pylons controller. To test a new behavior,
    add a new action in the `app.controllers.root` module.
    """

    conf_dir = os.path.dirname(os.path.abspath(__file__))

    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog):
        self._caplog = caplog

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
        with override_global_config(dict(_appsec_enabled=True)):
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
                if config._appsec:
                    assert _context.get_item("http.request.uri", span=span) == "http://localhost:80/?" + query_string
            else:
                assert http.QUERY_STRING not in span.get_tags()
                if config._appsec:
                    assert _context.get_item("http.request.uri", span=span) == "http://localhost:80/"
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

    def test_appsec_http_raw_uri(self):
        with self.override_global_config(dict(appsec_enabled=True)):
            self.test_query_string()
            self.test_multi_query_string()
            self.test_query_string_trace()
            self.test_multi_query_string_trace()

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
            "user-agent": "Agent/10.10",
        }
        config.pylons.http.trace_headers(["my-header"])
        res = self.app.get(url_for(controller="root", action="index"), headers=headers)
        assert res.status == 200
        spans = self.pop_spans()
        assert spans[0].get_tag("http.request.headers.my-header") == "value"
        assert spans[0].get_tag(http.USER_AGENT) == "Agent/10.10"

    def test_response_headers(self):
        config.pylons.http.trace_headers(["content-length", "custom-header"])
        res = self.app.get(url_for(controller="root", action="response_headers"))
        assert res.status == 200
        spans = self.pop_spans()
        # Pylons 0.96 and below don't report the content length
        if pylons.__version__ > (0, 9, 6):
            assert spans[0].get_tag("http.response.headers.content-length") == "2"
        assert spans[0].get_tag("http.response.headers.custom-header") == "value"

    def test_pylons_cookie_sql_injection(self):
        with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
            self.tracer._appsec_enabled = True
            # Hack: need to pass an argument to configure so that the processors are recreated
            self.tracer.configure(api_version="v0.4")
            self.app.cookies = {"attack": "w00tw00t.at.isc.sans.dfind"}
            self.app.get(url_for(controller="root", action="index"))

            spans = self.pop_spans()
            root_span = spans[0]

            appsec_json = root_span.get_tag("_dd.appsec.json")
            assert "triggers" in json.loads(appsec_json if appsec_json else "{}")

            span = _context.get_item("http.request.cookies", span=root_span)
            assert span["attack"] == "w00tw00t.at.isc.sans.dfind"

    def test_pylons_cookie(self):
        with override_global_config(dict(_appsec_enabled=True)):
            self.tracer._appsec_enabled = True
            # Hack: need to pass an argument to configure so that the processors are recreated
            self.tracer.configure(api_version="v0.4")
            self.app.cookies = {"testingcookie_key": "testingcookie_value"}
            self.app.get(url_for(controller="root", action="index"))

            spans = self.pop_spans()
            root_span = spans[0]

            assert root_span.get_tag("_dd.appsec.json") is None
            span = _context.get_item("http.request.cookies", span=root_span)
            assert span["testingcookie_key"] == "testingcookie_value"

    def test_pylons_body_urlencoded(self):
        with self.override_global_config(dict(_appsec_enabled=True)):

            self.tracer.configure(api_version="v0.4")
            payload = urlencode({"mytestingbody_key": "mytestingbody_value"})
            response = self.app.post(
                url_for(controller="root", action="body"),
                params=payload,
                extra_environ={"CONTENT_TYPE": "application/x-www-form-urlencoded"},
            )
            assert response.status == 200

            spans = self.pop_spans()
            assert spans

            root_span = spans[0]
            assert root_span
            assert root_span.get_tag("_dd.appsec.json") is None

            span = dict(_context.get_item("http.request.body", span=root_span))
            assert span
            assert span["mytestingbody_key"] == "mytestingbody_value"

    def test_pylons_request_body_urlencoded_appsec_disabled_then_no_body(self):
        self.tracer._appsec_enabled = False
        # Hack: need to pass an argument to configure so that the processors are recreated
        self.tracer.configure(api_version="v0.4")
        payload = urlencode({"mytestingbody_key": "mytestingbody_value"})
        self.app.post(url_for(controller="root", action="index"), params=payload)

        spans = self.pop_spans()
        root_span = spans[0]

        assert root_span
        assert not _context.get_item("http.request.body", span=root_span)

    def test_pylons_body_urlencoded_attack(self):
        with self.override_global_config(dict(_appsec_enabled=True)):
            with override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
                self.tracer._appsec_enabled = True
                # Hack: need to pass an argument to configure so that the processors are recreated
                self.tracer.configure(api_version="v0.4")
                payload = urlencode({"attack": "1' or '1' = '1'"})
                self.app.post(url_for(controller="root", action="index"), params=payload)

                spans = self.pop_spans()
                assert spans

                root_span = spans[0]
                assert root_span

                appsec_json = root_span.get_tag("_dd.appsec.json")
                assert appsec_json
                assert "triggers" in json.loads(appsec_json if appsec_json else "{}")

                query = dict(_context.get_item("http.request.body", span=root_span))
                assert query == {"attack": "1' or '1' = '1'"}

    def test_pylons_body_json(self):
        with override_global_config(dict(_appsec_enabled=True)):
            # Hack: need to pass an argument to configure so that the processors are recreated
            self.tracer.configure(api_version="v0.4")
            payload = json.dumps({"mytestingbody_key": "mytestingbody_value"})
            response = self.app.post(
                url_for(controller="root", action="body"),
                params=payload,
                extra_environ={"CONTENT_TYPE": "application/json"},
            )
            assert response.status == 200

            spans = self.pop_spans()
            assert spans

            root_span = spans[0]
            assert root_span
            assert root_span.get_tag("_dd.appsec.json") is None

            span = dict(_context.get_item("http.request.body", span=root_span))
            assert span
            assert span["mytestingbody_key"] == "mytestingbody_value"

    def test_pylons_body_json_attack(self):
        with self.override_global_config(dict(_appsec_enabled=True)):
            with override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
                self.tracer._appsec_enabled = True
                # Hack: need to pass an argument to configure so that the processors are recreated
                self.tracer.configure(api_version="v0.4")
                payload = json.dumps({"attack": "1' or '1' = '1'"})
                self.app.post(
                    url_for(controller="root", action="index"),
                    params=payload,
                    extra_environ={"CONTENT_TYPE": "application/json"},
                )

                spans = self.pop_spans()
                assert spans

                root_span = spans[0]
                appsec_json = root_span.get_tag("_dd.appsec.json")
                assert appsec_json
                assert "triggers" in json.loads(appsec_json if appsec_json else "{}")

                span = dict(_context.get_item("http.request.body", span=root_span))
                assert span
                assert span == {"attack": "1' or '1' = '1'"}

    def test_pylons_body_xml(self):
        with override_global_config(dict(_appsec_enabled=True)):
            # Hack: need to pass an argument to configure so that the processors are recreated
            self.tracer.configure(api_version="v0.4")
            payload = "<mytestingbody_key>mytestingbody_value</mytestingbody_key>"

            response = self.app.post(
                url_for(controller="root", action="body"),
                params=payload,
                extra_environ={"CONTENT_TYPE": "application/xml"},
            )
            assert response.status == 200

            spans = self.pop_spans()
            assert spans

            root_span = spans[0]
            assert root_span
            assert root_span.get_tag("_dd.appsec.json") is None

            span = dict(_context.get_item("http.request.body", span=root_span))
            assert span
            assert span["mytestingbody_key"] == "mytestingbody_value"

    def test_pylons_body_xml_attack(self):
        with override_global_config(dict(_appsec_enabled=True)):
            # Hack: need to pass an argument to configure so that the processors are recreated
            self.tracer.configure(api_version="v0.4")
            payload = "<attack>1' or '1' = '1'</attack>"
            self.app.post(
                url_for(controller="root", action="index"),
                params=payload,
                extra_environ={"CONTENT_TYPE": "application/xml"},
            )

            spans = self.pop_spans()
            assert spans

            root_span = spans[0]
            assert root_span
            assert root_span.get_tag("_dd.appsec.json") is None

            span = dict(_context.get_item("http.request.body", span=root_span))
            assert span
            assert span == {"attack": "1' or '1' = '1'"}

    def test_pylons_body_plain(self):
        with self.override_global_config(dict(_appsec_enabled=True)):
            self.tracer._appsec_enabled = True
            # Hack: need to pass an argument to configure so that the processors are recreated
            self.tracer.configure(api_version="v0.4")
            payload = "foo=bar"

            response = self.app.post(
                url_for(controller="root", action="body"), params=payload, extra_environ={"CONTENT_TYPE": "text/plain"}
            )
            assert response.status == 200

            spans = self.pop_spans()
            assert spans

            root_span = spans[0]
            assert root_span
            assert root_span.get_tag("_dd.appsec.json") is None

            span = _context.get_item("http.request.body", span=root_span)
            assert span
            assert span == "foo=bar"

    def test_pylons_body_plain_attack(self):
        with self.override_global_config(dict(_appsec_enabled=True)):
            self.tracer._appsec_enabled = True
            # Hack: need to pass an argument to configure so that the processors are recreated
            self.tracer.configure(api_version="v0.4")
            payload = "1' or '1' = '1'"
            self.app.post(
                url_for(controller="root", action="body"),
                params=payload,
                extra_environ={"CONTENT_TYPE": "text/plain"},
            )

            spans = self.pop_spans()
            assert spans

            root_span = spans[0]
            appsec_json = root_span.get_tag("_dd.appsec.json")
            assert "triggers" in json.loads(appsec_json if appsec_json else "{}")

            span = _context.get_item("http.request.body", span=root_span)
            assert span
            assert span == "1' or '1' = '1'"

    def test_request_method_get_200(self):
        res = self.app.get(url_for(controller="root", action="index"))
        assert res.status == 200
        spans = self.pop_spans()
        assert spans[0].get_tag("http.method") == "GET"

    def test_request_method_get_404(self):
        with pytest.raises(Exception):
            res = self.app.get(url_for(controller="root", action="index") + "nonexistent-path")
            assert res.status == 404
        spans = self.pop_spans()
        assert spans[0].get_tag("http.method") == "GET"

    def test_request_method_post_200(self):
        res = self.app.post(url_for(controller="root", action="index"))
        assert res.status == 200
        spans = self.pop_spans()
        assert spans[0].get_tag("http.method") == "POST"

    def test_pylon_path_params(self):
        with override_global_config(dict(_appsec_enabled=True)):
            self.tracer._appsec_enabled = False
            self.tracer.configure(api_version="v0.4")
            self.app.get("/path-params/2022/july/")

            spans = self.pop_spans()
            root_span = spans[0]
            assert root_span.get_tag("_dd.appsec.json") is None
            path_params = _context.get_item("http.request.path_params", span=root_span)

            assert path_params["month"] == "july"
            assert path_params["year"] == "2022"

    def test_pylon_path_params_attack(self):
        with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
            self.tracer._appsec_enabled = True

            self.tracer.configure(api_version="v0.4")
            self.app.get("/path-params/2022/w00tw00t.at.isc.sans.dfind/")

            spans = self.pop_spans()
            root_span = spans[0]

            appsec_json = root_span.get_tag("_dd.appsec.json")
            assert "triggers" in json.loads(appsec_json if appsec_json else "{}")

            query = dict(_context.get_item("http.request.path_params", span=root_span))
            assert query["month"] == "w00tw00t.at.isc.sans.dfind"
            assert query["year"] == "2022"

    def test_pylons_useragent(self):
        self.app.get(url_for(controller="root", action="index"), headers={"HTTP_USER_AGENT": "test/1.2.3"})
        spans = self.pop_spans()
        root_span = spans[0]
        assert root_span.get_tag(http.USER_AGENT) == "test/1.2.3"

    def test_pylons_body_json_empty_body(self):
        """
        "Failed to parse request body"
        """
        with self._caplog.at_level(logging.WARNING), override_global_config(dict(_appsec_enabled=True)):
            # Hack: need to pass an argument to configure so that the processors are recreated
            self.tracer.configure(api_version="v0.4")
            payload = ""

            self.app.post(
                url_for(controller="root", action="body"),
                params=payload,
                extra_environ={"CONTENT_TYPE": "application/json"},
            )
            assert "Failed to parse request body" in self._caplog.text

    def test_pylon_get_user(self):
        self.app.get("/identify")

        spans = self.pop_spans()
        root_span = spans[0]

        # Values defined in tests/contrib/pylons/app/controllers/root.py::RootController::identify
        assert root_span.get_tag(user.ID) == "usr.id"
        assert root_span.get_tag(user.EMAIL) == "usr.email"
        assert root_span.get_tag(user.SESSION_ID) == "usr.session_id"
        assert root_span.get_tag(user.NAME) == "usr.name"
        assert root_span.get_tag(user.ROLE) == "usr.role"
        assert root_span.get_tag(user.SCOPE) == "usr.scope"
