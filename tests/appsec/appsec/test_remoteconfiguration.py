import base64
import hashlib
import json
import os
import time

import mock
from mock.mock import ANY
import pytest

from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._capabilities import _appsec_rc_capabilities
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._constants import PRODUCTS
from ddtrace.appsec._processor import AppSecSpanProcessor
from ddtrace.appsec._remoteconfiguration import _appsec_callback
from ddtrace.appsec._remoteconfiguration import _appsec_rules_data
from ddtrace.appsec._remoteconfiguration import _preprocess_results_appsec_1click_activation
from ddtrace.appsec._remoteconfiguration import disable_appsec_rc
from ddtrace.appsec._remoteconfiguration import enable_appsec_rc
from ddtrace.appsec._utils import get_triggers
from ddtrace.contrib.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.remoteconfig.client import AgentPayload
from ddtrace.internal.remoteconfig.client import ConfigMetadata
from ddtrace.internal.remoteconfig.client import TargetFile
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings.asm import config as asm_config
import tests.appsec.rules as rules
from tests.appsec.utils import Either
from tests.utils import override_env
from tests.utils import override_global_config


def _set_and_get_appsec_tags(tracer):
    with _asm_request_context.asm_request_context_manager(), tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(
            span,
            {},
            raw_uri="http://example.com/.git",
            status_code="404",
            request_cookies={"cookie1": "im the cookie1"},
        )
    return get_triggers(span)


@pytest.mark.xfail(
    reason="DD_REMOTE_CONFIGURATION_ENABLED is set to false for all riot venvs, "
    "this is not the default behavior for users"
)
def test_rc_enabled_by_default(tracer):
    # TODO: remove https://github.com/DataDog/dd-trace-py/blob/1.x/riotfile.py#L100 or refactor this test
    result = _set_and_get_appsec_tags(tracer)
    assert result is None
    assert asm_config._asm_can_be_enabled


def test_rc_activate_is_active_and_get_processor_tags(tracer, remote_config_worker):
    with override_global_config(dict(_remote_config_enabled=True)):
        rc_config = {"config": {"asm": {"enabled": False}}}
        _appsec_callback(rc_config, tracer)
        result = _set_and_get_appsec_tags(tracer)
        assert result is None
        rc_config = {"config": {"asm": {"enabled": True}}}
        _appsec_callback(rc_config, tracer)
        assert _set_and_get_appsec_tags(tracer)


@pytest.mark.parametrize(
    "appsec_enabled, rc_value",
    [
        ("", True),
        ("true", True),
        ("true", False),
    ],
)
def test_rc_activation_states_on(tracer, appsec_enabled, rc_value, remote_config_worker):
    with override_env({APPSEC.ENV: appsec_enabled} if appsec_enabled else {}), override_global_config(
        dict(_asm_enabled=asbool(appsec_enabled), _remote_config_enabled=True)
    ):
        if appsec_enabled:
            tracer.configure(appsec_enabled=asbool(appsec_enabled))

        rc_config = {"config": {"asm": {"enabled": rc_value}}}
        _appsec_callback(rc_config, tracer)
        result = _set_and_get_appsec_tags(tracer)
        assert result


@pytest.mark.parametrize(
    "appsec_enabled, rc_value",
    [
        ("", False),
        ("false", False),
        ("false", True),
    ],
)
def test_rc_activation_states_off(tracer, appsec_enabled, rc_value, remote_config_worker):
    with override_env({APPSEC.ENV: appsec_enabled}), override_global_config(dict(_asm_enabled=True)):
        if appsec_enabled == "":
            del os.environ[APPSEC.ENV]
        else:
            tracer.configure(appsec_enabled=asbool(appsec_enabled))

        rc_config = {"config": {"asm": {"enabled": True}}}
        if rc_value is False:
            rc_config = {}

        _appsec_callback(rc_config, tracer)
        result = _set_and_get_appsec_tags(tracer)
        assert result is None


