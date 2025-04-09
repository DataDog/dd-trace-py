from contextlib import contextmanager
import itertools
import json
import sys
from typing import Any
from typing import Dict
from typing import List
from urllib.parse import quote
from urllib.parse import urlencode

import pytest

import ddtrace
from ddtrace.appsec import _asm_request_context
from ddtrace.appsec import _constants as asm_constants
from ddtrace.appsec._utils import get_triggers
from ddtrace.internal import constants
from ddtrace.settings.asm import config as asm_config
import tests.appsec.rules as rules
from tests.utils import DummyTracer
from tests.utils import override_env
from tests.utils import override_global_config


# patching asm_request_context to observe waf data internals

_init_finalize = _asm_request_context.finalize_asm_env
_addresses_store = []


def finalize_wrapper(env):
    _addresses_store.append(env.waf_addresses)
    _init_finalize(env)


_asm_request_context.finalize_asm_env = finalize_wrapper


class Interface:
    def __init__(self, name, framework, client):
        self.name = name
        self.framework = framework
        self.client = client

    def __repr__(self):
        return f"Interface({self.name}[{self.version}] Python[{sys.version}])"


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

    def get_stack_trace(self, root_span, namespace):
        appsec_traces = root_span().get_struct_tag(asm_constants.STACK_TRACE.TAG) or {}
        stacks = appsec_traces.get(namespace, [])
        return stacks

    def check_for_stack_trace(self, root_span):
        exploit = self.get_stack_trace(root_span, "exploit")
        stack_ids = sorted(set(t["id"] for t in exploit))
        triggers = get_triggers(root_span())
        stack_id_in_triggers = sorted(set(t["stack_id"] for t in (triggers or []) if "stack_id" in t))
        assert stack_ids == stack_id_in_triggers, f"stack_ids={stack_ids}, stack_id_in_triggers={stack_id_in_triggers}"
        return exploit

    def check_single_rule_triggered(self, rule_id: str, root_span):
        triggers = get_triggers(root_span())
        assert triggers is not None, "no appsec struct in root span"
        result = [t["rule"]["id"] for t in triggers]
        assert result == [rule_id], f"result={result}, expected={[rule_id]}"

    def check_rules_triggered(self, rule_id: List[str], root_span):
        triggers = get_triggers(root_span())
        assert triggers is not None, "no appsec struct in root span"
        result = sorted([t["rule"]["id"] for t in triggers])
        assert result == rule_id, f"result={result}, expected={rule_id}"

    def update_tracer(self, interface):
        interface.tracer._writer._api_version = "v0.4"
        interface.tracer._recreate()
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
            self.update_tracer(interface)
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
            triggers = get_triggers(root_span())
            assert triggers is not None, "no appsec struct in root span"
            # DEV: fastapi may send "requests" instead of "fastapi"
            # assert get_tag("component") == interface.name

    def test_simple_attack_timeout(self, interface: Interface, root_span, get_metric):
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        import ddtrace.internal.telemetry

        with override_global_config(dict(_asm_enabled=True, _waf_timeout=0.001)), mock_patch.object(
            ddtrace.internal.telemetry.telemetry_writer,
            "_namespace",
            MagicMock(),
        ) as mocked:
            self.update_tracer(interface)
            query_params = urlencode({"q": "1"})
            url = f"/?{query_params}"
            response = interface.client.get(url, headers={"User-Agent": "Arachni/v1.5.1"})
            assert response.status_code == 200
            assert get_metric("_dd.appsec.waf.timeouts") > 0, (root_span()._meta, root_span()._metrics)
            args_list = [
                (args[0].value, args[1].value) + args[2:]
                for args, kwargs in mocked.add_metric.call_args_list
                if args[2] == "waf.requests"
            ]
            assert len(args_list) == 1
            assert ("waf_timeout", "true") in args_list[0][4]

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("user_agent", "priority"),
        [("Mozilla/5.0", False), ("Arachni/v1.5.1", True), ("dd-test-scanner-log-block", True)],
    )
    def test_priority(self, interface: Interface, root_span, get_tag, asm_enabled, user_agent, priority):
        """Check that we only set manual keep for traces with appsec events."""
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            response = interface.client.get("/", headers={"User-Agent": user_agent})
            assert response.status_code == (403 if user_agent == "dd-test-scanner-log-block" and asm_enabled else 200)
        span_priority = root_span()._span.context.sampling_priority
        assert (span_priority == 2) if asm_enabled and priority else (span_priority < 2)

    def test_querystrings(self, interface: Interface, root_span):
        _addresses_store.clear()
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/?a=1&b&c=d")
            assert self.status(response) == 200
            query = _addresses_store[0].get("http.request.query")
            assert query in [
                {"a": "1", "b": "", "c": "d"},
                {"a": ["1"], "b": [""], "c": ["d"]},
                {"a": ["1"], "c": ["d"]},
            ]

    def test_no_querystrings(self, interface: Interface, root_span):
        _addresses_store.clear()
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/")
            assert self.status(response) == 200
            assert not _addresses_store[0].get("http.request.query")

    def test_truncation_tags(self, interface: Interface, get_metric):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            body: Dict[str, Any] = {"val": "x" * 5000}
            body.update({f"a_{i}": i for i in range(517)})
            response = interface.client.post(
                "/asm/",
                data=json.dumps(body),
                content_type="application/json",
            )
            assert self.status(response) == 200
            assert get_metric(asm_constants.APPSEC.TRUNCATION_STRING_LENGTH)
            # 12030 is due to response encoding
            assert int(get_metric(asm_constants.APPSEC.TRUNCATION_STRING_LENGTH)) == 12029
            assert get_metric(asm_constants.APPSEC.TRUNCATION_CONTAINER_SIZE)
            assert int(get_metric(asm_constants.APPSEC.TRUNCATION_CONTAINER_SIZE)) == 518

    def test_truncation_telemetry(self, interface: Interface, get_metric):
        from unittest.mock import ANY
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        import ddtrace.internal.telemetry

        with override_global_config(dict(_asm_enabled=True)), mock_patch.object(
            ddtrace.internal.telemetry.telemetry_writer,
            "_namespace",
            MagicMock(),
        ) as mocked:
            self.update_tracer(interface)
            body: Dict[str, Any] = {"val": "x" * 5000}
            body.update({f"a_{i}": i for i in range(517)})
            response = interface.client.post(
                "/asm/",
                data=json.dumps(body),
                content_type="application/json",
            )
            assert self.status(response) == 200
            args_list = [
                (args[0].value, args[1].value) + args[2:]
                for args, kwargs in mocked.add_metric.call_args_list
                if "truncated" in args[2] or args[2] == "waf.requests"
            ]
            assert args_list == [
                ("distributions", "appsec", "waf.truncated_value_size", 5000, (("truncation_reason", "1"),)),
                ("distributions", "appsec", "waf.truncated_value_size", 518, (("truncation_reason", "2"),)),
                ("count", "appsec", "waf.input_truncated", 1, (("truncation_reason", "3"),)),
                ("distributions", "appsec", "waf.truncated_value_size", 12029, (("truncation_reason", "1"),)),
                ("count", "appsec", "waf.input_truncated", 1, (("truncation_reason", "1"),)),
                (
                    "count",
                    "appsec",
                    "waf.requests",
                    1.0,
                    (
                        ("event_rules_version", ANY),
                        ("waf_version", ANY),
                        ("rule_triggered", "false"),
                        ("request_blocked", "false"),
                        ("waf_timeout", "false"),
                        ("input_truncated", "true"),
                        ("waf_error", "0"),
                        ("rate_limited", "false"),
                    ),
                ),
            ]

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("cookies", "attack"),
        [({"mytestingcookie_key": "mytestingcookie_value"}, False), ({"attack": "1' or '1' = '1'"}, True)],
    )
    def test_request_cookies(self, interface: Interface, root_span, asm_enabled, cookies, attack):
        _addresses_store.clear()
        with override_global_config(dict(_asm_enabled=asm_enabled, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
            self.update_tracer(interface)
            response = interface.client.get("/", cookies=cookies)
            assert self.status(response) == 200
            if asm_enabled:
                cookies_parsed = _addresses_store[0].get("http.request.cookies")
                # required for flask that is sending a ImmutableMultiDict
                if isinstance(cookies_parsed, dict):
                    cookies_parsed = dict(cookies_parsed)
                assert cookies_parsed == cookies, f"cookies={cookies_parsed}, expected={cookies}"
            else:
                assert not _addresses_store
            triggers = get_triggers(root_span())
            if asm_enabled and attack:
                assert triggers is not None, "no appsec struct in root span"
                assert len(triggers) == 1
                assert triggers[0]["rule"]["id"] == "crs-942-100"
            else:
                assert triggers is None, f"asm JSON tag in root span: asm_enabled={asm_enabled}, attack={attack}"

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
        asm_enabled,
        encode_payload,
        content_type,
        payload_struct,
        attack,
    ):
        _addresses_store.clear()
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            payload = encode_payload(payload_struct)
            response = interface.client.post("/asm/", data=payload, content_type=content_type)
            assert self.status(response) == 200  # Have to add end points in each framework application.

            body = _addresses_store[0].get("http.request.body") if _addresses_store else None
            if asm_enabled and content_type != "text/plain":
                assert body in [
                    payload_struct,
                    {k: [v] for k, v in payload_struct.items()},
                ]
            else:
                assert not body  # DEV: Flask send {} for text/plain with asm

            triggers = get_triggers(root_span())

            if asm_enabled and attack and content_type != "text/plain":
                assert triggers is not None, "no appsec struct in root span"
                assert len(triggers) == 1
                assert triggers[0]["rule"]["id"] == "crs-942-100"
            else:
                assert triggers is None, "asm JSON tag in root span"

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

        with caplog.at_level(logging.DEBUG), override_global_config(
            dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_GOOD_PATH)
        ):
            self.update_tracer(interface)
            payload = '{"attack": "bad_payload",}</attack>&='
            response = interface.client.post("/asm/", data=payload, content_type=content_type)
            assert response.status_code == 200

    @pytest.mark.parametrize("asm_enabled", [True, False])
    def test_request_path_params(self, interface: Interface, root_span, asm_enabled):
        _addresses_store.clear()
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            response = interface.client.get("/asm/137/abc/")
            assert self.status(response) == 200
            path_params = _addresses_store[0].get("http.request.path_params") if _addresses_store else None
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

        with override_global_config(dict(_asm_enabled=asm_enabled, _client_ip_header=env_var)):
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
            ({"X-Real-Ip": rules._IP.BLOCKED}, True, "BLOCKED_RESPONSE_JSON", "application/json"),
            (
                {"X-Real-Ip": rules._IP.BLOCKED, "Accept": "text/html"},
                True,
                "BLOCKED_RESPONSE_HTML",
                "text/html",
            ),
            ({"X-Real-Ip": rules._IP.DEFAULT}, False, None, None),
        ],
    )
    def test_request_ipblock(
        self, interface: Interface, get_tag, root_span, asm_enabled, headers, blocked, body, content_type
    ):
        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
            self.update_tracer(interface)
            response = interface.client.get("/", headers=headers)
            if blocked and asm_enabled:
                assert self.status(response) == 403
                assert self.body(response) == getattr(constants, body, None)
                assert get_tag("actor.ip") == rules._IP.BLOCKED
                assert get_tag(http.STATUS_CODE) == "403"
                assert get_tag(http.URL) == "http://localhost:8000/"
                assert get_tag(http.METHOD) == "GET"
                self.check_single_rule_triggered("blk-001-001", root_span)
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
            ("?x=MoNiToR_ThAt_VaLuE", False),
            ("?x=BlOcK_ThAt_VaLuE&y=1", True),
            ("?x=monitor_that_value", False),
            ("?x=block_that_value&y=1", True),
        ],
    )
    def test_request_ipmonitor(
        self, interface: Interface, get_tag, root_span, asm_enabled, headers, monitored, bypassed, query, blocked
    ):
        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
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
                    self.check_rules_triggered(["blk-001-010", rule], root_span)
                else:
                    self.check_rules_triggered([rule], root_span)
            else:
                assert get_triggers(root_span()) is None, f"asm struct in root span {get_triggers(root_span())}"

    SUSPICIOUS_IP = "34.65.27.85"

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("ip", [SUSPICIOUS_IP, "132.202.34.7"])
    @pytest.mark.parametrize(
        ["agent", "event", "status"],
        [
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
                False,
                200,
            ),
            ("Arachni/v1.5.1", True, 200),
            ("dd-test-scanner-log-block", True, 403),
        ],
    )
    def test_request_suspicious_attacker_blocking(
        self, interface: Interface, get_tag, root_span, asm_enabled, ip, agent, event, status
    ):
        from ddtrace.ext import http

        with override_global_config(
            dict(
                _asm_enabled=asm_enabled,
                _asm_static_rule_file=rules.RULES_SAB,
            )
        ):
            self.update_tracer(interface)
            response = interface.client.get("/", headers={"User-Agent": agent, "X-Real-Ip": ip})
            if not asm_enabled:
                status = 200
                event = False
            if event and ip == self.SUSPICIOUS_IP:
                status = 402
            assert self.status(response) == status, f"status={self.status(response)}, expected={status}"
            assert get_tag(http.STATUS_CODE) == str(status), f"status_code={self.status(response)}, expected={status}"
            if event:
                self.check_single_rule_triggered(
                    "ua0-600-56x" if agent == "dd-test-scanner-log-block" else "ua0-600-12x", root_span
                )
            else:
                assert get_triggers(root_span()) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
    @pytest.mark.parametrize(("method", "kwargs"), [("get", {}), ("post", {"data": {"key": "value"}}), ("options", {})])
    def test_request_suspicious_request_block_match_method(
        self, interface: Interface, get_tag, root_span, asm_enabled, metastruct, method, kwargs
    ):
        # GET must be blocked
        from ddtrace.ext import http

        with override_global_config(
            dict(
                _asm_enabled=asm_enabled,
                _use_metastruct_for_triggers=metastruct,
                _asm_static_rule_file=rules.RULES_SRB_METHOD,
            )
        ):
            self.update_tracer(interface)
            response = getattr(interface.client, method)("/", **kwargs)
            assert get_tag(http.URL) == "http://localhost:8000/"
            assert get_tag(http.METHOD) == method.upper()
            if asm_enabled and method == "get":
                assert self.status(response) == 403
                assert get_tag(http.STATUS_CODE) == "403"
                assert self.body(response) == constants.BLOCKED_RESPONSE_JSON
                self.check_single_rule_triggered("tst-037-006", root_span)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type")
                    == "application/json"
                )
                assert self.headers(response)["content-type"] == "application/json"
            else:
                assert self.status(response) == 200
                assert get_tag(http.STATUS_CODE) == "200"
                assert get_triggers(root_span()) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
    @pytest.mark.parametrize(("uri", "blocked"), [("/.git", True), ("/legit", False)])
    def test_request_suspicious_request_block_match_uri(
        self, interface: Interface, get_tag, root_span, asm_enabled, metastruct, uri, blocked
    ):
        # GET must be blocked
        from ddtrace.ext import http

        with override_global_config(
            dict(
                _asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct, _asm_static_rule_file=rules.RULES_SRB
            )
        ):
            self.update_tracer(interface)
            response = interface.client.get(uri)
            assert get_tag(http.URL) == f"http://localhost:8000{uri}"
            assert get_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                assert self.status(response) == 403
                assert get_tag(http.STATUS_CODE) == "403"
                assert self.body(response) == constants.BLOCKED_RESPONSE_JSON
                self.check_single_rule_triggered("tst-037-002", root_span)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type")
                    == "application/json"
                )
                assert self.headers(response)["content-type"] == "application/json"
            else:
                assert self.status(response) == 404
                assert get_tag(http.STATUS_CODE) == "404"
                assert get_triggers(root_span()) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
    @pytest.mark.parametrize("uri", ["/waf/../"])
    def test_request_suspicious_request_block_match_uri_lfi(
        self, interface: Interface, get_tag, root_span, asm_enabled, metastruct, uri
    ):
        if interface.name in ("fastapi",):
            raise pytest.skip(f"TODO: fix {interface.name}")

        from ddtrace.ext import http

        with override_global_config(dict(_asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct)):
            self.update_tracer(interface)
            interface.client.get(uri)
            # assert get_tag(http.URL) == f"http://localhost:8000{uri}"
            assert get_tag(http.METHOD) == "GET"
            if asm_enabled:
                self.check_single_rule_triggered("crs-930-110", root_span)
            else:
                assert get_triggers(root_span()) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
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
        self, interface: Interface, get_tag, root_span, asm_enabled, metastruct, path, blocked
    ):
        from ddtrace.ext import http

        uri = f"/asm/4352/{path}"  # removing trailer slash will cause errors
        with override_global_config(
            dict(
                _asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct, _asm_static_rule_file=rules.RULES_SRB
            )
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
                self.check_single_rule_triggered("tst-037-007", root_span)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type")
                    == "application/json"
                )
                assert self.headers(response)["content-type"] == "application/json"
            else:
                assert self.status(response) == 200
                assert get_tag(http.STATUS_CODE) == "200"
                assert get_triggers(root_span()) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
    @pytest.mark.parametrize(
        ("query", "blocked"),
        [
            ("?toto=xtrace", True),
            ("?toto=ytrace", False),
            ("?toto=xtrace&toto=ytrace", True),
        ],
    )
    def test_request_suspicious_request_block_match_query_params(
        self, interface: Interface, get_tag, root_span, asm_enabled, metastruct, query, blocked
    ):
        if interface.name in ("django",) and query == "?toto=xtrace&toto=ytrace":
            raise pytest.skip(f"{interface.name} does not support multiple query params with same name")

        from ddtrace.ext import http

        uri = f"/{query}"
        with override_global_config(
            dict(
                _asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct, _asm_static_rule_file=rules.RULES_SRB
            )
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
                self.check_single_rule_triggered("tst-037-001", root_span)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type")
                    == "application/json"
                )
                assert self.headers(response)["content-type"] == "application/json"
            else:
                assert self.status(response) == 200
                assert get_tag(http.STATUS_CODE) == "200"
                assert get_triggers(root_span()) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
    @pytest.mark.parametrize(
        ("headers", "blocked"),
        [
            ({"User-Agent": "01972498723465"}, True),
            ({"User_Agent": "01973498523465"}, False),
        ],
    )
    def test_request_suspicious_request_block_match_request_headers(
        self, interface: Interface, get_tag, root_span, asm_enabled, metastruct, headers, blocked
    ):
        from ddtrace.ext import http

        with override_global_config(
            dict(
                _asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct, _asm_static_rule_file=rules.RULES_SRB
            )
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
                self.check_single_rule_triggered("tst-037-004", root_span)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type")
                    == "application/json"
                )
                assert self.headers(response)["content-type"] == "application/json"
            else:
                assert self.status(response) == 200
                assert get_tag(http.STATUS_CODE) == "200"
                assert get_triggers(root_span()) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
    @pytest.mark.parametrize(
        ("cookies", "blocked"),
        [
            ({"mytestingcookie_key": "jdfoSDGFkivRG_234"}, True),
            ({"mytestingcookie_key": "jdfoSDGEkivRH_234"}, False),
        ],
    )
    def test_request_suspicious_request_block_match_request_cookies(
        self, interface: Interface, get_tag, root_span, asm_enabled, metastruct, cookies, blocked
    ):
        from ddtrace.ext import http

        with override_global_config(
            dict(
                _asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct, _asm_static_rule_file=rules.RULES_SRB
            )
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
                self.check_single_rule_triggered("tst-037-008", root_span)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type")
                    == "application/json"
                )
                assert self.headers(response)["content-type"] == "application/json"
            else:
                assert self.status(response) == 200
                assert get_tag(http.STATUS_CODE) == "200"
                assert get_triggers(root_span()) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
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
        self, interface: Interface, get_tag, root_span, asm_enabled, metastruct, uri, status, blocked
    ):
        from ddtrace.ext import http

        with override_global_config(
            dict(
                _asm_enabled=asm_enabled,
                _use_metastruct_for_triggers=metastruct,
                _asm_static_rule_file=rules.RULES_SRB_RESPONSE,
            )
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
                self.check_single_rule_triggered(blocked, root_span)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type")
                    == "application/json"
                )
                assert self.headers(response)["content-type"] == "application/json"
            else:
                assert self.status(response) == status
                assert get_tag(http.STATUS_CODE) == str(status)
                assert get_triggers(root_span()) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
    @pytest.mark.parametrize(
        ("uri", "headers", "blocked"),
        [
            ("/asm/1/a", {}, False),
            ("/asm/1/a", {"header_name": "MagicKey_Al4h7iCFep9s1"}, "tst-037-009"),
            ("/asm/1/a", {"key": "anything", "header_name": "HiddenMagicKey_Al4h7iCFep9s1Value"}, "tst-037-009"),
            ("/asm/1/a", {"header_name": "NoWorryBeHappy"}, None),
        ],
    )
    def test_request_suspicious_request_block_match_response_headers(
        self, interface: Interface, get_tag, asm_enabled, metastruct, root_span, uri, headers, blocked
    ):
        from ddtrace.ext import http

        with override_global_config(
            dict(
                _asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct, _asm_static_rule_file=rules.RULES_SRB
            )
        ):
            self.update_tracer(interface)
            if headers:
                uri += "?headers=" + quote(",".join(f"{k}={v}" for k, v in headers.items()))
            response = interface.client.get(uri)
            # DEV Warning: encoded URL will behave differently
            assert get_tag(http.URL) == "http://localhost:8000" + uri
            assert get_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                assert self.status(response) == 403
                assert get_tag(http.STATUS_CODE) == "403"
                assert self.body(response) == constants.BLOCKED_RESPONSE_JSON
                self.check_single_rule_triggered(blocked, root_span)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type")
                    == "application/json"
                )
                assert self.headers(response)["content-type"] == "application/json"
                for k in headers:
                    assert k not in self.headers(response)
            else:
                assert self.status(response) == 200
                assert get_tag(http.STATUS_CODE) == "200"
                assert get_triggers(root_span()) is None
            assert "content-length" in self.headers(response)
            assert int(self.headers(response)["content-length"]) == len(self.body(response).encode())

    LARGE_BODY = {
        f"key_{i}": {f"key_{i}_{j}": {f"key_{i}_{j}_{k}": f"value_{i}_{j}_{k}" for k in range(4)} for j in range(4)}
        for i in range(254)
    }
    LARGE_BODY["attack"] = "yqrweytqwreasldhkuqwgervflnmlnli"

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
    @pytest.mark.parametrize(
        ("body", "content_type", "blocked"),
        [
            # json body must be blocked
            ('{"attack": "yqrweytqwreasldhkuqwgervflnmlnli"}', "application/json", "tst-037-003"),
            ('{"attack": "yqrweytqwreasldhkuqwgervflnmlnli"}', "application/json", "tst-037-003"),
            (json.dumps(LARGE_BODY), "application/json", "tst-037-003"),
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
        ids=["json", "text_json", "json_large", "xml", "form", "form_multipart", "text", "no_attack"],
    )
    def test_request_suspicious_request_block_match_request_body(
        self, interface: Interface, get_tag, asm_enabled, metastruct, root_span, body, content_type, blocked
    ):
        from ddtrace.ext import http

        with override_global_config(
            dict(
                _asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct, _asm_static_rule_file=rules.RULES_SRB
            )
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
                self.check_single_rule_triggered(blocked, root_span)
                assert (
                    get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type")
                    == "application/json"
                )
                assert self.headers(response)["content-type"] == "application/json"
            else:
                assert self.status(response) == 200
                assert get_tag(http.STATUS_CODE) == "200"
                assert get_triggers(root_span()) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
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
        ["headers", "use_html"],
        [
            ({"Accept": "text/html"}, True),
            ({"Accept": "application/json"}, False),
            ({}, False),
            ({"Accept": "text/*"}, True),
            ({"Accept": "text/*;q=0.8, application/*;q=0.7, */*;q=0.9"}, True),
            ({"Accept": "text/*;q=0.7, application/*;q=0.8, */*;q=0.9"}, False),
            ({"Accept": "text/html;q=0.9, text/*;q=0.8, application/json;q=0.85, */*;q=0.9"}, True),
        ],
    )
    def test_request_suspicious_request_block_custom_actions(
        self,
        interface: Interface,
        get_tag,
        asm_enabled,
        metastruct,
        root_span,
        query,
        status,
        rule_id,
        action,
        headers,
        use_html,
    ):
        from ddtrace.ext import http
        import ddtrace.internal.utils.http as http_cache

        # remove cache to avoid using templates from other tests
        http_cache._HTML_BLOCKED_TEMPLATE_CACHE = None
        http_cache._JSON_BLOCKED_TEMPLATE_CACHE = None
        try:
            uri = f"/?param={query}"
            with override_global_config(
                dict(
                    _asm_enabled=asm_enabled,
                    _use_metastruct_for_triggers=metastruct,
                    _asm_static_rule_file=rules.RULES_SRBCA,
                )
            ), override_env(
                dict(
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
                    self.check_single_rule_triggered(rule_id, root_span)

                    if action == "blocked":
                        content_type = (
                            "text/html" if "html" in query or (("auto" in query) and use_html) else "application/json"
                        )
                        assert (
                            get_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type")
                            == content_type
                        )
                        assert self.headers(response)["content-type"] == content_type
                        if content_type == "application/json":
                            assert json.loads(self.body(response)) == {
                                "errors": [{"title": "You've been blocked", "detail": "Custom content"}]
                            }
                    elif action == "redirect":
                        # assert not self.body(response)
                        assert self.location(response) == "https://www.datadoghq.com"
                else:
                    assert self.status(response) == 200
                    assert get_tag(http.STATUS_CODE) == "200"
                    assert get_triggers(root_span()) is None
        finally:
            # remove cache to avoid using custom templates in other tests
            http_cache._HTML_BLOCKED_TEMPLATE_CACHE = None
            http_cache._JSON_BLOCKED_TEMPLATE_CACHE = None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    def test_nested_appsec_events(
        self,
        interface: Interface,
        get_tag,
        root_span,
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
                self.check_rules_triggered(["nfd-000-001", "ua0-600-12x"], root_span)
            else:
                assert get_triggers(root_span()) is None

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
    def test_api_security_schemas(
        self,
        interface: Interface,
        get_tag,
        root_span,
        apisec_enabled,
        name,
        expected_value,
        headers,
        event,
        blocked,
    ):
        import base64
        import gzip
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        from ddtrace.ext import http
        import ddtrace.internal.telemetry

        with override_global_config(dict(_asm_enabled=True, _api_security_enabled=apisec_enabled)), mock_patch.object(
            ddtrace.internal.telemetry.telemetry_writer,
            "_namespace",
            MagicMock(),
        ) as mocked:
            self.update_tracer(interface)
            response = interface.client.post(
                "/asm/324/huj/?x=1&y=2",
                data='{"key": "passwd", "ids": [0, 1, 2, 3]}',
                cookies={"secret": "aBcDeF"},
                headers=headers,
                content_type="application/json",
            )
            assert asm_config._api_security_enabled == apisec_enabled

            assert self.status(response) == (403 if blocked else 200)
            assert get_tag(http.STATUS_CODE) == ("403" if blocked else "200")
            if event:
                assert get_triggers(root_span()) is not None
            else:
                assert get_triggers(root_span()) is None
            value = get_tag(name)
            if apisec_enabled:
                assert value, name
                api = json.loads(gzip.decompress(base64.b64decode(value)).decode())
                assert api, name
                if expected_value is not None:
                    if name == "_dd.appsec.s.res.body" and blocked:
                        assert api == [{"errors": [[[{"detail": [8], "title": [8]}]], {"len": 1}]}]
                    else:
                        assert any(
                            all(api[0].get(k) == v for k, v in expected[0].items()) for expected in expected_value
                        ), (
                            api,
                            name,
                        )
                telemetry_calls = {
                    (c.value, f"{ns.value}.{nm}", t): v for (c, ns, nm, v, t), _ in mocked.add_metric.call_args_list
                }
                assert (
                    "count",
                    "appsec.api_security.request.schema",
                    (("framework", interface.name),),
                ) in telemetry_calls
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

        with override_global_config(dict(_asm_enabled=True, _api_security_enabled=apisec_enabled)):
            self.update_tracer(interface)
            response = interface.client.post(
                "/",
                data=json.dumps(payload),
                content_type="application/json",
            )
            assert self.status(response) == 200
            assert get_tag(http.STATUS_CODE) == "200"
            assert asm_config._api_security_enabled == apisec_enabled

            value = get_tag("_dd.appsec.s.req.body")
            if apisec_enabled:
                assert value
                api = json.loads(gzip.decompress(base64.b64decode(value)).decode())
                assert api == expected_value
            else:
                assert value is None

    @pytest.mark.parametrize("apisec_enabled", [True, False])
    @pytest.mark.parametrize("priority", ["keep", "drop"])
    @pytest.mark.parametrize("delay", [0.0, 120.0])
    def test_api_security_sampling(self, interface: Interface, get_tag, apisec_enabled, priority, delay):
        from ddtrace.ext import http

        payload = {"mastercard": "5123456789123456"}
        with override_global_config(
            dict(_asm_enabled=True, _api_security_enabled=apisec_enabled, _api_security_sample_delay=delay)
        ):
            self.update_tracer(interface)
            response = interface.client.post(
                f"/asm/?priority={priority}",
                data=json.dumps(payload),
                content_type="application/json",
            )
            assert self.status(response) == 200
            assert get_tag(http.STATUS_CODE) == "200"
            assert asm_config._api_security_enabled == apisec_enabled

            value = get_tag("_dd.appsec.s.req.body")
            if apisec_enabled and priority == "keep":
                assert value
            else:
                assert value is None
            # second request must be ignored
            self.update_tracer(interface)
            response = interface.client.post(
                f"/asm/?priority={priority}",
                data=json.dumps(payload),
                content_type="application/json",
            )
            assert self.status(response) == 200
            assert get_tag(http.STATUS_CODE) == "200"
            assert asm_config._api_security_enabled == apisec_enabled

            value = get_tag("_dd.appsec.s.req.body")
            if apisec_enabled and priority == "keep" and delay == 0.0:
                assert value
            else:
                assert value is None

    def test_request_invalid_rule_file(self, interface):
        """
        When the rule file is invalid, the tracer should not crash or prevent normal behavior
        """
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_BAD_VERSION)):
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

    @pytest.mark.parametrize("asm_enabled", [True, False])
    def test_asm_enabled_headers(self, asm_enabled, interface, get_tag, root_span):
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            response = interface.client.get(
                "/",
                headers={"accept": "testheaders/a1b2c3", "user-agent": "UnitTestAgent", "content-type": "test/x0y9z8"},
            )
            assert response.status_code == 200
            assert self.status(response) == 200
            if asm_enabled:
                assert get_tag("http.request.headers.accept") == "testheaders/a1b2c3"
                assert get_tag("http.request.headers.user-agent") == "UnitTestAgent"
                assert get_tag("http.request.headers.content-type") == "test/x0y9z8"
            else:
                assert get_tag("http.request.headers.accept") is None
                assert get_tag("http.request.headers.user-agent") is None
                assert get_tag("http.request.headers.content-type") is None

    @pytest.mark.parametrize(
        "header",
        [
            "X-Amzn-Trace-Id",
            "Cloudfront-Viewer-Ja3-Fingerprint",
            "Cf-Ray",
            "X-Cloud-Trace-Context",
            "X-Appgw-Trace-id",
            "Akamai-User-Risk",
            "X-SigSci-RequestID",
            "X-SigSci-Tags",
        ],
    )
    @pytest.mark.parametrize("asm_enabled", [True, False])
    # RFC: https://docs.google.com/document/d/1xf-s6PtSr6heZxmO_QLUtcFzY_X_rT94lRXNq6-Ghws/edit
    def test_asm_waf_integration_identify_requests(self, asm_enabled, header, interface, get_tag, root_span):
        import random
        import string

        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            random_value = "".join(random.choices(string.ascii_letters + string.digits, k=random.randint(6, 128)))
            response = interface.client.get(
                "/",
                headers={header: random_value},
            )
            assert response.status_code == 200
            assert self.status(response) == 200
            meta_tagname = "http.request.headers." + header.lower()
            if asm_enabled:
                assert get_tag(meta_tagname) == random_value
            else:
                assert get_tag(meta_tagname) is None

    def test_global_callback_list_length(self, interface):
        from ddtrace.appsec import _asm_request_context

        with override_global_config(
            dict(
                _asm_enabled=True,
                _api_security_enabled=True,
                _telemetry_enabled=True,
            )
        ):
            self.update_tracer(interface)
            assert ddtrace.config._remote_config_enabled
            for _ in range(20):
                response = interface.client.get("/new_service/awesome_test")
            assert self.status(response) == 200
            assert self.body(response) == "awesome_test"
            # only two global callbacks are expected for API Security and Nested Events
            assert len(_asm_request_context.GLOBAL_CALLBACKS.get(_asm_request_context._CONTEXT_CALL, [])) == 1

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
    def test_stream_response(
        self,
        interface: Interface,
        get_tag,
        asm_enabled,
        metastruct,
        root_span,
    ):
        if interface.name != "fastapi":
            raise pytest.skip("only fastapi tests have support for stream response")
        with override_global_config(
            dict(
                _asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct, _asm_static_rule_file=rules.RULES_SRB
            )
        ):
            self.update_tracer(interface)
            response = interface.client.get("/stream/")
            assert self.body(response) == "0123456789"

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("ep_enabled", [True, False])
    @pytest.mark.parametrize(
        ["endpoint", "parameters", "rule", "top_functions"],
        [("lfi", "filename1=/etc/passwd&filename2=/etc/master.passwd", "rasp-930-100", ("rasp",))]
        + [
            ("ssrf", f"url_{p1}_1=169.254.169.254&url_{p2}_2=169.254.169.253", "rasp-934-100", (f1, f2))
            for (p1, f1), (p2, f2) in itertools.product(
                [
                    ("urlopen_string", "urlopen"),
                    ("urlopen_request", "urlopen"),
                    ("requests", "request"),
                ],
                repeat=2,
            )
        ]
        + [("sql_injection", "user_id_1=1 OR 1=1&user_id_2=1 OR 1=1", "rasp-942-100", ("dispatch",))]
        + [
            (
                "shell_injection",
                "cmdsys_1=$(cat /etc/passwd 1>%262 ; echo .)&cmdrun_2=$(uname -a 1>%262 ; echo .)",
                "rasp-932-100",
                ("system", "rasp"),
            )
        ]
        + [
            (
                "command_injection",
                "cmda_1=/sbin/ping&cmds_2=/usr/bin/ls%20-la",
                "rasp-932-110",
                ("Popen", "rasp"),
            )
        ],
    )
    @pytest.mark.parametrize(
        ("rule_file", "action_level", "status_expected"),
        # action_level 0: no action, 1: report, 2: block
        [
            (rules.RULES_EXPLOIT_PREVENTION, 1, 200),
            (rules.RULES_EXPLOIT_PREVENTION_BLOCKING, 2, 403),
            (rules.RULES_EXPLOIT_PREVENTION_REDIRECTING, 2, 301),
            (rules.RULES_EXPLOIT_PREVENTION_DISABLED, 0, 200),
        ],
    )
    def test_exploit_prevention(
        self,
        interface,
        root_span,
        get_tag,
        get_metric,
        asm_enabled,
        ep_enabled,
        endpoint,
        parameters,
        rule,
        top_functions,
        rule_file,
        action_level,
        status_expected,
    ):
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        from ddtrace.appsec._constants import APPSEC
        from ddtrace.ext import http
        import ddtrace.internal.telemetry

        def validate_top_function(trace):
            top_function = trace["frames"][0]["function"]
            if any(top_function.endswith(top_function) for top_function in top_functions) or (
                asm_config._iast_enabled and top_function.endswith("ast_function")
            ):
                return True
            # some wrapper functions may be present
            assert top_function == "__call__"
            top_function = trace["frames"][1]["function"]
            return any(top_function.endswith(top_function) for top_function in top_functions) or (
                asm_config._iast_enabled and top_function.endswith("ast_function")
            )

        with override_global_config(
            dict(_asm_enabled=asm_enabled, _ep_enabled=ep_enabled, _asm_static_rule_file=rule_file)
        ), mock_patch.object(ddtrace.internal.telemetry.telemetry_writer, "_namespace", MagicMock()) as mocked:
            self.update_tracer(interface)
            assert asm_config._asm_enabled == asm_enabled
            response = interface.client.get(f"/rasp/{endpoint}/?{parameters}")
            code = status_expected if asm_enabled and ep_enabled else 200
            assert self.status(response) == code, (self.status(response), code)
            assert get_tag(http.STATUS_CODE) == str(code), (get_tag(http.STATUS_CODE), code)
            if code == 200:
                assert self.body(response).startswith(f"{endpoint} endpoint")
            telemetry_calls = {
                (c.value, f"{ns.value}.{nm}", t): v for (c, ns, nm, v, t), _ in mocked.add_metric.call_args_list
            }
            if asm_enabled and ep_enabled and action_level > 0:
                self.check_rules_triggered([rule] * (1 if action_level == 2 else 2), root_span)
                assert self.check_for_stack_trace(root_span)
                for trace in self.check_for_stack_trace(root_span):
                    assert "frames" in trace
                    assert validate_top_function(
                        trace
                    ), f"unknown top function {trace['frames'][0]} {[t['function'] for t in trace['frames'][:4]]}"
                # assert mocked.call_args_list == []
                expected_rule_type = "command_injection" if endpoint == "shell_injection" else endpoint
                expected_variant = (
                    "exec" if endpoint == "command_injection" else "shell" if endpoint == "shell_injection" else None
                )
                matches = [t for c, n, t in telemetry_calls if c == "count" and n == "appsec.rasp.rule.match"]
                # import delayed to get the correct version
                from ddtrace.appsec._metrics import ddwaf_version

                if expected_variant:
                    expected_tags = (
                        ("rule_type", expected_rule_type),
                        ("rule_variant", expected_variant),
                        ("waf_version", ddwaf_version),
                        ("event_rules_version", "rules_rasp"),
                    )
                else:
                    expected_tags = (
                        ("rule_type", expected_rule_type),
                        ("waf_version", ddwaf_version),
                        ("event_rules_version", "rules_rasp"),
                    )
                match_expected_tags = expected_tags + (("block", "irrelevant" if action_level < 2 else "success"),)
                assert matches == [match_expected_tags], (matches, match_expected_tags)
                evals = [t for c, n, t in telemetry_calls if c == "count" and n == "appsec.rasp.rule.eval"]
                # there may have been multiple evaluations of other rules too
                assert expected_tags in evals, (expected_tags, evals)
                if action_level == 2:
                    assert get_tag("rasp.request.done") is None, get_tag("rasp.request.done")
                else:
                    assert get_tag("rasp.request.done") == endpoint, get_tag("rasp.request.done")
                assert get_metric(APPSEC.RASP_DURATION) is not None
                assert get_metric(APPSEC.RASP_DURATION_EXT) is not None
                assert get_metric(APPSEC.RASP_RULE_EVAL) is not None
                assert float(get_metric(APPSEC.RASP_DURATION_EXT)) >= float(get_metric(APPSEC.RASP_DURATION))
                assert int(get_metric(APPSEC.RASP_RULE_EVAL)) > 0
            else:
                for _, n, _ in telemetry_calls:
                    assert "rasp" not in n
                assert get_triggers(root_span()) is None
                assert self.check_for_stack_trace(root_span) == []
                assert get_tag("rasp.request.done") == endpoint, get_tag("rasp.request.done")

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("auto_events_enabled", [True, False])
    @pytest.mark.parametrize("local_mode", ["disabled", "identification", "anonymization"])
    @pytest.mark.parametrize("rc_mode", [None, "disabled", "identification", "anonymization"])
    @pytest.mark.parametrize(
        ("user", "password", "status_code", "user_id"),
        [
            ("test", "1234", 200, "social-security-id"),
            ("testuuid", "12345", 401, "591dc126-8431-4d0f-9509-b23318d3dce4"),
            ("zouzou", "12345", 401, ""),
        ],
    )
    def test_auto_user_events(
        self,
        interface,
        root_span,
        get_tag,
        asm_enabled,
        auto_events_enabled,
        local_mode,
        rc_mode,
        user,
        password,
        status_code,
        user_id,
    ):
        from ddtrace.appsec._utils import _hash_user_id

        with override_global_config(
            dict(
                _asm_enabled=asm_enabled,
                _auto_user_instrumentation_local_mode=local_mode,
                _auto_user_instrumentation_rc_mode=rc_mode,
                _auto_user_instrumentation_enabled=auto_events_enabled,
            )
        ):
            mode = rc_mode if rc_mode is not None else local_mode
            self.update_tracer(interface)
            response = interface.client.get(f"/login/?username={user}&password={password}")
            assert self.status(response) == status_code
            assert get_tag("http.status_code") == str(status_code)
            username = user if mode == "identification" else _hash_user_id(user)
            user_id_hash = user_id if mode == "identification" else _hash_user_id(user_id)
            if asm_enabled and auto_events_enabled and mode != "disabled":
                if status_code == 401:
                    assert get_tag("appsec.events.users.login.failure.track") == "true"
                    assert get_tag("_dd.appsec.events.users.login.failure.auto.mode") == mode
                    assert get_tag("appsec.events.users.login.failure.usr.id") == (
                        user_id_hash if user_id else username
                    )
                    assert get_tag("appsec.events.users.login.failure.usr.exists") == str(user == "testuuid").lower()
                    # check for manual instrumentation tag in manual instrumented frameworks
                    if interface.name in ["flask", "fastapi"]:
                        assert get_tag("_dd.appsec.events.users.login.failure.sdk") == "true"
                    else:
                        assert get_tag("_dd.appsec.events.users.login.success.sdk") is None
                    if mode == "identification":
                        assert get_tag("_dd.appsec.usr.login") == user
                    elif mode == "anonymization":
                        assert get_tag("_dd.appsec.usr.login") == _hash_user_id(user)
                else:
                    assert get_tag("appsec.events.users.login.success.track") == "true"
                    assert get_tag("usr.id") == user_id_hash
                    assert get_tag("_dd.appsec.usr.id") == user_id_hash
                    if mode == "identification":
                        assert get_tag("_dd.appsec.usr.login") == user
                    # check for manual instrumentation tag in manual instrumented frameworks
                    if interface.name in ["flask", "fastapi"]:
                        assert get_tag("_dd.appsec.events.users.login.success.sdk") == "true"
                    else:
                        assert get_tag("_dd.appsec.events.users.login.success.sdk") is None

            else:
                assert get_tag("usr.id") is None
                assert not any(tag.startswith("appsec.events.users.login") for tag in root_span()._meta)
                assert not any(tag.startswith("_dd_appsec.events.users.login") for tag in root_span()._meta)
            # check for fingerprints when user events
            if asm_enabled:
                assert get_tag(asm_constants.FINGERPRINTING.HEADER)
                assert get_tag(asm_constants.FINGERPRINTING.NETWORK)
                assert get_tag(asm_constants.FINGERPRINTING.ENDPOINT)
                assert get_tag(asm_constants.FINGERPRINTING.SESSION)
            else:
                # assert get_tag(asm_constants.FINGERPRINTING.HEADER) is None
                assert get_tag(asm_constants.FINGERPRINTING.NETWORK) is None
                assert get_tag(asm_constants.FINGERPRINTING.ENDPOINT) is None
                assert get_tag(asm_constants.FINGERPRINTING.SESSION) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("user_agent", ["dd-test-scanner-log-block", "UnitTestAgent"])
    def test_fingerprinting(self, interface, root_span, get_tag, asm_enabled, user_agent):
        with override_global_config(dict(_asm_enabled=asm_enabled, _asm_static_rule_file=None)):
            self.update_tracer(interface)
            response = interface.client.post(
                "/asm/324/huj/?x=1&y=2", headers={"User-Agent": user_agent}, data={"test": "attack"}
            )
            code = 403 if asm_enabled and user_agent == "dd-test-scanner-log-block" else 200
            assert self.status(response) == code
            assert get_tag("http.status_code") == str(code)
            # check for fingerprints when security events
            if asm_enabled:
                assert get_tag(asm_constants.FINGERPRINTING.HEADER)
                assert get_tag(asm_constants.FINGERPRINTING.NETWORK)
                assert get_tag(asm_constants.FINGERPRINTING.ENDPOINT)
                assert get_tag(asm_constants.FINGERPRINTING.SESSION)
            else:
                assert get_tag(asm_constants.FINGERPRINTING.HEADER) is None
                assert get_tag(asm_constants.FINGERPRINTING.NETWORK) is None
                assert get_tag(asm_constants.FINGERPRINTING.ENDPOINT) is None
                assert get_tag(asm_constants.FINGERPRINTING.SESSION) is None

    def test_iast(self, interface, root_span, get_tag):
        from ddtrace.ext import http

        url = "/rasp/command_injection/?cmds=."
        self.update_tracer(interface)
        response = interface.client.get(url)
        assert self.status(response) == 200
        assert get_tag(http.STATUS_CODE) == "200"
        assert self.body(response).startswith("command_injection endpoint")
        stack_traces = self.get_stack_trace(root_span, "vulnerability")
        if asm_config._iast_enabled:
            assert get_tag("_dd.iast.json") is not None
            # checking for iast stack traces
            assert stack_traces
        else:
            assert get_tag("_dd.iast.json") is None
            assert stack_traces == []


@contextmanager
def test_tracer():
    from ddtrace.internal import core

    tracer = DummyTracer()
    original_tracer = ddtrace.tracer
    ddtrace.tracer = tracer
    core.tracer = tracer

    # Yield to our test
    yield tracer
    tracer.pop()
    ddtrace.tracer = original_tracer
    core.tracer = original_tracer


@contextmanager
def post_tracer(interface):
    original_tracer = getattr(ddtrace.trace.Pin.get_from(interface.framework), "tracer", None)
    ddtrace.trace.Pin._override(interface.framework, tracer=interface.tracer)
    yield
    if original_tracer is not None:
        ddtrace.trace.Pin._override(interface.framework, tracer=original_tracer)
