import json
import logging

from flask import Response
import pytest

from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._trace_utils import block_request_if_user_blocked
from ddtrace.contrib.sqlite3.patch import patch
from ddtrace.ext import http
from ddtrace.internal import constants
from ddtrace.internal import core
import tests.appsec.rules as rules
from tests.contrib.flask import BaseFlaskTestCase
from tests.utils import override_env
from tests.utils import override_global_config


_BLOCKED_USER = "123456"
_ALLOWED_USER = "111111"


def get_response_body(response):
    if hasattr(response, "text"):
        return response.text
    return response.data.decode("utf-8")


class FlaskAppSecTestCase(BaseFlaskTestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog):
        self._caplog = caplog

    def setUp(self):
        super(FlaskAppSecTestCase, self).setUp()
        patch()

    def _aux_appsec_prepare_tracer(self, appsec_enabled=True):
        self.tracer._asm_enabled = appsec_enabled
        # Hack: need to pass an argument to configure so that the processors are recreated
        self.tracer.configure(api_version="v0.4")

    def test_flask_client_ip_header_set_by_env_var_valid(self):
        with override_global_config(dict(_asm_enabled=True, client_ip_header="X-Use-This")):
            self.client.get("/?a=1&b&c=d", headers={"HTTP_X_CLIENT_IP": "8.8.8.8", "X-Use-This": "4.4.4.4"})
            spans = self.pop_spans()
            root_span = spans[0]
            assert root_span.get_tag(http.CLIENT_IP) == "4.4.4.4"

    def test_flask_body_json_empty_body_does_not_log_warning(self):
        with self._caplog.at_level(logging.DEBUG), override_global_config(dict(_asm_enabled=True)):
            self._aux_appsec_prepare_tracer()
            self.client.get("/", content_type="application/json")
            assert "Failed to parse request body" not in self._caplog.text

    def test_flask_body_json_bad_logs_debug(self):
        with self._caplog.at_level(logging.DEBUG), override_global_config(dict(_asm_enabled=True)):
            self._aux_appsec_prepare_tracer()
            self.client.post("/", data="not valid json", content_type="application/json")
            assert "Failed to parse request body" in self._caplog.text

    def test_flask_body_json_bad_logs_not_warning(self):
        with self._caplog.at_level(logging.WARNING), override_global_config(dict(_asm_enabled=True)):
            self._aux_appsec_prepare_tracer()
            self.client.post("/", data="not valid json", content_type="application/json")
            assert "Failed to parse request body" not in self._caplog.text

    def test_flask_body_xml_bad_logs_warning(self):
        with self._caplog.at_level(logging.DEBUG), override_global_config(dict(_asm_enabled=True)):
            self._aux_appsec_prepare_tracer()
            self.client.post("/", data="bad xml", content_type="application/xml")
            assert "Failed to parse request body" in self._caplog.text

    def test_flask_body_xml_empty_logs_warning(self):
        with self._caplog.at_level(logging.DEBUG), override_global_config(dict(_asm_enabled=True)):
            self._aux_appsec_prepare_tracer()
            self.client.post("/", data="", content_type="application/xml")
            assert "Failed to parse request body" in self._caplog.text

    def flask_ipblock_nomatch_200_json(self, ip):
        @self.app.route("/")
        def route():
            return "OK", 200

        for ip in [rules._IP.MONITORED, rules._IP.BYPASS, rules._IP.DEFAULT]:
            with override_global_config(dict(_asm_enabled=True)), override_env(
                dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)
            ):
                self._aux_appsec_prepare_tracer()
                resp = self.client.get("/", headers={"X-Real-Ip": ip})
                root_span = self.pop_spans()[0]
                assert resp.status_code == 200
                assert not core.get_item("http.request.blocked", span=root_span)

    def test_flask_ipblock_nomatch_200_bypass(self):
        self.flask_ipblock_nomatch_200_json(rules._IP.BYPASS)

    def test_flask_ipblock_nomatch_200_monitor(self):
        self.flask_ipblock_nomatch_200_json(rules._IP.MONITORED)

    def test_flask_ipblock_nomatch_200_default(self):
        self.flask_ipblock_nomatch_200_json(rules._IP.DEFAULT)

    def test_flask_ipblock_match_403_json(self):
        with override_global_config(dict(_asm_enabled=True)), override_env(dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)):
            self._aux_appsec_prepare_tracer()
            resp = self.client.get("/foobar", headers={"X-Real-Ip": rules._IP.BLOCKED})
            assert resp.status_code == 403
            assert get_response_body(resp) == constants.BLOCKED_RESPONSE_JSON
            root_span = self.pop_spans()[0]
            assert root_span.get_tag(http.STATUS_CODE) == "403"
            assert root_span.get_tag(http.URL) == "http://localhost/foobar"
            assert root_span.get_tag(http.METHOD) == "GET"
            assert root_span.get_tag(http.USER_AGENT).startswith("werkzeug/")
            assert root_span.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
            assert root_span.get_tag(APPSEC.JSON)
            loaded = json.loads(root_span.get_tag(APPSEC.JSON))
            assert loaded["triggers"][0]["rule"]["id"] == "blk-001-001"
            assert root_span.get_tag("appsec.event") == "true"
            assert root_span.get_tag("appsec.blocked") == "true"

    def test_flask_ip_200_monitor(self):
        @self.app.route("/")
        def route():
            return "OK", 200

        with override_global_config(dict(_asm_enabled=True)), override_env(dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)):
            self._aux_appsec_prepare_tracer()
            resp = self.client.get("/?value=block_that_value", headers={"X-Real-Ip": rules._IP.MONITORED})
            assert resp.status_code == 200
            root_span = self.pop_spans()[0]
            assert root_span.get_tag(http.STATUS_CODE) == "200"
            assert root_span.get_tag(http.URL) == "http://localhost/?value=block_that_value"
            assert root_span.get_tag(http.METHOD) == "GET"
            assert root_span.get_tag(http.USER_AGENT).startswith("werkzeug/")
            assert root_span.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") != "text/json"
            # rule detected but non blocking
            assert root_span.get_tag(APPSEC.JSON)
            loaded = json.loads(root_span.get_tag(APPSEC.JSON))
            ids = sorted(t["rule"]["id"] for t in loaded["triggers"])
            assert ids == ["blk-001-010", "tst-421-001"]
            on_match = [t["rule"]["on_match"] for t in loaded["triggers"]]
            assert on_match == [["monitor"], ["monitor"]]
            assert root_span.get_tag("appsec.event") == "true"
            assert root_span.get_tag("appsec.blocked") is None

    def test_flask_ip_200_bypass(self):
        @self.app.route("/")
        def route():
            return "OK", 200

        with override_global_config(dict(_asm_enabled=True)), override_env(dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)):
            self._aux_appsec_prepare_tracer()
            resp = self.client.get("/?value=block_that_value", headers={"X-Real-Ip": rules._IP.BYPASS})
            assert resp.status_code == 200
            root_span = self.pop_spans()[0]
            assert root_span.get_tag(http.STATUS_CODE) == "200"
            assert root_span.get_tag(http.URL) == "http://localhost/?value=block_that_value"
            assert root_span.get_tag(http.METHOD) == "GET"
            assert root_span.get_tag(http.USER_AGENT).startswith("werkzeug/")
            assert root_span.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") != "text/json"
            # rules bypass
            assert root_span.get_tag(APPSEC.JSON) is None
            assert root_span.get_tag("appsec.event") is None
            assert root_span.get_tag("appsec.blocked") is None

    def test_flask_ipblock_manually_json(self):
        # Most tests of flask blocking are in the test_flask_snapshot, this just
        # test a manual call to the blocking callable stored in _asm_request_context
        @self.app.route("/block")
        def test_route():
            from ddtrace.appsec._asm_request_context import block_request

            return block_request()

        with override_global_config(dict(_asm_enabled=True)), override_env(dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)):
            self._aux_appsec_prepare_tracer()
            resp = self.client.get("/block", headers={"X-REAL-IP": rules._IP.DEFAULT})
            # Should not block by IP but since the route is calling block_request it will be blocked
            assert resp.status_code == 403
            assert get_response_body(resp) == constants.BLOCKED_RESPONSE_JSON
            root_span = self.pop_spans()[0]
            assert root_span.get_tag(http.STATUS_CODE) == "403"
            assert root_span.get_tag(http.URL) == "http://localhost/block"
            assert root_span.get_tag(http.METHOD) == "GET"
            assert root_span.get_tag(http.USER_AGENT).startswith("werkzeug/")
            assert root_span.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"

    def test_flask_userblock_json(self):
        @self.app.route("/checkuser/<user_id>")
        def test_route(user_id):
            from ddtrace import tracer

            block_request_if_user_blocked(tracer, user_id)
            return "Ok", 200

        with override_global_config(dict(_asm_enabled=True)), override_env(dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)):
            self._aux_appsec_prepare_tracer()
            resp = self.client.get("/checkuser/%s" % _BLOCKED_USER)
            assert resp.status_code == 403
            assert get_response_body(resp) == constants.BLOCKED_RESPONSE_JSON
            root_span = self.pop_spans()[0]
            assert root_span.get_tag(http.STATUS_CODE) == "403"
            assert root_span.get_tag(http.URL) == "http://localhost/checkuser/%s" % _BLOCKED_USER
            assert root_span.get_tag(http.METHOD) == "GET"
            assert root_span.get_tag(http.USER_AGENT).startswith("werkzeug/")
            assert root_span.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"

            resp = self.client.get("/checkuser/%s" % _BLOCKED_USER, headers={"Accept": "text/html"})
            assert resp.status_code == 403
            assert get_response_body(resp) == constants.BLOCKED_RESPONSE_HTML

            resp = self.client.get("/checkuser/%s" % _ALLOWED_USER, headers={"Accept": "text/html"})
            assert resp.status_code == 200

    def test_request_invalid_rule_file(self):
        @self.app.route("/response-header/")
        def specific_reponse():
            resp = Response("Foo bar baz", 200)
            resp.headers["Content-Disposition"] = 'attachment;"'
            return resp

        with override_global_config(dict(_asm_enabled=True)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_BAD_VERSION)
        ):
            self._aux_appsec_prepare_tracer()
            resp = self.client.get("/response-header/")
            # it must not completely fail on an invalid rule file
            assert resp.status_code == 200

    def test_multiple_service_name(self):
        import time

        import flask

        import ddtrace

        @self.app.route("/new_service/<service_name>")
        def new_service(service_name):
            ddtrace.Pin.override(flask.Flask, service=service_name, tracer=ddtrace.tracer)
            return "Ok %s" % service_name, 200

        with override_global_config(dict(_remote_config_enabled=True)):
            self._aux_appsec_prepare_tracer()
            assert ddtrace.config._remote_config_enabled
            resp = self.client.get("/new_service/awesome_test")
            assert resp.status_code == 200
            assert get_response_body(resp) == "Ok awesome_test"
            for _ in range(10):
                time.sleep(1)
                if "awesome_test" in ddtrace.config._get_extra_services():
                    break
            else:
                raise AssertionError("extra service not found")