@pytest.mark.parametrize(
    "rc_enabled, appsec_enabled, capability",
    [
        (True, "true", "DYHkA/w="),  # All capabilities except ASM_ACTIVATION
        (False, "true", ""),
        (True, "false", "gAAAAA=="),
        (False, "false", ""),
        (True, "", "gAAAAg=="),  # ASM_ACTIVATION
        (False, "", ""),
    ],
)
def test_rc_capabilities(rc_enabled, appsec_enabled, capability, tracer):
    env = {}
    config = {}
    tracer.configure(appsec_enabled=False, api_version="v0.4")
    if appsec_enabled:
        env[APPSEC.ENV] = appsec_enabled
        config["appsec_enabled"] = asbool(appsec_enabled)
        config["api_version"] = "v0.4"
    with override_env(env):
        with override_global_config(dict(_remote_config_enabled=rc_enabled)):
            tracer.configure(**config)
            assert _appsec_rc_capabilities(test_tracer=tracer) == capability


@pytest.mark.parametrize(
    "env_rules, expected",
    [
        ({}, "DYHkA/4="),  # All capabilities
        ({"_asm_static_rule_file": DEFAULT.RULES}, "gAAAAg=="),  # Only ASM_FEATURES
    ],
)
def test_rc_activation_capabilities(tracer, remote_config_worker, env_rules, expected):
    global_config = dict(_asm_enabled=False, _remote_config_enabled=True)
    global_config.update(env_rules)
    with override_global_config(global_config):
        rc_config = {"config": {"asm": {"enabled": True}}}
        # flaky test
        # assert not remoteconfig_poller._worker

        _appsec_callback(rc_config, test_tracer=tracer)

        assert _appsec_rc_capabilities(test_tracer=tracer) == expected


def test_rc_activation_validate_products(tracer, remote_config_worker):
    with override_global_config(dict(_asm_enabled=False, _remote_config_enabled=True, api_version="v0.4")):
        assert not remoteconfig_poller._worker

        enable_appsec_rc()

        assert remoteconfig_poller._client._products["ASM_FEATURES"]
    disable_appsec_rc()


@pytest.mark.parametrize(
    "env_rules, expected",
    [
        ({}, True),  # All capabilities
        ({"_asm_static_rule_file": DEFAULT.RULES}, False),  # Only ASM_FEATURES
    ],
)
@pytest.mark.parametrize(
    "enable_config, disable_config",
    [
        ({"asm": {"enabled": True}}, {"asm": {}}),
        (
            {"asm": {"enabled": True}, "data": [{"id": 1}]},
            {"asm": {}, "data": [{"id": 1}]},
        ),  # additional data in the same product should not change the result
        (
            {"asm": {"enabled": True, "data": 0}},
            {"asm": {"data": 0}},
        ),  # additional data in the same config should not change the result
    ],
)
def test_rc_activation_check_asm_features_product_disables_rest_of_products(
    tracer, remote_config_worker, env_rules, expected, enable_config, disable_config
):
    global_config = dict(_remote_config_enabled=True, _asm_enabled=True)
    global_config.update(env_rules)
    from ddtrace.internal.remoteconfig.client import config as rc_config

    rc_config.skip_shutdown = False
    empty_config = {}
    with override_global_config(global_config):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        enable_appsec_rc(tracer)

        assert bool(remoteconfig_poller._client._products.get(PRODUCTS.ASM_DATA)) is expected
        assert bool(remoteconfig_poller._client._products.get(PRODUCTS.ASM)) is expected
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM_FEATURES)

        # sending nothing should not change anything (configuration is the same)
        _preprocess_results_appsec_1click_activation(empty_config)

        assert bool(remoteconfig_poller._client._products.get(PRODUCTS.ASM_DATA)) is expected
        assert bool(remoteconfig_poller._client._products.get(PRODUCTS.ASM)) is expected
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM_FEATURES)

        # sending empty config for asm should disable asm (meaning asm was deleted)
        _preprocess_results_appsec_1click_activation(disable_config)

        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM_DATA) is None
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM) is None
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM_FEATURES)

        # sending nothing should not change anything (configuration is the same)
        _preprocess_results_appsec_1click_activation(empty_config)
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM_DATA) is None
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM) is None
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM_FEATURES)

        # sending config should enable asm again
        _preprocess_results_appsec_1click_activation(enable_config)
        assert bool(remoteconfig_poller._client._products.get(PRODUCTS.ASM_DATA)) is expected
        assert bool(remoteconfig_poller._client._products.get(PRODUCTS.ASM)) is expected
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM_FEATURES)

    disable_appsec_rc()


