# -*- coding: utf-8 -*-
import base64
import datetime
import hashlib
import json
import warnings

import mock
import pytest

from ddtrace.internal.compat import PY2
from ddtrace.internal.remoteconfig import RemoteConfig
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.constants import ASM_FEATURES_PRODUCT
from ddtrace.internal.remoteconfig.constants import REMOTE_CONFIG_AGENT_ENDPOINT
from ddtrace.internal.remoteconfig.worker import RemoteConfigWorker
from ddtrace.internal.remoteconfig.worker import get_poll_interval_seconds
from tests.internal.test_utils_version import _assert_and_get_version_agent_format
from tests.utils import override_env


def to_bytes(string):
    if PY2:
        return bytes(string)
    else:
        return bytes(string, encoding="utf-8")


def to_str(bytes_string):
    if PY2:
        return str(bytes_string)
    else:
        return str(bytes_string, encoding="utf-8")


def get_mock_encoded_msg(msg):
    expires_date = datetime.datetime.strftime(
        datetime.datetime.now() + datetime.timedelta(days=1), "%Y-%m-%dT%H:%M:%SZ"
    )
    path = "datadog/2/%s/asm_features_activation/config" % ASM_FEATURES_PRODUCT
    data = {
        "signatures": [{"keyid": "", "sig": ""}],
        "signed": {
            "_type": "targets",
            "custom": {"opaque_backend_state": ""},
            "expires": expires_date,
            "spec_version": "1.0.0",
            "targets": {
                path: {
                    "custom": {"c": [""], "v": 0},
                    "hashes": {"sha256": hashlib.sha256(msg).hexdigest()},
                    "length": 24,
                }
            },
            "version": 0,
        },
    }
    return {
        "roots": [
            to_str(
                base64.b64encode(
                    to_bytes(
                        json.dumps(
                            {
                                "signatures": [],
                                "signed": {
                                    "_type": "root",
                                    "consistent_snapshot": True,
                                    "expires": "1986-12-11T00:00:00Z",
                                    "keys": {},
                                    "roles": {},
                                    "spec_version": "1.0",
                                    "version": 2,
                                },
                            }
                        ),
                    )
                )
            )
        ],
        "targets": to_str(base64.b64encode(to_bytes(json.dumps(data)))),
        "target_files": [
            {
                "path": path,
                "raw": to_str(base64.b64encode(msg)),
            }
        ],
        "client_configs": [path],
    }


def test_remote_config_register_auto_enable():
    # ASM_FEATURES product is enabled by default, but LIVE_DEBUGGER isn't
    with override_env(dict(DD_REMOTE_CONFIGURATION_ENABLED="true")):
        assert RemoteConfig._worker is None

        RemoteConfig.register("LIVE_DEBUGGER", lambda m, c: None)

        assert RemoteConfig._worker._client._products["LIVE_DEBUGGER"] is not None

        RemoteConfig.disable()


def test_remote_config_register_validate_rc_disabled():
    assert RemoteConfig._worker is None

    with override_env(dict(DD_REMOTE_CONFIGURATION_ENABLED="false")):
        RemoteConfig.register("LIVE_DEBUGGER", lambda m, c: None)

        assert RemoteConfig._worker is None


def test_remote_config_enable_validate_rc_disabled():
    assert RemoteConfig._worker is None

    with override_env(dict(DD_REMOTE_CONFIGURATION_ENABLED="false")):
        RemoteConfig.enable()

        assert RemoteConfig._worker is None


@pytest.mark.subprocess
def test_remote_config_forksafe():
    import os

    from ddtrace.internal.remoteconfig import RemoteConfig
    from tests.utils import override_env

    with override_env(dict(DD_REMOTE_CONFIGURATION_ENABLED="true")):
        RemoteConfig.enable()

        parent_worker = RemoteConfig._worker
        assert parent_worker is not None

        if os.fork() == 0:
            assert RemoteConfig._worker is not None
            assert RemoteConfig._worker is not parent_worker
            exit(0)


