import json
import logging
import os.path

import mock
import pytest

from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._constants import FINGERPRINTING
from ddtrace.appsec._constants import WAF_DATA_NAMES
from ddtrace.appsec._ddwaf import DDWaf
from ddtrace.appsec._ddwaf.ddwaf_types import py_ddwaf_builder_get_config_paths
from ddtrace.appsec._processor import AppSecSpanProcessor
from ddtrace.appsec._processor import WAFUpdateError
from ddtrace.appsec._processor import _transform_headers
from ddtrace.appsec._processor import asm_config as _processor_asm_config
from ddtrace.appsec._processor import waf_update
from ddtrace.appsec._utils import get_triggers
from ddtrace.constants import USER_KEEP
from ddtrace.contrib.internal.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
import tests.appsec.rules as rules
from tests.appsec.utils import asm_context
from tests.appsec.utils import get_waf_addresses
from tests.appsec.utils import is_blocked
from tests.utils import override_env
from tests.utils import override_global_config
from tests.utils import snapshot


APPSEC_JSON_TAG = f"meta.{APPSEC.JSON}"
config_asm = {"_asm_enabled": True}
config_good_rules = {"_asm_static_rule_file": rules.RULES_GOOD_PATH, "_asm_enabled": True}
config_bad_rules = {"_asm_static_rule_file": rules.RULES_BAD_PATH, "_asm_enabled": True, "_raise": True}


def test_transform_headers():
    transformed = _transform_headers(
        {
            "hello": "world",
            "Foo": "bar1",
            "foo": "bar2",
            "fOO": "bar3",
            "BAR": "baz",
            "COOKIE": "secret",
        },
    )
    assert set(transformed.keys()) == {"hello", "bar", "foo"}
    assert transformed["hello"] == "world"
    assert transformed["bar"] == "baz"
    assert set(transformed["foo"]) == {"bar1", "bar2", "bar3"}


def test_enable(tracer):
    with asm_context(tracer=tracer, config=config_asm) as span:
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    assert span.get_metric("_dd.appsec.enabled") == 1.0


def test_enable_custom_rules():
    with override_global_config(dict(_asm_static_rule_file=rules.RULES_GOOD_PATH)):
        processor = AppSecSpanProcessor()
        processor.delayed_init()

    assert processor.enabled
    assert processor.rule_filename == rules.RULES_GOOD_PATH


def test_ddwaf_ctx(tracer):
    with asm_context(tracer=tracer, config=config_good_rules) as span:
        processor = AppSecSpanProcessor()
        processor.on_span_start(span)
        ctx = _asm_request_context._get_asm_context()
        assert ctx
        processor.on_span_finish(span)
        assert _asm_request_context._get_asm_context() is None


@pytest.mark.parametrize("rule, _exc", [(rules.RULES_MISSING_PATH, IOError), (rules.RULES_BAD_PATH, ValueError)])
def test_enable_bad_rules(rule, _exc, tracer):
    # by default enable must not crash but display errors in the logs
    with asm_context(tracer=tracer, config=config_bad_rules) as span:
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")


def test_retain_traces(tracer):
    with asm_context(tracer=tracer, config=config_asm) as span:
        print(">>> set HTTP meta", flush=True)
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    assert span.context.sampling_priority == USER_KEEP


def test_valid_json(tracer):
    with asm_context(tracer=tracer, config=config_asm) as span:
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    assert get_triggers(span)


def test_header_attack(tracer):
    with asm_context(tracer=tracer, config=dict(retrieve_client_ip=True, _asm_enabled=True)) as span:
        set_http_meta(
            span,
            rules.Config(),
            request_headers={
                "User-Agent": "Arachni/v1",
                "user-agent": "aa",
                "x-forwarded-for": "8.8.8.8",
            },
        )

    assert get_triggers(span)
    assert span.get_tag("actor.ip") == "8.8.8.8"


def test_headers_collection(tracer):
    with asm_context(tracer=tracer, config=config_asm) as span:
        set_http_meta(
            span,
            rules.Config(),
            raw_uri="http://example.com/.git",
            status_code="404",
            request_headers={
                "hello": "world",
                "accept": "something",
                "x-Forwarded-for": "127.0.0.1",
            },
            response_headers={
                "foo": "bar",
                "Content-Length": "500",
            },
        )

    assert span.get_tag("http.request.headers.hello") is None
    assert span.get_tag("http.request.headers.accept") == "something"
    assert span.get_tag("http.request.headers.x-forwarded-for") == "127.0.0.1"
    assert span.get_tag("http.response.headers.content-length") == "500"
    assert span.get_tag("http.response.headers.foo") is None


@snapshot(
    include_tracer=True,
    ignores=[
        "meta_struct",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
        APPSEC_JSON_TAG,
        "meta." + FINGERPRINTING.NETWORK,
        "meta." + FINGERPRINTING.HEADER,
        "meta." + FINGERPRINTING.ENDPOINT,
        "meta." + FINGERPRINTING.SESSION,
        "service",
        "meta._dd.rc.client_id",
        "meta._dd.appsec.rc_products",
        "meta._dd.svc_src",
    ],
)
def test_appsec_cookies_no_collection_snapshot(tracer):
    # We use tracer instead of tracer_appsec because snapshot is looking for tracer fixture and not understands
    # other fixtures
    with asm_context(tracer=tracer, config=config_asm) as span:
        set_http_meta(
            span,
            {},
            raw_uri="http://example.com/.git",
            status_code="404",
            request_cookies={"cookie1": "im the cookie1"},
        )

    assert get_triggers(span)