@pytest.mark.parametrize("auto_user", [True, False])
def test_rc_activation_with_auto_user_appsec_fixed(tracer, remote_config_worker, auto_user):
    with override_env({APPSEC.ENV: "true"}), override_global_config(
        dict(
            _remote_config_enabled=True,
            _asm_enabled=True,
            _auto_user_instrumentation_enabled=auto_user,
            api_version="v0.4",
        )
    ):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        enable_appsec_rc(tracer)

        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM_DATA)
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM)
        assert bool(remoteconfig_poller._client._products.get(PRODUCTS.ASM_FEATURES)) == auto_user

    disable_appsec_rc()


@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_1click_activation")
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_rules_data")
def test_load_new_configurations_dispatch_applied_configs(
    mock_appsec_rules_data, mock_appsec_1click_activation, remote_config_worker, tracer
):
    with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        enable_appsec_rc(tracer)
        asm_features_data = b'{"asm":{"enabled":true}}'
        asm_data_data = b'{"data": [{"test": "data"}]}'
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
        list_callbacks = []
        remoteconfig_poller._client._load_new_configurations(list_callbacks, {}, client_configs, payload=payload)
        remoteconfig_poller._client._publish_configuration(list_callbacks)
        remoteconfig_poller._poll_data()
        mock_appsec_rules_data.assert_called_with({"asm": {"enabled": True}, "data": [{"test": "data"}]}, None)

        mock_appsec_1click_activation.assert_called_with({"asm": {"enabled": True}, "data": [{"test": "data"}]}, None)
    disable_appsec_rc()


@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_1click_activation")
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_rules_data")
def test_load_new_configurations_empty_config(
    mock_appsec_rules_data, mock_appsec_1click_activation, remote_config_worker, tracer
):
    with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        enable_appsec_rc(tracer)
        asm_features_data = b'{"asm":{"enabled":true}}'
        asm_data_data = b'{"data": []}'
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
        list_callbacks = []
        remoteconfig_poller._client._load_new_configurations(list_callbacks, {}, client_configs, payload=payload)
        remoteconfig_poller._client._publish_configuration(list_callbacks)

        remoteconfig_poller._poll_data()
        mock_appsec_rules_data.assert_called_with({"asm": {"enabled": True}, "data": []}, None)

        mock_appsec_1click_activation.assert_called_with({"asm": {"enabled": True}, "data": []}, None)
    disable_appsec_rc()


@mock.patch("ddtrace.appsec._remoteconfiguration._preprocess_results_appsec_1click_activation")
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_1click_activation")
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_rules_data")
def test_load_new_configurations_remove_config_and_dispatch_applied_configs(
    mock_appsec_rules_data,
    mock_appsec_1click_activation,
    mock_preprocess_results_appsec_1click_activation,
    remote_config_worker,
):
    with override_global_config(dict(_remote_config_enabled=True)):
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
    mock_preprocess_results_appsec_1click_activation.return_value = {"asm": {"enabled": False}}

    remoteconfig_poller._client._applied_configs = client_configs
    list_callbacks = []
    remoteconfig_poller._client._remove_previously_applied_configurations(list_callbacks, {}, {}, {})
    remoteconfig_poller._client._publish_configuration(list_callbacks)

    remoteconfig_poller._poll_data()

    mock_preprocess_results_appsec_1click_activation.assert_called_with({}, ANY)
    mock_appsec_1click_activation.assert_called_with({"asm": {"enabled": False}}, None)
    mock_appsec_rules_data.assert_called_with({"asm": {"enabled": False}}, None)

    mock_appsec_1click_activation.reset_mock()
    mock_appsec_rules_data.reset_mock()

    # Not called because with this configuration the above condition is True:
    # applied_config = self._applied_configs.get(target)
    # if applied_config == config:
    #     continue
    remoteconfig_poller._client._load_new_configurations(list_callbacks, {}, client_configs, payload=payload)
    remoteconfig_poller._client._publish_configuration(list_callbacks)
    remoteconfig_poller._poll_data()
    mock_appsec_rules_data.assert_not_called()  # ({"asm": {"enabled": False}}, None)
    mock_appsec_1click_activation.assert_not_called()  # ({"asm": {"enabled": False}}, None)
    disable_appsec_rc()