@mock.patch.object(RemoteConfigClient, "_send_request")
def test_remote_configuration_1_click(mock_send_request):
    class Callback:
        features = {}

        def _reload_features(self, metadata, features):
            self.features = features

    callback = Callback()
    with override_env(dict(DD_REMOTE_CONFIGURATION_ENABLED="true")):
        with RemoteConfig() as rc:
            mock_send_request.return_value = get_mock_encoded_msg(b'{"asm":{"enabled":true}}')
            rc.register(ASM_FEATURES_PRODUCT, callback._reload_features)
            rc._worker._online()
            mock_send_request.assert_called()
            assert callback.features == {"asm": {"enabled": True}}


def test_remote_configuration_check_deprecated_var():
    with override_env(dict(DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS="0.1")):
        with warnings.catch_warnings(record=True) as capture:
            get_poll_interval_seconds()
            assert len(capture) == 0


def test_remote_configuration_check_deprecated_var_message():
    with override_env(dict(DD_REMOTECONFIG_POLL_SECONDS="0.1")):
        with warnings.catch_warnings(record=True) as capture:
            get_poll_interval_seconds()
            assert len(capture) == 1
            assert str(capture[0].message).startswith("Using environment")


def test_remote_configuration_check_deprecated_override():
    with override_env(dict(DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS="0.1", DD_REMOTECONFIG_POLL_SECONDS="0.5")):
        with warnings.catch_warnings(record=True) as capture:
            assert get_poll_interval_seconds() == 0.1
            assert len(capture) == 1
            assert str(capture[0].message).startswith("Using environment")


@mock.patch.object(RemoteConfigClient, "_send_request")
def test_remote_configuration_ip_blocking(mock_send_request):
    class Callback:
        features = {}

        def _reload_features(self, metadata, features):
            self.features = features

    callback = Callback()

    with override_env(dict(DD_REMOTE_CONFIGURATION_ENABLED="true", DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS="0.1")):
        mock_send_request.return_value = get_mock_encoded_msg(
            b'{"rules_data": [{"data": [{"expiration": 1662804872, "value": "127.0.0.0"}, '
            b'{"expiration": 1662804872, "value": "52.80.198.1"}], "id": "blocking_ips", '
            b'"type": "ip_with_expiration"}]}'
        )
        with RemoteConfig() as rc:
            rc.register(ASM_FEATURES_PRODUCT, callback._reload_features)
            rc._worker._online()
            assert callback.features == {
                "rules_data": [
                    {
                        "data": [
                            {"expiration": 1662804872, "value": "127.0.0.0"},
                            {"expiration": 1662804872, "value": "52.80.198.1"},
                        ],
                        "id": "blocking_ips",
                        "type": "ip_with_expiration",
                    }
                ]
            }


def test_remoteconfig_semver():
    _assert_and_get_version_agent_format(RemoteConfigClient()._client_tracer["tracer_version"])


@pytest.mark.parametrize(
    "result,expected",
    [
        (None, False),
        ({}, False),
        ({"endpoints": []}, False),
        ({"endpoints": ["/info"]}, False),
        ({"endpoints": ["/info", "/errors"]}, False),
        ({"endpoints": ["/info", "/errors", REMOTE_CONFIG_AGENT_ENDPOINT]}, True),
        ({"endpoints": ["/info", "/errors", "/" + REMOTE_CONFIG_AGENT_ENDPOINT]}, True),
    ],
)
@mock.patch("ddtrace.internal.agent.info")
def test_remote_configuration_check_remote_config_enable_in_agent_errors(mock_info, result, expected):
    mock_info.return_value = result

    worker = RemoteConfigWorker()

    # Check that the initial state is agent_check
    assert worker._state == worker._agent_check

    worker.periodic()

    # Check that the state is online if the agent supports remote config
    assert worker._state == worker._online if expected else worker._agent_check
