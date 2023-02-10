import json
import os

import mock
import pytest

from ddtrace.appsec._remoteconfiguration import _appsec_rules_data
from ddtrace.appsec._remoteconfiguration import appsec_rc_reload_features
from ddtrace.appsec.utils import _appsec_rc_capabilities
from ddtrace.appsec.utils import _appsec_rc_features_is_enabled
from ddtrace.constants import APPSEC_ENV
from ddtrace.constants import APPSEC_JSON
from ddtrace.contrib.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from ddtrace.internal.remoteconfig import RemoteConfig
from tests.utils import override_env


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
    ],
)
def test_rc_activation_states_on(tracer, appsec_enabled, rc_value, remote_config_worker):
    tracer.configure(appsec_enabled=False)
    with override_env({APPSEC_ENV: appsec_enabled}):
        if appsec_enabled == "":
            del os.environ[APPSEC_ENV]

        appsec_rc_reload_features(tracer)(None, {"asm": {"enabled": rc_value}})
        result = _set_and_get_appsec_tags(tracer)
        assert "triggers" in result


@pytest.mark.parametrize(
    "appsec_enabled, rc_value",
    [
        ("", False),
        ("false", False),
        ("false", True),
        ("true", False),
    ],
)
def test_rc_activation_states_off(tracer, appsec_enabled, rc_value, remote_config_worker):
    tracer.configure(appsec_enabled=True)
    with override_env({APPSEC_ENV: appsec_enabled}):
        if appsec_enabled == "":
            del os.environ[APPSEC_ENV]

        rc_config = {"asm": {"enabled": True}}
        if rc_value is False:
            rc_config = False

        appsec_rc_reload_features(tracer)(None, rc_config)
        result = _set_and_get_appsec_tags(tracer)
        assert result is None


@pytest.mark.parametrize(
    "rc_enabled, capability",
    [
        ("true", "Bg=="),
        ("false", ""),
    ],
)
def test_rc_capabilities(rc_enabled, capability):
    with override_env({"DD_REMOTE_CONFIGURATION_ENABLED": rc_enabled}):
        assert _appsec_rc_capabilities() == capability


@mock.patch.object(RemoteConfig, "_check_remote_config_enable_in_agent")
def test_rc_activation_validate_products(mock_check_remote_config_enable_in_agent, tracer, remote_config_worker):
    mock_check_remote_config_enable_in_agent.return_value = True
    tracer.configure(appsec_enabled=False, api_version="v0.4")

    rc_config = {"asm": {"enabled": True}}

    assert not RemoteConfig._worker
    RemoteConfig.enable()
    appsec_rc_reload_features(tracer)(None, rc_config)

    assert RemoteConfig._worker._client._products["ASM_DATA"]


def test_rc_rules_data(tracer):
    tracer.configure(appsec_enabled=True)
    with override_env({APPSEC_ENV: "true"}):
        with open("ddtrace/appsec/rules.json", "r") as dd_rules:
            config = {
                "rules_data": [],
                "custom_rules": [],
                "rules": json.load(dd_rules),
            }
            _appsec_rules_data(tracer, config)