@snapshot(
    include_tracer=True,
    ignores=[
        "meta_struct",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
        APPSEC_JSON_TAG,
        "meta." + FINGERPRINTING.NETWORK,
        "meta." + FINGERPRINTING.HEADER,
        "meta." + FINGERPRINTING.ENDPOINT,
        "meta." + FINGERPRINTING.SESSION,
        "service",
        "meta._dd.rc.client_id",
        "meta._dd.appsec.rc_products",
        "meta._dd.svc_src",
    ],
)
def test_appsec_body_no_collection_snapshot(tracer):
    with asm_context(tracer=tracer, config=config_asm) as span:
        set_http_meta(
            span,
            {},
            raw_uri="http://example.com/.git",
            status_code="404",
            request_body={"somekey": "somekey value"},
        )

    assert get_triggers(span)


def test_ip_block(tracer):
    with asm_context(tracer=tracer, ip_addr=rules._IP.BLOCKED, config=config_good_rules) as span:
        set_http_meta(
            span,
            rules.Config(),
        )
    assert get_triggers(span)
    assert get_waf_addresses("http.request.remote_ip") == rules._IP.BLOCKED
    assert is_blocked(span)


@pytest.mark.parametrize("ip", [rules._IP.MONITORED, rules._IP.BYPASS, rules._IP.DEFAULT])
def test_ip_not_block(tracer, ip):
    with asm_context(tracer=tracer, ip_addr=ip, config=config_good_rules) as span:
        set_http_meta(
            span,
            rules.Config(),
        )

    assert get_waf_addresses("http.request.remote_ip") == ip
    assert is_blocked(span) is False


def test_ip_update_rules_and_block(tracer):
    with asm_context(tracer=tracer, ip_addr=rules._IP.BLOCKED, config=config_asm) as span1:
        core.dispatch(
            "waf.update",
            (
                [],
                [
                    (
                        "ASM",
                        "Datadog/1/ASM/data",
                        {
                            "rules_data": [
                                {
                                    "data": [
                                        {"value": rules._IP.BLOCKED},
                                    ],
                                    "id": "blocked_ips",
                                    "type": "ip_with_expiration",
                                },
                            ]
                        },
                    )
                ],
            ),
        )
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(
                span,
                rules.Config(),
            )

    assert get_waf_addresses("http.request.remote_ip") == rules._IP.BLOCKED
    assert is_blocked(span1)
    assert (span._local_root or span).get_tag(APPSEC.RC_PRODUCTS) == "[ASM:1] u:1 r:2"

    from ddtrace.appsec._processor import AppSecSpanProcessor

    assert AppSecSpanProcessor._instance
    assert py_ddwaf_builder_get_config_paths(AppSecSpanProcessor._instance._ddwaf._builder, "ASM/data") == 1


def test_ip_update_rules_expired_no_block(tracer):
    with asm_context(tracer=tracer, ip_addr=rules._IP.BLOCKED, config=config_asm):
        core.dispatch(
            "waf.update",
            (
                [],
                [
                    (
                        "ASM",
                        "Datadog/1/ASM/data",
                        {
                            "rules_data": [
                                {
                                    "data": [
                                        {"expiration": 1662804872, "value": rules._IP.BLOCKED},
                                    ],
                                    "id": "blocked_ips",
                                    "type": "ip_with_expiration",
                                },
                            ]
                        },
                    )
                ],
            ),
        )
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(
                span,
                rules.Config(),
            )

    assert get_waf_addresses("http.request.remote_ip") == rules._IP.BLOCKED
    assert is_blocked(span) is False
    assert (span._local_root or span).get_tag(APPSEC.RC_PRODUCTS) == "[ASM:1] u:1 r:2"


@snapshot(
    include_tracer=True,
    ignores=[
        "meta_struct",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
        APPSEC_JSON_TAG,
        "meta." + FINGERPRINTING.NETWORK,
        "meta." + FINGERPRINTING.HEADER,
        "meta." + FINGERPRINTING.ENDPOINT,
        "meta." + FINGERPRINTING.SESSION,
        "service",
        "meta._dd.base_service",
        "meta._dd.rc.client_id",
        "meta._dd.appsec.rc_products",
        "meta._dd.svc_src",
    ],
)
def test_appsec_span_tags_snapshot(tracer):
    with asm_context(tracer=tracer, config=config_asm, service="test") as span:
        span.set_tag("http.url", "http://example.com/.git")
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    assert get_triggers(span)


