import pytest

from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._trace_utils import block_request_if_user_blocked
from ddtrace.contrib.internal.sqlite3.patch import patch
from ddtrace.ext import http
from ddtrace.internal import constants
import tests.appsec.rules as rules
from tests.contrib.flask import BaseFlaskTestCase
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
        # Hack: need to pass an argument to configure so that the processors are recreated
        self.tracer._configure(api_version="v0.4")

    def test_flask_ipblock_manually_json(self):
        # Most tests of flask blocking are in the test_flask_snapshot, this just
        # test a manual call to the blocking callable stored in _asm_request_context
        @self.app.route("/block")
        def test_route():
            from ddtrace.appsec._asm_request_context import block_request

            return block_request()

        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
            self._aux_appsec_prepare_tracer()
            resp = self.client.get("/block", headers={"X-REAL-IP": rules._IP.DEFAULT})
            # Should not block by IP but since the route is calling block_request it will be blocked
            assert resp.status_code == 403
            assert get_response_body(resp) == constants.BLOCKED_RESPONSE_JSON
            root_span = self.pop_spans()[0]
            assert root_span.get_tag(http.STATUS_CODE) == "403"
            assert root_span.get_tag(http.URL) == "http://localhost/block"
            assert root_span.get_tag(http.METHOD) == "GET"
            assert root_span.get_tag(http.USER_AGENT).lower().startswith("werkzeug/")
            assert (
                root_span.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "application/json"
            )

    def test_flask_userblock_json(self):
        @self.app.route("/checkuser/<user_id>")
        def test_route(user_id):
            from ddtrace.trace import tracer

            block_request_if_user_blocked(tracer, user_id)
            return "Ok", 200

        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
            self._aux_appsec_prepare_tracer()
            resp = self.client.get("/checkuser/%s" % _BLOCKED_USER)
            assert resp.status_code == 403
            assert get_response_body(resp) == constants.BLOCKED_RESPONSE_JSON
            root_span = self.pop_spans()[0]
            assert root_span.get_tag(http.STATUS_CODE) == "403"
            assert root_span.get_tag(http.URL) == "http://localhost/checkuser/%s" % _BLOCKED_USER
            assert root_span.get_tag(http.METHOD) == "GET"
            assert root_span.get_tag(http.USER_AGENT).lower().startswith("werkzeug/")
            assert (
                root_span.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "application/json"
            )

            resp = self.client.get("/checkuser/%s" % _BLOCKED_USER, headers={"Accept": "text/html"})
            assert resp.status_code == 403
            assert get_response_body(resp) == constants.BLOCKED_RESPONSE_HTML

            resp = self.client.get("/checkuser/%s" % _ALLOWED_USER, headers={"Accept": "text/html"})
            assert resp.status_code == 200