def test_load_new_configurations_remove_config_and_dispatch_applied_configs_error(remote_config_worker):
    """
    The previous code raises a key error in `self._products[config.product_name]` when appsec features is disabled
    with ASM_FEATURES product and then loops over the config in _load_new_configurations
    """
    with override_global_config(dict(_remote_config_enabled=True)):
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
    list_callbacks = []
    remoteconfig_poller._client._applied_configs = client_configs
    remoteconfig_poller._client._remove_previously_applied_configurations(list_callbacks, {}, {}, {})
    remoteconfig_poller._client._publish_configuration(list_callbacks)

    remoteconfig_poller._client._load_new_configurations(list_callbacks, {}, client_configs, payload=payload)
    remoteconfig_poller._client._publish_configuration(list_callbacks)
    disable_appsec_rc()


@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_1click_activation")
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_rules_data")
def test_load_multiple_targets_file_same_product(
    mock_appsec_rules_data, mock_appsec_1click_activation, remote_config_worker, tracer
):
    with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        enable_appsec_rc(tracer)
        asm_features_data = b'{"asm":{"enabled":true}}'
        asm_data_data1 = b'{"data": [{"a":1}]}'
        asm_data_data2 = b'{"data": [{"b":2}]}'
        payload = AgentPayload(
            target_files=[
                TargetFile(path="mock/ASM_FEATURES", raw=base64.b64encode(asm_features_data)),
                TargetFile(path="mock/ASM_DATA/1", raw=base64.b64encode(asm_data_data1)),
                TargetFile(path="mock/ASM_DATA/2", raw=base64.b64encode(asm_data_data2)),
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
            "mock/ASM_DATA/1": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data1).hexdigest(),
                length=5,
                tuf_version=5,
            ),
            "mock/ASM_DATA/2": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data2).hexdigest(),
                length=5,
                tuf_version=5,
            ),
        }
        list_callbacks = []
        remoteconfig_poller._client._load_new_configurations(list_callbacks, {}, client_configs, payload=payload)
        remoteconfig_poller._client._publish_configuration(list_callbacks)
        remoteconfig_poller._poll_data()
        mock_appsec_rules_data.assert_called_with({"asm": {"enabled": True}, "data": [{"a": 1}, {"b": 2}]}, None)

        mock_appsec_1click_activation.assert_called_with({"asm": {"enabled": True}, "data": [{"a": 1}, {"b": 2}]}, None)
    disable_appsec_rc()


@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_1click_activation")
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_rules_data")
def test_load_new_config_and_remove_targets_file_same_product(
    mock_appsec_rules_data, mock_appsec_1click_activation, remote_config_worker, tracer
):
    with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        applied_configs = {}
        enable_appsec_rc(tracer)
        asm_features_data = b'{"asm":{"enabled":true}}'
        asm_data_data1 = b'{"data": [{"c":1}]}'
        asm_data_data2 = b'{"data": [{"d":2}]}'
        payload = AgentPayload(
            target_files=[
                TargetFile(path="mock/ASM_FEATURES", raw=base64.b64encode(asm_features_data)),
                TargetFile(path="mock/ASM_DATA/1", raw=base64.b64encode(asm_data_data1)),
                TargetFile(path="mock/ASM_DATA/2", raw=base64.b64encode(asm_data_data2)),
            ]
        )
        first_config = {
            "mock/ASM_FEATURES": ConfigMetadata(
                id="",
                product_name="ASM_FEATURES",
                sha256_hash=hashlib.sha256(asm_features_data).hexdigest(),
                length=5,
                tuf_version=5,
            ),
            "mock/ASM_DATA/1": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data1).hexdigest(),
                length=5,
                tuf_version=5,
            ),
            "mock/ASM_DATA/2": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data2).hexdigest(),
                length=5,
                tuf_version=5,
            ),
        }

        second_config = {
            "mock/ASM_FEATURES": ConfigMetadata(
                id="",
                product_name="ASM_FEATURES",
                sha256_hash=hashlib.sha256(asm_features_data).hexdigest(),
                length=5,
                tuf_version=5,
            ),
            "mock/ASM_DATA/1": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data1).hexdigest(),
                length=5,
                tuf_version=5,
            ),
        }

        list_callbacks = []
        remoteconfig_poller._client._remove_previously_applied_configurations(
            list_callbacks, {}, first_config, first_config
        )
        remoteconfig_poller._client._load_new_configurations(
            list_callbacks, applied_configs, first_config, payload=payload
        )
        remoteconfig_poller._client._publish_configuration(list_callbacks)
        remoteconfig_poller._client._applied_configs = applied_configs
        remoteconfig_poller._poll_data()

        mock_appsec_rules_data.assert_called_with({"asm": {"enabled": True}, "data": [{"c": 1}, {"d": 2}]}, None)
        mock_appsec_rules_data.reset_mock()

        list_callbacks = []
        remoteconfig_poller._client._remove_previously_applied_configurations(
            list_callbacks, {}, second_config, second_config
        )
        remoteconfig_poller._client._load_new_configurations(list_callbacks, {}, second_config, payload=payload)
        remoteconfig_poller._client._publish_configuration(list_callbacks)
        remoteconfig_poller._poll_data()

        mock_appsec_rules_data.assert_called_with({"asm": {"enabled": True}, "data": [{"c": 1}]}, None)
        disable_appsec_rc()


