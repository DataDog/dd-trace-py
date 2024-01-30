from contextlib import contextmanager
import json
from typing import Dict
from urllib.parse import urlencode

import pytest

import ddtrace
from ddtrace.appsec import _constants as asm_constants
from ddtrace.appsec._constants import APPSEC
from ddtrace.internal import constants
from ddtrace.internal import core
from ddtrace.settings.asm import config as asm_config
import tests.appsec.rules as rules
from tests.utils import DummyTracer
from tests.utils import override_env
from tests.utils import override_global_config


class Interface:
    def __init__(self, name, framework, client):
        self.name = name
        self.framework = framework
        self.client = client


def payload_to_xml(payload: Dict[str, str]) -> str:
    return "".join(f"<{k}>{v}</{k}>" for k, v in payload.items())


def payload_to_plain_text(payload: Dict[str, str]) -> str:
    return "\n".join(f"{k}={v}" for k, v in payload.items())


class Contrib_TestClass_For_Threats:
    """
    Factorized test class for threats tests on all supported frameworks
    """

    SERVER_PORT = 8000

    @pytest.fixture
    def interface(self) -> Interface:
        raise NotImplementedError

    def status(self, response) -> int:
        raise NotImplementedError

    def headers(self, response) -> Dict[str, str]:
        raise NotImplementedError

    def body(self, response) -> str:
        raise NotImplementedError

    def check_single_rule_triggered(self, rule_id: str, get_tag):
        tag = get_tag(APPSEC.JSON)
        assert tag is not None, "no JSON tag in root span"
        loaded = json.loads(tag)
        assert [t["rule"]["id"] for t in loaded["triggers"]] == [rule_id]

    def update_tracer(self, interface):
        interface.tracer._asm_enabled = asm_config._asm_enabled
        interface.tracer._iast_enabled = asm_config._iast_enabled
        interface.tracer.configure(api_version="v0.4")

    @pytest.mark.parametrize("asm_enabled", [True, False])
    def test_healthcheck(self, interface: Interface, get_tag, asm_enabled: bool):
        # you can disable any test in a framework like that:
        # if interface.name == "fastapi":
        #    raise pytest.skip("fastapi does not have a healthcheck endpoint")
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            response = interface.client.get("/")
            assert self.status(response) == 200, "healthcheck failed"
            assert self.body(response) == "ok ASM"
            from ddtrace.settings.asm import config as asm_config

            assert asm_config._asm_enabled is asm_enabled
            assert get_tag("http.status_code") == "200"
            assert self.headers(response)["content-type"] == "text/html; charset=utf-8"

    def test_simple_attack(self, interface: Interface, root_span, get_tag):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/.git?q=1")
            assert response.status_code == 404
            str_json = get_tag(APPSEC.JSON)
            assert str_json is not None, "no JSON tag in root span"
            assert "triggers" in json.loads(str_json)
            assert core.get_item("http.request.uri", span=root_span()) == "http://localhost:8000/.git?q=1"
            assert core.get_item("http.request.headers", span=root_span()) is not None
            query = dict(core.get_item("http.request.query", span=root_span()))
            assert query == {"q": "1"} or query == {"q": ["1"]}

    def test_querystrings(self, interface: Interface, root_span):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/?a=1&b&c=d")
            assert self.status(response) == 200
            query = dict(core.get_item("http.request.query", span=root_span()))
            assert query in [
                {"a": "1", "b": "", "c": "d"},
                {"a": ["1"], "b": [""], "c": ["d"]},
                {"a": ["1"], "c": ["d"]},
            ]

    def test_no_querystrings(self, interface: Interface, root_span):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/")
            assert self.status(response) == 200
            assert not core.get_item("http.request.query", span=root_span())

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("cookies", "attack"),
        [({"mytestingcookie_key": "mytestingcookie_value"}, False), ({"attack": "1' or '1' = '1'"}, True)],
    )
    def test_request_cookies(self, interface: Interface, root_span, get_tag, asm_enabled, cookies, attack):
        with override_global_config(dict(_asm_enabled=asm_enabled)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)
        ):
            self.update_tracer(interface)
            response = interface.client.get("/", cookies=cookies)
            assert self.status(response) == 200
            if asm_enabled:
                cookies_parsed = dict(core.get_item("http.request.cookies", span=root_span()))
                assert cookies_parsed == cookies
            else:
                assert core.get_item("http.request.cookies", span=root_span()) is None
            str_json = get_tag(APPSEC.JSON)
            if asm_enabled and attack:
                assert str_json is not None, "no JSON tag in root span"
                json_payload = json.loads(str_json)
                assert len(json_payload["triggers"]) == 1
                assert json_payload["triggers"][0]["rule"]["id"] == "crs-942-100"
            else:
                assert str_json is None, f"asm JSON tag in root span: asm_enabled={asm_enabled}, attack={attack}"

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("encode_payload", "content_type"),
        [
            (urlencode, "application/x-www-form-urlencoded"),
            (json.dumps, "application/json"),
            (payload_to_xml, "text/xml"),
            (payload_to_plain_text, "text/plain"),
        ],
    )
    @pytest.mark.parametrize(
        ("payload_struct", "attack"),
        [({"mytestingbody_key": "mytestingbody_value"}, False), ({"attack": "1' or '1' = '1'"}, True)],
    )
    def test_request_body(
        self,
        interface: Interface,
        root_span,
        get_tag,
        asm_enabled,
        encode_payload,
        content_type,
        payload_struct,
        attack,
    ):
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            payload = encode_payload(payload_struct)
            response = interface.client.post("/asm/", data=payload, content_type=content_type)
            assert self.status(response) == 200  # Have to add end points in each framework application.

            body = core.get_item("http.request.body", span=root_span())
            if asm_enabled and content_type != "text/plain":
                assert body in [
                    payload_struct,
                    {k: [v] for k, v in payload_struct.items()},
                ]
            else:
                assert not body  # DEV: Flask send {} for text/plain with asm

            str_json = get_tag(APPSEC.JSON)

            if asm_enabled and attack and content_type != "text/plain":
                assert str_json is not None, "no JSON tag in root span"
                json_payload = json.loads(str_json)
                assert len(json_payload["triggers"]) == 1
                assert json_payload["triggers"][0]["rule"]["id"] == "crs-942-100"
            else:
                assert str_json is None, "asm JSON tag in root span"

    @pytest.mark.parametrize(
        ("content_type"),
        [
            ("application/x-www-form-urlencoded"),
            ("application/json"),
            ("text/xml"),
        ],
    )
    def test_request_body_bad(self, caplog, interface: Interface, root_span, get_tag, content_type):
        # Ensure no crash when body is not parsable
        import logging

        with caplog.at_level(logging.DEBUG), override_global_config(dict(_asm_enabled=True)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)
        ):
            self.update_tracer(interface)
            payload = '{"attack": "bad_payload",}</attack>&='
            response = interface.client.post("/asm/", data=payload, content_type=content_type)
            assert response.status_code == 200
            # do not work with urlencoded or fastapi for now
            # assert "Failed to parse request body" in caplog.text

    @pytest.mark.parametrize("asm_enabled", [True, False])
    def test_request_path_params(self, interface: Interface, root_span, asm_enabled):
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            response = interface.client.get("/asm/137/abc/")
            assert self.status(response) == 200
            path_params = core.get_item("http.request.path_params", span=root_span())
            if asm_enabled:
                assert path_params["param_str"] == "abc"
                assert int(path_params["param_int"]) == 137
            else:
                assert path_params is None

    def test_useragent(self, interface: Interface, root_span, get_tag):
        if interface.name == "fastapi":
            raise pytest.skip("fastapi does not seem to report user-agent")

        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/", headers={"HTTP_USER_AGENT": "test/1.2.3"})
            assert self.status(response) == 200
            assert get_tag(http.USER_AGENT) == "test/1.2.3"

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("headers", "expected"),
        [
            ({"HTTP_X_REAL_IP": "8.8.8.8"}, "8.8.8.8"),
            ({"HTTP_X_CLIENT_IP": "", "HTTP_X_FORWARDED_FOR": "4.4.4.4"}, "4.4.4.4"),
            ({"HTTP_X_CLIENT_IP": "192.168.1.3,4.4.4.4"}, "4.4.4.4"),
            ({"HTTP_X_CLIENT_IP": "4.4.4.4,8.8.8.8"}, "4.4.4.4"),
            ({"HTTP_X_CLIENT_IP": "192.168.1.10,192.168.1.20"}, "192.168.1.10"),
        ],
    )
    def test_client_ip_asm_enabled_reported(self, interface: Interface, get_tag, asm_enabled, headers, expected):
        if interface.name in ("fastapi", "flask"):
            raise pytest.skip(f"{interface.name} does not support this feature")
        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            interface.client.get("/", headers=headers)
            if asm_enabled:
                assert get_tag(http.CLIENT_IP) == expected  # only works on Django for now
            else:
                assert get_tag(http.CLIENT_IP) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("env_var", "headers", "expected"),
        [
            ("Fooipheader", {"HTTP_FOOIPHEADER": "", "HTTP_X_REAL_IP": "8.8.8.8"}, None),
            ("Fooipheader", {"HTTP_FOOIPHEADER": "invalid_ip", "HTTP_X_REAL_IP": "8.8.8.8"}, None),
            ("Fooipheader", {"HTTP_FOOIPHEADER": "", "HTTP_X_REAL_IP": "アスダス"}, None),
            ("X-Use-This", {"HTTP_X_USE_THIS": "4.4.4.4", "HTTP_X_REAL_IP": "8.8.8.8"}, "4.4.4.4"),
        ],
    )
    def test_client_ip_header_set_by_env_var(
        self, interface: Interface, get_tag, asm_enabled, env_var, headers, expected
    ):
        if interface.name in ("fastapi", "flask"):
            raise pytest.skip(f"{interface.name} does not support this feature")

        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled, client_ip_header=env_var)):
            self.update_tracer(interface)
            response = interface.client.get("/", headers=headers)
            assert self.status(response) == 200
            if asm_enabled:
                assert get_tag(http.CLIENT_IP) == expected
            else:
                assert get_tag(http.CLIENT_IP) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("headers", "blocked", "body", "content_type"),
        [
            ({"HTTP_X_REAL_IP": rules._IP.BLOCKED}, True, "BLOCKED_RESPONSE_JSON", "text/json"),
            (
                {"HTTP_X_REAL_IP": rules._IP.BLOCKED, "HTTP_ACCEPT": "text/html"},
                True,
                "BLOCKED_RESPONSE_HTML",
                "text/html",
            ),
            ({"HTTP_X_REAL_IP": rules._IP.DEFAULT}, False, None, None),
        ],
    )
    def test_request_ipblock(self, interface: Interface, get_tag, asm_enabled, headers, blocked, body, content_type):
        if interface.name in ("fastapi", "flask"):
            raise pytest.skip(f"{interface.name} does not support this feature")
        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)
        ):
            self.update_tracer(interface)
            response = interface.client.get("/", headers=headers)
            if blocked and asm_enabled:
                assert self.status(response) == 403
                assert self.body(response) == getattr(constants, body, None)
                assert get_tag("actor.ip") == rules._IP.BLOCKED
                assert get_tag(http.STATUS_CODE) == "403"
                assert get_tag(http.URL) == "http://localhost:8000/"
                assert get_tag(http.METHOD) == "GET"
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == content_type
                )
                assert self.headers(response)["content-type"] == content_type
            else:
                assert self.status(response) == 200

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(("method", "kwargs"), [("get", {}), ("post", {"data": {"key": "value"}}), ("options", {})])
    def test_request_suspicious_request_block_match_method(
        self, interface: Interface, get_tag, asm_enabled, method, kwargs
    ):
        # GET must be blocked
        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_SRB_METHOD)
        ):
            self.update_tracer(interface)
            response = getattr(interface.client, method)("/", **kwargs)
            assert get_tag(http.URL) == "http://localhost:8000/"
            assert get_tag(http.METHOD) == method.upper()
            if asm_enabled and method == "get":
                assert self.status(response) == 403
                assert get_tag(http.STATUS_CODE) == "403"
                assert self.body(response) == constants.BLOCKED_RESPONSE_JSON
                self.check_single_rule_triggered("tst-037-006", get_tag)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
                )
                assert self.headers(response)["content-type"] == "text/json"
            else:
                assert self.status(response) == 200
                assert get_tag(http.STATUS_CODE) == "200"
                assert get_tag(APPSEC.JSON) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(("uri", "blocked"), [("/.git", True), ("/legit", False)])
    def test_request_suspicious_request_block_match_uri(self, interface: Interface, get_tag, asm_enabled, uri, blocked):
        # GET must be blocked
        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_SRB)
        ):
            self.update_tracer(interface)
            response = interface.client.get(uri)
            assert get_tag(http.URL) == f"http://localhost:8000{uri}"
            assert get_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                assert self.status(response) == 403
                assert get_tag(http.STATUS_CODE) == "403"
                assert self.body(response) == constants.BLOCKED_RESPONSE_JSON
                self.check_single_rule_triggered("tst-037-002", get_tag)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
                )
                assert self.headers(response)["content-type"] == "text/json"
            else:
                assert self.status(response) == 404
                assert get_tag(http.STATUS_CODE) == "404"
                assert get_tag(APPSEC.JSON) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("path", "blocked"),
        [
            ("AiKfOeRcvG45/", True),
            ("Anything/", False),
            ("AiKfOeRcvG45", True),
            ("NoTralingSlash", False),
        ],
    )
    def test_request_suspicious_request_block_match_path_params(
        self, interface: Interface, get_tag, asm_enabled, path, blocked
    ):
        from ddtrace.ext import http

        uri = f"/asm/4352/{path}"  # removing trailer slash will cause errors
        with override_global_config(dict(_asm_enabled=asm_enabled)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_SRB)
        ):
            self.update_tracer(interface)
            response = interface.client.get(uri)
            # DEV Warning: encoded URL will behave differently
            assert get_tag(http.URL) == "http://localhost:8000" + uri
            assert get_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                assert self.status(response) == 403
                assert get_tag(http.STATUS_CODE) == "403"
                assert self.body(response) == constants.BLOCKED_RESPONSE_JSON
                self.check_single_rule_triggered("tst-037-007", get_tag)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
                )
                assert self.headers(response)["content-type"] == "text/json"
            else:
                assert self.status(response) == 200
                assert get_tag(http.STATUS_CODE) == "200"
                assert get_tag(APPSEC.JSON) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("query", "blocked"),
        [
            ("?toto=xtrace", True),
            ("?toto=ytrace", False),
            ("?toto=xtrace&toto=ytrace", True),
        ],
    )
    def test_request_suspicious_request_block_match_query_params(
        self, interface: Interface, get_tag, asm_enabled, query, blocked
    ):
        if interface.name in ("django") and query == "?toto=xtrace&toto=ytrace":
            raise pytest.skip(f"{interface.name} does not support multiple query params with same name")

        from ddtrace.ext import http

        uri = f"/{query}"
        with override_global_config(dict(_asm_enabled=asm_enabled)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_SRB)
        ):
            self.update_tracer(interface)
            response = interface.client.get(uri)
            # DEV Warning: encoded URL will behave differently
            assert get_tag(http.URL) == "http://localhost:8000" + uri
            assert get_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                assert self.status(response) == 403
                assert get_tag(http.STATUS_CODE) == "403"
                assert self.body(response) == constants.BLOCKED_RESPONSE_JSON
                self.check_single_rule_triggered("tst-037-001", get_tag)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
                )
                assert self.headers(response)["content-type"] == "text/json"
            else:
                assert self.status(response) == 200
                assert get_tag(http.STATUS_CODE) == "200"
                assert get_tag(APPSEC.JSON) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("headers", "blocked"),
        [
            ({"HTTP_USER_AGENT": "01972498723465"}, True),
            ({"HTTP_USER_AGENT": "01973498523465"}, False),
        ],
    )
    def test_request_suspicious_request_block_match_request_headers(
        self, interface: Interface, get_tag, asm_enabled, headers, blocked
    ):
        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_SRB)
        ):
            self.update_tracer(interface)
            response = interface.client.get("/", headers=headers)
            # DEV Warning: encoded URL will behave differently
            assert get_tag(http.URL) == "http://localhost:8000/"
            assert get_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                assert self.status(response) == 403
                assert get_tag(http.STATUS_CODE) == "403"
                assert self.body(response) == constants.BLOCKED_RESPONSE_JSON
                self.check_single_rule_triggered("tst-037-004", get_tag)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
                )
                assert self.headers(response)["content-type"] == "text/json"
            else:
                assert self.status(response) == 200
                assert get_tag(http.STATUS_CODE) == "200"
                assert get_tag(APPSEC.JSON) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("cookies", "blocked"),
        [
            ({"mytestingcookie_key": "jdfoSDGFkivRG_234"}, True),
            ({"mytestingcookie_key": "jdfoSDGEkivRH_234"}, False),
        ],
    )
    def test_request_suspicious_request_block_match_request_cookies(
        self, interface: Interface, get_tag, asm_enabled, cookies, blocked
    ):
        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_SRB)
        ):
            self.update_tracer(interface)
            response = interface.client.get("/", cookies=cookies)
            # DEV Warning: encoded URL will behave differently
            assert get_tag(http.URL) == "http://localhost:8000/"
            assert get_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                assert self.status(response) == 403
                assert get_tag(http.STATUS_CODE) == "403"
                assert self.body(response) == constants.BLOCKED_RESPONSE_JSON
                self.check_single_rule_triggered("tst-037-008", get_tag)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
                )
                assert self.headers(response)["content-type"] == "text/json"
            else:
                assert self.status(response) == 200
                assert get_tag(http.STATUS_CODE) == "200"
                assert get_tag(APPSEC.JSON) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("uri", "status", "blocked"),
        [
            ("/donotexist", 404, "tst-037-005"),
            ("/asm/1/a?status=216", 216, "tst-037-216"),
            ("/asm/1/a?status=415", 415, None),
            ("/", 200, None),
        ],
    )
    def test_request_suspicious_request_block_match_response_status(
        self, interface: Interface, get_tag, asm_enabled, uri, status, blocked
    ):
        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_SRB_RESPONSE)
        ):
            self.update_tracer(interface)
            response = interface.client.get(uri)
            # DEV Warning: encoded URL will behave differently
            assert get_tag(http.URL) == "http://localhost:8000" + uri
            assert get_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                assert self.status(response) == 403
                assert get_tag(http.STATUS_CODE) == "403"
                assert self.body(response) == constants.BLOCKED_RESPONSE_JSON
                self.check_single_rule_triggered(blocked, get_tag)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
                )
                assert self.headers(response)["content-type"] == "text/json"
            else:
                assert self.status(response) == status
                assert get_tag(http.STATUS_CODE) == str(status)
                assert get_tag(APPSEC.JSON) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("uri", "blocked"),
        [
            ("/asm/1/a", False),
            ("/asm/1/a?headers=header_name=MagicKey_Al4h7iCFep9s1", "tst-037-009"),
            ("/asm/1/a?headers=key=anythin,header_name=HiddenMagicKey_Al4h7iCFep9s1Value", "tst-037-009"),
            ("/asm/1/a?headers=header_name=NoWorryBeHappy", None),
        ],
    )
    def test_request_suspicious_request_block_match_response_headers(
        self, interface: Interface, get_tag, asm_enabled, root_span, uri, blocked
    ):
        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_SRB)
        ):
            self.update_tracer(interface)
            response = interface.client.get(uri)
            # DEV Warning: encoded URL will behave differently
            assert get_tag(http.URL) == "http://localhost:8000" + uri
            assert get_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                assert self.status(response) == 403
                assert get_tag(http.STATUS_CODE) == "403"
                assert self.body(response) == constants.BLOCKED_RESPONSE_JSON
                self.check_single_rule_triggered(blocked, get_tag)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
                )
                assert self.headers(response)["content-type"] == "text/json"
            else:
                assert self.status(response) == 200
                assert get_tag(http.STATUS_CODE) == "200"
                assert get_tag(APPSEC.JSON) is None


@contextmanager
def test_tracer():
    tracer = DummyTracer()
    original_tracer = ddtrace.tracer
    ddtrace.tracer = tracer

    # Yield to our test
    tracer.configure(api_version="v0.4")
    yield tracer
    tracer.pop()
    ddtrace.tracer = original_tracer


@contextmanager
def post_tracer(interface):
    original_tracer = ddtrace.Pin.get_from(interface.framework).tracer
    ddtrace.Pin.override(interface.framework, tracer=interface.tracer)
    yield
    ddtrace.Pin.override(interface.framework, tracer=original_tracer)
