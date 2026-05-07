import itertools
import json
import sys
from typing import Any
from typing import ClassVar
from typing import Generator
from urllib.parse import quote
from urllib.parse import urlencode

import pytest

import ddtrace
from ddtrace.appsec import _asm_request_context
from ddtrace.appsec import _constants as asm_constants
from ddtrace.appsec._utils import get_triggers
from ddtrace.ext import http
from ddtrace.internal import constants
from ddtrace.internal import core
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils.http import _format_template
import tests.appsec.rules as rules
from tests.utils import override_env
from tests.utils import override_global_config


SECID: str = "[security_response_id]"

try:
    from ddtrace.appsec import track_user_sdk as _track_user_sdk  # noqa: F401

    USER_SDK_V2 = True
except ImportError:
    USER_SDK_V2 = False

_init_finalize = _asm_request_context.finalize_asm_env
_addresses_store = []


def finalize_wrapper(env):
    _addresses_store.append(env.waf_addresses)
    _init_finalize(env)


_asm_request_context.finalize_asm_env = finalize_wrapper


class Interface:
    def __init__(self, name: str, framework, client):
        self.name: str = name
        self.framework = framework
        self.client = client
        self.version: tuple[int, ...] = ()

    def __repr__(self):
        return f"Interface({self.name}[{self.version}] Python[{sys.version}])"


def payload_to_xml(payload: dict[str, str]) -> str:
    return "".join(f"<{k}>{v}</{k}>" for k, v in payload.items())


def payload_to_plain_text(payload: dict[str, str]) -> str:
    return "\n".join(f"{k}={v}" for k, v in payload.items())


class _Contrib_TestClass_Base:
    """
    Common infrastructure for all threat test classes.
    Contains fixtures, helper methods, and abstract response accessors.
    No test methods — subclasses add those.
    """

    SERVER_PORT = 8000

    @pytest.fixture
    def interface(self, printer) -> Generator[Interface, Any, None]:
        raise NotImplementedError

    @pytest.fixture(autouse=True)
    def xfail_by_interface(self, request, interface):
        if request.node.get_closest_marker("xfail_interface"):
            if interface.name in request.node.get_closest_marker("xfail_interface").args:
                skip = request.node.get_closest_marker("xfail_interface").kwargs.get("skip", False)
                if skip:
                    pytest.skip(f"xfail on this platform: {interface.name}")
                else:
                    request.node.add_marker(pytest.mark.xfail(reason=f"xfail on this platform: {interface.name}"))

    def status(self, response) -> int:
        raise NotImplementedError

    def headers(self, response) -> dict[str, str]:
        raise NotImplementedError

    def location(self, response) -> str:
        raise NotImplementedError

    def body(self, response) -> str:
        raise NotImplementedError

    def get_stack_trace(self, entry_span, namespace):
        appsec_traces = entry_span()._get_struct_tag(asm_constants.STACK_TRACE.TAG) or {}
        stacks = appsec_traces.get(namespace, [])
        return stacks

    def check_for_stack_trace(self, entry_span):
        exploit = self.get_stack_trace(entry_span, "exploit")
        stack_ids = sorted(set(t["id"] for t in exploit))
        triggers = get_triggers(entry_span())
        stack_id_in_triggers = sorted(set(t["stack_id"] for t in (triggers or []) if "stack_id" in t))
        assert stack_ids == stack_id_in_triggers, f"stack_ids={stack_ids}, stack_id_in_triggers={stack_id_in_triggers}"
        return exploit

    def check_single_rule_triggered(self, rule_id: str, entry_span) -> str:
        triggers = get_triggers(entry_span())
        assert triggers is not None, "no appsec struct in root span"
        result = [t["rule"]["id"] for t in triggers]
        assert result == [rule_id], f"result={result}, expected={[rule_id]}"
        return triggers[0].get("security_response_id", None)

    def check_rules_triggered(self, rule_id: list[str], entry_span):
        triggers = get_triggers(entry_span())
        assert triggers is not None, "no appsec struct in root span"
        result = sorted([t["rule"]["id"] for t in triggers])
        assert result == rule_id, f"result={result}, expected={rule_id}"

    def check_rule_triggered(self, rule_id: str, entry_span):
        """Check that the given rule_id is among the triggered rules."""
        triggers = get_triggers(entry_span())
        assert triggers is not None, "no appsec struct in root span"
        result = [t["rule"]["id"] for t in triggers]
        assert rule_id in result, f"rule {rule_id} not found in triggers: {result}"

    def _get_waf_info(self):
        """Return the current WAF info from the AppSecSpanProcessor."""
        from ddtrace.appsec._processor import AppSecSpanProcessor

        processor = AppSecSpanProcessor._instance
        assert processor is not None, "AppSecSpanProcessor not initialized"
        return processor._ddwaf.info

    def check_waf_no_errors(self):
        """Check that the WAF has no errors after a rule update."""
        info = self._get_waf_info()
        assert info.failed == 0, f"WAF has {info.failed} failed rules after update"
        assert info.errors == "", f"WAF has errors after update: {info.errors}"

    def check_waf_errors(self, expected_failed: int, expected_errors: dict):
        """Check that the WAF reports the expected errors after a rule update."""
        info = self._get_waf_info()
        assert info.failed == expected_failed, f"Expected {expected_failed} failed rules, got {info.failed}"
        errors = json.loads(info.errors)
        assert errors == expected_errors, f"Expected WAF errors {expected_errors}, got {errors}"

    def assert_blocked(self, response, entry_span, get_entry_span_tag, rule_id, content_type="application/json"):
        """Assert that the response was blocked with the expected rule, status 403, and content type."""
        assert (st := self.status(response)) == 403, f"status mismatch {st}"
        assert (st := get_entry_span_tag(http.STATUS_CODE)) == "403", f"status code mismatch {st}"
        block_id = self.check_single_rule_triggered(rule_id, entry_span)
        expected_body = _format_template(
            constants.BLOCKED_RESPONSE_HTML if content_type == "text/html" else constants.BLOCKED_RESPONSE_JSON,
            block_id,
        )
        assert self.body(response) == expected_body, self.body(response)
        assert (
            get_entry_span_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type")
            == content_type
        )
        assert self.headers(response)["content-type"] == content_type

    def update_tracer(self, interface):
        interface.tracer._span_aggregator.writer._api_version = "v0.4"
        interface.tracer._recreate()
        # update sampling rate for api10
        from ddtrace.appsec._asm_request_context import UINT64_MAX
        from ddtrace.appsec._asm_request_context import DownstreamRequests

        DownstreamRequests.sampling_rate = int(asm_config._dr_sample_rate * UINT64_MAX)
        assert asm_config._asm_libddwaf_available

    def setup_method(self, method):
        """called before each test method"""
        _addresses_store.clear()