@mock.patch.object(AppSecSpanProcessor, "_update_rules")
def test_fullpath_appsec_rules_data(mock_update_rules, remote_config_worker, tracer):
    with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        applied_configs = {}
        enable_appsec_rc(tracer)
        asm_features_data = b'{"asm":{"enabled":true}}'
        asm_data_data1 = b'{"exclusions": [{"e":1}]}'
        asm_data_data2 = b'{"exclusions": [{"f":2}]}'
        payload = AgentPayload(
            target_files=[
                TargetFile(path="mock/ASM_FEATURES", raw=base64.b64encode(asm_features_data)),
                TargetFile(path="mock/ASM_DATA/1", raw=base64.b64encode(asm_data_data1)),
                TargetFile(path="mock/ASM_DATA/2", raw=base64.b64encode(asm_data_data2)),
            ]
        )
        first_config = {
            "mock/ASM_FEATURES": ConfigMetadata(
                id="",
                product_name="ASM_FEATURES",
                sha256_hash=hashlib.sha256(asm_features_data).hexdigest(),
                length=5,
                tuf_version=5,
            ),
            "mock/ASM_DATA/1": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data1).hexdigest(),
                length=5,
                tuf_version=5,
            ),
            "mock/ASM_DATA/2": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data2).hexdigest(),
                length=5,
                tuf_version=5,
            ),
        }

        second_config = {
            "mock/ASM_FEATURES": ConfigMetadata(
                id="",
                product_name="ASM_FEATURES",
                sha256_hash=hashlib.sha256(asm_features_data).hexdigest(),
                length=5,
                tuf_version=5,
            ),
            "mock/ASM_DATA/1": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data1).hexdigest(),
                length=5,
                tuf_version=5,
            ),
        }

        list_callbacks = []
        remoteconfig_poller._client._remove_previously_applied_configurations(
            list_callbacks, {}, first_config, first_config
        )
        remoteconfig_poller._client._load_new_configurations(
            list_callbacks, applied_configs, first_config, payload=payload
        )
        remoteconfig_poller._client._applied_configs = applied_configs
        remoteconfig_poller._client._publish_configuration(list_callbacks)
        remoteconfig_poller._poll_data()

        mock_update_rules.assert_called_with({"exclusions": [{"e": 1}, {"f": 2}]})
        mock_update_rules.reset_mock()

        # ensure enable_appsec_rc is reentrant
        enable_appsec_rc(tracer)
        list_callbacks = []
        remoteconfig_poller._client._remove_previously_applied_configurations(
            list_callbacks, {}, second_config, second_config
        )
        remoteconfig_poller._client._load_new_configurations(list_callbacks, {}, second_config, payload=payload)
        remoteconfig_poller._client._publish_configuration(list_callbacks)
        remoteconfig_poller._poll_data()

        mock_update_rules.assert_called_with({"exclusions": [{"e": 1}]})
    disable_appsec_rc()


