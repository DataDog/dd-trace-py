# -*- coding: utf-8 -*-
import json
import os

import mock
from mock.mock import ANY

from ddtrace.appsec._remoteconfiguration import AppSecRC
from ddtrace.appsec._remoteconfiguration import _preprocess_results_appsec_1click_activation
from ddtrace.appsec._remoteconfiguration import enable_appsec_rc
from ddtrace.internal import runtime
import ddtrace.internal.remoteconfig._connectors
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.utils.version import _pep440_to_semver
from tests.utils import override_global_config


def _expected_payload(
    rc_client,
    capabilities="IAjwAA==",  # this was gathered by running the test and observing the payload
    has_errors=False,
    targets_version=0,
    backend_client_state=None,
    config_states=None,
    cached_target_files=None,
    error_msg=None,
):
    if config_states is None:
        config_states = []
    if cached_target_files is None:
        cached_target_files = []

    payload = {
        "client": {
            "id": rc_client.id,
            "products": ["ASM_FEATURES"],
            "is_tracer": True,
            "client_tracer": {
                "runtime_id": runtime.get_runtime_id(),
                "language": "python",
                "tracer_version": _pep440_to_semver(),
                "service": None,
                "extra_services": [],
                "env": None,
                "app_version": None,
            },
            "state": {
                "root_version": 1,
                "targets_version": targets_version,
                "config_states": config_states,
                "has_error": has_errors,
            },
            "capabilities": capabilities,
        },
        "cached_target_files": cached_target_files,
    }
    if backend_client_state:
        payload["client"]["state"]["backend_client_state"] = backend_client_state
    if has_errors:
        payload["client"]["state"]["error"] = error_msg
    return payload


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
MOCK_AGENT_RESPONSES_FILE = os.path.join(ROOT_DIR, "rc_mocked_responses_asm_features.json")


def _assert_response(mock_send_request, expected_response):
    expected_response["cached_target_files"].sort(key=lambda x: x["path"], reverse=True)
    expected_response["client"]["state"]["config_states"].sort(key=lambda x: x["id"], reverse=True)
    response = json.loads(mock_send_request.call_args.args[0])
    response["cached_target_files"].sort(key=lambda x: x["path"], reverse=True)
    response["client"]["state"]["config_states"].sort(key=lambda x: x["id"], reverse=True)

    assert response["client"]["client_tracer"]["tags"]
    del response["client"]["client_tracer"]["tags"]

    assert response == expected_response


