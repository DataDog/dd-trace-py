import base64
import hashlib
import os
import time

import mock
import pytest

from ddtrace.appsec._capabilities import _appsec_rc_capabilities
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._constants import PRODUCTS
from ddtrace.appsec._remoteconfiguration import _appsec_callback
from ddtrace.appsec._remoteconfiguration import _preprocess_results_appsec_1click_activation
from ddtrace.appsec._remoteconfiguration import disable_appsec_rc
from ddtrace.appsec._remoteconfiguration import enable_appsec_rc
from ddtrace.appsec._utils import get_triggers
from ddtrace.contrib.internal.trace_utils import set_http_meta
from ddtrace.internal.remoteconfig.client import AgentPayload
from ddtrace.internal.remoteconfig.client import ConfigMetadata
from ddtrace.internal.remoteconfig.client import TargetFile
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings.asm import config as asm_config
import tests.appsec.rules as rules
from tests.appsec.utils import asm_context
from tests.appsec.utils import build_payload
from tests.appsec.utils import get_waf_addresses
from tests.utils import override_env
from tests.utils import override_global_config


def _set_and_get_appsec_tags(tracer, check_client_id=False):
    with asm_context(tracer) as span:
        set_http_meta(
            span,
            {},
            raw_uri="http://example.com/.git",
            status_code="404",
            request_cookies={"cookie1": "im the cookie1"},
        )
    if check_client_id:
        assert span._local_root._meta.get(APPSEC.RC_CLIENT_ID)
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
        rc_config = build_payload("ASM_FEATURES", {"asm": {"enabled": True}}, "config")
        _appsec_callback([rc_config], tracer)
        assert tracer._appsec_processor
        assert _set_and_get_appsec_tags(tracer)
        rc_config = build_payload("ASM_FEATURES", None, "config")
        _appsec_callback([rc_config], tracer)
        result = _set_and_get_appsec_tags(tracer)
        assert result is None
        assert tracer._appsec_processor is None
        rc_config = build_payload("ASM_FEATURES", {"asm": {"enabled": True}}, "config")
        _appsec_callback([rc_config], tracer)
        assert tracer._appsec_processor
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
        rc_config = build_payload("ASM_FEATURES", {"asm": {"enabled": rc_value}}, "config")
        _appsec_callback([rc_config], tracer)
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
    with override_env({APPSEC.ENV: appsec_enabled}):
        if appsec_enabled == "":
            del os.environ[APPSEC.ENV]
        with override_global_config(dict(_asm_enabled=True)):
            tracer.configure(appsec_enabled=asbool(appsec_enabled))

            rc_configs = [build_payload("ASM_FEATURES", {"asm": {"enabled": True}}, "config")]
            if rc_value is False:
                rc_configs = []

            _appsec_callback(rc_configs, tracer)
            result = _set_and_get_appsec_tags(tracer)
            assert result is None


