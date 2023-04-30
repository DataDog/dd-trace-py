import base64
import hashlib
import json
import os
import sys
import time

import mock
from mock.mock import ANY
import pytest

from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import PRODUCTS
from ddtrace.appsec._remoteconfiguration import RCAppSecCallBack
from ddtrace.appsec._remoteconfiguration import RCAppSecFeaturesCallBack
from ddtrace.appsec._remoteconfiguration import _appsec_rules_data
from ddtrace.appsec._remoteconfiguration import enable_appsec_rc
from ddtrace.appsec.processor import AppSecSpanProcessor
from ddtrace.appsec.utils import _appsec_rc_capabilities
from ddtrace.appsec.utils import _appsec_rc_features_is_enabled
from ddtrace.contrib.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from ddtrace.internal import _context
from ddtrace.internal.remoteconfig import RemoteConfig
from ddtrace.internal.remoteconfig.client import AgentPayload
from ddtrace.internal.remoteconfig.client import ConfigMetadata
from ddtrace.internal.remoteconfig.client import TargetFile
from ddtrace.internal.utils.formats import asbool
from tests.appsec.test_processor import Config
from tests.appsec.test_processor import ROOT_DIR
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
    with _asm_request_context.asm_request_context_manager(), tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(
            span,
            {},
            raw_uri="http://example.com/.git",
            status_code="404",
            request_cookies={"cookie1": "im the cookie1"},
        )
    return span.get_tag(APPSEC.JSON)


def test_rc_enabled_by_default(tracer):
    os.environ.pop("DD_REMOTE_CONFIGURATION_ENABLED", None)
    result = _set_and_get_appsec_tags(tracer)
    assert result is None
    assert _appsec_rc_features_is_enabled()


def test_rc_activate_is_active_and_get_processor_tags(tracer, remote_config_worker):
    result = _set_and_get_appsec_tags(tracer)
    assert result is None
    RCAppSecFeaturesCallBack(tracer)(None, {"asm": {"enabled": True}})
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
    with override_global_config(dict(_appsec_enabled=False)), override_env({APPSEC.ENV: appsec_enabled}):
        if appsec_enabled == "":
            del os.environ[APPSEC.ENV]

        RCAppSecFeaturesCallBack(tracer)(None, {"asm": {"enabled": rc_value}})
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
    with override_global_config(dict(_appsec_enabled=True)), override_env({APPSEC.ENV: appsec_enabled}):
        if appsec_enabled == "":
            del os.environ[APPSEC.ENV]

        rc_config = {"asm": {"enabled": True}}
        if rc_value is False:
            rc_config = False

        RCAppSecFeaturesCallBack(tracer)(None, rc_config)
        result = _set_and_get_appsec_tags(tracer)
        assert result is None


@pytest.mark.parametrize(
    "rc_enabled, appsec_enabled, capability",
    [
        ("true", "true", "Afw="),  # All capabilities except ASM_ACTIVATION
        ("false", "true", ""),
        ("true", "false", ""),
        ("false", "false", ""),
        ("true", "", "Ag=="),  # ASM_ACTIVATION
        ("false", "", ""),
    ],
)
def test_rc_capabilities(rc_enabled, appsec_enabled, capability, tracer):
    env = {"DD_REMOTE_CONFIGURATION_ENABLED": rc_enabled}
    config = {}
    tracer.configure(appsec_enabled=False, api_version="v0.4")
    if appsec_enabled:
        env[APPSEC.ENV] = appsec_enabled
        config["appsec_enabled"] = asbool(appsec_enabled)
        config["api_version"] = "v0.4"
    with override_env(env):
        tracer.configure(**config)
        assert _appsec_rc_capabilities(test_tracer=tracer) == capability


def test_rc_activation_capabilities(tracer, remote_config_worker):
    env = {"DD_REMOTE_CONFIGURATION_ENABLED": "true"}
    with override_env(env), override_global_config(dict(_appsec_enabled=False, api_version="v0.4")):
        rc_config = {"asm": {"enabled": True}}

        assert not RemoteConfig._worker

        RCAppSecFeaturesCallBack(tracer)(None, rc_config)

        assert _appsec_rc_capabilities(test_tracer=tracer) == "Af4="  # All capabilities