@snapshot(
    include_tracer=True,
    ignores=[
        "meta_struct",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
        APPSEC_JSON_TAG,
        "meta._dd.appsec.event_rules.errors",
        "service",
        "meta._dd.base_service",
        "meta._dd.rc.client_id",
        "meta._dd.appsec.rc_products",
        "meta._dd.svc_src",
    ],
)
def test_appsec_span_tags_snapshot_with_errors(tracer):
    config = dict(
        _asm_enabled=True,
        _asm_static_rule_file=os.path.join(rules.ROOT_DIR, "rules-with-2-errors.json"),
        _waf_timeout=50_000,
    )
    with asm_context(tracer=tracer, config=config, service="test") as span:
        span.set_tag("http.url", "http://example.com/.git")
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    assert get_triggers(span) is None


def test_appsec_span_rate_limit(tracer):
    with override_env(dict(DD_APPSEC_TRACE_RATE_LIMIT="1")):
        with asm_context(tracer=tracer, config=config_asm) as span1:
            set_http_meta(span1, {}, raw_uri="http://example.com/.git", status_code="404")

        with asm_context(tracer=tracer, config={}) as span2:
            set_http_meta(span2, {}, raw_uri="http://example.com/.git", status_code="404")
            span2.start_ns = span1.start_ns + 1

        with asm_context(tracer=tracer, config={}) as span3:
            set_http_meta(span3, {}, raw_uri="http://example.com/.git", status_code="404")
            span2.start_ns = span1.start_ns + 2

        assert get_triggers(span1)
        assert get_triggers(span2) is None
        assert get_triggers(span3) is None


def test_ddwaf_not_raises_exception():
    with open(DEFAULT.RULES, "br") as rules:
        rules_json_str = rules.read()
        DDWaf(
            rules_json_str,
            DEFAULT.APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP.encode("utf-8"),
            DEFAULT.APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP.encode("utf-8"),
        )


@pytest.mark.subprocess(err="Disabling AppSec: libddwaf failed to load (mock libddwaf load failure)\n")
def test_appsec_abort_on_waf_failure():
    """Simulate a libddwaf loading error

    AppSecSpanProcessor enablement occurs in `load_appsec` with `override_global_config` and should abort
    completely if an error is found in the bindings layer.
    """
    import ctypes

    import mock

    from ddtrace.internal.settings.asm import config as asm_config
    from tests.utils import override_global_config

    original_cdll = ctypes.CDLL

    ERROR_MESSAGE = "mock libddwaf load failure"

    def _raise_on_libddwaf(path, *args, **kwargs):
        if path == asm_config._asm_libddwaf:
            raise OSError(ERROR_MESSAGE)
        return original_cdll(path, *args, **kwargs)

    with (
        mock.patch("ctypes.CDLL", side_effect=_raise_on_libddwaf),
    ):
        with override_global_config(
            dict(
                _asm_enabled=True,
                _asm_can_be_enabled=True,
                _asm_rc_enabled=True,
                _api_security_active=True,
                _load_modules=True,
            )
        ):
            assert asm_config._asm_libddwaf_available is False
            assert asm_config._asm_enabled is False
            assert asm_config._asm_can_be_enabled is False
            assert asm_config._asm_rc_enabled is False
            assert asm_config._load_modules is False


def test_obfuscation_parameter_key_empty():
    with override_global_config(dict(_asm_obfuscation_parameter_key_regexp="")):
        processor = AppSecSpanProcessor()
        processor.delayed_init()

    assert processor.enabled


def test_obfuscation_parameter_value_empty():
    with override_global_config(dict(_asm_obfuscation_parameter_value_regexp="")):
        processor = AppSecSpanProcessor()
        processor.delayed_init()

    assert processor.enabled


def test_obfuscation_parameter_key_and_value_empty():
    with override_global_config(
        dict(_asm_obfuscation_parameter_key_regexp="", _asm_obfuscation_parameter_value_regexp="")
    ):
        processor = AppSecSpanProcessor()
        processor.delayed_init()

    assert processor.enabled


def test_obfuscation_parameter_key_invalid_regex():
    with override_global_config(dict(_asm_obfuscation_parameter_key_regexp="(")):
        processor = AppSecSpanProcessor()
        processor.delayed_init()

    assert processor.enabled


def test_obfuscation_parameter_invalid_regex():
    with override_global_config(dict(_asm_obfuscation_parameter_value_regexp="(")):
        processor = AppSecSpanProcessor()
        processor.delayed_init()

    assert processor.enabled


def test_obfuscation_parameter_key_and_value_invalid_regex():
    with override_global_config(
        dict(_asm_obfuscation_parameter_key_regexp="(", _asm_obfuscation_parameter_value_regexp="(")
    ):
        processor = AppSecSpanProcessor()
        processor.delayed_init()

    assert processor.enabled


def test_obfuscation_parameter_value_unconfigured_not_matching(tracer):
    with asm_context(tracer=tracer, config=config_asm) as span:
        set_http_meta(span, rules.Config(), raw_uri="http://example.com/.git?hello=goodbye", status_code="404")

    triggers = get_triggers(span)
    assert triggers
    values = [
        value.get("value")
        for rule in triggers
        for match in rule.get("rule_matches", [])
        for value in match.get("parameters", [])
    ]
    assert any("hello" in value for value in values)
    assert any("goodbye" in value for value in values)
    assert all("<Redacted>" not in value for value in values)