@mock.patch(
    "ddtrace.internal.remoteconfig._connectors.PublisherSubscriberConnector.write",
    side_effect=ddtrace.internal.remoteconfig._connectors.PublisherSubscriberConnector.write,
    autospec=True,
)
@mock.patch.object(RemoteConfigClient, "_send_request")
@mock.patch("ddtrace.appsec._capabilities._appsec_rc_capabilities")
def test_remote_config_client_steps(mock_appsec_rc_capabilities, mock_send_request, mock_write):
    remoteconfig_poller.disable()
    assert remoteconfig_poller.status == ServiceStatus.STOPPED

    with open(MOCK_AGENT_RESPONSES_FILE, "r") as f:
        MOCK_AGENT_RESPONSES = json.load(f)

    mock_callback = mock.MagicMock()
    mock_preprocess_results = mock.MagicMock()

    def _mock_appsec_callback(features, test_tracer=None):
        mock_callback(features)

    def _mock_mock_preprocess_results(features, test_tracer=None):
        features = _preprocess_results_appsec_1click_activation(features)
        mock_preprocess_results(features)
        return features

    mock_appsec_rc_capabilities.return_value = "Ag=="

    with override_global_config(dict(_remote_config_enabled=False)):
        enable_appsec_rc()
        rc_client = RemoteConfigClient()

        asm_callback = AppSecRC(_mock_mock_preprocess_results, _mock_appsec_callback)
        rc_client.register_product("ASM_FEATURES", asm_callback)

    assert len(rc_client._products) == 1
    assert remoteconfig_poller.status == ServiceStatus.STOPPED

    # 0.
    mock_send_request.return_value = MOCK_AGENT_RESPONSES[0]
    rc_client.request()
    expected_response = _expected_payload(rc_client)

    assert rc_client._last_error is None
    _assert_response(mock_send_request, expected_response)

    asm_callback._poll_data()

    mock_preprocess_results.assert_not_called()
    mock_callback.assert_not_called()
    mock_write.assert_not_called()

    mock_send_request.reset_mock()
    mock_preprocess_results.reset_mock()
    mock_callback.reset_mock()
    mock_write.reset_mock()

    # 1. An update that doesn’t have any new config files but does have an updated TUF Targets file.
    # The tracer is supposed to process this update and store that the latest TUF Targets version is 1.
    mock_send_request.return_value = MOCK_AGENT_RESPONSES[1]
    rc_client.request()
    expected_response = _expected_payload(rc_client, targets_version=1, backend_client_state="eyJmb28iOiAiYmFyIn0=")

    assert rc_client._last_error is None
    _assert_response(mock_send_request, expected_response)

    asm_callback._poll_data()

    mock_preprocess_results.assert_called_with({"asm": {"enabled": True}})
    mock_callback.assert_called_with({"metadata": {}, "config": {"asm": {"enabled": True}}, "shared_data_counter": 1})
    mock_write.assert_called_with(ANY, {}, {"asm": {"enabled": True}})

    mock_send_request.reset_mock()
    mock_preprocess_results.reset_mock()
    mock_callback.reset_mock()
    mock_write.reset_mock()

    # 2. A single configuration for the product is added. (“base”)
    mock_send_request.return_value = MOCK_AGENT_RESPONSES[2]
    rc_client.request()
    expected_response = _expected_payload(
        rc_client,
        targets_version=2,
        config_states=[{"id": "ASM_FEATURES-base", "version": 1, "product": "ASM_FEATURES", "apply_state": 2}],
        backend_client_state="eyJmb28iOiAiYmFyIn0=",
        cached_target_files=[
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            }
        ],
    )

    assert rc_client._last_error is None
    _assert_response(mock_send_request, expected_response)

    asm_callback._poll_data()

    mock_preprocess_results.assert_called_with({"asm": {"enabled": False}})
    mock_callback.assert_called_with({"metadata": {}, "config": {"asm": {"enabled": False}}, "shared_data_counter": 2})

    mock_send_request.reset_mock()
    mock_preprocess_results.reset_mock()
    mock_callback.reset_mock()
    mock_write.reset_mock()

    # 3. The “base” configuration is modified.
    mock_send_request.return_value = MOCK_AGENT_RESPONSES[3]
    rc_client.request()
    expected_response = _expected_payload(
        rc_client,
        targets_version=3,
        config_states=[{"id": "ASM_FEATURES-base", "version": 2, "product": "ASM_FEATURES", "apply_state": 2}],
        backend_client_state="eyJmb28iOiAiYmFyIn0=",
        cached_target_files=[
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config",
                "length": 48,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "a38ebf9fa256071f9823a5f512a84e8a786c8da2b0719452deeb5dc287ec990f",
                    }
                ],
            }
        ],
    )

    assert rc_client._last_error is None
    _assert_response(mock_send_request, expected_response)

    asm_callback._poll_data()

    mock_preprocess_results.assert_called_with({"asm": {}})
    mock_callback.assert_called_with({"metadata": {}, "config": {"asm": {}}, "shared_data_counter": ANY})

    mock_send_request.reset_mock()
    mock_preprocess_results.reset_mock()
    mock_callback.reset_mock()
    mock_write.reset_mock()

    # 4. The “base” configuration is removed.
    mock_send_request.return_value = MOCK_AGENT_RESPONSES[4]
    rc_client.request()
    expected_response = _expected_payload(
        rc_client,
        targets_version=4,
        config_states=[],
        backend_client_state="eyJmb28iOiAiYmFyIn0=",
        cached_target_files=[],
    )

    assert rc_client._last_error is None
    _assert_response(mock_send_request, expected_response)

    asm_callback._poll_data()

    mock_preprocess_results.assert_called_with({"asm": {"enabled": True}})
    mock_callback.assert_called_with({"metadata": {}, "config": {"asm": {"enabled": True}}, "shared_data_counter": ANY})

    mock_send_request.reset_mock()
    mock_preprocess_results.reset_mock()
    mock_callback.reset_mock()
    mock_write.reset_mock()

    # 5. The “base” configuration is added along with the “second” configuration.
    mock_send_request.return_value = MOCK_AGENT_RESPONSES[5]
    rc_client.request()
    expected_response = _expected_payload(
        rc_client,
        targets_version=5,
        config_states=[
            {"id": "ASM_FEATURES-base", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
            {"id": "ASM_FEATURES-second", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
        ],
        backend_client_state="eyJmb28iOiAiYmFyIn0=",
        cached_target_files=[
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            },
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-second/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            },
        ],
    )

    assert rc_client._last_error is None
    _assert_response(mock_send_request, expected_response)

    asm_callback._poll_data()

    mock_preprocess_results.assert_called_with({"asm": {"enabled": True}})
    mock_callback.assert_not_called()
    mock_write.assert_called_with(ANY, {}, {"asm": {"enabled": True}})

    mock_preprocess_results.reset_mock()
    mock_send_request.reset_mock()
    mock_callback.reset_mock()
    mock_write.reset_mock()

    # 6. The “third” configuration is added, the “second” configuration is removed
    mock_send_request.return_value = MOCK_AGENT_RESPONSES[6]
    rc_client.request()
    expected_response = _expected_payload(
        rc_client,
        targets_version=6,
        config_states=[
            {"id": "ASM_FEATURES-base", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
            {"id": "ASM_FEATURES-third", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
        ],
        backend_client_state="eyJmb28iOiAiYmFyIn0=",
        cached_target_files=[
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            },
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-third/config",
                "length": 41,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "1e4a75edfd5f65e0adec704cc7c32f79987117d84755544ae4905045cdb0a443",
                    }
                ],
            },
        ],
    )

    assert rc_client._last_error is None
    _assert_response(mock_send_request, expected_response)

    asm_callback._poll_data()

    mock_preprocess_results.assert_called()

    mock_callback.assert_called_with(
        {"metadata": {}, "config": {"asm": {"enabled": False}}, "shared_data_counter": ANY}
    )

    mock_preprocess_results.reset_mock()
    mock_send_request.reset_mock()
    mock_callback.reset_mock()
    mock_write.reset_mock()

    # 7. The “second” configuration is added back. The “first” configuration is modified.
    mock_send_request.return_value = MOCK_AGENT_RESPONSES[7]
    rc_client.request()
    expected_response = _expected_payload(
        rc_client,
        targets_version=7,
        config_states=[
            {"id": "ASM_FEATURES-third", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
            {"id": "ASM_FEATURES-base", "version": 2, "product": "ASM_FEATURES", "apply_state": 2},
            {"id": "ASM_FEATURES-second", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
        ],
        backend_client_state="eyJmb28iOiAiYmFyIn0=",
        cached_target_files=[
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-third/config",
                "length": 41,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "1e4a75edfd5f65e0adec704cc7c32f79987117d84755544ae4905045cdb0a443",
                    }
                ],
            },
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config",
                "length": 48,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "a38ebf9fa256071f9823a5f512a84e8a786c8da2b0719452deeb5dc287ec990f",
                    }
                ],
            },
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-second/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            },
        ],
    )

    assert rc_client._last_error is None
    _assert_response(mock_send_request, expected_response)

    asm_callback._poll_data()

    mock_preprocess_results.assert_called_with({"asm": {"enabled": True}})
    mock_callback.assert_called_with({"metadata": {}, "config": {"asm": {"enabled": True}}, "shared_data_counter": ANY})

    mock_preprocess_results.reset_mock()
    mock_send_request.reset_mock()
    mock_callback.reset_mock()
    mock_write.reset_mock()

    # 8. The “first” configuration is modified again, the “third” configuration is removed.
    mock_send_request.return_value = MOCK_AGENT_RESPONSES[8]
    rc_client.request()
    expected_response = _expected_payload(
        rc_client,
        targets_version=8,
        config_states=[
            {"id": "ASM_FEATURES-second", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
            {"id": "ASM_FEATURES-base", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
        ],
        backend_client_state="eyJmb28iOiAiYmFyIn0=",
        cached_target_files=[
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-second/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            },
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            },
        ],
    )

    assert rc_client._last_error is None
    _assert_response(mock_send_request, expected_response)

    asm_callback._poll_data()

    mock_preprocess_results.assert_not_called()
    mock_callback.assert_not_called()
    mock_write.assert_not_called()

    mock_preprocess_results.reset_mock()
    mock_send_request.reset_mock()
    mock_callback.reset_mock()
    mock_write.reset_mock()

    # 9. Another update that doesn’t have any new or updated config files but has a newer TUF Targets file.
    # This tests that a tracer handles this scenario and still reports that it has tracked config files in
    # the next update.
    mock_send_request.return_value = MOCK_AGENT_RESPONSES[9]
    rc_client.request()
    expected_response = _expected_payload(
        rc_client,
        targets_version=9,
        config_states=[
            {"id": "ASM_FEATURES-second", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
            {"id": "ASM_FEATURES-base", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
        ],
        backend_client_state="eyJmb28iOiAiYmFyIn0=",
        cached_target_files=[
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-second/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            },
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            },
        ],
    )

    assert rc_client._last_error is None
    _assert_response(mock_send_request, expected_response)

    asm_callback._poll_data()

    mock_preprocess_results.assert_not_called()
    mock_callback.assert_not_called()
    mock_write.assert_not_called()

    mock_preprocess_results.reset_mock()
    mock_send_request.reset_mock()
    mock_callback.reset_mock()
    mock_write.reset_mock()

    # 10. A file is included in TUF Targets that is NOT specified in client_configs.
    # The tracer should ignore this file and not report it in the next update request.
    mock_send_request.return_value = MOCK_AGENT_RESPONSES[10]
    rc_client.request()
    expected_response = _expected_payload(
        rc_client,
        targets_version=10,
        config_states=[
            {"id": "ASM_FEATURES-second", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
            {"id": "ASM_FEATURES-base", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
        ],
        backend_client_state="eyJmb28iOiAiYmFyIn0=",
        cached_target_files=[
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-second/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            },
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            },
        ],
    )

    assert rc_client._last_error is None
    _assert_response(mock_send_request, expected_response)

    asm_callback._poll_data()

    mock_preprocess_results.assert_called_with({"asm": {"enabled": True}})
    mock_callback.assert_not_called()
    mock_write.assert_called_with(ANY, {}, {"asm": {"enabled": True}})

    mock_preprocess_results.reset_mock()
    mock_send_request.reset_mock()
    mock_callback.reset_mock()
    mock_write.reset_mock()

    # 11. The “first” configuration is modified. Another file is included in the TUF Targets that is not specified
    # in client_configs. This again tests that the tracer is only processing files in client_configs.
    mock_send_request.return_value = MOCK_AGENT_RESPONSES[11]
    rc_client.request()
    expected_response = _expected_payload(
        rc_client,
        targets_version=11,
        config_states=[
            {"id": "ASM_FEATURES-second", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
            {"id": "ASM_FEATURES-base", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
            {"id": "ASM_FEATURES-third", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
        ],
        backend_client_state="eyJmb28iOiAiYmFyIn0=",
        cached_target_files=[
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-second/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            },
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            },
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-third/testname",
                "length": 41,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "1e4a75edfd5f65e0adec704cc7c32f79987117d84755544ae4905045cdb0a443",
                    }
                ],
            },
        ],
    )

    assert rc_client._last_error is None
    _assert_response(mock_send_request, expected_response)

    asm_callback._poll_data()
    # At this point, publisher has 3 config files with the same config for the same key:
    #  - datadog/2/ASM_FEATURES/ASM_FEATURES-third/testname -> {"asm": {"enabled": True}}
    #  - datadog/2/ASM_FEATURES/ASM_FEATURES-base/config -> {"asm": {"enabled": True}}
    #  - datadog/2/ASM_FEATURES/ASM_FEATURES-second/config -> {"asm": {"enabled": False}}
    # Depends of the Python version, the order of this configuration could change and the result could be different
    # It doesn't matter because this problem can't exist on production
    mock_preprocess_results.assert_called_with({"asm": {"enabled": ANY}})
    mock_callback.assert_called_with(
        {"metadata": {}, "config": {"asm": {"enabled": False}}, "shared_data_counter": ANY}
    )

    mock_preprocess_results.reset_mock()
    mock_send_request.reset_mock()
    mock_callback.reset_mock()
    mock_write.reset_mock()

    # 12. A new configuration file’s raw bytes are missing from target_files that is referenced by client_configs
    # and targets.signed.targets. This update should fail. The tracer client’s state should not change other
    # than reporting the error in the has_error field along with a message in the error field of the next request.
    mock_send_request.return_value = MOCK_AGENT_RESPONSES[12]
    rc_client.request()
    expected_response = _expected_payload(
        rc_client,
        targets_version=12,
        config_states=[
            {"id": "ASM_FEATURES-second", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
            {"id": "ASM_FEATURES-third", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
            {"id": "ASM_FEATURES-base", "version": 2, "product": "ASM_FEATURES", "apply_state": 2},
        ],
        backend_client_state="eyJmb28iOiAiYmFyIn0=",
        cached_target_files=[
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-second/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            },
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-third/testname",
                "length": 41,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "1e4a75edfd5f65e0adec704cc7c32f79987117d84755544ae4905045cdb0a443",
                    }
                ],
            },
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config",
                "length": 48,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "a38ebf9fa256071f9823a5f512a84e8a786c8da2b0719452deeb5dc287ec990f",
                    }
                ],
            },
        ],
    )

    assert rc_client._last_error == "Not all client configurations have target files"
    _assert_response(mock_send_request, expected_response)

    asm_callback._poll_data()

    mock_preprocess_results.assert_not_called()
    mock_callback.assert_not_called()
    mock_write.assert_not_called()

    mock_preprocess_results.reset_mock()
    mock_send_request.reset_mock()
    mock_callback.reset_mock()
    mock_write.reset_mock()

    # 13. A new configuration file is missing the TUF metadata in targets.signed.targets but is referenced in
    # client_configs and target_files. This update should fail. The tracer client’s state should not change
    # other than reporting the error in the has_error field along with a message in the error field of the next
    # request.
    mock_send_request.return_value = MOCK_AGENT_RESPONSES[13]
    rc_client.request()
    expected_response = _expected_payload(
        rc_client,
        targets_version=12,
        has_errors=True,
        error_msg="Not all client configurations have target files",
        config_states=[
            {"id": "ASM_FEATURES-second", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
            {"id": "ASM_FEATURES-third", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
            {"id": "ASM_FEATURES-base", "version": 2, "product": "ASM_FEATURES", "apply_state": 2},
        ],
        backend_client_state="eyJmb28iOiAiYmFyIn0=",
        cached_target_files=[
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-second/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            },
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-third/testname",
                "length": 41,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "1e4a75edfd5f65e0adec704cc7c32f79987117d84755544ae4905045cdb0a443",
                    }
                ],
            },
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config",
                "length": 48,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "a38ebf9fa256071f9823a5f512a84e8a786c8da2b0719452deeb5dc287ec990f",
                    }
                ],
            },
        ],
    )

    assert rc_client._last_error == (
        "target file datadog/2/ASM_FEATURES/ASM_FEATURES-third/testname "
        "not exists in client_config and signed targets"
    )
    _assert_response(mock_send_request, expected_response)

    asm_callback._poll_data()

    mock_preprocess_results.assert_not_called()
    mock_callback.assert_not_called()
    mock_write.assert_not_called()

    mock_preprocess_results.reset_mock()
    mock_send_request.reset_mock()
    mock_callback.reset_mock()
    mock_write.reset_mock()

    # 14.
    mock_send_request.return_value = MOCK_AGENT_RESPONSES[13]
    rc_client.request()
    expected_response = _expected_payload(
        rc_client,
        targets_version=12,
        has_errors=True,
        error_msg=(
            "target file datadog/2/ASM_FEATURES/ASM_FEATURES-third/testname "
            "not exists in client_config and signed targets"
        ),
        config_states=[
            {"id": "ASM_FEATURES-second", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
            {"id": "ASM_FEATURES-third", "version": 1, "product": "ASM_FEATURES", "apply_state": 2},
            {"id": "ASM_FEATURES-base", "version": 2, "product": "ASM_FEATURES", "apply_state": 2},
        ],
        backend_client_state="eyJmb28iOiAiYmFyIn0=",
        cached_target_files=[
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-second/config",
                "length": 47,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                    }
                ],
            },
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-third/testname",
                "length": 41,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "1e4a75edfd5f65e0adec704cc7c32f79987117d84755544ae4905045cdb0a443",
                    }
                ],
            },
            {
                "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config",
                "length": 48,
                "hashes": [
                    {
                        "algorithm": "sha256",
                        "hash": "a38ebf9fa256071f9823a5f512a84e8a786c8da2b0719452deeb5dc287ec990f",
                    }
                ],
            },
        ],
    )

    assert rc_client._last_error == (
        "target file datadog/2/ASM_FEATURES/ASM_FEATURES-third/testname "
        "not exists in client_config and signed targets"
    )
    _assert_response(mock_send_request, expected_response)

    asm_callback._poll_data()

    mock_preprocess_results.assert_not_called()
    mock_callback.assert_not_called()
    mock_write.assert_not_called()

    mock_preprocess_results.reset_mock()
    mock_send_request.reset_mock()
    mock_callback.reset_mock()
    mock_write.reset_mock()