@pytest.mark.parametrize(
    "rc_enabled, appsec_enabled, capability",
    [
        (True, "true", "DC+B5AP8"),  # All capabilities except ASM_ACTIVATION
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
    tracer.configure(appsec_enabled=False)
    if appsec_enabled:
        env[APPSEC.ENV] = appsec_enabled
        config["appsec_enabled"] = asbool(appsec_enabled)
    with override_env(env):
        with override_global_config(dict(_remote_config_enabled=rc_enabled)):
            tracer.configure(**config)
            assert _appsec_rc_capabilities(test_tracer=tracer) == capability


@pytest.mark.parametrize(
    "env_rules, expected",
    [
        ({}, "DC+B5AP+"),  # All capabilities
        ({"_asm_static_rule_file": DEFAULT.RULES}, "gAAAAg=="),  # Only ASM_FEATURES
    ],
)
def test_rc_activation_capabilities(tracer, remote_config_worker, env_rules, expected):
    global_config = dict(_asm_enabled=False, _remote_config_enabled=True)
    global_config.update(env_rules)
    with override_global_config(global_config):
        rc_configs = [build_payload("ASM_FEATURES", {"asm": {"enabled": True}}, "config")]
        # flaky test
        # assert not remoteconfig_poller._worker

        _appsec_callback(rc_configs, test_tracer=tracer)

        assert _appsec_rc_capabilities(test_tracer=tracer) == expected


def test_rc_activation_validate_products(tracer, remote_config_worker):
    with override_global_config(dict(_asm_enabled=False, _remote_config_enabled=True, api_version="v0.4")):
        assert not remoteconfig_poller._worker

        enable_appsec_rc()

        assert remoteconfig_poller._client._products["ASM_FEATURES"]
    disable_appsec_rc()


def test_rc_activation_validate_client_id(tracer, remote_config_worker):
    with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True)
        enable_appsec_rc()
        _set_and_get_appsec_tags(tracer, True)
    disable_appsec_rc()


@pytest.mark.parametrize(
    "env_rules, expected",
    [
        ({"_asm_can_be_enabled": True}, True),  # All capabilities
        ({"_asm_static_rule_file": DEFAULT.RULES, "_asm_can_be_enabled": False}, False),  # Only ASM_FEATURES
    ],
)
@pytest.mark.parametrize(
    "enable_config_content, disable_config_content",
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
    tracer, remote_config_worker, env_rules, expected, enable_config_content, disable_config_content
):
    global_config = dict(_remote_config_enabled=True, _asm_enabled=True)
    global_config.update(env_rules)
    from ddtrace.internal.remoteconfig.client import config as rc_config

    rc_config.skip_shutdown = False
    empty_config = []
    enable_config = [build_payload("ASM_FEATURES", enable_config_content, "config")]
    disable_config = [build_payload("ASM_FEATURES", disable_config_content, "config")]
    with override_global_config(global_config):
        tracer.configure(appsec_enabled=True)
        enable_appsec_rc(tracer)
        pubsub = remoteconfig_poller.get_registered(PRODUCTS.ASM_FEATURES)
        assert bool(remoteconfig_poller._client._products.get(PRODUCTS.ASM_DATA)) is expected
        assert bool(remoteconfig_poller._client._products.get(PRODUCTS.ASM)) is expected
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM_FEATURES)

        # sending nothing should not change anything (configuration is the same)
        _preprocess_results_appsec_1click_activation(empty_config, pubsub)

        assert bool(remoteconfig_poller._client._products.get(PRODUCTS.ASM_DATA)) is expected
        assert bool(remoteconfig_poller._client._products.get(PRODUCTS.ASM)) is expected
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM_FEATURES)

        # sending empty config for asm should disable asm (meaning asm was deleted)
        _preprocess_results_appsec_1click_activation(disable_config, pubsub)

        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM_DATA) is None
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM) is None
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM_FEATURES)

        # sending nothing should not change anything (configuration is the same)
        _preprocess_results_appsec_1click_activation(empty_config, pubsub)
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM_DATA) is None
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM) is None
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM_FEATURES)

        # sending config should enable asm again
        _preprocess_results_appsec_1click_activation(enable_config, pubsub)
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
        tracer.configure(appsec_enabled=True)
        enable_appsec_rc(tracer)

        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM_DATA)
        assert remoteconfig_poller._client._products.get(PRODUCTS.ASM)
        assert bool(remoteconfig_poller._client._products.get(PRODUCTS.ASM_FEATURES)) == auto_user

    disable_appsec_rc()


@mock.patch(
    "ddtrace.appsec._remoteconfiguration._preprocess_results_appsec_1click_activation",
    wraps=_preprocess_results_appsec_1click_activation,
)
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_callback")
def test_load_new_configurations_dispatch_applied_configs(
    mock_appsec_rules_data, mock_appsec_1click_activation, remote_config_worker, tracer
):
    with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True)
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
        assert list_callbacks
        remoteconfig_poller._client._publish_configuration(list_callbacks)
        remoteconfig_poller._poll_data()
        for mockf in [mock_appsec_1click_activation, mock_appsec_rules_data]:
            assert mockf.call_count == 1
            arg = mockf.call_args[0][0]
            assert isinstance(arg, list)
            assert len(arg) == 2
            assert arg[0].metadata.product_name == "ASM_FEATURES"
            assert arg[1].metadata.product_name == "ASM_DATA"
        disable_appsec_rc()