@pytest.mark.parametrize("key", ["password", "public_key", "jsessionid", "jwt"])
def test_obfuscation_parameter_value_unconfigured_matching(tracer, key):
    with asm_context(tracer=tracer, config=config_asm) as span:
        set_http_meta(span, rules.Config(), raw_uri=f"http://example.com/.git?{key}=goodbye", status_code="404")

    triggers = get_triggers(span)
    assert triggers
    values = [
        value.get("value")
        for rule in triggers
        for match in rule.get("rule_matches", [])
        for value in match.get("parameters", [])
    ]
    assert all("goodbye" not in value for value in values)
    assert any("<Redacted>" in value for value in values)


def test_obfuscation_parameter_value_configured_not_matching(tracer):
    config = dict(_asm_enabled=True, _asm_obfuscation_parameter_value_regexp="token")
    with asm_context(tracer=tracer, config=config) as span:
        set_http_meta(span, rules.Config(), raw_uri="http://example.com/.git?password=goodbye", status_code="404")

    triggers = get_triggers(span)
    assert triggers
    values = [
        value.get("value")
        for rule in triggers
        for match in rule.get("rule_matches", [])
        for value in match.get("parameters", [])
    ]
    assert any("password" in value for value in values)
    assert any("goodbye" in value for value in values)
    assert all("<Redacted>" not in value for value in values)


def test_obfuscation_parameter_value_configured_matching(tracer):
    config = dict(_asm_enabled=True, _asm_obfuscation_parameter_value_regexp="token")
    with asm_context(tracer=tracer, config=config) as span:
        set_http_meta(span, rules.Config(), raw_uri="http://example.com/.git?token=goodbye", status_code="404")

    triggers = get_triggers(span)
    assert triggers
    values = [
        value.get("value")
        for rule in triggers
        for match in rule.get("rule_matches", [])
        for value in match.get("parameters", [])
    ]
    assert all("token" not in value for value in values)
    assert all("goodbye" not in value for value in values)
    assert any("<Redacted>" in value for value in values)


def test_ddwaf_run():
    with open(rules.RULES_GOOD_PATH, "br") as rule_set:
        rules_json_str = rule_set.read()
        _ddwaf = DDWaf(rules_json_str, b"", b"")
        data = {
            "server.request.query": {},
            "server.request.headers.no_cookies": {"user-agent": "werkzeug/2.1.2", "host": "localhost"},
            "server.request.cookies": {"attack": "1' or '1' = '1'"},
            "server.response.headers.no_cookies": {"content-type": "text/html; charset=utf-8", "content-length": "207"},
        }
        ctx = _ddwaf._at_request_start()
        res = _ddwaf.run(ctx, data, timeout_ms=DEFAULT.WAF_TIMEOUT)  # res is a serialized json
        assert res.data
        assert res.data[0]["rule"]["id"] == "crs-942-100"
        assert res.runtime > 0
        assert res.total_runtime > 0
        assert res.total_runtime > res.runtime
        assert res.timeout is False


def test_ddwaf_run_timeout():
    with open(rules.RULES_GOOD_PATH, "br") as rule_set:
        rules_json = rule_set.read()
        _ddwaf = DDWaf(rules_json, b"", b"")
        data = {
            "server.request.path_params": {"param_{}".format(i): "value_{}".format(i) for i in range(100)},
            "server.request.cookies": {"attack{}".format(i): "1' or '1' = '{}'".format(i) for i in range(100)},
        }
        ctx = _ddwaf._at_request_start()
        res = _ddwaf.run(ctx, data, timeout_ms=0.001)  # res is a serialized json
        assert res.runtime > 0
        assert res.total_runtime > 0
        assert res.total_runtime > res.runtime
        assert res.timeout is True


def test_ddwaf_info():
    with open(rules.RULES_GOOD_PATH, "br") as rule_set:
        rules_json_str = rule_set.read()
        _ddwaf = DDWaf(rules_json_str, b"", b"")

        info = _ddwaf.info
        rules_json = json.loads(rules_json_str.decode())
        assert info.loaded == len(rules_json["rules"])
        assert info.failed == 0
        assert info.errors == ""
        assert info.version == "rules_good"


def test_ddwaf_info_with_2_errors():
    with open(os.path.join(rules.ROOT_DIR, "rules-with-2-errors.json"), "br") as rule_set:
        rules_json_str = rule_set.read()
        _ddwaf = DDWaf(rules_json_str, b"", b"")

        info = _ddwaf.info
        assert info.loaded == 1
        assert info.failed == 2
        # Compare dict contents insensitive to ordering
        expected_dict = sorted(
            {"missing key 'conditions'": ["crs-913-110"], "missing key 'tags'": ["crs-942-100"]}.items()
        )
        assert sorted(json.loads(info.errors).items()) == expected_dict
        assert info.version == "5.5.5"


def test_ddwaf_info_with_3_errors():
    with open(os.path.join(rules.ROOT_DIR, "rules-with-3-errors.json"), "br") as rule_set:
        rules_json_str = rule_set.read()
        _ddwaf = DDWaf(rules_json_str, b"", b"")

        info = _ddwaf.info
        assert info.loaded == 1
        assert info.failed == 2
        assert json.loads(info.errors) == {"missing key 'name'": ["crs-942-100", "crs-913-120"]}


