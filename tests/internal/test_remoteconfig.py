# -*- coding: utf-8 -*-
import base64
import datetime
import hashlib
import json
import re
from time import sleep

import mock
import pytest

from ddtrace.internal.compat import PY2
from ddtrace.internal.remoteconfig import RemoteConfig
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.constants import ASM_FEATURES_PRODUCT
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
    assert RemoteConfig._worker is None

    RemoteConfig.register("LIVE_DEBUGGER", lambda m, c: None)

    assert RemoteConfig._worker._client._products["LIVE_DEBUGGER"] is not None

    RemoteConfig.disable()

    assert RemoteConfig._worker is None


@pytest.mark.subprocess
def test_remote_config_forksafe():
    import os

    from ddtrace.internal.remoteconfig import RemoteConfig

    RemoteConfig.enable()

    parent_worker = RemoteConfig._worker
    assert parent_worker is not None

    if os.fork() == 0:
        assert RemoteConfig._worker is not None
        assert RemoteConfig._worker is not parent_worker
        exit(0)


@mock.patch.object(RemoteConfigClient, "_send_request")
def test_remote_configuration(mock_send_request):
    class Callback:
        features = {}

        def _reload_features(self, metadata, features):
            self.features = features

    callback = Callback()

    with override_env(dict(DD_REMOTECONFIG_POLL_SECONDS="0.1")):
        mock_send_request.return_value = get_mock_encoded_msg(b'{"asm":{"enabled":true}}')
        rc = RemoteConfig()
        rc.register(ASM_FEATURES_PRODUCT, callback._reload_features)
        sleep(0.2)
        mock_send_request.assert_called_once()
        assert callback.features == {"asm": {"enabled": True}}


def test_remoteconfig_semver():
    assert re.match(
        r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*["
        r"a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$",
        RemoteConfigClient()._client_tracer["tracer_version"],
    )
