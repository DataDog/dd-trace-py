import base64
import hashlib
import json
import os
import time

import mock
import pytest

from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._remoteconfiguration import _appsec_rules_data
from ddtrace.appsec._remoteconfiguration import appsec_rc_reload_features
from ddtrace.appsec._remoteconfiguration import enable_appsec_rc
from ddtrace.appsec.utils import _appsec_rc_capabilities
from ddtrace.appsec.utils import _appsec_rc_features_is_enabled
from ddtrace.constants import APPSEC_ENV
from ddtrace.constants import APPSEC_JSON
from ddtrace.contrib.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from ddtrace.internal import _context
from ddtrace.internal.remoteconfig import RemoteConfig
from ddtrace.internal.remoteconfig.client import AgentPayload
from ddtrace.internal.remoteconfig.client import ConfigMetadata
from ddtrace.internal.remoteconfig.client import TargetFile
from tests.appsec.test_processor import Config
from tests.utils import override_env
from tests.utils import override_global_config


def _stop_remote_config_worker():
    if RemoteConfig._worker:
        RemoteConfig._worker._stop_service()
        RemoteConfig._worker = None


@pytest.fixture
def remote_config_worker(tracer):
    _stop_remote_config_worker()
    try:
        yield
    finally:
        _stop_remote_config_worker()


def _set_and_get_appsec_tags(tracer):
    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(
            span,
            {},
            raw_uri="http://example.com/.git",
            status_code="404",
            request_cookies={"cookie1": "im the cookie1"},
        )
    return span.get_tag(APPSEC_JSON)


def test_rc_enabled_by_default(tracer):
    os.environ.pop("DD_REMOTE_CONFIGURATION_ENABLED", None)
    result = _set_and_get_appsec_tags(tracer)
    assert result is None
    assert _appsec_rc_features_is_enabled()


def test_rc_activate_is_active_and_get_processor_tags(tracer, remote_config_worker):
    result = _set_and_get_appsec_tags(tracer)
    assert result is None
    appsec_rc_reload_features(tracer)(None, {"asm": {"enabled": True}})
    assert "triggers" in _set_and_get_appsec_tags(tracer)


@pytest.mark.parametrize(
    "appsec_enabled, rc_value",
    [
        ("", True),
        ("true", True),
        ("true", False),
    ],
)
def test_rc_activation_states_on(tracer, appsec_enabled, rc_value, remote_config_worker):
    with override_global_config(dict(_appsec_enabled=False)), override_env({APPSEC_ENV: appsec_enabled}):
        if appsec_enabled == "":
            del os.environ[APPSEC_ENV]

        appsec_rc_reload_features(tracer)(None, {"asm": {"enabled": rc_value}})
        result = _set_and_get_appsec_tags(tracer)
        assert result
        assert "triggers" in result


@pytest.mark.parametrize(
    "appsec_enabled, rc_value",
    [
        ("", False),
        ("false", False),
        ("false", True),
    ],
)
def test_rc_activation_states_off(tracer, appsec_enabled, rc_value, remote_config_worker):
    with override_global_config(dict(_appsec_enabled=True)), override_env({APPSEC_ENV: appsec_enabled}):
        if appsec_enabled == "":
            del os.environ[APPSEC_ENV]

        rc_config = {"asm": {"enabled": True}}
        if rc_value is False:
            rc_config = False

        appsec_rc_reload_features(tracer)(None, rc_config)
        result = _set_and_get_appsec_tags(tracer)
        assert result is None


@pytest.mark.parametrize(
    "rc_enabled, appsec_enabled, capability",
    [
        ("true", "true", "HA=="),
        ("false", "true", "HA=="),
        ("true", "false", "HA=="),
        ("false", "false", ""),
        ("true", "", "Hg=="),
        ("false", "", ""),
    ],
)
def test_rc_capabilities(rc_enabled, appsec_enabled, capability):
    env = {"DD_REMOTE_CONFIGURATION_ENABLED": rc_enabled}
    if appsec_enabled:
        env[APPSEC_ENV] = appsec_enabled
    with override_env(env):
        assert _appsec_rc_capabilities() == capability


@mock.patch.object(RemoteConfig, "_check_remote_config_enable_in_agent")
def test_rc_activation_validate_products(mock_check_remote_config_enable_in_agent, tracer, remote_config_worker):
    mock_check_remote_config_enable_in_agent.return_value = True
    with override_global_config(dict(_appsec_enabled=False, api_version="v0.4")):
        rc_config = {"asm": {"enabled": True}}

        assert not RemoteConfig._worker

        appsec_rc_reload_features(tracer)(None, rc_config)

        assert RemoteConfig._worker._client._products["ASM_DATA"]