def test_ddwaf_update_invalid_asm_dd_keeps_default_ruleset():
    """Regression test for APPSEC-68544.

    A rejected (invalid/malicious) ASM_DD remote-config payload must not drop the bundled default
    detection ruleset. Before the fix, update_rules removed the default from the builder and marked
    the rejected path as loaded in ``_asm_dd_cache`` before checking the result, leaving the WAF
    without the default rules (fail-open).
    """
    from ddtrace.appsec._ddwaf.waf import ASM_DD_DEFAULT

    with open(rules.RULES_GOOD_PATH, "br") as rule_set:
        _ddwaf = DDWaf(rule_set.read(), b"", b"")

    # The bundled default ruleset is the only ASM_DD config loaded at startup.
    assert _ddwaf.initialized
    assert _ddwaf._asm_dd_cache == {ASM_DD_DEFAULT}
    assert py_ddwaf_builder_get_config_paths(_ddwaf._builder, ASM_DD_DEFAULT) == 1
    default_required_data = set(_ddwaf.required_data)

    # An ASM_DD payload that libddwaf rejects (here: "rules" is not a list, so no rule loads).
    rejected_path = "datadog/2/ASM_DD/rejected/config"
    ok = _ddwaf.update_rules([], [("ASM_DD", rejected_path, {"version": "2.1", "rules": "not-a-list"})])

    # The update reports failure and the default ruleset is preserved, not the rejected payload.
    assert ok is False
    assert _ddwaf._asm_dd_cache == {ASM_DD_DEFAULT}
    assert py_ddwaf_builder_get_config_paths(_ddwaf._builder, ASM_DD_DEFAULT) == 1
    assert py_ddwaf_builder_get_config_paths(_ddwaf._builder, rejected_path) == 0
    assert _ddwaf.initialized
    assert set(_ddwaf.required_data) == default_required_data

    # Remote config may later remove the previously-rejected config. Removing a config that was
    # never stored is the desired end-state, so it must not be reported as a failed update.
    ok = _ddwaf.update_rules([("ASM_DD", rejected_path)], [])
    assert ok is True
    assert _ddwaf._asm_dd_cache == {ASM_DD_DEFAULT}
    assert py_ddwaf_builder_get_config_paths(_ddwaf._builder, ASM_DD_DEFAULT) == 1

    # A valid ASM_DD payload must still take over and displace the default ruleset.
    with open(rules.RULES_GOOD_PATH) as rule_set:
        valid_rules = json.load(rule_set)
    accepted_path = "datadog/2/ASM_DD/accepted/config"
    ok = _ddwaf.update_rules([], [("ASM_DD", accepted_path, valid_rules)])

    assert ok is True
    assert _ddwaf._asm_dd_cache == {accepted_path}
    assert py_ddwaf_builder_get_config_paths(_ddwaf._builder, ASM_DD_DEFAULT) == 0
    assert py_ddwaf_builder_get_config_paths(_ddwaf._builder, accepted_path) == 1


def test_ddwaf_run_contained_typeerror(tracer, caplog):
    config = rules.Config()
    config.http_tag_query_string = True

    with (
        caplog.at_level(logging.DEBUG),
        mock.patch(
            "ddtrace.appsec._ddwaf.waf.ddwaf_context_eval",
            side_effect=TypeError("expected c_long instead of int"),
        ),
    ):
        with asm_context(tracer=tracer, config=config_asm) as span:
            set_http_meta(
                span,
                config,
                method="PATCH",
                url="http://localhost/api/unstable/role_requests/dab1e9ae-9d99-11ed-bfdf-da7ad0900000?_authentication_token=2b0297348221f294de3a047e2ecf1235abb866b6",  # noqa: E501
                status_code="200",
                raw_uri="http://localhost/api/unstable/role_requests/dab1e9ae-9d99-11ed-bfdf-da7ad0900000?_authentication_token=2b0297348221f294de3a047e2ecf1235abb866b6",  # noqa: E501
                request_headers={
                    "host": "localhost",
                    "user-agent": "aa",
                    "content-length": "73",
                },
                response_headers={
                    "content-length": "501",
                    "x-ratelimit-remaining": "363",
                    "x-ratelimit-name": "role_api",
                    "x-ratelimit-limit": "500",
                    "x-ratelimit-period": "60",
                    "content-type": "application/json",
                    "x-ratelimit-reset": "16",
                },
                request_body={"_authentication_token": "2b0297348221f294de3a047e2ecf1235abb866b6"},
            )

    assert get_triggers(span) is None
    assert "TypeError: expected c_long instead of int" in caplog.text


