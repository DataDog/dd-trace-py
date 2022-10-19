import json
import logging

from flask import request
import pytest

from ddtrace.constants import APPSEC_JSON
from ddtrace.ext import http
from ddtrace.internal import _context
from ddtrace.internal.compat import urlencode
from tests.appsec.test_processor import RULES_GOOD_PATH
from tests.contrib.flask import BaseFlaskTestCase
from tests.utils import override_env
from tests.utils import override_global_config


class FlaskAppSecTestCase(BaseFlaskTestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog):
        self._caplog = caplog

    def _aux_appsec_prepare_tracer(self, appsec_enabled=True):
        self.tracer._appsec_enabled = appsec_enabled
        # Hack: need to pass an argument to configure so that the processors are recreated
        self.tracer.configure(api_version="v0.4")

    def test_flask_simple_attack(self):
        with override_global_config(dict(_appsec_enabled=True)):
            self._aux_appsec_prepare_tracer()
            resp = self.client.get("/.git?q=1")
            assert resp.status_code == 404
            # Read response data from the test client to close flask.request and flask.response spans
            assert resp.data is not None
            root_span = self.pop_spans()[0]

            appsec_json = root_span.get_tag(APPSEC_JSON)
            assert "triggers" in json.loads(appsec_json if appsec_json else "{}")
            assert _context.get_item("http.request.uri", span=root_span) == "http://localhost/.git?q=1"
            query = dict(_context.get_item("http.request.query", span=root_span))
            assert query == {"q": "1"} or query == {"q": ["1"]}

    def test_flask_path_params(self):
        @self.app.route("/params/<item>")
        def dynamic_url(item):
            return item

        with override_global_config(dict(_appsec_enabled=True)):
            self._aux_appsec_prepare_tracer()
            resp = self.client.get("/params/attack")
            assert resp.status_code == 200
            # Read response data from the test client to close flask.request and flask.response spans
            assert resp.data is not None
            root_span = self.pop_spans()[0]

            flask_args = root_span.get_tag("flask.view_args.item")
            assert flask_args == "attack"

            path_params = _context.get_item("http.request.path_params", span=root_span)
            assert path_params == {"item": "attack"}

    def test_flask_path_params_attack(self):
        @self.app.route("/params/<item>")
        def dynamic_url(item):
            return item

        with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
            self._aux_appsec_prepare_tracer()
            resp = self.client.get("/params/w00tw00t.at.isc.sans.dfind")
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]

            appsec_json = root_span.get_tag(APPSEC_JSON)
            assert "triggers" in json.loads(appsec_json if appsec_json else "{}")

            query = dict(_context.get_item("http.request.path_params", span=root_span))
            assert query == {"item": "w00tw00t.at.isc.sans.dfind"}

    def test_flask_querystrings(self):
        with override_global_config(dict(_appsec_enabled=True)):
            self._aux_appsec_prepare_tracer()
            self.client.get("/?a=1&b&c=d")
            root_span = self.pop_spans()[0]
            query = dict(_context.get_item("http.request.query", span=root_span))
            assert query == {"a": "1", "b": "", "c": "d"} or query == {"a": ["1"], "b": [""], "c": ["d"]}
            self.client.get("/")
            root_span = self.pop_spans()[0]
            assert len(_context.get_item("http.request.query", span=root_span)) == 0

    def test_flask_cookie_sql_injection(self):
        with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
            self._aux_appsec_prepare_tracer()
            self.client.set_cookie("localhost", "attack", "1' or '1' = '1'")
            resp = self.client.get("/")
            assert resp.status_code == 404
            root_span = self.pop_spans()[0]

            appsec_json = root_span.get_tag(APPSEC_JSON)
            assert "triggers" in json.loads(appsec_json if appsec_json else "{}")
            assert _context.get_item("http.request.cookies", span=root_span)["attack"] == "1' or '1' = '1'"
            query = dict(_context.get_item("http.request.cookies", span=root_span))
            assert query == {"attack": "1' or '1' = '1'"} or query == {"attack": ["1' or '1' = '1'"]}

    def test_flask_cookie(self):
        with override_global_config(dict(_appsec_enabled=True)):
            self._aux_appsec_prepare_tracer()
            self.client.set_cookie("localhost", "testingcookie_key", "testingcookie_value")
            resp = self.client.get("/")
            assert resp.status_code == 404
            # Read response data from the test client to close flask.request and flask.response spans
            assert resp.data is not None
            root_span = self.pop_spans()[0]

            assert root_span.get_tag(APPSEC_JSON) is None
            assert (
                _context.get_item("http.request.cookies", span=root_span)["testingcookie_key"] == "testingcookie_value"
            )
            query = dict(_context.get_item("http.request.cookies", span=root_span))
            assert query == {"testingcookie_key": "testingcookie_value"} or query == {
                "testingcookie_key": ["testingcookie_value"]
            }

    def test_flask_useragent(self):
        self.client.get("/", headers={"User-Agent": "test/1.2.3"})
        root_span = self.pop_spans()[0]
        assert root_span.get_tag(http.USER_AGENT) == "test/1.2.3"

    def test_flask_client_ip_header_set_by_env_var_valid(self):
        with override_global_config(dict(_appsec_enabled=True)), override_env(
            dict(DD_TRACE_CLIENT_IP_HEADER="X-Use-This")
        ):
            self.client.get("/?a=1&b&c=d", headers={"HTTP_CLIENT_IP": "8.8.8.8", "X-Use-This": "4.4.4.4"})
            spans = self.pop_spans()
            root_span = spans[0]
            assert root_span.get_tag(http.CLIENT_IP) == "4.4.4.4"

    def test_flask_body_urlencoded(self):
        @self.app.route("/body", methods=["GET", "POST", "DELETE"])
        def body():
            data = dict(request.form)
            return str(data), 200

        with override_global_config(dict(_appsec_enabled=True)):
            self._aux_appsec_prepare_tracer()
            data = {"mytestingbody_key": "mytestingbody_value"}
            payload = urlencode(data)

            self.client.post("/body", data=payload, content_type="application/x-www-form-urlencoded")

            root_span = self.pop_spans()[0]
            query = dict(_context.get_item("http.request.body", span=root_span))

            assert root_span.get_tag(APPSEC_JSON) is None
            assert query == {"mytestingbody_key": "mytestingbody_value"}

    def test_flask_body_urlencoded_appsec_disabled_then_no_body(self):
        with override_global_config(dict(_appsec_enabled=False)):
            self._aux_appsec_prepare_tracer()
            payload = urlencode({"mytestingbody_key": "mytestingbody_value"})
            self.client.post("/", data=payload, content_type="application/x-www-form-urlencoded")
            root_span = self.pop_spans()[0]

            assert not _context.get_item("http.request.body", span=root_span)

    def test_flask_request_body_urlencoded_attack(self):
        with override_global_config(dict(_appsec_enabled=True)):
            self._aux_appsec_prepare_tracer()
            payload = urlencode({"attack": "1' or '1' = '1'"})
            self.client.post("/", data=payload, content_type="application/x-www-form-urlencoded")
            root_span = self.pop_spans()[0]
            query = dict(_context.get_item("http.request.body", span=root_span))
            assert "triggers" in json.loads(root_span.get_tag(APPSEC_JSON))
            assert query == {"attack": "1' or '1' = '1'"}

    def test_flask_body_json(self):
        @self.app.route("/body", methods=["GET", "POST", "DELETE"])
        def body():
            data = request.get_json()
            return str(data), 200

        with override_global_config(dict(_appsec_enabled=True)):
            self._aux_appsec_prepare_tracer()
            payload = {"mytestingbody_key": "mytestingbody_value"}

            self.client.post("/body", json=payload, content_type="application/json")

            root_span = self.pop_spans()[0]
            query = dict(_context.get_item("http.request.body", span=root_span))

            assert root_span.get_tag(APPSEC_JSON) is None
            assert query == {"mytestingbody_key": "mytestingbody_value"}

    def test_flask_body_json_attack(self):
        with override_global_config(dict(_appsec_enabled=True)):
            self._aux_appsec_prepare_tracer()
            payload = {"attack": "1' or '1' = '1'"}
            self.client.post("/", json=payload, content_type="application/json")
            root_span = self.pop_spans()[0]
            query = dict(_context.get_item("http.request.body", span=root_span))
            assert "triggers" in json.loads(root_span.get_tag(APPSEC_JSON))
            assert query == {"attack": "1' or '1' = '1'"}

    def test_flask_body_xml(self):
        @self.app.route("/body", methods=["GET", "POST", "DELETE"])
        def body():
            data = request.data
            return data, 200

        with override_global_config(dict(_appsec_enabled=True)):
            self._aux_appsec_prepare_tracer()
            payload = "<mytestingbody_key>mytestingbody_value</mytestingbody_key>"
            response = self.client.post("/body", data=payload, content_type="application/xml")
            assert response.status_code == 200
            assert response.data == b"<mytestingbody_key>mytestingbody_value</mytestingbody_key>"

            root_span = self.pop_spans()[0]
            query = dict(_context.get_item("http.request.body", span=root_span))

            assert root_span.get_tag(APPSEC_JSON) is None
            assert query == {"mytestingbody_key": "mytestingbody_value"}

    def test_flask_body_xml_attack(self):
        with override_global_config(dict(_appsec_enabled=True)):
            self._aux_appsec_prepare_tracer()
            payload = "<attack>1' or '1' = '1'</attack>"
            self.client.post("/", data=payload, content_type="application/xml")
            root_span = self.pop_spans()[0]
            query = dict(_context.get_item("http.request.body", span=root_span))

            assert "triggers" in json.loads(root_span.get_tag(APPSEC_JSON))
            assert query == {"attack": "1' or '1' = '1'"}

    def test_flask_body_json_empty_body_logs_warning(self):
        with self._caplog.at_level(logging.WARNING), override_global_config(dict(_appsec_enabled=True)):
            self._aux_appsec_prepare_tracer()
            self.client.post("/", data="", content_type="application/json")
            assert "Failed to parse werkzeug request body" in self._caplog.text