def test_rc_activation_validate_products(tracer, remote_config_worker):
    with override_global_config(dict(_appsec_enabled=False, api_version="v0.4")):
        rc_config = {"asm": {"enabled": True}}

        assert not RemoteConfig._worker

        RCAppSecFeaturesCallBack(tracer)(None, rc_config)

        assert RemoteConfig._worker._client._products["ASM_DATA"]


def test_rc_activation_check_asm_features_product_disables_rest_of_products(tracer, remote_config_worker):
    with override_global_config(dict(_appsec_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        enable_appsec_rc(tracer)

        assert RemoteConfig._worker._client._products.get(PRODUCTS.ASM_DATA)
        assert RemoteConfig._worker._client._products.get(PRODUCTS.ASM)
        assert RemoteConfig._worker._client._products.get(PRODUCTS.ASM_FEATURES)

        RCAppSecFeaturesCallBack(tracer)(None, False)

        assert RemoteConfig._worker._client._products.get(PRODUCTS.ASM_DATA) is None
        assert RemoteConfig._worker._client._products.get(PRODUCTS.ASM) is None
        assert RemoteConfig._worker._client._products.get(PRODUCTS.ASM_FEATURES)


def test_load_new_configurations_invalid_content(remote_config_worker, tracer):
    with override_global_config(dict(_appsec_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        enable_appsec_rc(tracer)
        asm_features_data = b'{"asm":{"enabled":true}}'
        asm_data_data = b'{"data": "data"}'
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
        with pytest.raises(ValueError):
            RemoteConfig._worker._client._load_new_configurations({}, client_configs, payload=payload)


@mock.patch.object(RCAppSecFeaturesCallBack, "_appsec_1click_activation")
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_rules_data")
def test_load_new_configurations_dispatch_applied_configs(
    mock_appsec_rules_data, mock_appsec_1click_activation, remote_config_worker, tracer
):
    with override_global_config(dict(_appsec_enabled=True, api_version="v0.4")):
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

        RemoteConfig._worker._client._load_new_configurations({}, client_configs, payload=payload)
        mock_appsec_rules_data.assert_called_with(ANY, {"data": [{"test": "data"}]})
        mock_appsec_1click_activation.assert_called_with({"asm": {"enabled": True}})


@mock.patch.object(RCAppSecFeaturesCallBack, "_appsec_1click_activation")
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_rules_data")
def test_load_new_configurations_empty_config(
    mock_appsec_rules_data, mock_appsec_1click_activation, remote_config_worker, tracer
):
    with override_global_config(dict(_appsec_enabled=True, api_version="v0.4")):
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

        RemoteConfig._worker._client._load_new_configurations({}, client_configs, payload=payload)
        mock_appsec_rules_data.assert_called_with(ANY, {"data": []})
        mock_appsec_1click_activation.assert_called_with({"asm": {"enabled": True}})


@mock.patch.object(RCAppSecFeaturesCallBack, "_appsec_1click_activation")
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_rules_data")
def test_load_new_configurations_remove_config_and_dispatch_applied_configs(
    mock_appsec_rules_data, mock_appsec_1click_activation, remote_config_worker
):
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

    mock_appsec_1click_activation.assert_called_with(False)
    mock_appsec_rules_data.assert_not_called()
    mock_appsec_1click_activation.reset_mock()
    mock_appsec_rules_data.reset_mock()

    # Not called because with this configuration the above condition is True:
    # applied_config = self._applied_configs.get(target)
    # if applied_config == config:
    #     continue
    RemoteConfig._worker._client._load_new_configurations({}, client_configs, payload=payload)
    mock_appsec_rules_data.assert_not_called()
    mock_appsec_1click_activation.assert_not_called()


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


@pytest.mark.skipif(sys.version_info[:2] < (3, 6), reason="Mock return order is different in python <= 3.5")
@mock.patch.object(RCAppSecFeaturesCallBack, "_appsec_1click_activation")
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_rules_data")
def test_load_multiple_targets_file_same_product(
    mock_appsec_rules_data, mock_appsec_1click_activation, remote_config_worker, tracer
):
    with override_global_config(dict(_appsec_enabled=True, api_version="v0.4")):
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

        RemoteConfig._worker._client._load_new_configurations({}, client_configs, payload=payload)
        mock_appsec_rules_data.assert_called_with(ANY, {"data": [{"a": 1}, {"b": 2}]})
        mock_appsec_1click_activation.assert_called_with({"asm": {"enabled": True}})


@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_rules_data")
def test_remove_targets_file_with_previous_configuration(mock_appsec_rules_data, remote_config_worker, tracer):
    with override_global_config(dict(_appsec_enabled=True, api_version="v0.4")):
        RCAppSecCallBack.configs = {}
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        enable_appsec_rc(tracer)
        asm_data_data1 = b'{"data": [{"a":1}]}'
        asm_data_data2 = b'{"data": [{"b":2}]}'
        payload = AgentPayload(
            target_files=[
                TargetFile(path="mock/ASM_DATA/1", raw=base64.b64encode(asm_data_data1)),
                TargetFile(path="mock/ASM_DATA/2", raw=base64.b64encode(asm_data_data2)),
            ]
        )
        applied_configs = {
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

        client_configs = {
            "mock/ASM_DATA/1": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data1).hexdigest(),
                length=5,
                tuf_version=5,
            ),
        }

        target_file = {
            "mock/ASM_DATA/2": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data2).hexdigest(),
                length=5,
                tuf_version=5,
            )
        }

        RemoteConfig._worker._client._applied_configs = applied_configs
        RemoteConfig._worker._client._remove_previously_applied_configurations({}, client_configs, target_file)

        RemoteConfig._worker._client._load_new_configurations({}, client_configs, payload=payload)
        mock_appsec_rules_data.assert_not_called()


@pytest.mark.skipif(sys.version_info[:2] < (3, 6), reason="Mock return order is different in python <= 3.5")
@mock.patch.object(RCAppSecFeaturesCallBack, "_appsec_1click_activation")
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_rules_data")
def test_load_new_config_and_remove_targets_file_same_product(
    mock_appsec_rules_data, mock_appsec_1click_activation, remote_config_worker, tracer
):
    with override_global_config(dict(_appsec_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        applied_configs = {}
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

        target_file = {
            "mock/ASM_DATA/2": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data2).hexdigest(),
                length=5,
                tuf_version=5,
            )
        }
        RemoteConfig._worker._client._remove_previously_applied_configurations({}, first_config, {})

        RemoteConfig._worker._client._load_new_configurations(applied_configs, first_config, payload=payload)
        RemoteConfig._worker._client._applied_configs = applied_configs

        mock_appsec_rules_data.assert_called_with(ANY, {"data": [{"a": 1}, {"b": 2}]})
        mock_appsec_rules_data.reset_mock()

        RemoteConfig._worker._client._remove_previously_applied_configurations({}, second_config, target_file)

        RemoteConfig._worker._client._load_new_configurations({}, second_config, payload=payload)
        mock_appsec_rules_data.assert_called_with(ANY, {"data": [{"a": 1}]})


@pytest.mark.skipif(sys.version_info[:2] < (3, 6), reason="Mock return order is different in python <= 3.5")
@mock.patch.object(AppSecSpanProcessor, "_update_rules")
def test_fullpath_appsec_rules_data(mock_update_rules, remote_config_worker, tracer):
    with override_global_config(dict(_appsec_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        applied_configs = {}
        enable_appsec_rc(tracer)
        asm_features_data = b'{"asm":{"enabled":true}}'
        asm_data_data1 = b'{"exclusions": [{"a":1}]}'
        asm_data_data2 = b'{"exclusions": [{"b":2}]}'
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

        target_file = {
            "mock/ASM_DATA/2": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data2).hexdigest(),
                length=5,
                tuf_version=5,
            )
        }
        RemoteConfig._worker._client._remove_previously_applied_configurations({}, first_config, {})

        RemoteConfig._worker._client._load_new_configurations(applied_configs, first_config, payload=payload)
        RemoteConfig._worker._client._applied_configs = applied_configs

        mock_update_rules.assert_called_with({"exclusions": [{"a": 1}, {"b": 2}]})
        mock_update_rules.reset_mock()

        RemoteConfig._worker._client._remove_previously_applied_configurations({}, second_config, target_file)

        RemoteConfig._worker._client._load_new_configurations({}, second_config, payload=payload)
        mock_update_rules.assert_called_with({"exclusions": [{"a": 1}]})


@pytest.mark.skipif(sys.version_info[:2] < (3, 6), reason="Mock return order is different in python <= 3.5")
@mock.patch.object(AppSecSpanProcessor, "_update_rules")
def test_fullpath_appsec_rules_data_empty_data(mock_update_rules, remote_config_worker, tracer):
    with override_global_config(dict(_appsec_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        applied_configs = {}
        enable_appsec_rc(tracer)
        asm_data_data1 = b'{"exclusions": [{"a":1}]}'
        asm_data_data2 = b'{"exclusions": []}'
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
            "mock/ASM_DATA/1": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data1).hexdigest(),
                length=5,
                tuf_version=5,
            ),
        }

        target_file = {
            "mock/ASM_DATA/2": ConfigMetadata(
                id="",
                product_name="ASM_DATA",
                sha256_hash=hashlib.sha256(asm_data_data2).hexdigest(),
                length=5,
                tuf_version=5,
            )
        }
        RemoteConfig._worker._client._remove_previously_applied_configurations({}, first_config, {})

        RemoteConfig._worker._client._load_new_configurations(applied_configs, first_config, payload=payload)
        RemoteConfig._worker._client._applied_configs = applied_configs

        mock_update_rules.assert_called_with({"exclusions": [{"a": 1}]})
        mock_update_rules.reset_mock()

        RemoteConfig._worker._client._remove_previously_applied_configurations({}, second_config, target_file)

        RemoteConfig._worker._client._load_new_configurations({}, second_config, payload=payload)
        mock_update_rules.assert_called_with({"exclusions": [{"a": 1}]})