def test_ddwaf_run_contained_oserror(tracer, caplog):
    config = rules.Config()
    config.http_tag_query_string = True

    with (
        caplog.at_level(logging.DEBUG),
        mock.patch("ddtrace.appsec._ddwaf.waf.ddwaf_context_eval", side_effect=OSError("ddwaf run failed")),
    ):
        with asm_context(tracer=tracer, config=config_asm) as span:
            set_http_meta(
                span,
                config,
                method="PATCH",
                url="http://localhost/api/unstable/role_requests/dab1e9ae-9d99-11ed-bfdf-da7ad0900000?_authentication_token=2b0297348221f294de3a047e2ecf1235abb866b6",  # noqa: E501
                status_code="200",
                raw_uri="http://localhost/api/unstable/role_requests/dab1e9ae-9d99-11ed-bfdf-da7ad0900000?_authentication_token=2b0297348221f294de3a047e2ecf1235abb866b6",  # noqa: E501
                request_headers={
                    "host": "localhost",
                    "user-agent": "aa",
                    "content-length": "73",
                },
                response_headers={
                    "content-length": "501",
                    "x-ratelimit-remaining": "363",
                    "x-ratelimit-name": "role_api",
                    "x-ratelimit-limit": "500",
                    "x-ratelimit-period": "60",
                    "content-type": "application/json",
                    "x-ratelimit-reset": "16",
                },
                request_body={"_authentication_token": "2b0297348221f294de3a047e2ecf1235abb866b6"},
            )

    assert get_triggers(span) is None
    assert "OSError: ddwaf run failed" in caplog.text


def test_asm_context_registration(tracer):
    from ddtrace.appsec._asm_request_context import _ASM_CONTEXT

    # For a web type span, a context manager is added, but then removed
    with asm_context(tracer=tracer, config=config_asm) as span:
        assert core.find_item(_ASM_CONTEXT) is not None
    assert core.find_item(_ASM_CONTEXT) is None

    # Regression test, if the span type changes after being created, we always removed
    with asm_context(tracer=tracer, config=config_asm) as span:
        span.span_type = SpanTypes.HTTP
        assert core.find_item(_ASM_CONTEXT) is not None
    assert core.find_item(_ASM_CONTEXT) is None


CUSTOM_RULE_METHOD = [
    (
        "ASM",
        "Datadog/3421/ASM/data",
        {
            "custom_rules": [
                {
                    "conditions": [
                        {
                            "operator": "match_regex",
                            "parameters": {
                                "inputs": [{"address": "server.request.method"}],
                                "options": {"case_sensitive": False},
                                "regex": "GET",
                            },
                        }
                    ],
                    "id": "32b243c7-26eb-4046-adf4-custom",
                    "name": "test required",
                    "tags": {"category": "attack_attempt", "custom": "1", "type": "custom"},
                    "transformers": [],
                },
                {
                    "conditions": [
                        {
                            "operator": "match_regex",
                            "parameters": {
                                "inputs": [{"address": "usr.login"}],
                                "options": {"case_sensitive": False},
                                "regex": "GET",
                            },
                        }
                    ],
                    "id": "32b243c7-26eb-4046-bbbb-custom",
                    "name": "test required",
                    "tags": {"category": "attack_attempt", "custom": "1", "type": "custom"},
                    "transformers": [],
                },
                {
                    "conditions": [
                        {
                            "operator": "equals",
                            "parameters": {
                                "inputs": [
                                    {"address": "server.business_logic.payment.creation", "key_path": ["id"]},
                                    {"address": "server.business_logic.payment.cancellation", "key_path": ["id"]},
                                    {"address": "server.business_logic.payment.failure", "key_path": ["id"]},
                                    {"address": "server.business_logic.payment.success", "key_path": ["id"]},
                                    {"address": "server.business_logic.llm.event", "key_path": ["provider"]},
                                ],
                                "type": "string",
                                "value": "stripe",
                            },
                        }
                    ],
                    "id": "stripe",
                    "name": "required to test payment addresses",
                    "tags": {"category": "attack_attempt", "custom": "2", "type": "custom"},
                    "transformers": [],
                },
            ]
        },
    )
]


def test_required_addresses():
    with override_global_config(dict(_asm_static_rule_file=rules.RULES_GOOD_PATH)):
        processor = AppSecSpanProcessor()
        processor.delayed_init()

    assert processor._addresses_to_keep == {
        "grpc.server.request.message",
        "http.client_ip",
        "server.request.body",
        "server.request.cookies",
        "server.request.headers.no_cookies",
        "server.request.path_params",
        "server.request.query",
        "server.response.headers.no_cookies",
        "usr.id",
    }

    processor._update_rules([], CUSTOM_RULE_METHOD)

    assert processor._addresses_to_keep == {
        "grpc.server.request.message",
        "http.client_ip",
        "server.request.body",
        "server.request.cookies",
        "server.request.headers.no_cookies",
        "server.request.method",  # New required address
        "server.request.path_params",
        "server.request.query",
        "server.response.headers.no_cookies",
        "server.business_logic.payment.cancellation",
        "server.business_logic.payment.creation",
        "server.business_logic.payment.success",
        "server.business_logic.payment.failure",
        "server.business_logic.llm.event",
        "usr.id",
        "usr.login",
    }