@mock.patch(
    "ddtrace.appsec._remoteconfiguration._preprocess_results_appsec_1click_activation",
    wraps=_preprocess_results_appsec_1click_activation,
)
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_callback")
def test_load_new_configurations_empty_config(
    mock_appsec_rules_data, mock_appsec_1click_activation, remote_config_worker, tracer
):
    with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True)
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
        for mockf in [mock_appsec_1click_activation, mock_appsec_rules_data]:
            assert mockf.call_count == 1
            arg = mockf.call_args[0][0]
            assert isinstance(arg, list)
            assert len(arg) == 2
            assert arg[0].metadata.product_name == "ASM_FEATURES"
            assert arg[1].metadata.product_name == "ASM_DATA"
    disable_appsec_rc()


@mock.patch(
    "ddtrace.appsec._remoteconfiguration._preprocess_results_appsec_1click_activation",
    wraps=_preprocess_results_appsec_1click_activation,
)
@mock.patch("ddtrace.appsec._remoteconfiguration._appsec_callback")
def test_load_new_configurations_remove_config_and_dispatch_applied_configs(
    mock_appsec_rules_data,
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

    payload_mock = [build_payload("ASM_FEATURES", {"asm": {"enabled": False}}, "config")]
    mock_preprocess_results_appsec_1click_activation.return_value = payload_mock

    remoteconfig_poller._client._applied_configs = client_configs
    list_callbacks = []
    remoteconfig_poller._client._remove_previously_applied_configurations(list_callbacks, {}, {}, {})
    remoteconfig_poller._client._publish_configuration(list_callbacks)

    remoteconfig_poller._poll_data()

    arg = mock_preprocess_results_appsec_1click_activation.call_args[0][0]
    assert isinstance(arg, list)
    assert len(arg) == 1
    mock_appsec_rules_data.assert_called_with(payload_mock)

    mock_appsec_rules_data.reset_mock()

    # Not called because with this configuration the above condition is True:
    # applied_config = self._applied_configs.get(target)
    # if applied_config == config:
    #     continue
    remoteconfig_poller._client._load_new_configurations(list_callbacks, {}, client_configs, payload=payload)
    remoteconfig_poller._client._publish_configuration(list_callbacks)
    remoteconfig_poller._poll_data()
    mock_appsec_rules_data.assert_not_called()  # ({"asm": {"enabled": False}}, None)
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


def test_rc_activation_ip_blocking_data(tracer, remote_config_worker):
    with override_env({APPSEC.ENV: "true"}), override_global_config({}):
        tracer.configure(appsec_enabled=True)
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
        assert remoteconfig_poller.status == ServiceStatus.STOPPED

        _appsec_callback([build_payload("ASM_DATA", rc_config, "data")], tracer)
        with asm_context(tracer, ip_addr="8.8.4.4") as span:
            set_http_meta(
                span,
                rules.Config(),
            )
        assert get_triggers(span)
        assert get_waf_addresses("http.request.remote_ip") == "8.8.4.4"


def test_rc_activation_ip_blocking_data_expired(tracer, remote_config_worker):
    with override_env({APPSEC.ENV: "true"}), override_global_config({}):
        tracer.configure(appsec_enabled=True)
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

        assert remoteconfig_poller.status == ServiceStatus.STOPPED

        _appsec_callback([build_payload("ASM_DATA", rc_config, "data")], tracer)

        with asm_context(tracer, ip_addr="8.8.4.4") as span:
            set_http_meta(
                span,
                rules.Config(),
            )
        assert get_triggers(span) is None


def test_rc_activation_ip_blocking_data_not_expired(tracer, remote_config_worker):
    with override_env({APPSEC.ENV: "true"}), override_global_config({}):
        tracer.configure(appsec_enabled=True)
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

        assert remoteconfig_poller.status == ServiceStatus.STOPPED

        _appsec_callback([build_payload("ASM_DATA", rc_config, "data")], tracer)

        with asm_context(tracer, ip_addr="8.8.4.4") as span:
            set_http_meta(
                span,
                rules.Config(),
            )
        assert get_triggers(span)
        assert get_waf_addresses("http.request.remote_ip") == "8.8.4.4"