@mock.patch.object(AppSecSpanProcessor, "_update_rules")
def test_fullpath_appsec_rules_data_empty_data(mock_update_rules, remote_config_worker, tracer):
    with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        applied_configs = {}
        enable_appsec_rc(tracer)
        asm_data_data1 = b'{"asm":{"enabled":true}, "exclusions": [{"t":1}]}'
        asm_data_data2 = b'{"asm":{"enabled":true}, "exclusions": []}'
        payload = AgentPayload(
            target_files=[
                TargetFile(path="mock/ASM_DATA/1", raw=base64.b64encode(asm_data_data1)),
                TargetFile(path="mock/ASM_DATA/2", raw=base64.b64encode(asm_data_data2)),
            ]
        )
        first_config = {
            "mock/ASM_DATA/1": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data1).hexdigest(),
                length=5,
                tuf_version=5,
            ),
            "mock/ASM_DATA/2": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data2).hexdigest(),
                length=5,
                tuf_version=5,
            ),
        }

        second_config = {
            "mock/ASM_DATA/2": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data2).hexdigest(),
                length=5,
                tuf_version=5,
            )
        }
        list_callbacks = []
        remoteconfig_poller._client._remove_previously_applied_configurations(
            list_callbacks, {}, first_config, first_config
        )
        remoteconfig_poller._client._load_new_configurations(
            list_callbacks, applied_configs, first_config, payload=payload
        )
        remoteconfig_poller._client._applied_configs = applied_configs
        remoteconfig_poller._client._publish_configuration(list_callbacks)
        remoteconfig_poller._poll_data(tracer)

        mock_update_rules.assert_called_with({"exclusions": [{"t": 1}]})
        mock_update_rules.reset_mock()

        list_callbacks = []
        remoteconfig_poller._client._remove_previously_applied_configurations(
            list_callbacks, {}, second_config, second_config
        )
        remoteconfig_poller._client._load_new_configurations(list_callbacks, {}, second_config, payload=payload)
        remoteconfig_poller._client._publish_configuration(list_callbacks)
        remoteconfig_poller._poll_data(tracer)

        mock_update_rules.assert_called_with({"exclusions": []})
    disable_appsec_rc()


@mock.patch.object(AppSecSpanProcessor, "_update_rules")
def test_fullpath_appsec_rules_data_add_delete_file(mock_update_rules, remote_config_worker, tracer):
    with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True)):
        tracer.configure(appsec_enabled=True)
        applied_configs = {}
        enable_appsec_rc(tracer)
        asm_data_data1 = b'{"asm":{"enabled":true}, "exclusions": [{"b":1}]}'
        asm_data_data2 = b'{"asm":{"enabled":true}, "exclusions": []}'
        payload = AgentPayload(
            target_files=[
                TargetFile(path="mock/ASM_DATA/1", raw=base64.b64encode(asm_data_data1)),
            ]
        )
        first_config = {
            "mock/ASM_DATA/1": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data1).hexdigest(),
                length=5,
                tuf_version=5,
            ),
        }

        second_payload = AgentPayload(
            target_files=[
                TargetFile(path="mock/ASM_DATA/1", raw=base64.b64encode(asm_data_data2)),
            ]
        )
        second_config = {
            "mock/ASM_DATA/1": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data2).hexdigest(),
                length=5,
                tuf_version=5,
            ),
        }

        target_file = {
            "mock/ASM_DATA/1": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data1).hexdigest(),
                length=5,
                tuf_version=5,
            )
        }
        list_callbacks = []
        remoteconfig_poller._client._remove_previously_applied_configurations(list_callbacks, {}, first_config, {})
        remoteconfig_poller._client._load_new_configurations(
            list_callbacks, applied_configs, first_config, payload=payload
        )
        remoteconfig_poller._client._applied_configs = applied_configs
        remoteconfig_poller._client._publish_configuration(list_callbacks)

        remoteconfig_poller._poll_data(tracer)
        mock_update_rules.assert_called_with({"exclusions": [{"b": 1}]})
        mock_update_rules.reset_mock()

        list_callbacks = []
        remoteconfig_poller._client._remove_previously_applied_configurations(
            list_callbacks, {}, second_config, target_file
        )
        remoteconfig_poller._client._load_new_configurations(list_callbacks, {}, second_config, payload=second_payload)
        remoteconfig_poller._client._publish_configuration(list_callbacks)
        remoteconfig_poller._poll_data(tracer)

        mock_update_rules.assert_called_with({"exclusions": []})
    disable_appsec_rc()