@pytest.mark.parametrize(
    "persistent", [key for key, value in WAF_DATA_NAMES if value in WAF_DATA_NAMES.PERSISTENT_ADDRESSES]
)
@pytest.mark.parametrize("non_persistent", ["LFI_ADDRESS", "PROCESSOR_SETTINGS"])
@mock.patch("ddtrace.appsec._ddwaf.waf.DDWaf.run")
def test_persistent_dedup_and_non_persistent_resend(mock_run, persistent, non_persistent):
    # dd-trace-py only sends each persistent address to the WAF once per request (it persists in
    # the context), while non-persistent addresses are re-sent on every call. This dedup policy is
    # independent of the libddwaf version. call_args[0][1] is the single `data` argument of DDWaf.run.
    from ddtrace.appsec._utils import DDWaf_result
    from ddtrace.appsec._utils import _observator
    from ddtrace.trace import tracer

    mock_run.return_value = DDWaf_result(0, [], {}, 0.0, 0.0, False, _observator(), {})

    with asm_context(tracer=tracer, config=config_asm, rc_payload=CUSTOM_RULE_METHOD) as span:
        processor = AppSecSpanProcessor._instance
        assert processor
        # first call sends both the persistent and the non-persistent address to the waf
        processor._waf_action(span, None, {persistent: {"key_1": "value_1"}, non_persistent: {"key_2": "value_2"}})
        assert mock_run.call_args
        assert mock_run.call_args[0]
        assert mock_run.call_args[0][1] == {
            WAF_DATA_NAMES[persistent]: {"key_1": "value_1"},
            WAF_DATA_NAMES[non_persistent]: {"key_2": "value_2"},
        }
        # second call must not re-send the persistent address, but must re-send the non-persistent one
        processor._waf_action(span, None, {persistent: {"key_1": "value_1"}, non_persistent: {"key_2": "value_3"}})
        assert mock_run.call_args
        assert mock_run.call_args[0]
        assert mock_run.call_args[0][1] == {WAF_DATA_NAMES[non_persistent]: {"key_2": "value_3"}}
    assert (span._local_root or span).get_tag(APPSEC.RC_PRODUCTS) == "[ASM:1] u:1 r:1"


@mock.patch("ddtrace.appsec._ddwaf.waf.DDWaf.run")
def test_waf_action_none_value_non_persistent_address(mock_run):
    from ddtrace.appsec._utils import DDWaf_result
    from ddtrace.appsec._utils import _observator
    from ddtrace.trace import tracer

    mock_run.return_value = DDWaf_result(0, [], {}, 0.0, 0.0, False, _observator(), {})

    with asm_context(tracer=tracer, config=config_asm) as span:
        processor = AppSecSpanProcessor._instance
        assert processor
        # A None value for a non-persistent address must still be sent (it signals the address is
        # present, e.g. a login failure with no known user), not discarded.
        processor._waf_action(span, None, {"LOGIN_FAILURE": None})
        assert mock_run.call_args
        assert mock_run.call_args[0]
        assert mock_run.call_args[0][1] == {WAF_DATA_NAMES.LOGIN_FAILURE: None}


@pytest.mark.parametrize("skip_event", [True, False])
def test_lambda_unsupported_event(tracer, skip_event):
    """
    Test that the processor correctly handles the appsec_skip_next_lambda_event flag.
    """
    if skip_event:
        core.set_item("appsec_skip_next_lambda_event", True)

    config = {
        "_asm_enabled": True,
        "_asm_processed_span_types": {SpanTypes.SERVERLESS},
    }

    with asm_context(tracer=tracer, config=config, span_type=SpanTypes.SERVERLESS) as span:
        pass

    if skip_event:
        # When skip_event is True, the metric should be set and context item should be discarded
        assert span.get_metric(APPSEC.UNSUPPORTED_EVENT_TYPE) == 1.0
        assert core.find_item("appsec_skip_next_lambda_event") is None
    else:
        # When skip_event is False, the metric should not be set
        assert span.get_metric(APPSEC.UNSUPPORTED_EVENT_TYPE) is None


@pytest.mark.parametrize("inferred_span_name", ["aws.apigateway", "aws.httpapi", "azure.apim"])
def test_lambda_inferred_span(tracer, inferred_span_name):
    """
    Ensure that when the service entry span is below an inferred span, both spans have
    AppSec enabled and contain the waf triggers.
    """
    config = {
        "_asm_enabled": True,
        "_asm_processed_span_types": {SpanTypes.WEB, SpanTypes.SERVERLESS},
        "_asm_http_span_types": {SpanTypes.WEB, SpanTypes.SERVERLESS},
    }

    with override_global_config(config):
        tracer._recreate()
        with tracer.trace(inferred_span_name, span_type=SpanTypes.WEB, service="api_gateway") as gateway_span:
            with tracer.trace("aws.lambda", span_type=SpanTypes.SERVERLESS, service="test_function") as lambda_span:
                set_http_meta(lambda_span, {}, raw_uri="http://example.com/.git", status_code="404")

    assert lambda_span.get_metric(APPSEC.ENABLED) == 1.0
    assert gateway_span.get_metric(APPSEC.ENABLED) == 1.0
    assert get_triggers(lambda_span)
    assert get_triggers(gateway_span)