@pytest.mark.skipif(sys.version_info[:2] < (3, 6), reason="Mock return order is different in python <= 3.5")
@mock.patch.object(AppSecSpanProcessor, "_update_rules")
def test_fullpath_appsec_rules_data_add_delete_file(mock_update_rules, remote_config_worker, tracer):
    with override_global_config(dict(_appsec_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        applied_configs = {}
        enable_appsec_rc(tracer)
        asm_data_data1 = b'{"exclusions": [{"a":1}]}'
        asm_data_data2 = b'{"exclusions": []}'
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
        RemoteConfig._worker._client._remove_previously_applied_configurations({}, first_config, {})

        RemoteConfig._worker._client._load_new_configurations(applied_configs, first_config, payload=payload)
        RemoteConfig._worker._client._applied_configs = applied_configs

        mock_update_rules.assert_called_with({"exclusions": [{"a": 1}]})
        mock_update_rules.reset_mock()

        RemoteConfig._worker._client._remove_previously_applied_configurations({}, second_config, target_file)

        RemoteConfig._worker._client._load_new_configurations({}, second_config, payload=second_payload)
        mock_update_rules.assert_called_with({"exclusions": []})


@pytest.mark.skipif(sys.version_info[:2] < (3, 6), reason="Mock return order is different in python <= 3.5")
@mock.patch.object(RCAppSecFeaturesCallBack, "_appsec_1click_activation")
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_rules_data")
def test_load_new_empty_config_and_remove_targets_file_same_product(
    mock_appsec_rules_data, remote_config_worker, tracer
):
    with override_global_config(dict(_appsec_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        RCAppSecCallBack.configs = {}
        applied_configs = {}
        enable_appsec_rc(tracer)
        asm_features_data = b'{"asm":{"enabled":true}}'
        asm_data_data1 = b'{"data": [{"a":1}]}'
        asm_data_data2 = b'{"data2": [{"b":2}]}'
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

        RemoteConfig._worker._client._remove_previously_applied_configurations({}, first_config, {})

        RemoteConfig._worker._client._load_new_configurations(applied_configs, first_config, payload=payload)
        RemoteConfig._worker._client._applied_configs = {}

        mock_appsec_rules_data.assert_called_with(ANY, {"data": [{"a": 1}], "data2": [{"b": 2}]})
        mock_appsec_rules_data.reset_mock()

        payload = AgentPayload(
            target_files=[
                TargetFile(path="mock/ASM_FEATURES", raw=base64.b64encode(asm_features_data)),
                TargetFile(path="mock/ASM_DATA/1", raw=base64.b64encode(asm_data_data1)),
                TargetFile(path="mock/ASM_DATA/2", raw=base64.b64encode(asm_data_data_empty)),
            ]
        )

        RemoteConfig._worker._client._load_new_configurations({}, second_config, payload=payload)
        mock_appsec_rules_data.assert_called_with(ANY, {"data": [{"a": 1}], "data2": []})


def test_rc_activation_ip_blocking_data(tracer, remote_config_worker):
    with override_env({APPSEC.ENV: "true"}):
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

        RCAppSecCallBack(tracer)(None, rc_config)
        with _asm_request_context.asm_request_context_manager("8.8.4.4", {}):
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                set_http_meta(
                    span,
                    Config(),
                )
            assert "triggers" in json.loads(span.get_tag(APPSEC.JSON))
            assert _context.get_item("http.request.remote_ip", span) == "8.8.4.4"


def test_rc_activation_ip_blocking_data_expired(tracer, remote_config_worker):
    with override_env({APPSEC.ENV: "true"}):
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

        RCAppSecCallBack(tracer)(None, rc_config)

        with _asm_request_context.asm_request_context_manager("8.8.4.4", {}):
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                set_http_meta(
                    span,
                    Config(),
                )
            assert span.get_tag(APPSEC.JSON) is None


def test_rc_activation_ip_blocking_data_not_expired(tracer, remote_config_worker):
    with override_env({APPSEC.ENV: "true"}):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        rc_config = {
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

        assert not RemoteConfig._worker

        RCAppSecCallBack(tracer)(None, rc_config)

        with _asm_request_context.asm_request_context_manager("8.8.4.4", {}):
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                set_http_meta(
                    span,
                    Config(),
                )
            assert "triggers" in json.loads(span.get_tag(APPSEC.JSON))
            assert _context.get_item("http.request.remote_ip", span) == "8.8.4.4"


def test_rc_rules_data(tracer):
    RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(ROOT_DIR)), "ddtrace/appsec/rules.json")
    with override_global_config(dict(_appsec_enabled=True)), override_env({APPSEC.ENV: "true"}), open(
        RULES_PATH, "r"
    ) as dd_rules:
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        config = {
            "rules_data": [],
            "custom_rules": [],
            "rules": json.load(dd_rules)["rules"],
        }
        assert _appsec_rules_data(tracer, config)


def test_rc_rules_data_error_empty(tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env({APPSEC.ENV: "true"}):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        config = {}
        assert not _appsec_rules_data(tracer, config)


@pytest.mark.skipif(sys.version_info < (3, 5), reason="Python 2 is handling that test differently")
def test_rc_rules_data_error_ddwaf(tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env({APPSEC.ENV: "true"}):
        tracer.configure(appsec_enabled=True, api_version="v0.4")
        config = {
            "rules": [{"invalid": mock.MagicMock()}],
        }
        assert not _appsec_rules_data(tracer, config)