def test_load_new_configurations_remove_config_and_dispatch_applied_configs_error(remote_config_worker):
    """
    The previous code raises a key error in `self._products[config.product_name]` when appsec features is disabled
    with ASM_FEATURES product and then loops over the config in _load_new_configurations
    """
    enable_appsec_rc()
    asm_features_data = b'{"asm":{"enabled":true}}'
    asm_data_data = b'{"data":{}}'
    payload = AgentPayload(
        target_files=[
            TargetFile(path="mock/ASM_FEATURES", raw=base64.b64encode(asm_features_data)),
            TargetFile(path="mock/ASM_DATA", raw=base64.b64encode(asm_data_data)),
        ]
    )
    client_configs = {
        "mock/ASM_FEATURES": ConfigMetadata(
            id="",
            product_name="ASM_FEATURES",
            sha256_hash=hashlib.sha256(asm_features_data).hexdigest(),
            length=5,
            tuf_version=5,
        ),
        "mock/ASM_DATA": ConfigMetadata(
            id="",
            product_name="ASM_DATA",
            sha256_hash=hashlib.sha256(asm_data_data).hexdigest(),
            length=5,
            tuf_version=5,
        ),
    }

    RemoteConfig._worker._client._applied_configs = client_configs
    RemoteConfig._worker._client._remove_previously_applied_configurations({}, {}, [])

    RemoteConfig._worker._client._load_new_configurations({}, client_configs, payload=payload)


def test_rc_activation_ip_blocking_data(tracer, remote_config_worker):
    with override_env({APPSEC_ENV: "true"}):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        rc_config = {
            "rules_data": [
                {
                    "data": [{"expiration": 1755346879, "value": "user8"}],
                    "id": "blocked_users",
                    "type": "data_with_expiration",
                },
                {
                    "data": [
                        {"value": "8.8.4.4"},
                    ],
                    "id": "blocked_ips",
                    "type": "ip_with_expiration",
                },
            ]
        }

        assert not RemoteConfig._worker

        appsec_rc_reload_features(tracer)(None, rc_config)
        with _asm_request_context.asm_request_context_manager("8.8.4.4", {}):
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                set_http_meta(
                    span,
                    Config(),
                )
            assert "triggers" in json.loads(span.get_tag(APPSEC_JSON))
            assert _context.get_item("http.request.remote_ip", span) == "8.8.4.4"


def test_rc_activation_ip_blocking_data_expired(tracer, remote_config_worker):
    with override_env({APPSEC_ENV: "true"}):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        rc_config = {
            "rules_data": [
                {
                    "data": [
                        {"expiration": int(time.time()) - 10000, "value": "8.8.4.4"},
                    ],
                    "id": "blocked_ips",
                    "type": "ip_with_expiration",
                },
            ]
        }

        assert not RemoteConfig._worker

        appsec_rc_reload_features(tracer)(None, rc_config)

        with _asm_request_context.asm_request_context_manager("8.8.4.4", {}):
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                set_http_meta(
                    span,
                    Config(),
                )
            assert span.get_tag(APPSEC_JSON) is None


def test_rc_activation_ip_blocking_data_not_expired(tracer, remote_config_worker):
    with override_env({APPSEC_ENV: "true"}):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        rc_config = {
            "rules_data": [
                {
                    "data": [
                        {"expiration": int(time.time()), "value": "8.8.4.4"},
                    ],
                    "id": "blocked_ips",
                    "type": "ip_with_expiration",
                },
            ]
        }

        assert not RemoteConfig._worker

        appsec_rc_reload_features(tracer)(None, rc_config)

        with _asm_request_context.asm_request_context_manager("8.8.4.4", {}):
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                set_http_meta(
                    span,
                    Config(),
                )
            assert "triggers" in json.loads(span.get_tag(APPSEC_JSON))
            assert _context.get_item("http.request.remote_ip", span) == "8.8.4.4"


def test_rc_rules_data(tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env({APPSEC_ENV: "true"}), open(
        "ddtrace/appsec/rules.json", "r"
    ) as dd_rules:
        config = {
            "rules_data": [],
            "custom_rules": [],
            "rules": json.load(dd_rules),
        }
        _appsec_rules_data(tracer, config)
