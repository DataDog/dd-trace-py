from contextlib import contextmanager
import json
from typing import Dict
from typing import List
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

    def location(self, response) -> str:
        return NotImplementedError

    def body(self, response) -> str:
        raise NotImplementedError

    def check_single_rule_triggered(self, rule_id: str, get_tag):
        tag = get_tag(APPSEC.JSON)
        assert tag is not None, "no JSON tag in root span"
        loaded = json.loads(tag)
        result = [t["rule"]["id"] for t in loaded["triggers"]]
        assert result == [rule_id], f"result={result}, expected={[rule_id]}"

    def check_rules_triggered(self, rule_id: List[str], get_tag):
        tag = get_tag(APPSEC.JSON)
        assert tag is not None, "no JSON tag in root span"
        loaded = json.loads(tag)
        result = sorted([t["rule"]["id"] for t in loaded["triggers"]])
        assert result == rule_id, f"result={result}, expected={rule_id}"

    def update_tracer(self, interface):
        interface.tracer._asm_enabled = asm_config._asm_enabled
        interface.tracer._iast_enabled = asm_config._iast_enabled
        interface.tracer.configure(api_version="v0.4")
        assert asm_config._asm_libddwaf_available
        # Only for tests diagnostics

    #         if interface.tracer._appsec_processor:
    #             interface.printer(
    #                 f"""ASM enabled: {asm_config._asm_enabled}
    # {ddtrace.appsec._ddwaf.version()}
    # {interface.tracer._appsec_processor._ddwaf.info}
    # {asm_config._asm_libddwaf}
    # """
    #            )

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
        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/", headers={"user-agent": "test/1.2.3"})
            assert self.status(response) == 200
            assert get_tag(http.USER_AGENT) == "test/1.2.3"

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("headers", "expected"),
        [
            ({"X-Real-Ip": "8.8.8.8"}, "8.8.8.8"),
            ({"x-client-ip": "", "X-Forwarded-For": "4.4.4.4"}, "4.4.4.4"),
            ({"x-client-ip": "192.168.1.3,4.4.4.4"}, "4.4.4.4"),
            ({"x-client-ip": "4.4.4.4,8.8.8.8"}, "4.4.4.4"),
            ({"x-client-ip": "192.168.1.10,192.168.1.20"}, "192.168.1.10"),
        ],
    )
    def test_client_ip_asm_enabled_reported(self, interface: Interface, get_tag, asm_enabled, headers, expected):
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
            ("Fooipheader", {"Fooipheader": "", "X-Real-Ip": "8.8.8.8"}, None),
            ("Fooipheader", {"Fooipheader": "invalid_ip", "X-Real-Ip": "8.8.8.8"}, None),
            ("Fooipheader", {"Fooipheader": "", "X-Real-Ip": "アスダス"}, None),
            ("X-Use-This", {"X-Use-This": "4.4.4.4", "X-Real-Ip": "8.8.8.8"}, "4.4.4.4"),
        ],
    )
    def test_client_ip_header_set_by_env_var(
        self, interface: Interface, get_tag, root_span, asm_enabled, env_var, headers, expected
    ):
        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled, client_ip_header=env_var)):
            self.update_tracer(interface)
            response = interface.client.get("/", headers=headers)
            assert self.status(response) == 200
            if asm_enabled:
                assert get_tag(http.CLIENT_IP) == expected or (
                    expected is None and get_tag(http.CLIENT_IP) == "127.0.0.1"
                )
            else:
                assert get_tag(http.CLIENT_IP) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("headers", "blocked", "body", "content_type"),
        [
            ({"X-Real-Ip": rules._IP.BLOCKED}, True, "BLOCKED_RESPONSE_JSON", "text/json"),
            (
                {"X-Real-Ip": rules._IP.BLOCKED, "Accept": "text/html"},
                True,
                "BLOCKED_RESPONSE_HTML",
                "text/html",
            ),
            ({"X-Real-Ip": rules._IP.DEFAULT}, False, None, None),
        ],
    )
    def test_request_ipblock(self, interface: Interface, get_tag, asm_enabled, headers, blocked, body, content_type):
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
                self.check_single_rule_triggered("blk-001-001", get_tag)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == content_type
                )
                assert self.headers(response)["content-type"] == content_type
            else:
                assert self.status(response) == 200

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("headers", "monitored", "bypassed"),
        [
            ({"X-Real-Ip": rules._IP.MONITORED}, True, False),
            ({"X-Real-Ip": rules._IP.DEFAULT}, False, False),
            ({"X-Real-Ip": rules._IP.BYPASS}, False, True),
        ],
    )
    @pytest.mark.parametrize(
        ("query", "blocked"),
        [
            # lowercase transformer is currently bugged on libddwaf
            # ("?x=MoNiToR_ThAt_VaLuE", False),
            # ("?x=BlOcK_ThAt_VaLuE&y=1", True),
            ("?x=monitor_that_value", False),
            ("?x=block_that_value&y=1", True),
        ],
    )
    def test_request_ipmonitor(
        self, interface: Interface, get_tag, asm_enabled, headers, monitored, bypassed, query, blocked
    ):
        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)
        ):
            self.update_tracer(interface)
            response = interface.client.get("/" + query, headers=headers)
            code = 403 if not bypassed and not monitored and asm_enabled and blocked else 200
            rule = "tst-421-001" if blocked else "tst-421-002"
            assert self.status(response) == code, f"status={self.status(response)}, expected={code}"
            assert get_tag(http.STATUS_CODE) == str(code), f"status_code={get_tag(http.STATUS_CODE)}, expected={code}"
            if asm_enabled and not bypassed:
                assert get_tag(http.URL) == f"http://localhost:8000/{query}"
                assert get_tag(http.METHOD) == "GET", f"method={get_tag(http.METHOD)}, expected=GET"
                assert (
                    get_tag("actor.ip") == headers["X-Real-Ip"]
                ), f"actor.ip={get_tag('actor.ip')}, expected={headers['X-Real-Ip']}"
                if monitored:
                    self.check_rules_triggered(["blk-001-010", rule], get_tag)
                else:
                    self.check_rules_triggered([rule], get_tag)
            else:
                assert get_tag(APPSEC.JSON) is None, f"asm JSON tag in root span {get_tag(APPSEC.JSON)}"

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
        if interface.name in ("django",) and query == "?toto=xtrace&toto=ytrace":
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
            ({"User-Agent": "01972498723465"}, True),
            ({"User_Agent": "01973498523465"}, False),
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

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("body", "content_type", "blocked"),
        [
            # json body must be blocked
            ('{"attack": "yqrweytqwreasldhkuqwgervflnmlnli"}', "application/json", "tst-037-003"),
            ('{"attack": "yqrweytqwreasldhkuqwgervflnmlnli"}', "text/json", "tst-037-003"),
            # xml body must be blocked
            (
                '<?xml version="1.0" encoding="UTF-8"?><attack>yqrweytqwreasldhkuqwgervflnmlnli</attack>',
                "text/xml",
                "tst-037-003",
            ),
            # form body must be blocked
            ("attack=yqrweytqwreasldhkuqwgervflnmlnli", "application/x-www-form-urlencoded", "tst-037-003"),
            (
                '--52d1fb4eb9c021e53ac2846190e4ac72\r\nContent-Disposition: form-data; name="attack"\r\n'
                'Content-Type: application/json\r\n\r\n{"test": "yqrweytqwreasldhkuqwgervflnmlnli"}\r\n'
                "--52d1fb4eb9c021e53ac2846190e4ac72--\r\n",
                "multipart/form-data; boundary=52d1fb4eb9c021e53ac2846190e4ac72",
                "tst-037-003",
            ),
            # raw body must not be blocked
            ("yqrweytqwreasldhkuqwgervflnmlnli", "text/plain", False),
            # other values must not be blocked
            ('{"attack": "zqrweytqwreasldhkuqxgervflnmlnli"}', "application/json", False),
        ],
    )
    def test_request_suspicious_request_block_match_request_body(
        self, interface: Interface, get_tag, asm_enabled, root_span, body, content_type, blocked
    ):
        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_SRB)
        ):
            self.update_tracer(interface)
            response = interface.client.post("/asm/", data=body, content_type=content_type)
            # DEV Warning: encoded URL will behave differently
            assert get_tag(http.URL) == "http://localhost:8000/asm/"
            assert get_tag(http.METHOD) == "POST"
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

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("query", "status", "rule_id", "action"),
        [
            ("suspicious_306_auto", 306, "tst-040-001", "blocked"),
            ("suspicious_429_json", 429, "tst-040-002", "blocked"),
            ("suspicious_503_html", 503, "tst-040-003", "blocked"),
            ("suspicious_301", 301, "tst-040-004", "redirect"),
            ("suspicious_303", 303, "tst-040-005", "redirect"),
            ("nothing", 200, None, None),
        ],
    )
    @pytest.mark.parametrize(
        "headers",
        [{"Accept": "text/html"}, {"Accept": "text/json"}, {}],
    )
    def test_request_suspicious_request_block_custom_actions(
        self, interface: Interface, get_tag, asm_enabled, root_span, query, status, rule_id, action, headers
    ):
        from ddtrace.ext import http
        import ddtrace.internal.utils.http as http_cache

        # remove cache to avoid using templates from other tests
        http_cache._HTML_BLOCKED_TEMPLATE_CACHE = None
        http_cache._JSON_BLOCKED_TEMPLATE_CACHE = None
        try:
            uri = f"/?param={query}"
            with override_global_config(dict(_asm_enabled=asm_enabled)), override_env(
                dict(
                    DD_APPSEC_RULES=rules.RULES_SRBCA,
                    DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON=rules.RESPONSE_CUSTOM_JSON,
                    DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML=rules.RESPONSE_CUSTOM_HTML,
                )
            ):
                self.update_tracer(interface)
                response = interface.client.get(uri, headers=headers)
                # DEV Warning: encoded URL will behave differently
                assert get_tag(http.URL) == "http://localhost:8000" + uri
                assert get_tag(http.METHOD) == "GET"
                if asm_enabled and action:
                    assert self.status(response) == status
                    assert get_tag(http.STATUS_CODE) == str(status)
                    self.check_single_rule_triggered(rule_id, get_tag)

                    if action == "blocked":
                        content_type = (
                            "text/html"
                            if "html" in query or ("auto" in query) and headers.get("Accept") == "text/html"
                            else "text/json"
                        )
                        assert (
                            get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type")
                            == content_type
                        )
                        assert self.headers(response)["content-type"] == content_type
                        if content_type == "text/json":
                            assert json.loads(self.body(response)) == {
                                "errors": [{"title": "You've been blocked", "detail": "Custom content"}]
                            }
                    elif action == "redirect":
                        # assert not self.body(response)
                        assert self.location(response) == "https://www.datadoghq.com"
                else:
                    assert self.status(response) == 200
                    assert get_tag(http.STATUS_CODE) == "200"
                    assert get_tag(APPSEC.JSON) is None
        finally:
            # remove cache to avoid using custom templates in other tests
            http_cache._HTML_BLOCKED_TEMPLATE_CACHE = None
            http_cache._JSON_BLOCKED_TEMPLATE_CACHE = None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    def test_nested_appsec_events(
        self,
        interface: Interface,
        get_tag,
        asm_enabled,
    ):
        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            response = interface.client.get("/config.php", headers={"user-agent": "Arachni/v1.5.1"})
            # DEV Warning: encoded URL will behave differently
            assert get_tag(http.URL) == "http://localhost:8000/config.php"
            assert get_tag(http.METHOD) == "GET"
            assert self.status(response) == 404
            assert get_tag(http.STATUS_CODE) == "404"
            if asm_enabled:
                self.check_rules_triggered(["nfd-000-001", "ua0-600-12x"], get_tag)
            else:
                assert get_tag(APPSEC.JSON) is None

    @pytest.mark.parametrize("apisec_enabled", [True, False])
    @pytest.mark.parametrize(
        ("name", "expected_value"),
        [
            ("_dd.appsec.s.req.body", ([{"key": [8], "ids": [[[4]], {"len": 4}]}],)),
            ("_dd.appsec.s.req.cookies", ([{"secret": [8]}],)),
            (
                "_dd.appsec.s.req.headers",
                (
                    [{"content-length": [8], "content-type": [8], "user-agent": [8]}],  # Django
                    [  # FastAPI
                        {
                            "content-length": [8],
                            "content-type": [8],
                            "accept-encoding": [8],
                            "user-agent": [8],
                            "connection": [8],
                            "accept": [8],
                            "host": [8],
                        }
                    ],
                    [{"content-length": [8], "content-type": [8], "host": [8], "user-agent": [8]}],  # Flask
                ),
            ),
            (
                "_dd.appsec.s.req.query",
                (
                    [{"y": [8], "x": [8]}],
                    [{"y": [[[8]], {"len": 1}], "x": [[[8]], {"len": 1}]}],
                ),
            ),
            ("_dd.appsec.s.req.params", ([{"param_int": [4], "param_str": [8]}],)),
            ("_dd.appsec.s.res.headers", None),
            (
                "_dd.appsec.s.res.body",
                (
                    [
                        {
                            "method": [8],
                            "body": [8],
                            "query_params": [{"y": [8], "x": [8]}],
                            "cookies": [{"secret": [8]}],
                            "path_params": [{"param_str": [8], "param_int": [4]}],
                        }
                    ],
                    [
                        {
                            "method": [8],
                            "body": [8],
                            "query_params": [{"y": [[[8]], {"len": 1}], "x": [[[8]], {"len": 1}]}],
                            "cookies": [{"secret": [8]}],
                            "path_params": [{"param_str": [8], "param_int": [4]}],
                        }
                    ],
                ),
            ),
        ],
    )
    @pytest.mark.parametrize(
        ("headers", "event", "blocked"),
        [
            ({"User-Agent": "dd-test-scanner-log-block"}, True, True),
            ({"User-Agent": "Arachni/v1.5.1"}, True, False),
            ({"User-Agent": "AllOK"}, False, False),
        ],
    )
    @pytest.mark.parametrize("sample_rate", [0.0, 1.0])
    def test_api_security_schemas(
        self, interface: Interface, get_tag, apisec_enabled, name, expected_value, headers, event, blocked, sample_rate
    ):
        import base64
        import gzip

        from ddtrace.ext import http

        with override_global_config(
            dict(_asm_enabled=True, _api_security_enabled=apisec_enabled, _api_security_sample_rate=sample_rate)
        ):
            self.update_tracer(interface)
            response = interface.client.post(
                "/asm/324/huj/?x=1&y=2",
                data='{"key": "passwd", "ids": [0, 1, 2, 3]}',
                cookies={"secret": "aBcDeF"},
                headers=headers,
                content_type="application/json",
            )
            assert asm_config._api_security_enabled == apisec_enabled
            assert asm_config._api_security_sample_rate == sample_rate

            assert self.status(response) == 403 if blocked else 200
            assert get_tag(http.STATUS_CODE) == "403" if blocked else "200"
            if event:
                assert get_tag(APPSEC.JSON) is not None
            else:
                assert get_tag(APPSEC.JSON) is None
            value = get_tag(name)
            if apisec_enabled and sample_rate:
                assert value, name
                api = json.loads(gzip.decompress(base64.b64decode(value)).decode())
                assert api, name
                if expected_value is not None:
                    if name == "_dd.appsec.s.res.body" and blocked:
                        assert api == [{"errors": [[[{"detail": [8], "title": [8]}]], {"len": 1}]}]
                    else:
                        assert api in expected_value, (api, name)
            else:
                assert value is None, name

    @pytest.mark.parametrize("apisec_enabled", [True, False])
    @pytest.mark.parametrize(
        ("payload", "expected_value"),
        [
            (
                {"mastercard": "5123456789123456"},
                [{"mastercard": [8, {"card_type": "mastercard", "category": "payment", "type": "card"}]}],
            ),
            ({"SSN": "123-45-6789"}, [{"SSN": [8, {"category": "pii", "type": "us_ssn"}]}]),
        ],
    )
    def test_api_security_scanners(self, interface: Interface, get_tag, apisec_enabled, payload, expected_value):
        import base64
        import gzip

        from ddtrace.ext import http

        with override_global_config(
            dict(_asm_enabled=True, _api_security_enabled=apisec_enabled, _api_security_sample_rate=1.0)
        ):
            self.update_tracer(interface)
            response = interface.client.post(
                "/",
                data=json.dumps(payload),
                content_type="application/json",
            )
            assert self.status(response) == 200
            assert get_tag(http.STATUS_CODE) == "200"
            assert asm_config._api_security_enabled == apisec_enabled
            assert asm_config._api_security_sample_rate == 1.0

            value = get_tag("_dd.appsec.s.req.body")
            if apisec_enabled:
                assert value
                api = json.loads(gzip.decompress(base64.b64decode(value)).decode())
                assert api == expected_value
            else:
                assert value is None

    def test_request_invalid_rule_file(self, interface):
        """
        When the rule file is invalid, the tracer should not crash or prevent normal behavior
        """
        with override_global_config(dict(_asm_enabled=True)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_BAD_VERSION)
        ):
            self.update_tracer(interface)
            response = interface.client.get("/")
            assert self.status(response) == 200

    def test_multiple_service_name(self, interface):
        import time

        with override_global_config(dict(_remote_config_enabled=True)):
            self.update_tracer(interface)
            assert ddtrace.config._remote_config_enabled
            response = interface.client.get("/new_service/awesome_test")
            assert self.status(response) == 200
            assert self.body(response) == "awesome_test"
            for _ in range(10):
                if "awesome_test" in ddtrace.config._get_extra_services():
                    break
                time.sleep(1)
            else:
                raise AssertionError("extra service not found")


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