class Contrib_TestClass_For_Threats(_Contrib_TestClass_Base):
    """
    Factorized test class for threats tests on all supported frameworks
    """

    # Expected ep.path values (as stored in the endpoint collection) that must
    # be discovered. Each child class must override with framework-specific routes.
    ENDPOINT_DISCOVERY_EXPECTED_PATHS: ClassVar[set[str]] = set()

    @staticmethod
    def endpoint_path_to_uri(path: str) -> str:
        """Convert an ep.path from the endpoint collection to a requestable URI.

        Each framework test class must implement this with its own route format logic.
        """
        raise NotImplementedError("Subclasses must implement endpoint_path_to_uri")

    @pytest.mark.parametrize("asm_enabled", [True, False])
    def test_healthcheck(self, interface: Interface, get_entry_span_tag, asm_enabled: bool):
        # you can disable any test in a framework like that:
        # if interface.name == "fastapi":
        #    raise pytest.skip("fastapi does not have a healthcheck endpoint")
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            response = interface.client.get("/")
            assert self.status(response) == 200, f"healthcheck failed {self.status}"
            assert self.body(response) == "ok ASM"
            from ddtrace.internal.settings.asm import config as asm_config

            assert asm_config._asm_enabled is asm_enabled
            assert get_entry_span_tag("http.status_code") == "200"
            assert self.headers(response)["content-type"] in ("text/html; charset=utf-8", "text/html"), (
                f"{self.headers(response)}"
            )

    def test_simple_attack_on_404(self, interface: Interface, entry_span, get_entry_span_tag):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/.git?q=1")
            assert self.status(response) == 404
            triggers = get_triggers(entry_span())
            assert get_entry_span_tag("http.response.headers.content-length")
            assert triggers is not None, "no appsec struct in root span"

    def test_simple_attack_timeout(self, interface: Interface, entry_span, get_entry_span_metric):
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        import ddtrace.internal.telemetry

        with (
            override_global_config(dict(_asm_enabled=True, _waf_timeout=0.001)),
            mock_patch.object(
                ddtrace.internal.telemetry.telemetry_writer,
                "_namespace",
                MagicMock(),
            ) as mocked,
        ):
            self.update_tracer(interface)
            query_params = urlencode({"q": "1"})
            url = f"/?{query_params}"
            response = interface.client.get(url, headers={"User-Agent": "Arachni/v1.5.1"})
            assert self.status(response) == 200
            assert get_entry_span_metric("_dd.appsec.waf.timeouts") > 0, (
                entry_span()._get_str_attributes(),
                entry_span()._get_numeric_attributes(),
            )
            args_list = [
                (args[0].value, args[1].value) + args[2:]
                for args, kwargs in mocked.add_metric.call_args_list
                if args[2] == "waf.requests"
            ]
            assert len(args_list) == 1
            assert ("waf_timeout", "true") in args_list[0][4]

    def test_api_endpoint_discovery(self, interface: Interface, find_resource):
        """Check that API endpoint discovery works in the framework.

        Also ensure the resource name is set correctly.
        """
        from ddtrace.internal.endpoints import endpoint_collection

        expected = self.ENDPOINT_DISCOVERY_EXPECTED_PATHS
        assert expected, "ENDPOINT_DISCOVERY_EXPECTED_PATHS must be set on the test class"

        found: set[str] = set()
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            # required to load the endpoints
            interface.client.get("/")
            collection = endpoint_collection.endpoints
            assert collection, f"no collection {collection}"
            for ep in collection:
                assert ep.method
                # path could be empty, but must be a string
                assert isinstance(ep.path, str)
                assert ep.resource_name
                assert ep.operation_name
                if ep.method not in ("GET", "*", "POST") or ep.path.startswith("/static"):
                    continue
                found.add(ep.path)
                uri = self.endpoint_path_to_uri(ep.path)
                response = (
                    interface.client.post(uri, data=json.dumps({"data": "content"}), content_type="application/json")
                    if ep.method == "POST"
                    else interface.client.get(uri)
                )
                assert self.status(response) in (
                    200,
                    401,
                ), f"ep.path failed: [{self.status(response)}] {ep.path} -> {uri}"
                resource = "GET" + ep.resource_name[1:] if ep.resource_name.startswith("* ") else ep.resource_name
                assert find_resource(resource)
        assert expected <= found, f"missing paths: {expected - found}"

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("user_agent", "priority"),
        [("Mozilla/5.0", False), ("Arachni/v1.5.1", True), ("dd-test-scanner-log-block", True)],
    )
    def test_priority(self, interface: Interface, entry_span, get_entry_span_tag, asm_enabled, user_agent, priority):
        """Check that we only set manual keep for traces with appsec events."""
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            response = interface.client.get("/", headers={"User-Agent": user_agent})
            assert self.status(response) == (
                403 if user_agent == "dd-test-scanner-log-block" and asm_enabled else 200
            ), f"status code {self.status(response)} mismatch for user agent {user_agent}"
        span_priority = entry_span()._span.context.sampling_priority
        assert (span_priority == 2) if asm_enabled and priority else (span_priority < 2), (
            f"span priority {span_priority} mismatch for user agent {user_agent} with asm_enabled={asm_enabled}"
        )

    def test_querystrings(self, interface: Interface, entry_span):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/?a=1&b&c=d")
            assert self.status(response) == 200
            assert _addresses_store, "no waf addresses stored"
            query = _addresses_store[0].get("http.request.query")
            assert query in [
                {"a": "1", "b": "", "c": "d"},
                {"a": ["1"], "b": [""], "c": ["d"]},
                {"a": [b"1"], "b": [b""], "c": [b"d"]},
                {"a": ["1"], "c": ["d"]},
            ], f"querystrings={query}"

    def test_no_querystrings(self, interface: Interface, entry_span):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/")
            assert self.status(response) == 200
            assert _addresses_store, "no waf addresses stored"
            assert not _addresses_store[0].get("http.request.query")

    def test_truncation_tags(self, interface: Interface, get_entry_span_metric):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            body: dict[str, Any] = {"val": "x" * 5000}
            body.update({f"a_{i}": i for i in range(517)})
            response = interface.client.post(
                "/asm/",
                data=json.dumps(body),
                content_type="application/json",
            )
            assert self.status(response) == 200, f"status code {self.status(response)} mismatch"
            assert (met := get_entry_span_metric(asm_constants.APPSEC.TRUNCATION_STRING_LENGTH)), (
                f"no {asm_constants.APPSEC.TRUNCATION_STRING_LENGTH} metric {met}"
            )
            # 12030 is due to response encoding
            truncation_str_len = int(get_entry_span_metric(asm_constants.APPSEC.TRUNCATION_STRING_LENGTH))
            assert truncation_str_len == 12029, truncation_str_len
            assert get_entry_span_metric(asm_constants.APPSEC.TRUNCATION_CONTAINER_SIZE)
            assert int(get_entry_span_metric(asm_constants.APPSEC.TRUNCATION_CONTAINER_SIZE)) == 518

    def test_truncation_telemetry(self, interface: Interface, get_entry_span_metric):
        from unittest.mock import ANY
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        import ddtrace.internal.telemetry

        with (
            override_global_config(dict(_asm_enabled=True)),
            mock_patch.object(
                ddtrace.internal.telemetry.telemetry_writer,
                "_namespace",
                MagicMock(),
            ) as mocked,
        ):
            self.update_tracer(interface)
            body: dict[str, Any] = {"val": "x" * 5000}
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
                        ("waf_error", "false"),
                        ("rate_limited", "false"),
                    ),
                ),
            ], args_list

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("cookies", "attack"),
        [({"mytestingcookie_key": "mytestingcookie_value"}, False), ({"attack": "1' or '1' = '1'"}, True)],
    )
    def test_request_cookies(self, interface: Interface, entry_span, asm_enabled, cookies, attack):
        with override_global_config(dict(_asm_enabled=asm_enabled, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
            self.update_tracer(interface)
            response = interface.client.get("/", cookies=cookies)
            assert self.status(response) == 200
            if asm_enabled:
                assert _addresses_store, "no waf addresses stored"
                cookies_parsed = _addresses_store[0].get("http.request.cookies")
                # required for flask that is sending a ImmutableMultiDict
                if isinstance(cookies_parsed, dict):
                    cookies_parsed = dict(cookies_parsed)
                assert cookies_parsed == cookies, f"cookies={cookies_parsed}, expected={cookies}"
            else:
                assert not _addresses_store
            triggers = get_triggers(entry_span())
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
        entry_span,
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
            body = _addresses_store[0].get("http.request.body") if _addresses_store else None
            if asm_enabled and content_type != "text/plain":
                alternatives = [
                    payload_struct,
                    {k: [v] for k, v in payload_struct.items()},
                    {k: v.encode() for k, v in payload_struct.items()},
                ]
                assert body in alternatives, f"body {body} not found in {alternatives}"
            else:
                assert not body  # DEV: Flask send {} for text/plain with asm

            triggers = get_triggers(entry_span())

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
    def test_request_body_bad(self, caplog, interface: Interface, entry_span, get_entry_span_tag, content_type):
        # Ensure no crash when body is not parsable
        import logging

        with (
            caplog.at_level(logging.DEBUG),
            override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_GOOD_PATH)),
        ):
            self.update_tracer(interface)
            payload = '{"attack": "bad_payload",}</attack>&='
            response = interface.client.post("/asm/", data=payload, content_type=content_type)
            assert self.status(response) == 200

    @pytest.mark.parametrize("asm_enabled", [True, False])
    def test_request_path_params(self, interface: Interface, entry_span, asm_enabled):
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            response = interface.client.get("/asm/137/abc/")
            assert self.status(response) == 200
            path_params = _addresses_store[0].get("http.request.path_params") if _addresses_store else None
            if asm_enabled:
                assert path_params, path_params
                assert (path_params["param_str"] if isinstance(path_params, dict) else path_params[1]) in (
                    "abc",
                    b"abc",
                )
                assert int((path_params["param_int"] if isinstance(path_params, dict) else path_params[0])) == 137
            else:
                assert path_params is None

    def test_useragent(self, interface: Interface, entry_span, get_entry_span_tag):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/", headers={"user-agent": "test/1.2.3"})
            assert self.status(response) == 200
            assert (st := get_entry_span_tag(http.USER_AGENT)) == "test/1.2.3", f"user agent tag mismatch {st}"

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
    def test_client_ip_asm_enabled_reported(
        self, interface: Interface, get_entry_span_tag, asm_enabled, headers, expected
    ):
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            interface.client.get("/", headers=headers)
            if asm_enabled:
                assert (st := get_entry_span_tag(http.CLIENT_IP)) == expected, f"client ip tag mismatch {st}"
            else:
                assert get_entry_span_tag(http.CLIENT_IP) is None

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
        self, interface: Interface, get_entry_span_tag, entry_span, asm_enabled, env_var, headers, expected
    ):
        if interface.name == "tornado" and not headers.get("X-Real-Ip", "").isascii():
            pytest.skip("tornado fails on non ascii headers with decode error, which is fine.")

        with override_global_config(dict(_asm_enabled=asm_enabled, _client_ip_header=env_var)):
            self.update_tracer(interface)
            response = interface.client.get("/", headers=headers)
            assert self.status(response) == 200
            if asm_enabled:
                if expected is None:
                    expected_list = [None, "127.0.0.1"]
                else:
                    expected_list = [expected]
                assert (st := get_entry_span_tag(http.CLIENT_IP)) in expected_list, (
                    f"client ip tag mismatch {st}!={expected}"
                )
            else:
                assert get_entry_span_tag(http.CLIENT_IP) is None

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
        self, interface: Interface, get_entry_span_tag, entry_span, asm_enabled, headers, blocked, body, content_type
    ):
        with override_global_config(dict(_asm_enabled=asm_enabled, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
            self.update_tracer(interface)
            response = interface.client.get("/", headers=headers)
            if blocked and asm_enabled:
                assert (st := self.status(response)) == 403, f"status mismatch {st}"
                assert get_entry_span_tag("actor.ip") == rules._IP.BLOCKED
                assert get_entry_span_tag(http.STATUS_CODE) == "403"
                assert get_entry_span_tag(http.URL) == f"http://localhost:{interface.SERVER_PORT}/"
                assert get_entry_span_tag(http.METHOD) == "GET"
                block_id = self.check_single_rule_triggered("blk-001-001", entry_span)
                assert self.body(response) == _format_template(getattr(constants, body, ""), block_id), self.body(
                    response
                )
                assert (
                    get_entry_span_tag(asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type")
                    == content_type
                )
                assert self.headers(response)["content-type"] == content_type
            else:
                assert self.status(response) == 200

    @pytest.mark.skipif(sys.version_info < (3, 11), reason="BaseExceptionGroup requires Python 3.11+")
    def test_exception_group_blocking(self, interface: Interface, get_entry_span_tag, entry_span):
        """Test that BlockingException wrapped in BaseExceptionGroup is properly caught and returns 403."""
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
            self.update_tracer(interface)
            response = interface.client.get("/exception-group-block?block=true")
            assert (st := self.status(response)) == 403, f"expected 403 but got {st}"

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
        self,
        interface: Interface,
        get_entry_span_tag,
        entry_span,
        asm_enabled,
        headers,
        monitored,
        bypassed,
        query,
        blocked,
    ):
        with override_global_config(dict(_asm_enabled=asm_enabled, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
            self.update_tracer(interface)
            response = interface.client.get("/" + query, headers=headers)
            code = 403 if not bypassed and not monitored and asm_enabled and blocked else 200
            rule = "tst-421-001" if blocked else "tst-421-002"
            assert self.status(response) == code, f"status={self.status(response)}, expected={code}"
            assert get_entry_span_tag(http.STATUS_CODE) == str(code), (
                f"status_code={get_entry_span_tag(http.STATUS_CODE)}, expected={code}"
            )
            if asm_enabled and not bypassed:
                assert (st := get_entry_span_tag(http.URL)) == f"http://localhost:{interface.SERVER_PORT}/{query}", (
                    f"url tag mismatch {st}"
                )
                assert get_entry_span_tag(http.METHOD) == "GET", (
                    f"method={get_entry_span_tag(http.METHOD)}, expected=GET"
                )
                assert get_entry_span_tag("actor.ip") == headers["X-Real-Ip"], (
                    f"actor.ip={get_entry_span_tag('actor.ip')}, expected={headers['X-Real-Ip']}"
                )
                if monitored:
                    self.check_rules_triggered(["blk-001-010", rule], entry_span)
                else:
                    self.check_rules_triggered([rule], entry_span)
            else:
                assert get_triggers(entry_span()) is None, f"asm struct in root span {get_triggers(entry_span())}"

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
        self, interface: Interface, get_entry_span_tag, entry_span, asm_enabled, ip, agent, event, status
    ):
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
            assert get_entry_span_tag(http.STATUS_CODE) == str(status), (
                f"status_code={self.status(response)}, expected={status}"
            )
            if event:
                self.check_single_rule_triggered(
                    "ua0-600-56x" if agent == "dd-test-scanner-log-block" else "ua0-600-12x", entry_span
                )
            else:
                assert get_triggers(entry_span()) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
    @pytest.mark.parametrize(("method", "kwargs"), [("get", {}), ("post", {"data": {"key": "value"}}), ("options", {})])
    def test_request_suspicious_request_block_match_method(
        self, interface: Interface, get_entry_span_tag, entry_span, asm_enabled, metastruct, method, kwargs
    ):
        # GET must be blocked

        with override_global_config(
            dict(
                _asm_enabled=asm_enabled,
                _use_metastruct_for_triggers=metastruct,
                _asm_static_rule_file=rules.RULES_SRB_METHOD,
            )
        ):
            self.update_tracer(interface)
            response = getattr(interface.client, method)("/", **kwargs)
            assert (st := get_entry_span_tag(http.URL)) == f"http://localhost:{interface.SERVER_PORT}/", (
                f"url tag mismatch {st}"
            )
            assert get_entry_span_tag(http.METHOD) == method.upper()
            if asm_enabled and method == "get":
                self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-006")
            else:
                assert self.status(response) == 200
                assert get_entry_span_tag(http.STATUS_CODE) == "200"
                assert get_triggers(entry_span()) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
    @pytest.mark.parametrize(("uri", "blocked"), [("/.git", True), ("/legit", False)])
    def test_request_suspicious_request_block_match_uri(
        self, interface: Interface, get_entry_span_tag, entry_span, asm_enabled, metastruct, uri, blocked
    ):
        # GET must be blocked

        with override_global_config(
            dict(
                _asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct, _asm_static_rule_file=rules.RULES_SRB
            )
        ):
            self.update_tracer(interface)
            response = interface.client.get(uri)
            assert (st := get_entry_span_tag(http.URL)) == f"http://localhost:{interface.SERVER_PORT}{uri}", (
                f"url tag mismatch {st}"
            )
            assert get_entry_span_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-002")
            else:
                assert self.status(response) == 404
                assert get_entry_span_tag(http.STATUS_CODE) == "404"
                assert get_triggers(entry_span()) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
    @pytest.mark.parametrize("uri", ["/waf/../"])
    def test_request_suspicious_request_block_match_uri_lfi(
        self, interface: Interface, get_entry_span_tag, entry_span, asm_enabled, metastruct, uri
    ):
        # On FastAPI with older Starlette/httpx, the TestClient doesn't expose _transport
        # so we can't inject raw_path into the ASGI scope to test path traversal detection.
        if interface.name == "fastapi" and not getattr(interface.client, "_transport", None):
            pytest.skip("TestClient too old to support raw_path injection")

        with override_global_config(dict(_asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct)):
            self.update_tracer(interface)
            interface.client.get(uri)
            assert get_entry_span_tag(http.METHOD) == "GET"
            if asm_enabled:
                self.check_single_rule_triggered("crs-930-110", entry_span)
            else:
                assert get_triggers(entry_span()) is None

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
        self, interface: Interface, get_entry_span_tag, entry_span, asm_enabled, metastruct, path, blocked
    ):
        uri = f"/asm/4352/{path}"  # removing trailer slash will cause errors
        with override_global_config(
            dict(
                _asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct, _asm_static_rule_file=rules.RULES_SRB
            )
        ):
            self.update_tracer(interface)
            response = interface.client.get(uri)

            assert (st := get_entry_span_tag(http.URL)) == f"http://localhost:{interface.SERVER_PORT}" + uri, (
                f"url tag mismatch {st}"
            )
            assert get_entry_span_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-007")
            else:
                assert self.status(response) == 200
                assert get_entry_span_tag(http.STATUS_CODE) == "200"
                assert get_triggers(entry_span()) is None

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
        self, interface: Interface, get_entry_span_tag, entry_span, asm_enabled, metastruct, query, blocked
    ):
        if interface.name in ("django",) and query == "?toto=xtrace&toto=ytrace":
            raise pytest.skip(f"{interface.name} does not support multiple query params with same name")

        uri = f"/{query}"
        with override_global_config(
            dict(
                _asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct, _asm_static_rule_file=rules.RULES_SRB
            )
        ):
            self.update_tracer(interface)
            response = interface.client.get(uri)

            assert (st := get_entry_span_tag(http.URL)) == f"http://localhost:{interface.SERVER_PORT}" + uri, (
                f"url tag mismatch {st}"
            )
            assert get_entry_span_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-001")
            else:
                assert self.status(response) == 200
                assert get_entry_span_tag(http.STATUS_CODE) == "200"
                assert get_triggers(entry_span()) is None

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
        self, interface: Interface, get_entry_span_tag, entry_span, asm_enabled, metastruct, headers, blocked
    ):
        with override_global_config(
            dict(
                _asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct, _asm_static_rule_file=rules.RULES_SRB
            )
        ):
            self.update_tracer(interface)
            response = interface.client.get("/", headers=headers)

            assert get_entry_span_tag(http.URL) == f"http://localhost:{interface.SERVER_PORT}/"
            assert get_entry_span_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-004")
            else:
                assert self.status(response) == 200
                assert get_entry_span_tag(http.STATUS_CODE) == "200"
                assert get_triggers(entry_span()) is None

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
        self, interface: Interface, get_entry_span_tag, entry_span, asm_enabled, metastruct, cookies, blocked
    ):
        with override_global_config(
            dict(
                _asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct, _asm_static_rule_file=rules.RULES_SRB
            )
        ):
            self.update_tracer(interface)
            response = interface.client.get("/", cookies=cookies)

            assert get_entry_span_tag(http.URL) == f"http://localhost:{interface.SERVER_PORT}/"
            assert get_entry_span_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-008")
            else:
                assert self.status(response) == 200
                assert get_entry_span_tag(http.STATUS_CODE) == "200"
                assert get_triggers(entry_span()) is None

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
        self, interface: Interface, get_entry_span_tag, entry_span, asm_enabled, metastruct, uri, status, blocked
    ):
        with override_global_config(
            dict(
                _asm_enabled=asm_enabled,
                _use_metastruct_for_triggers=metastruct,
                _asm_static_rule_file=rules.RULES_SRB_RESPONSE,
            )
        ):
            self.update_tracer(interface)
            response = interface.client.get(uri)

            assert get_entry_span_tag(http.URL) == f"http://localhost:{interface.SERVER_PORT}" + uri
            assert get_entry_span_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                self.assert_blocked(response, entry_span, get_entry_span_tag, blocked)
            else:
                assert self.status(response) == status
                assert get_entry_span_tag(http.STATUS_CODE) == str(status)
                assert get_triggers(entry_span()) is None

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
    @pytest.mark.parametrize("rename_service", [True, False])
    def test_request_suspicious_request_block_match_response_headers(
        self,
        interface: Interface,
        get_entry_span_tag,
        asm_enabled,
        metastruct,
        entry_span,
        uri,
        headers,
        blocked,
        rename_service,
    ):
        with override_global_config(
            dict(
                _asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct, _asm_static_rule_file=rules.RULES_SRB
            )
        ):
            self.update_tracer(interface)
            if headers:
                uri += "?headers=" + quote(",".join(f"{k}={v}" for k, v in headers.items()))
            response = interface.client.get(uri, headers={"x-rename-service": "true" if rename_service else "false"})

            assert get_entry_span_tag(http.URL) == f"http://localhost:{interface.SERVER_PORT}" + uri
            assert get_entry_span_tag(http.METHOD) == "GET"
            if asm_enabled and blocked:
                self.assert_blocked(response, entry_span, get_entry_span_tag, blocked)
                for k in headers:
                    assert k not in self.headers(response)
            else:
                assert self.status(response) == 200
                assert get_entry_span_tag(http.STATUS_CODE) == "200"
                assert get_triggers(entry_span()) is None
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
            # multipart with duplicate keys
            (
                '--52d1fb4eb9c021e53ac2846190e4ac72\r\nContent-Disposition: form-data; name="field"\r\n'
                "\r\nsafe_value\r\n"
                '--52d1fb4eb9c021e53ac2846190e4ac72\r\nContent-Disposition: form-data; name="field"\r\n'
                "\r\nyqrweytqwreasldhkuqwgervflnmlnli\r\n"
                '--52d1fb4eb9c021e53ac2846190e4ac72\r\nContent-Disposition: form-data; name="field"\r\n'
                "\r\nanother_safe_value\r\n"
                "--52d1fb4eb9c021e53ac2846190e4ac72--\r\n",
                "multipart/form-data; boundary=52d1fb4eb9c021e53ac2846190e4ac72",
                "tst-037-003",
            ),
            # raw body must not be blocked
            ("yqrweytqwreasldhkuqwgervflnmlnli", "text/plain", False),
            # other values must not be blocked
            ('{"attack": "zqrweytqwreasldhkuqxgervflnmlnli"}', "application/json", False),
        ],
        ids=[
            "json",
            "text_json",
            "json_large",
            "xml",
            "form",
            "form_multipart",
            "form_multipart_duplicate_keys",
            "text",
            "no_attack",
        ],
    )
    def test_request_suspicious_request_block_match_request_body(
        self, interface: Interface, get_entry_span_tag, asm_enabled, metastruct, entry_span, body, content_type, blocked
    ):
        with override_global_config(
            dict(
                _asm_enabled=asm_enabled, _use_metastruct_for_triggers=metastruct, _asm_static_rule_file=rules.RULES_SRB
            )
        ):
            self.update_tracer(interface)
            response = interface.client.post("/asm/", data=body, content_type=content_type)

            assert get_entry_span_tag(http.URL) == f"http://localhost:{interface.SERVER_PORT}/asm/"
            assert get_entry_span_tag(http.METHOD) == "POST"
            if asm_enabled and blocked:
                self.assert_blocked(response, entry_span, get_entry_span_tag, blocked)
            else:
                assert self.status(response) == 200
                assert get_entry_span_tag(http.STATUS_CODE) == "200"
                assert get_triggers(entry_span()) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
    @pytest.mark.parametrize(
        ("query", "status", "rule_id", "action", "headers", "use_html"),
        [
            # "auto" action: content type depends on Accept header negotiation.
            # Test all Accept header variants here since this is the only query
            # where use_html affects the outcome.
            ("suspicious_306_auto", 306, "tst-040-001", "blocked", {"Accept": "text/html"}, True),
            ("suspicious_306_auto", 306, "tst-040-001", "blocked", {"Accept": "application/json"}, False),
            ("suspicious_306_auto", 306, "tst-040-001", "blocked", {}, False),
            ("suspicious_306_auto", 306, "tst-040-001", "blocked", {"Accept": "text/*"}, True),
            (
                "suspicious_306_auto",
                306,
                "tst-040-001",
                "blocked",
                {"Accept": "text/*;q=0.8, application/*;q=0.7, */*;q=0.9"},
                True,
            ),
            (
                "suspicious_306_auto",
                306,
                "tst-040-001",
                "blocked",
                {"Accept": "text/*;q=0.7, application/*;q=0.8, */*;q=0.9"},
                False,
            ),
            (
                "suspicious_306_auto",
                306,
                "tst-040-001",
                "blocked",
                {"Accept": "text/html;q=0.9, text/*;q=0.8, application/json;q=0.85, */*;q=0.9"},
                True,
            ),
            # For the remaining queries, use_html doesn't affect the outcome:
            # "html" in query forces text/html, "json" forces application/json,
            # redirects and "nothing" don't check content type at all.
            # Use one JSON and one HTML Accept header as representative cases.
            ("suspicious_429_json", 429, "tst-040-002", "blocked", {"Accept": "application/json"}, False),
            ("suspicious_429_json", 429, "tst-040-002", "blocked", {"Accept": "text/html"}, True),
            ("suspicious_503_html", 503, "tst-040-003", "blocked", {"Accept": "application/json"}, False),
            ("suspicious_503_html", 503, "tst-040-003", "blocked", {"Accept": "text/html"}, True),
            ("suspicious_301", 301, "tst-040-004", "redirect", {"Accept": "application/json"}, False),
            ("suspicious_301", 301, "tst-040-004", "redirect", {"Accept": "text/html"}, True),
            ("suspicious_303", 303, "tst-040-005", "redirect", {"Accept": "application/json"}, False),
            ("suspicious_303", 303, "tst-040-005", "redirect", {"Accept": "text/html"}, True),
            ("nothing", 200, None, None, {"Accept": "application/json"}, False),
            ("nothing", 200, None, None, {"Accept": "text/html"}, True),
        ],
    )
    def test_request_suspicious_request_block_custom_actions(
        self,
        interface: Interface,
        get_entry_span_tag,
        asm_enabled,
        metastruct,
        entry_span,
        query,
        status,
        rule_id,
        action,
        headers,
        use_html,
    ):
        import ddtrace.internal.utils.http as http_cache

        # remove cache to avoid using templates from other tests
        http_cache._HTML_BLOCKED_TEMPLATE_CACHE = None
        http_cache._JSON_BLOCKED_TEMPLATE_CACHE = None
        try:
            uri = f"/?param={query}"
            with (
                override_global_config(
                    dict(
                        _asm_enabled=asm_enabled,
                        _use_metastruct_for_triggers=metastruct,
                        _asm_static_rule_file=rules.RULES_SRBCA,
                    )
                ),
                override_env(
                    dict(
                        DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON=rules.RESPONSE_CUSTOM_JSON,
                        DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML=rules.RESPONSE_CUSTOM_HTML,
                    )
                ),
            ):
                self.update_tracer(interface)
                response = interface.client.get(uri, headers=headers)

                assert get_entry_span_tag(http.URL) == f"http://localhost:{interface.SERVER_PORT}" + uri
                assert get_entry_span_tag(http.METHOD) == "GET"
                if asm_enabled and action:
                    assert (st := self.status(response)) == status, f"status mismatch {st}"
                    assert (st := get_entry_span_tag(http.STATUS_CODE)) == str(status), f"status code mismatch {st}"
                    self.check_single_rule_triggered(rule_id, entry_span)

                    if action == "blocked":
                        content_type = (
                            "text/html" if "html" in query or (("auto" in query) and use_html) else "application/json"
                        )
                        assert (
                            get_entry_span_tag(
                                asm_constants.SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type"
                            )
                            == content_type
                        )
                        assert self.headers(response)["content-type"] == content_type
                        if content_type == "application/json":
                            assert json.loads(self.body(response)) == {
                                "errors": [{"title": "You've been blocked", "detail": "Custom content"}]
                            }
                    elif action == "redirect":
                        assert self.location(response) == "https://www.datadoghq.com"
                else:
                    assert self.status(response) == 200
                    assert get_entry_span_tag(http.STATUS_CODE) == "200"
                    assert get_triggers(entry_span()) is None
        finally:
            # remove cache to avoid using custom templates in other tests
            http_cache._HTML_BLOCKED_TEMPLATE_CACHE = None
            http_cache._JSON_BLOCKED_TEMPLATE_CACHE = None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    def test_nested_appsec_events(
        self,
        interface: Interface,
        get_entry_span_tag,
        entry_span,
        asm_enabled,
    ):
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            response = interface.client.get("/config.php", headers={"user-agent": "Arachni/v1.5.1"})

            assert (st := get_entry_span_tag(http.URL)) == f"http://localhost:{interface.SERVER_PORT}/config.php", (
                f"url tag mismatch {st}"
            )
            assert get_entry_span_tag(http.METHOD) == "GET"
            assert (st := self.status(response)) == 404, f"status mismatch {st}"
            assert (st := get_entry_span_tag(http.STATUS_CODE)) == "404", f"status code mismatch {st}"
            if asm_enabled:
                self.check_rules_triggered(["nfd-000-001", "ua0-600-12x"], entry_span)
            else:
                assert get_triggers(entry_span()) is None

    @pytest.mark.parametrize("apisec_enabled", [True, False])
    @pytest.mark.parametrize("apm_tracing_enabled", [True, False])
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
                    [
                        {
                            "method": [8],
                            "body": [8],
                            "query_params": [{"y": [[[8]], {"len": 1}], "x": [[[8]], {"len": 1}]}],
                            "cookies": [{"secret": [8]}],
                            "path_params": [[[8]], {"len": 2}],
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
        get_entry_span_tag,
        entry_span,
        apisec_enabled,
        apm_tracing_enabled,
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

        import ddtrace.internal.telemetry

        with (
            override_global_config(
                dict(_asm_enabled=True, _api_security_enabled=apisec_enabled, _apm_tracing_enabled=apm_tracing_enabled)
            ),
            mock_patch.object(
                ddtrace.internal.telemetry.telemetry_writer,
                "_namespace",
                MagicMock(),
            ) as mocked,
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

            assert (st := self.status(response)) == (403 if blocked else 200), f"status mismatch {st}"
            assert (st := get_entry_span_tag(http.STATUS_CODE)) == ("403" if blocked else "200"), (
                f"status code mismatch {st}"
            )
            if event:
                assert get_triggers(entry_span()) is not None, "expected triggers but none found"
            else:
                assert get_triggers(entry_span()) is None, "expected no triggers but some found"
            value = get_entry_span_tag(name)
            if apisec_enabled and not (name.startswith("_dd.appsec.s.res") and blocked):
                if name == "_dd.appsec.s.req.body" and blocked:
                    # we may not collect the body if the request is blocked early
                    return
                assert value, name
                api = json.loads(gzip.decompress(base64.b64decode(value)).decode())
                assert api, name
                # all tornado path params are always strings
                if interface.name == "tornado" and name == "_dd.appsec.s.req.params":
                    expected_value = [[{"param_int": [8], "param_str": [8]}], [[[8]], {"len": 2}]]
                if expected_value is not None:
                    if name == "_dd.appsec.s.res.body" and blocked:
                        assert api == [{"errors": [[[{"detail": [8], "title": [8]}]], {"len": 1}]}]
                    else:
                        assert any(
                            all(api[0].get(k) == v for k, v in expected[0].items())
                            if isinstance(api[0], dict) and isinstance(expected[0], dict)
                            else api[0] == expected[0]
                            for expected in expected_value
                        ), (api, name, expected_value)
                telemetry_calls = {
                    (c.value, f"{ns.value}.{nm}", t): v for (c, ns, nm, v, t), _ in mocked.add_metric.call_args_list
                }
                assert (
                    "count",
                    "appsec.api_security.request.schema",
                    (("framework", interface.name),),
                ) in telemetry_calls

                if not apm_tracing_enabled:
                    span_sampling_priority = entry_span()._span.context.sampling_priority
                    sampling_decision = get_entry_span_tag(constants.SAMPLING_DECISION_TRACE_TAG_KEY)
                    assert span_sampling_priority == constants.USER_KEEP, (
                        f"Expected 2 (USER_KEEP), got {span_sampling_priority}"
                    )
                    assert sampling_decision == f"-{constants.SamplingMechanism.APPSEC}", (
                        f"Expected '-5' (APPSEC), got {sampling_decision}"
                    )
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
    def test_api_security_scanners(
        self, interface: Interface, get_entry_span_tag, apisec_enabled, payload, expected_value
    ):
        import base64
        import gzip

        with override_global_config(dict(_asm_enabled=True, _api_security_enabled=apisec_enabled)):
            self.update_tracer(interface)
            response = interface.client.post(
                "/",
                data=json.dumps(payload),
                content_type="application/json",
            )
            assert (st := self.status(response)) == 200, f"status mismatch {st}"
            assert (st := get_entry_span_tag(http.STATUS_CODE)) == "200", f"status code mismatch {st}"
            assert asm_config._api_security_enabled == apisec_enabled

            value = get_entry_span_tag("_dd.appsec.s.req.body")
            if apisec_enabled:
                assert value, "_dd.appsec.s.req.body"
                api = json.loads(gzip.decompress(base64.b64decode(value)).decode())
                assert api == expected_value
            else:
                assert value is None

    @pytest.mark.parametrize("apisec_enabled", [True, False])
    def test_api_custom_scanners(self, interface: Interface, get_entry_span_tag, apisec_enabled):
        import base64
        import gzip

        magic_key = "weqpfdjwlekfjowekhgfjiew"

        with override_global_config(
            dict(
                _asm_enabled=True,
                _api_security_enabled=apisec_enabled,
                _asm_static_rule_file=rules.RULES_CUSTOM_SCANNERS,
            )
        ):
            self.update_tracer(interface)
            response = interface.client.get("/", headers={magic_key: "A0000B1111C2222"})
            assert (st := self.status(response)) == 200, f"status mismatch {st}"
            assert (st := get_entry_span_tag(http.STATUS_CODE)) == "200", f"status code mismatch {st}"
            assert asm_config._api_security_enabled == apisec_enabled

            value = get_entry_span_tag("_dd.appsec.s.req.headers")
            if apisec_enabled:
                assert value, "_dd.appsec.s.req.headers"
                api = json.loads(gzip.decompress(base64.b64decode(value)).decode())
                assert isinstance(api, list)
                assert api
                headers = api[0]
                assert magic_key in headers
                assert headers[magic_key][1] == {
                    "type": "custom_type",
                    "category": "custom_category",
                    "custom": "custom_data",
                }
            else:
                assert value is None

    @pytest.mark.parametrize("apisec_enabled", [True, False])
    @pytest.mark.parametrize("priority", ["keep", "drop"])
    @pytest.mark.parametrize("delay", [0.0, 120.0])
    def test_api_security_sampling(self, interface: Interface, get_entry_span_tag, apisec_enabled, priority, delay):
        from ddtrace.appsec._api_security.api_manager import APIManager

        payload = {"mastercard": "5123456789123456"}
        with override_global_config(
            dict(_asm_enabled=True, _api_security_enabled=apisec_enabled, _api_security_sample_delay=delay)
        ):
            # Clear sampling state after AppSec has been reconfigured for this case.
            if apisec_enabled:
                assert APIManager._instance, "APIManager instance should be initialized"
                APIManager._instance._hashtable.clear()

            self.update_tracer(interface)
            response = interface.client.post(
                f"/asm/?priority={priority}",
                data=json.dumps(payload),
                content_type="application/json",
            )
            assert (st := self.status(response)) == 200, f"status mismatch {st}"
            assert (st := get_entry_span_tag(http.STATUS_CODE)) == "200", f"status code mismatch {st}"
            assert asm_config._api_security_enabled == apisec_enabled

            value = get_entry_span_tag("_dd.appsec.s.req.body")
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
            assert get_entry_span_tag(http.STATUS_CODE) == "200"
            assert asm_config._api_security_enabled == apisec_enabled

            value = get_entry_span_tag("_dd.appsec.s.req.body")
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

    @pytest.mark.parametrize("asm_enabled", [True, False])
    def test_asm_enabled_headers(self, asm_enabled, interface, get_entry_span_tag, entry_span):
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            response = interface.client.get(
                "/",
                headers={"accept": "testheaders/a1b2c3", "user-agent": "UnitTestAgent", "content-type": "test/x0y9z8"},
            )
            assert (st := self.status(response)) == 200, f"status mismatch {st}"
            if asm_enabled:
                assert (st := get_entry_span_tag("http.request.headers.accept")) == "testheaders/a1b2c3", (
                    f"accept header mismatch {st}"
                )
                assert get_entry_span_tag("http.request.headers.user-agent") == "UnitTestAgent"
                assert get_entry_span_tag("http.request.headers.content-type") == "test/x0y9z8"
            else:
                assert get_entry_span_tag("http.request.headers.accept") is None
                assert get_entry_span_tag("http.request.headers.user-agent") is None
                assert get_entry_span_tag("http.request.headers.content-type") is None

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
    def test_asm_waf_integration_identify_requests(
        self, asm_enabled, header, interface, get_entry_span_tag, entry_span
    ):
        import random
        import string

        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            random_value = "".join(random.choices(string.ascii_letters + string.digits, k=random.randint(6, 128)))
            response = interface.client.get(
                "/",
                headers={header: random_value},
            )
            assert self.status(response) == 200
            meta_tagname = "http.request.headers." + header.lower()
            if asm_enabled:
                assert (st := get_entry_span_tag(meta_tagname)) == random_value, (
                    f"meta tag mismatch {st}={random_value}"
                )
            else:
                assert get_entry_span_tag(meta_tagname) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("metastruct", [True, False])
    @pytest.mark.xfail_interface("django", "flask")
    def test_stream_response(
        self,
        interface: Interface,
        get_entry_span_tag,
        asm_enabled,
        metastruct,
        entry_span,
    ):
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
        [
            (
                "lfi",
                {"filename1": "/etc/passwd", "filename2": "/etc/master.passwd"},
                "rasp-930-100",
                ("rasp",),
            ),
            (
                "lfi",
                {"filename_pathlib1": "/etc/passwd", "filename_pathlib2": "/etc/master.passwd"},
                "rasp-930-100",
                ("rasp",),
            ),
        ]
        + [
            ("ssrf", {f"url_{p1}_1": "169.254.169.254", f"url_{p2}_2": "169.254.169.253"}, "rasp-934-100", (f1, f2))
            for (p1, f1), (p2, f2) in itertools.product(
                [
                    ("urlopen_string", "do_open"),
                    ("urlopen_request", "do_open"),
                    ("requests", "urlopen"),
                    ("httpx", "send"),
                    ("httpx_async", "send"),
                ],
                repeat=2,
            )
        ]
        + [("sql_injection", {"user_id_1": "1 OR 1=1", "user_id_2": "1 OR 1=1"}, "rasp-942-100", ("rasp",))]
        + [
            (
                "shell_injection",
                {"cmdsys_1": "$(cat /etc/passwd 1>&2 ; echo .)", "cmdrun_2": "$(uname -a 1>&2 ; echo .)"},
                "rasp-932-100",
                ("system", "rasp"),
            )
        ]
        + [
            (
                "command_injection",
                {"cmda_1": "/sbin/ping", "cmds_2": "/usr/bin/ls%20-la"},
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
        entry_span,
        get_entry_span_tag,
        get_entry_span_metric,
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
        import ddtrace.internal.telemetry

        def validate_top_function(trace):
            # Validate that the stack trace contains an expected function near the top.
            # The crop mechanism removes most ddtrace frames, but some integration
            # wrappers (e.g. with_traced_module) may remain. Check first 3 frames:
            # - frame 0: the caller right above the crop point
            # - frame 1-2: allow for integration wrappers or qualified names
            for frame in trace["frames"][:3]:
                fname = frame["function"]
                fname_lower = fname.lower()
                if any(fname.endswith(tf) or tf.lower() in fname_lower for tf in top_functions) or (
                    asm_config._iast_enabled and fname.endswith("ast_function")
                ):
                    return True
                # On Python <3.11 co_qualname is unavailable, so Tornado's
                # "RaspHandler._handle" appears as just "_handle". Accept it
                # only if the frame is from a test app file, not ddtrace internals.
                if fname == "_handle" and "contrib_appsec" in frame.get("file", ""):
                    return True
            return False

        with (
            override_global_config(
                dict(_asm_enabled=asm_enabled, _ep_enabled=ep_enabled, _asm_static_rule_file=rule_file)
            ),
            mock_patch.object(ddtrace.internal.telemetry.telemetry_writer, "_namespace", MagicMock()) as mocked,
        ):
            self.update_tracer(interface)
            assert asm_config._asm_enabled == asm_enabled
            response = interface.client.get(f"/rasp/{endpoint}/?{urlencode(parameters)}")
            code = status_expected if asm_enabled and ep_enabled else 200
            assert self.status(response) == code, (self.status(response), code, self.body(response))
            assert get_entry_span_tag(http.STATUS_CODE) == str(code), (get_entry_span_tag(http.STATUS_CODE), code)
            if code == 200:
                assert self.body(response).startswith(f"{endpoint} endpoint")
            telemetry_calls = {
                (c.value, f"{ns.value}.{nm}", t): v for (c, ns, nm, v, t), _ in mocked.add_metric.call_args_list
            }
            if asm_enabled and ep_enabled and action_level > 0:
                self.check_rules_triggered([rule] * (1 if action_level == 2 else 2), entry_span)
                assert self.check_for_stack_trace(entry_span)
                for trace in self.check_for_stack_trace(entry_span):
                    assert "frames" in trace
                    assert validate_top_function(trace), (
                        f"unknown top function {trace['frames'][0]} {[t['function'] for t in trace['frames'][:4]]}"
                    )
                expected_rule_type = "command_injection" if endpoint == "shell_injection" else endpoint
                expected_variant = (
                    "exec"
                    if endpoint == "command_injection"
                    else "shell"
                    if endpoint == "shell_injection"
                    else "request"
                    if endpoint == "ssrf"
                    else None
                )
                matches = [t for c, n, t in telemetry_calls if c == "count" and n == "appsec.rasp.rule.match"]

                if expected_variant:
                    expected_tags = (
                        ("rule_type", expected_rule_type),
                        ("rule_variant", expected_variant),
                        ("waf_version", asm_config._ddwaf_version),
                        ("event_rules_version", "rules_rasp"),
                    )
                else:
                    expected_tags = (
                        ("rule_type", expected_rule_type),
                        ("waf_version", asm_config._ddwaf_version),
                        ("event_rules_version", "rules_rasp"),
                    )
                match_expected_tags = expected_tags + (("block", "irrelevant" if action_level < 2 else "success"),)
                assert matches == [match_expected_tags], (matches, match_expected_tags)
                evals = [t for c, n, t in telemetry_calls if c == "count" and n == "appsec.rasp.rule.eval"]
                # there may have been multiple evaluations of other rules too
                assert expected_tags in evals, (expected_tags, evals)
                if action_level == 2:
                    assert get_entry_span_tag("rasp.request.done") is None, get_entry_span_tag("rasp.request.done")
                else:
                    assert get_entry_span_tag("rasp.request.done") == endpoint, get_entry_span_tag("rasp.request.done")
                assert get_entry_span_metric(APPSEC.RASP_DURATION) is not None
                assert get_entry_span_metric(APPSEC.RASP_DURATION_EXT) is not None
                assert get_entry_span_metric(APPSEC.RASP_RULE_EVAL) is not None
                assert float(get_entry_span_metric(APPSEC.RASP_DURATION_EXT)) >= float(
                    get_entry_span_metric(APPSEC.RASP_DURATION)
                )
                assert int(get_entry_span_metric(APPSEC.RASP_RULE_EVAL)) > 0
            else:
                for _, n, _ in telemetry_calls:
                    assert "rasp" not in n
                assert get_triggers(entry_span()) is None
                assert self.check_for_stack_trace(entry_span) == []
                assert get_entry_span_tag("rasp.request.done") == endpoint, get_entry_span_tag("rasp.request.done")

    @pytest.mark.parametrize(
        ("asm_enabled", "auto_events_enabled", "local_mode", "rc_mode"),
        [
            # Active path: asm + auto_events enabled, effective mode != disabled.
            # rc_mode overrides local_mode when not None.
            (True, True, "identification", None),
            (True, True, "anonymization", None),
            (True, True, "disabled", "identification"),
            (True, True, "identification", "identification"),
            (True, True, "anonymization", "identification"),
            (True, True, "disabled", "anonymization"),
            (True, True, "identification", "anonymization"),
            (True, True, "anonymization", "anonymization"),
            # Disabled paths: verify no tags are set.
            # Each disabling condition is tested with one representative combo.
            (False, True, "identification", None),  # asm disabled
            (True, False, "identification", None),  # auto_events disabled
            (True, True, "disabled", None),  # mode disabled (rc_mode=None, local_mode=disabled)
            (True, True, "identification", "disabled"),  # mode disabled (rc_mode overrides)
        ],
    )
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
        entry_span,
        get_entry_span_tag,
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
            assert get_entry_span_tag("http.status_code") == str(status_code)
            username = user if mode == "identification" else _hash_user_id(user)
            user_id_hash = user_id if mode == "identification" else _hash_user_id(user_id)
            if asm_enabled and auto_events_enabled and mode != "disabled":
                if status_code == 401:
                    assert get_entry_span_tag("appsec.events.users.login.failure.track") == "true"
                    assert get_entry_span_tag("_dd.appsec.events.users.login.failure.auto.mode") == mode
                    assert get_entry_span_tag("appsec.events.users.login.failure.usr.id") == (
                        user_id_hash if user_id else username
                    )
                    assert (
                        get_entry_span_tag("appsec.events.users.login.failure.usr.exists")
                        == str(user == "testuuid").lower()
                    )
                    # check for manual instrumentation tag in manual instrumented frameworks
                    if interface.name in ["flask", "fastapi", "tornado"]:
                        assert get_entry_span_tag("_dd.appsec.events.users.login.failure.sdk") == "true"
                    else:
                        assert get_entry_span_tag("_dd.appsec.events.users.login.success.sdk") is None
                    if mode == "identification":
                        assert get_entry_span_tag("_dd.appsec.usr.login") == user
                    elif mode == "anonymization":
                        assert get_entry_span_tag("_dd.appsec.usr.login") == _hash_user_id(user)
                else:
                    assert get_entry_span_tag("appsec.events.users.login.success.track") == "true"
                    assert get_entry_span_tag("usr.id") == user_id_hash
                    assert get_entry_span_tag("_dd.appsec.usr.id") == user_id_hash
                    if mode == "identification":
                        assert get_entry_span_tag("_dd.appsec.usr.login") == user
                    # check for manual instrumentation tag in manual instrumented frameworks
                    if interface.name in ["flask", "fastapi", "tornado"]:
                        assert get_entry_span_tag("_dd.appsec.events.users.login.success.sdk") == "true"
                    else:
                        assert get_entry_span_tag("_dd.appsec.events.users.login.success.sdk") is None

            else:
                assert get_entry_span_tag("usr.id") is None
                assert not any(
                    tag.startswith("appsec.events.users.login") for tag in entry_span()._get_str_attributes()
                )
                assert not any(
                    tag.startswith("_dd_appsec.events.users.login") for tag in entry_span()._get_str_attributes()
                )
            # check for fingerprints when user events
            if asm_enabled:
                assert (st := get_entry_span_tag(asm_constants.FINGERPRINTING.HEADER)) is not None, (
                    f"header fingerprint missing {st}"
                )
                assert (st := get_entry_span_tag(asm_constants.FINGERPRINTING.NETWORK)) is not None, (
                    f"network fingerprint missing {st}"
                )
                assert (st := get_entry_span_tag(asm_constants.FINGERPRINTING.ENDPOINT)) is not None, (
                    f"endpoint fingerprint missing {st}"
                )
                assert (st := get_entry_span_tag(asm_constants.FINGERPRINTING.SESSION)) is not None, (
                    f"session fingerprint missing {st}"
                )
            else:
                assert get_entry_span_tag(asm_constants.FINGERPRINTING.NETWORK) is None
                assert get_entry_span_tag(asm_constants.FINGERPRINTING.ENDPOINT) is None
                assert get_entry_span_tag(asm_constants.FINGERPRINTING.SESSION) is None

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("username", "password", "status_code", "user_id"),
        [
            ("test", "1234", 200, "social-security-id"),
            ("testuuid", "12345", 401, "591dc126-8431-4d0f-9509-b23318d3dce4"),
            ("zouzou", "12345", 401, ""),
        ],
    )
    def test_auto_user_events_sdk_v2(
        self,
        interface,
        entry_span,
        get_entry_span_tag,
        asm_enabled,
        username,
        password,
        status_code,
        user_id,
    ):
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        import ddtrace.internal.telemetry

        if not USER_SDK_V2:
            raise pytest.skip("SDK v2 not available")

        with (
            override_global_config(
                dict(
                    _asm_enabled=asm_enabled,
                    _auto_user_instrumentation_local_mode="identification",
                    _auto_user_instrumentation_enabled=True,
                )
            ),
            mock_patch.object(ddtrace.internal.telemetry.telemetry_writer, "_namespace", MagicMock()) as telemetry_mock,
        ):
            self.update_tracer(interface)
            metadata = json.dumps(
                {
                    "a": "a",
                    "load_a": {
                        "b": True,
                        "load_b": {
                            "c": 3,
                            "load_c": {
                                "d": "value",
                                "load_d": {
                                    "e": 1.32,
                                    "load_e": {
                                        "f": 3.1415926,
                                        "load_f": {"g": "ghost", "load_g": {"h": "heavy", "load_h": {}}},
                                    },
                                },
                            },
                        },
                    },
                },
                separators=(",", ":"),
            )
            response = interface.client.get(f"/login_sdk/?username={username}&password={password}&metadata={metadata}")
            assert self.status(response) == status_code
            assert get_entry_span_tag("http.status_code") == str(status_code)
            telemetry_calls = {
                (c.value, f"{ns.value}.{nm}", t): v for (c, ns, nm, v, t), _ in telemetry_mock.add_metric.call_args_list
            }
            if status_code == 401:
                assert get_entry_span_tag("appsec.events.users.login.failure.track") == "true"
                if user_id:
                    assert get_entry_span_tag("appsec.events.users.login.failure.usr.id") == user_id
                assert (
                    get_entry_span_tag("appsec.events.users.login.failure.usr.exists")
                    == str(username == "testuuid").lower()
                )
                assert get_entry_span_tag("_dd.appsec.events.users.login.failure.sdk") == "true"
                assert any(
                    t[:2] == ("count", "appsec.sdk.event") and ("event_type", "login_failure") == t[2][0]
                    for t in telemetry_calls
                ), telemetry_calls
            else:
                assert get_entry_span_tag("appsec.events.users.login.success.track") == "true"
                assert get_entry_span_tag("usr.id") == user_id, (user_id, get_entry_span_tag("usr.id"))
                assert any(tag.startswith("appsec.events.users.login") for tag in entry_span()._get_str_attributes())
                assert get_entry_span_tag("_dd.appsec.events.users.login.success.sdk") == "true"
                assert any(
                    t[:2] == ("count", "appsec.sdk.event") and ("event_type", "login_success") == t[2][0]
                    for t in telemetry_calls
                ), telemetry_calls

            # no auto instrumentation
            assert not any(
                tag.startswith("_dd_appsec.events.users.login") for tag in entry_span()._get_str_attributes()
            )

            # check for fingerprints when user events
            if asm_enabled:
                assert (st := get_entry_span_tag(asm_constants.FINGERPRINTING.HEADER)) is not None, (
                    f"header fingerprint missing {st}"
                )
                assert (st := get_entry_span_tag(asm_constants.FINGERPRINTING.NETWORK)) is not None, (
                    f"network fingerprint missing {st}"
                )
                assert (st := get_entry_span_tag(asm_constants.FINGERPRINTING.ENDPOINT)) is not None, (
                    f"endpoint fingerprint missing {st}"
                )
                assert (st := get_entry_span_tag(asm_constants.FINGERPRINTING.SESSION)) is not None, (
                    f"session fingerprint missing {st}"
                )
            else:
                assert get_entry_span_tag(asm_constants.FINGERPRINTING.NETWORK) is None
                assert get_entry_span_tag(asm_constants.FINGERPRINTING.ENDPOINT) is None
                assert get_entry_span_tag(asm_constants.FINGERPRINTING.SESSION) is None

            # metadata
            success = "success" if status_code == 200 else "failure"
            assert get_entry_span_tag(f"appsec.events.users.login.{success}.a") == "a", (
                entry_span()._get_str_attributes()
            )
            assert get_entry_span_tag(f"appsec.events.users.login.{success}.load_a.b") == "true", (
                entry_span()._get_str_attributes()
            )
            assert get_entry_span_tag(f"appsec.events.users.login.{success}.load_a.load_b.c") == "3", (
                entry_span()._get_str_attributes()
            )
            assert get_entry_span_tag(f"appsec.events.users.login.{success}.load_a.load_b.load_c.load_d.e") == "1.32", (
                entry_span()._get_str_attributes()
            )
            assert (
                get_entry_span_tag(f"appsec.events.users.login.{success}.load_a.load_b.load_c.load_d.load_e.f") is None
            ), entry_span()._get_str_attributes()

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize("user_agent", ["dd-test-scanner-log-block", "UnitTestAgent"])
    def test_fingerprinting(self, interface, entry_span, get_entry_span_tag, asm_enabled, user_agent):
        with override_global_config(dict(_asm_enabled=asm_enabled, _asm_static_rule_file=None)):
            self.update_tracer(interface)
            response = interface.client.post(
                "/asm/324/huj/?x=1&y=2", headers={"User-Agent": user_agent}, data={"test": "attack"}
            )
            code = 403 if asm_enabled and user_agent == "dd-test-scanner-log-block" else 200
            assert (st := self.status(response)) == code, f"status mismatch {st}={code}"
            assert get_entry_span_tag("http.status_code") == str(code)
            # check for fingerprints when security events
            if asm_enabled:
                assert (st := get_entry_span_tag(asm_constants.FINGERPRINTING.HEADER)) is not None, (
                    f"header fingerprint missing {st}"
                )
                assert (st := get_entry_span_tag(asm_constants.FINGERPRINTING.NETWORK)) is not None, (
                    f"network fingerprint missing {st}"
                )
                assert (st := get_entry_span_tag(asm_constants.FINGERPRINTING.ENDPOINT)) is not None, (
                    f"endpoint fingerprint missing {st}"
                )
                assert (st := get_entry_span_tag(asm_constants.FINGERPRINTING.SESSION)) is not None, (
                    f"session fingerprint missing {st}"
                )
            else:
                assert get_entry_span_tag(asm_constants.FINGERPRINTING.HEADER) is None
                assert get_entry_span_tag(asm_constants.FINGERPRINTING.NETWORK) is None
                assert get_entry_span_tag(asm_constants.FINGERPRINTING.ENDPOINT) is None
                assert get_entry_span_tag(asm_constants.FINGERPRINTING.SESSION) is None

    @pytest.mark.parametrize("exploit_prevention_enabled", [True, False])
    @pytest.mark.parametrize("api_security_enabled", [True, False])
    def test_trace_tagging(
        self,
        interface,
        entry_span,
        get_entry_span_tag,
        get_entry_span_metric,
        exploit_prevention_enabled,
        api_security_enabled,
    ):
        with override_global_config(
            dict(
                _asm_enabled=True,
                _asm_static_rule_file=rules.RULES_TRACE_TAGGING,
                _ep_enabled=exploit_prevention_enabled,
                _api_security_enabled=api_security_enabled,
            )
        ):
            self.update_tracer(interface)
            random_value = "oweh1jfoi4wejflk7sdgf"
            response = interface.client.get(f"/?test_tag=tag_this_trace_{random_value}")
            assert self.status(response) == 200
            assert get_entry_span_tag("http.status_code") == "200"
            # test for trace tagging with fixed value
            assert (st := get_entry_span_tag("dd.appsec.custom_tag")) == "tagged_trace", f"custom tag mismatch {st}"
            # test for metric tagging with fixed value
            assert get_entry_span_metric("dd.appsec.custom_metric") == 37
            # test for trace tagging with dynamic value
            assert get_entry_span_tag("dd.appsec.custom_tag_value") == f"tag_this_trace_{random_value}"
            # test for sampling priority changes. Appsec should not change the sampling priority (keep=false)
            span_sampling_priority = entry_span()._span.context.sampling_priority
            sampling_decision = get_entry_span_tag(constants.SAMPLING_DECISION_TRACE_TAG_KEY)
            assert span_sampling_priority < 2 or sampling_decision != f"-{constants.SamplingMechanism.APPSEC}"

    @pytest.mark.parametrize("endpoint", ["urlopen_request", "urlopen_string", "httpx", "httpx_async"])
    def test_api10(self, endpoint, interface, get_tag):
        """test api10 on downstream request headers on rasp endpoint"""
        TAG_AGENT: str = "TAG_API10_REQ_HEADERS"
        with override_global_config(
            dict(
                _asm_enabled=True,
                _api_security_enabled=True,
                _ep_enabled=True,
                _asm_static_rule_file=rules.RULES_EXPLOIT_PREVENTION,
            )
        ):
            self.update_tracer(interface)
            response = interface.client.get(
                f"/rasp/ssrf/?url_{endpoint}=https%3A%2F%2Fwww.datadoghq.com%2Ftest%3Fx%3D1",
            )
            assert self.status(response) == 200, f"{self.status(response)} is not 200"
            tag = get_tag("_dd.appsec.trace.mark")
            assert tag == TAG_AGENT, f"[{tag}] is not [{TAG_AGENT}]"

    @pytest.mark.parametrize(
        ("route", "data", "tag"),
        [
            ("request-headers", None, "TAG_API10_REQ_HEADERS"),
            ("request-body", {"payload": "api10-request-body"}, "TAG_API10_REQ_BODY"),
            ("response-headers", None, "TAG_API10_RESP_HEADERS"),
            ("response-body", None, "TAG_API10_RESP_BODY"),
            ("response-status", None, "TAG_API10_RESP_STATUS"),
        ],
    )
    @pytest.mark.parametrize("integration", ["", "_requests", "_httpx", "_httpx_async"])
    def test_api10_addresses(self, integration, route, data, tag, interface, api10_http_server_port, get_tag):
        """test api10 on downstream request/response headers and body"""

        with override_global_config(
            dict(
                _asm_enabled=True,
                _api_security_enabled=True,
                _ep_enabled=True,
                _asm_static_rule_file=rules.RULES_EXPLOIT_PREVENTION,
                _dr_sample_rate=1.0,
            )
        ):
            self.update_tracer(interface)
            url = f"/redirect{integration}/{route}/{api10_http_server_port}"
            if data:
                response = interface.client.post(url, data=json.dumps(data), content_type="application/json")
            else:
                response = interface.client.get(url)
            assert self.status(response) == 200, f"{self.status(response)} is not 200"
            c_tag = get_tag("_dd.appsec.trace.mark")
            assert c_tag == tag, f"[{c_tag}] is not [{tag}] {self.body(response)}"

    @pytest.mark.parametrize("integration", ["", "_requests", "_httpx", "_httpx_async"])
    def test_api10_addresses_redirects(self, integration, interface, api10_http_server_port, entry_span):
        INSPECTED_FINAL_RESP_BODY = "apiA-100-004"
        INSPECTED_REDIRECT_RESP_HEADERS = "apiA-100-006"
        INSPECTED_REDIRECT_RESP_STATUS = "apiA-100-007"

        url = f"/redirect{integration}/redirect-source/{api10_http_server_port}"

        with override_global_config(
            dict(
                _asm_enabled=True,
                _api_security_enabled=True,
                _ep_enabled=True,
                _asm_static_rule_file=rules.RULES_EXPLOIT_PREVENTION,
                _dr_sample_rate=1.0,
            )
        ):
            self.update_tracer(interface)
            response = interface.client.get(url)
            assert self.status(response) == 200, f"{self.status(response)} is not 200"
            redirect_response_payload = json.loads(self.body(response)).get("payload")
            api_response_payload = json.loads(redirect_response_payload).get("payload")
            assert api_response_payload == "api10-response-body"

            expected_rules = [
                INSPECTED_FINAL_RESP_BODY,
                INSPECTED_REDIRECT_RESP_HEADERS,
                INSPECTED_REDIRECT_RESP_STATUS,
            ]

            self.check_rules_triggered(sorted(expected_rules), entry_span)


class Contrib_TestClass_For_Threats_RC(_Contrib_TestClass_Base):
    """
    Factorized test class for threats tests requiring remote config enabled.
    """

    def test_rc_ip_blocklist_update_lifecycle(self, interface, test_spans, entry_span):
        """Test the full lifecycle of RC rule updates:
        1. Default config: IP not blocked
        2. RC update adds IP to blocklist: IP blocked
        3. RC update changes blocklist to different IP: original IP no longer blocked
        4. RC data removed (revert to default): no IPs blocked
        """
        ip_a = "8.8.4.4"
        ip_b = "9.9.9.9"

        def _make_rc_data(blocked_ip):
            return {
                "rules_data": [
                    {
                        "data": [{"value": blocked_ip}],
                        "id": "blocked_ips",
                        "type": "ip_with_expiration",
                    },
                ]
            }

        with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True)):
            self.update_tracer(interface)

            # Step 1: Default config — IP should not be blocked
            response = interface.client.get("/", headers={"X-Real-Ip": ip_a})
            assert self.status(response) == 200, "Step 1: expected 200 with default config"
            test_spans.reset()

            # Step 2: RC update — add ip_a to blocked_ips via ASM_DATA
            core.dispatch(
                "waf.update",
                (
                    [],
                    [("ASM_DATA", "Datadog/1/ASM_DATA/blocked_ips", _make_rc_data(ip_a))],
                ),
            )
            self.check_waf_no_errors()
            response = interface.client.get("/", headers={"X-Real-Ip": ip_a})
            assert self.status(response) == 403, "Step 2: expected 403 after blocking ip_a"
            self.check_single_rule_triggered("blk-001-001", entry_span)
            test_spans.reset()

            # Step 3: RC update — change blocklist to ip_b, ip_a should no longer be blocked
            core.dispatch(
                "waf.update",
                (
                    [],
                    [("ASM_DATA", "Datadog/1/ASM_DATA/blocked_ips", _make_rc_data(ip_b))],
                ),
            )
            self.check_waf_no_errors()
            response = interface.client.get("/", headers={"X-Real-Ip": ip_a})
            assert self.status(response) == 200, "Step 3: expected 200 for ip_a after switching to ip_b"
            test_spans.reset()

            response = interface.client.get("/", headers={"X-Real-Ip": ip_b})
            assert self.status(response) == 403, "Step 3: expected 403 for ip_b"
            self.check_single_rule_triggered("blk-001-001", entry_span)
            test_spans.reset()

            # Step 4: Revert — remove ASM_DATA, no IPs should be blocked
            core.dispatch(
                "waf.update",
                (
                    [("ASM_DATA", "Datadog/1/ASM_DATA/blocked_ips")],
                    [],
                ),
            )
            self.check_waf_no_errors()
            response = interface.client.get("/", headers={"X-Real-Ip": ip_a})
            assert self.status(response) == 200, "Step 4: expected 200 for ip_a after revert"
            test_spans.reset()

            response = interface.client.get("/", headers={"X-Real-Ip": ip_b})
            assert self.status(response) == 200, "Step 4: expected 200 for ip_b after revert"

    def test_rc_custom_rules_update_lifecycle(self, interface, test_spans, entry_span):
        """Test the full lifecycle of RC custom rule updates:
        1. Default config: Arachni user-agent is detected but not blocked
        2. RC update pushes a custom rule that blocks Arachni user-agent: blocked
        3. RC update replaces with a custom rule that blocks a query param pattern: Arachni no longer blocked
        4. RC data removed (revert to default): back to default behavior
        """
        arachni_ua = "Arachni/v1.5.1"
        attack_query = "1 OR 1=1"

        def _make_custom_rule(rule_id, address, regex, key_path=None):
            """Build a custom rule payload that blocks requests matching a regex pattern."""
            input_spec = {"address": address}
            if key_path:
                input_spec["key_path"] = key_path
            return {
                "custom_rules": [
                    {
                        "id": rule_id,
                        "name": f"Custom rule {rule_id}",
                        "tags": {"type": "custom", "category": "attack_attempt"},
                        "conditions": [
                            {
                                "operator": "match_regex",
                                "parameters": {
                                    "inputs": [input_spec],
                                    "regex": regex,
                                    "options": {"case_sensitive": False},
                                },
                            }
                        ],
                        "transformers": [],
                        "on_match": ["block"],
                    }
                ]
            }

        with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True)):
            self.update_tracer(interface)

            # Step 1: Default config — Arachni detected but not blocked
            response = interface.client.get("/", headers={"User-Agent": arachni_ua})
            assert self.status(response) == 200, "Step 1: expected 200 for Arachni with default rules"
            self.check_single_rule_triggered("ua0-600-12x", entry_span)
            test_spans.reset()

            # Step 2: RC update — push custom rule that blocks Arachni user-agent
            custom_rule_ua = _make_custom_rule(
                "custom-ua-block-001",
                "server.request.headers.no_cookies",
                regex="^Arachni",
                key_path=["user-agent"],
            )
            core.dispatch(
                "waf.update",
                (
                    [],
                    [("ASM", "Datadog/1/ASM/custom_rules", custom_rule_ua)],
                ),
            )
            self.check_waf_no_errors()
            response = interface.client.get("/", headers={"User-Agent": arachni_ua})
            assert self.status(response) == 403, "Step 2: expected 403 after custom rule blocks Arachni"
            # The custom blocking rule must fire; the default monitoring rule may also fire
            self.check_rule_triggered("custom-ua-block-001", entry_span)
            test_spans.reset()

            # Step 3: RC update — replace with custom rule blocking query param pattern
            custom_rule_query = _make_custom_rule(
                "custom-query-block-001",
                "server.request.query",
                regex="OR\\s+1=1",
            )
            core.dispatch(
                "waf.update",
                (
                    [],
                    [("ASM", "Datadog/1/ASM/custom_rules", custom_rule_query)],
                ),
            )
            self.check_waf_no_errors()
            # Arachni should no longer be blocked by the custom rule
            response = interface.client.get("/", headers={"User-Agent": arachni_ua})
            assert self.status(response) == 200, "Step 3: expected 200 for Arachni after rule change"
            test_spans.reset()

            # SQL injection pattern should now be blocked
            response = interface.client.get(f"/?q={quote(attack_query)}", headers={"User-Agent": "Mozilla/5.0"})
            assert self.status(response) == 403, "Step 3: expected 403 for SQL injection query"
            # The custom blocking rule must fire; default rules may also match the pattern
            self.check_rule_triggered("custom-query-block-001", entry_span)
            test_spans.reset()

            # Step 4: Revert — remove custom rules, back to default behavior
            core.dispatch(
                "waf.update",
                (
                    [("ASM", "Datadog/1/ASM/custom_rules")],
                    [],
                ),
            )
            self.check_waf_no_errors()
            # Arachni detected but not blocked (default behavior)
            response = interface.client.get("/", headers={"User-Agent": arachni_ua})
            assert self.status(response) == 200, "Step 4: expected 200 for Arachni after revert"
            self.check_single_rule_triggered("ua0-600-12x", entry_span)
            test_spans.reset()

            # SQL injection pattern should no longer be blocked
            response = interface.client.get(f"/?q={quote(attack_query)}", headers={"User-Agent": "Mozilla/5.0"})
            assert self.status(response) == 200, "Step 4: expected 200 for query after revert"

    def test_rc_ruleset_update_lifecycle(self, interface, test_spans, entry_span):
        """Test the full lifecycle of ASM_DD rule set updates:
        1. Default rules: dd-test-scanner-log-block user-agent is blocked, Arachni is detected but not blocked
        2. ASM_DD update replaces ruleset with one that blocks Arachni: Arachni blocked,
           dd-test-scanner-log-block no longer blocked (its rule was replaced away)
        3. ASM_DD update replaces ruleset with one that blocks a query param pattern:
           Arachni no longer blocked, query param blocked
        4. ASM_DD removed (revert): default rules restored, dd-test-scanner-log-block blocked again
        """
        canary_ua = "dd-test-scanner-log-block"
        arachni_ua = "Arachni/v1.5.1"
        attack_query = "block_that_value"

        def _make_ruleset(rule_id, address, key_path, operator, pattern):
            """Build a minimal ASM_DD ruleset with a single blocking rule."""
            condition = {
                "operator": operator,
                "parameters": {
                    "inputs": [{"address": address}],
                },
            }
            if key_path:
                condition["parameters"]["inputs"][0]["key_path"] = key_path
            if operator == "match_regex":
                condition["parameters"]["regex"] = pattern
                condition["parameters"]["options"] = {"case_sensitive": False}
            elif operator == "phrase_match":
                condition["parameters"]["list"] = [pattern]

            return {
                "rules": [
                    {
                        "id": rule_id,
                        "name": f"Test rule {rule_id}",
                        "tags": {"type": "test_type", "category": "attack_attempt"},
                        "conditions": [condition],
                        "transformers": [],
                        "on_match": ["block"],
                    }
                ]
            }

        with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True)):
            self.update_tracer(interface)

            # Step 1: Default rules — canary UA is blocked, Arachni detected but not blocked
            response = interface.client.get("/", headers={"User-Agent": canary_ua})
            assert self.status(response) == 403, "Step 1: expected 403 for canary UA with default rules"
            self.check_single_rule_triggered("ua0-600-56x", entry_span)
            test_spans.reset()

            response = interface.client.get("/", headers={"User-Agent": arachni_ua})
            assert self.status(response) == 200, "Step 1: expected 200 for Arachni with default rules"
            self.check_single_rule_triggered("ua0-600-12x", entry_span)
            test_spans.reset()

            # Step 2: ASM_DD update — replace entire ruleset with one that blocks Arachni
            ruleset_block_arachni = _make_ruleset(
                rule_id="test-arachni-block",
                address="server.request.headers.no_cookies",
                key_path=["user-agent"],
                operator="match_regex",
                pattern="^Arachni",
            )
            core.dispatch(
                "waf.update",
                (
                    [],
                    [("ASM_DD", "Datadog/1/ASM_DD/rules", ruleset_block_arachni)],
                ),
            )
            self.check_waf_no_errors()
            # Arachni should now be blocked
            response = interface.client.get("/", headers={"User-Agent": arachni_ua})
            assert self.status(response) == 403, "Step 2: expected 403 for Arachni after ASM_DD update"
            self.check_single_rule_triggered("test-arachni-block", entry_span)
            test_spans.reset()

            # Canary UA should no longer be blocked (its rule was replaced)
            response = interface.client.get("/", headers={"User-Agent": canary_ua})
            assert self.status(response) == 200, "Step 2: expected 200 for canary UA after ruleset replacement"
            test_spans.reset()

            # Step 3: ASM_DD update — replace with a rule that blocks a query param
            ruleset_block_query = _make_ruleset(
                rule_id="test-query-block",
                address="server.request.query",
                key_path=None,
                operator="phrase_match",
                pattern=attack_query,
            )
            core.dispatch(
                "waf.update",
                (
                    [],
                    [("ASM_DD", "Datadog/1/ASM_DD/rules", ruleset_block_query)],
                ),
            )
            self.check_waf_no_errors()
            # Arachni should no longer be blocked
            response = interface.client.get("/", headers={"User-Agent": arachni_ua})
            assert self.status(response) == 200, "Step 3: expected 200 for Arachni after second ruleset update"
            test_spans.reset()

            # Query param should be blocked
            response = interface.client.get(f"/?q={attack_query}")
            assert self.status(response) == 403, "Step 3: expected 403 for query param attack"
            self.check_single_rule_triggered("test-query-block", entry_span)
            test_spans.reset()

            # Step 4: Revert — remove ASM_DD, default rules should be restored
            core.dispatch(
                "waf.update",
                (
                    [("ASM_DD", "Datadog/1/ASM_DD/rules")],
                    [],
                ),
            )
            self.check_waf_no_errors()
            # Canary UA should be blocked again (default rule restored)
            response = interface.client.get("/", headers={"User-Agent": canary_ua})
            assert self.status(response) == 403, "Step 4: expected 403 for canary UA after revert"
            self.check_single_rule_triggered("ua0-600-56x", entry_span)
            test_spans.reset()

            # Arachni should be detected but not blocked (default behavior)
            response = interface.client.get("/", headers={"User-Agent": arachni_ua})
            assert self.status(response) == 200, "Step 4: expected 200 for Arachni after revert"
            self.check_single_rule_triggered("ua0-600-12x", entry_span)
            test_spans.reset()

            # Query param should no longer be blocked
            response = interface.client.get(f"/?q={attack_query}")
            assert self.status(response) == 200, "Step 4: expected 200 for query param after revert"

    def test_rc_ruleset_duplicate_rules_report_errors(self, interface, test_spans, entry_span):
        """Test that the WAF reports errors when two ASM_DD configs contain duplicate rule IDs.
        1. Push a first ASM_DD ruleset with a rule
        2. Push a second ASM_DD ruleset on a different path with the same rule ID: WAF should report errors
        3. Remove both configs: WAF should be clean again
        """
        duplicate_rule_id = "test-duplicate-rule"

        def _make_ruleset(ua_pattern):
            return {
                "rules": [
                    {
                        "id": duplicate_rule_id,
                        "name": f"Test rule matching {ua_pattern}",
                        "tags": {"type": "test_type", "category": "attack_attempt"},
                        "conditions": [
                            {
                                "operator": "match_regex",
                                "parameters": {
                                    "inputs": [
                                        {
                                            "address": "server.request.headers.no_cookies",
                                            "key_path": ["user-agent"],
                                        }
                                    ],
                                    "regex": ua_pattern,
                                    "options": {"case_sensitive": False},
                                },
                            }
                        ],
                        "transformers": [],
                        "on_match": ["block"],
                    }
                ]
            }

        with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True)):
            self.update_tracer(interface)

            # Step 1: Push first ASM_DD ruleset — no errors expected
            core.dispatch(
                "waf.update",
                (
                    [],
                    [("ASM_DD", "Datadog/1/ASM_DD/rules_v1", _make_ruleset("^Arachni"))],
                ),
            )
            self.check_waf_no_errors()

            # Step 2: Push second ASM_DD ruleset with the same rule ID on a different path
            # WAF should report errors about the duplicate rule
            core.dispatch(
                "waf.update",
                (
                    [],
                    [("ASM_DD", "Datadog/1/ASM_DD/rules_v2", _make_ruleset("^Nessus"))],
                ),
            )
            self.check_waf_errors(
                expected_failed=1,
                expected_errors={"duplicate rule": [duplicate_rule_id]},
            )

            # The first ruleset's rule should still work despite the duplicate error
            response = interface.client.get("/", headers={"User-Agent": "Arachni/v1.5.1"})
            assert self.status(response) == 403, "First rule should still block Arachni"
            self.check_single_rule_triggered(duplicate_rule_id, entry_span)
            test_spans.reset()

            # Step 3: Remove both configs — WAF should be clean (default rules restored)
            core.dispatch(
                "waf.update",
                (
                    [
                        ("ASM_DD", "Datadog/1/ASM_DD/rules_v1"),
                        ("ASM_DD", "Datadog/1/ASM_DD/rules_v2"),
                    ],
                    [],
                ),
            )
            self.check_waf_no_errors()

    def test_multiple_service_name(self, interface):
        import time

        with override_global_config(dict(_remote_config_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/new_service/awesome_test")
            assert self.status(response) == 200
            assert self.body(response) == "awesome_test"
            for _ in range(10):
                if "awesome_test" in ddtrace.config._get_extra_services():
                    break
                time.sleep(1)
            else:
                raise AssertionError("extra service not found")