def test_rasp_subcontext_scope_shared_for_ssrf():
    """A nested SSRF operation (e.g. requests driving urllib3) must share ONE subcontext.

    The reentrant open must not let an inner scope clobber the outer holder, and SSRF_REQ +
    SSRF_RES of the same outgoing request must resolve the same subcontext.
    """
    from ddtrace.appsec import _asm_request_context as arc
    from ddtrace.internal import core

    class _FakeSubctx:
        pass

    class _FakeWaf:
        def __init__(self):
            self.created = 0

        def new_subcontext(self, ctx):
            self.created += 1
            return _FakeSubctx()

    waf = _FakeWaf()
    main_ctx = object()
    with core.context_with_data("outer_request"):
        arc.open_rasp_subcontext_scope()
        s_req = arc.get_or_create_rasp_subcontext(waf, main_ctx, "ssrf_req")
        # Inner client opens a nested scope: it must detect the parent holder and not create one.
        with core.context_with_data("inner_send"):
            arc.open_rasp_subcontext_scope()  # reentrant: reuses the outer holder
            s_inner = arc.get_or_create_rasp_subcontext(waf, main_ctx, "ssrf_req")
            assert s_inner is s_req
        # Final response on the outer scope still shares the same subcontext.
        s_res = arc.get_or_create_rasp_subcontext(waf, main_ctx, "ssrf_res")
        assert s_res is s_req
    assert waf.created == 1


def test_rasp_subcontext_distinct_per_concurrent_outgoing_request():
    """Concurrent outgoing requests each run in their own per-request core context, so they must
    get DISTINCT subcontexts (the holder is rooted per request, never shared via a common parent).
    """
    from ddtrace.appsec import _asm_request_context as arc
    from ddtrace.internal import core

    class _FakeSubctx:
        pass

    class _FakeWaf:
        def __init__(self):
            self.created = 0

        def new_subcontext(self, ctx):
            self.created += 1
            return _FakeSubctx()

    waf = _FakeWaf()
    main_ctx = object()
    with core.context_with_data("inbound_request"):  # shared parent (the inbound request)
        with core.context_with_data("outgoing_a"):
            arc.open_rasp_subcontext_scope()
            a = arc.get_or_create_rasp_subcontext(waf, main_ctx, "ssrf_req")
        with core.context_with_data("outgoing_b"):  # sibling, not nested under A
            arc.open_rasp_subcontext_scope()
            b = arc.get_or_create_rasp_subcontext(waf, main_ctx, "ssrf_req")
        assert a is not b
    assert waf.created == 2


def test_rasp_subcontext_fresh_per_non_ssrf_call():
    """LFI/CMDI/SHI/SQLI must get a fresh subcontext on every call (one per guarded operation)."""
    from ddtrace.appsec import _asm_request_context as arc
    from ddtrace.internal import core

    class _FakeSubctx:
        pass

    class _FakeWaf:
        def __init__(self):
            self.created = 0

        def new_subcontext(self, ctx):
            self.created += 1
            return _FakeSubctx()

    waf = _FakeWaf()
    main_ctx = object()
    with core.context_with_data("request"):
        arc.open_rasp_subcontext_scope()
        a = arc.get_or_create_rasp_subcontext(waf, main_ctx, "lfi")
        b = arc.get_or_create_rasp_subcontext(waf, main_ctx, "lfi")
        assert a is not b
    assert waf.created == 2


@mock.patch("ddtrace.appsec._ddwaf.waf.DDWaf.run")
def test_rasp_bypassed_when_subcontext_unavailable(mock_run):
    """If a RASP subcontext can't be created, the WAF call is bypassed entirely (not run on the
    main context, which would persist the non-persisting RASP data).
    """
    from ddtrace.appsec._constants import EXPLOIT_PREVENTION
    from ddtrace.trace import tracer

    with asm_context(tracer=tracer, config=config_asm) as span:
        processor = AppSecSpanProcessor._instance
        assert processor
        with mock.patch.object(_asm_request_context, "get_or_create_rasp_subcontext", return_value=None):
            res = processor._waf_action(
                span,
                None,
                {EXPLOIT_PREVENTION.ADDRESS.LFI: "/etc/passwd"},
                rule_type=EXPLOIT_PREVENTION.TYPE.LFI,
            )
        assert res is None
        mock_run.assert_not_called()


def test_waf_update_raises_when_waf_rejects_update():
    """A WAF that rejects the update makes waf_update raise so RC reports an error."""
    instance = mock.MagicMock()
    instance._update_rules.return_value = False
    with mock.patch.object(AppSecSpanProcessor, "_instance", instance):
        with mock.patch.object(_processor_asm_config, "_asm_static_rule_file", None):
            with pytest.raises(WAFUpdateError):
                waf_update([], [("ASM_DATA", "path", {})])
    instance._update_rules.assert_called_once()


def test_waf_update_does_not_raise_with_static_rule_file():
    """A static rule file makes RC WAF updates an intentional no-op, not a failure."""
    instance = mock.MagicMock()
    instance._update_rules.return_value = False
    with mock.patch.object(AppSecSpanProcessor, "_instance", instance):
        with mock.patch.object(_processor_asm_config, "_asm_static_rule_file", "some_rules.json"):
            waf_update([], [("ASM_DATA", "path", {})])  # must not raise


def test_waf_update_does_not_raise_on_success():
    instance = mock.MagicMock()
    instance._update_rules.return_value = True
    with mock.patch.object(AppSecSpanProcessor, "_instance", instance):
        with mock.patch.object(_processor_asm_config, "_asm_static_rule_file", None):
            waf_update([], [("ASM_DATA", "path", {})])  # must not raise