@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_1click_activation")
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_rules_data")
def test_load_new_empty_config_and_remove_targets_file_same_product(
    mock_appsec_rules_data, remote_config_worker, tracer
):
    with override_global_config(dict(_asm_enabled=True, api_version="v0.4", _remote_config_enabled=True)):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        applied_configs = {}
        enable_appsec_rc(tracer)
        asm_features_data = b'{"asm":{"enabled":true}}'
        asm_data_data1 = b'{"data": [{"x":1}]}'
        asm_data_data2 = b'{"data2": [{"y":2}]}'
        asm_data_data_empty = b'{"data2": []}'
        payload = AgentPayload(
            target_files=[
                TargetFile(path="mock/ASM_FEATURES", raw=base64.b64encode(asm_features_data)),
                TargetFile(path="mock/ASM_DATA/1", raw=base64.b64encode(asm_data_data1)),
                TargetFile(path="mock/ASM_DATA/2", raw=base64.b64encode(asm_data_data2)),
            ]
        )
        first_config = {
            "mock/ASM_FEATURES": ConfigMetadata(
                id="",
                product_name="ASM_FEATURES",
                sha256_hash=hashlib.sha256(asm_features_data).hexdigest(),
                length=5,
                tuf_version=5,
            ),
            "mock/ASM_DATA/1": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data1).hexdigest(),
                length=5,
                tuf_version=5,
            ),
            "mock/ASM_DATA/2": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data2).hexdigest(),
                length=5,
                tuf_version=5,
            ),
        }

        second_config = {
            "mock/ASM_FEATURES": ConfigMetadata(
                id="",
                product_name="ASM_FEATURES",
                sha256_hash=hashlib.sha256(asm_features_data).hexdigest(),
                length=5,
                tuf_version=5,
            ),
            "mock/ASM_DATA/2": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data_empty).hexdigest(),
                length=5,
                tuf_version=5,
            ),
        }

        list_callbacks = []
        remoteconfig_poller._client._remove_previously_applied_configurations(list_callbacks, {}, first_config, {})
        remoteconfig_poller._client._load_new_configurations(
            list_callbacks, applied_configs, first_config, payload=payload
        )
        remoteconfig_poller._client._publish_configuration(list_callbacks)
        remoteconfig_poller._client._applied_configs = {}
        remoteconfig_poller._poll_data()

        mock_appsec_rules_data.assert_called_with(
            {"asm": {"enabled": Either(True, None)}, "data": [{"x": 1}], "data2": [{"y": 2}]}, None
        )
        mock_appsec_rules_data.reset_mock()

        payload = AgentPayload(
            target_files=[
                TargetFile(path="mock/ASM_FEATURES", raw=base64.b64encode(asm_features_data)),
                TargetFile(path="mock/ASM_DATA/1", raw=base64.b64encode(asm_data_data1)),
                TargetFile(path="mock/ASM_DATA/2", raw=base64.b64encode(asm_data_data_empty)),
            ]
        )

        list_callbacks = []
        remoteconfig_poller._client._load_new_configurations(list_callbacks, {}, second_config, payload=payload)
        remoteconfig_poller._client._publish_configuration(list_callbacks)
        remoteconfig_poller._poll_data()

        mock_appsec_rules_data.assert_called_with(
            {"asm": {"enabled": Either(True, None)}, "data": [{"x": 1}], "data2": []}, None
        )
    disable_appsec_rc()