@mock.patch.object(RemoteConfigClient, "_send_request")
@mock.patch("ddtrace.appsec._capabilities._appsec_rc_capabilities")
def test_remote_config_client_callback_error(
    mock_appsec_rc_capabilities,
    mock_send_request,
):
    with open(MOCK_AGENT_RESPONSES_FILE, "r") as f:
        MOCK_AGENT_RESPONSES = json.load(f)

    def callback_with_exception():
        raise Exception("fake error")

    mock_appsec_rc_capabilities.return_value = "Ag=="

    rc_client = RemoteConfigClient()
    mock_callback = mock.mock.MagicMock()
    rc_client.register_product("ASM_FEATURES", callback_with_exception)

    with override_global_config(dict(_remote_config_enabled=False)):
        # 0.
        mock_send_request.return_value = MOCK_AGENT_RESPONSES[0]
        rc_client.request()
        expected_response = _expected_payload(rc_client)

        assert rc_client._last_error is None
        _assert_response(mock_send_request, expected_response)
        mock_callback.assert_not_called()
        mock_send_request.reset_mock()
        mock_callback.reset_mock()

        # 1. An update that doesn’t have any new config files but does have an updated TUF Targets file.
        # The tracer is supposed to process this update and store that the latest TUF Targets version is 1.
        mock_send_request.return_value = MOCK_AGENT_RESPONSES[1]
        rc_client.request()
        expected_response = _expected_payload(rc_client, targets_version=1, backend_client_state="eyJmb28iOiAiYmFyIn0=")

        assert rc_client._last_error is None
        _assert_response(mock_send_request, expected_response)
        mock_send_request.reset_mock()
        mock_callback.reset_mock()

        # 2. A single configuration for the product is added. (“base”)
        mock_send_request.return_value = MOCK_AGENT_RESPONSES[2]
        rc_client.request()
        expected_response = _expected_payload(
            rc_client,
            targets_version=2,
            config_states=[
                {
                    "id": "ASM_FEATURES-base",
                    "version": 1,
                    "product": "ASM_FEATURES",
                    "apply_state": 3,
                    "apply_error": "Failed to apply configuration "
                    "ConfigMetadata(id='ASM_FEATURES-base', product_name='ASM_FEATURES', "
                    "sha256_hash='9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6', "
                    "length=47, "
                    "tuf_version=1, "
                    "apply_state=1, "
                    "apply_error=None) for "
                    "product "
                    "'ASM_FEATURES'",
                }
            ],
            backend_client_state="eyJmb28iOiAiYmFyIn0=",
            cached_target_files=[
                {
                    "path": "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config",
                    "length": 47,
                    "hashes": [
                        {
                            "algorithm": "sha256",
                            "hash": "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6",
                        }
                    ],
                }
            ],
        )

        _assert_response(mock_send_request, expected_response)