def test_rc_activation_ip_blocking_data(tracer, remote_config_worker):
    with override_env({APPSEC.ENV: "true"}), override_global_config({}):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        rc_config = {
            "config": {
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
        }
        assert remoteconfig_poller.status == ServiceStatus.STOPPED

        _appsec_callback(rc_config, tracer)
        with _asm_request_context.asm_request_context_manager("8.8.4.4", {}):
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                set_http_meta(
                    span,
                    rules.Config(),
                )
            assert get_triggers(span)
            assert core.get_item("http.request.remote_ip", span) == "8.8.4.4"


def test_rc_activation_ip_blocking_data_expired(tracer, remote_config_worker):
    with override_env({APPSEC.ENV: "true"}), override_global_config({}):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        rc_config = {
            "config": {
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
        }

        assert remoteconfig_poller.status == ServiceStatus.STOPPED

        _appsec_callback(rc_config, tracer)

        with _asm_request_context.asm_request_context_manager("8.8.4.4", {}):
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                set_http_meta(
                    span,
                    rules.Config(),
                )
            assert get_triggers(span) is None


def test_rc_activation_ip_blocking_data_not_expired(tracer, remote_config_worker):
    with override_env({APPSEC.ENV: "true"}), override_global_config({}):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        rc_config = {
            "config": {
                "rules_data": [
                    {
                        "data": [
                            {"expiration": int(time.time()) + 10000, "value": "8.8.4.4"},
                        ],
                        "id": "blocked_ips",
                        "type": "ip_with_expiration",
                    },
                ]
            }
        }

        assert remoteconfig_poller.status == ServiceStatus.STOPPED

        _appsec_callback(rc_config, tracer)

        with _asm_request_context.asm_request_context_manager("8.8.4.4", {}):
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                set_http_meta(
                    span,
                    rules.Config(),
                )
            assert get_triggers(span)
            assert core.get_item("http.request.remote_ip", span) == "8.8.4.4"


def test_rc_rules_data(tracer):
    RULES_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(rules.ROOT_DIR))), "ddtrace/appsec/rules.json"
    )
    with override_env({APPSEC.ENV: "true"}), override_global_config(dict(_asm_enabled=True)), open(
        RULES_PATH, "r"
    ) as dd_rules:
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        config = {
            "rules_data": [],
            "custom_rules": [],
            "actions": [],
            "rules": json.load(dd_rules)["rules"],
            "rules_override": [],
            "scanners": [],
            "processors": [],
            "ignore": [],
        }
        with mock.patch("ddtrace.appsec._processor.AppSecSpanProcessor._update_rules", autospec=True) as mock_update:
            mock_update.reset_mock()
            _appsec_rules_data(config, tracer)
            calls = mock_update.mock_calls
            for v in config:
                if v == "ignore":
                    assert v not in calls[-1][1][1]
                else:
                    assert v in calls[-1][1][1]


def test_rc_rules_data_error_empty(tracer):
    with override_env({APPSEC.ENV: "true"}), override_global_config(dict(_asm_enabled=True)):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        config = {}
        assert not _appsec_rules_data(config, tracer)


def test_rc_rules_data_error_ddwaf(tracer):
    with override_env({APPSEC.ENV: "true"}), override_global_config(dict(_asm_enabled=True)):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        config = {
            "rules": [{"invalid": mock.MagicMock()}],
        }
        assert not _appsec_rules_data(config, tracer)


def test_rules_never_empty(tracer):
    with override_global_config(dict(_asm_enabled=True)):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        with mock.patch("ddtrace.appsec._processor.AppSecSpanProcessor._update_rules", autospec=True) as mock_update:
            mock_update.reset_mock()
            _appsec_rules_data({"rules": []}, tracer)
            call = mock_update.mock_calls
            args = call[-1][1][1]
            assert "rules" in args
            assert args["rules"], "empty rules should not be possible, it must switch to default."


def test_static_rules_never_modified(tracer):
    with override_global_config(dict(_asm_enabled=True)):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        processors = str(tracer._appsec_processor._rules["processors"])
        scanners = str(tracer._appsec_processor._rules["scanners"])
        proc_add = {"id": "new_processor"}
        scan_add = {"id": "new_scanner"}
        with mock.patch("ddtrace.appsec._processor.AppSecSpanProcessor._update_rules", autospec=True) as mock_update:
            mock_update.reset_mock()
            _appsec_rules_data({"rules": [], "processors": [proc_add], "scanners": [scan_add]}, tracer)
            call = mock_update.mock_calls
            args = call[-1][1][1]
            # check the new rules have been sent and merged
            assert "processors" in args
            assert proc_add in args["processors"]
            assert len(args["processors"]) > 1
            assert "scanners" in args
            assert scan_add in args["scanners"]
            assert len(args["scanners"]) > 1
            # check that the original rules are still there unmodified
            assert processors == str(tracer._appsec_processor._rules["processors"])
            assert scanners == str(tracer._appsec_processor._rules["scanners"])
