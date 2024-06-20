# -*- coding: utf-8 -*-
import base64
import datetime
import hashlib
import json
import sys
from time import sleep

import mock
from mock.mock import ANY
import pytest

from ddtrace import config
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisherMergeDicts
from ddtrace.internal.remoteconfig._pubsub import PubSub
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.constants import ASM_FEATURES_PRODUCT
from ddtrace.internal.remoteconfig.constants import REMOTE_CONFIG_AGENT_ENDPOINT
from ddtrace.internal.remoteconfig.worker import RemoteConfigPoller
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.service import ServiceStatus
from ddtrace.sampler import DatadogSampler
from ddtrace.sampling_rule import SamplingRule
from tests.internal.test_utils_version import _assert_and_get_version_agent_format
from tests.utils import override_global_config


class RCMockPubSub(PubSub):
    __subscriber_class__ = RemoteConfigSubscriber
    __publisher_class__ = RemoteConfigPublisherMergeDicts
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self, _preprocess_results, callback):
        self._publisher = self.__publisher_class__(self.__shared_data__, _preprocess_results)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, callback, "TESTS")


def to_bytes(string):
    return bytes(string, encoding="utf-8")


def to_str(bytes_string):
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


def get_mock_encoded_msg_with_signed_errors(msg, path, signed_errors):
    data = {
        "signatures": [{"keyid": "", "sig": ""}],
        "signed": signed_errors,
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


def test_remote_config_register_auto_enable(remote_config_worker):
    # ASM_FEATURES product is enabled by default, but LIVE_DEBUGGER isn't
    class MockPubsub(PubSub):
        def stop(self, *args, **kwargs):
            pass

    mock_pubsub = MockPubsub()
    remoteconfig_poller.disable()
    with override_global_config(dict(_remote_config_enabled=True)):
        assert remoteconfig_poller.status == ServiceStatus.STOPPED

        remoteconfig_poller.register("LIVE_DEBUGGER", mock_pubsub)

        assert remoteconfig_poller.status == ServiceStatus.RUNNING
        assert remoteconfig_poller._client._products["LIVE_DEBUGGER"] is not None

        remoteconfig_poller.disable()


def test_remote_config_register_validate_rc_disabled(remote_config_worker):
    remoteconfig_poller.disable()

    class MockPubsub(PubSub):
        def stop(self, *args, **kwargs):
            pass

    assert remoteconfig_poller.status == ServiceStatus.STOPPED

    with override_global_config(dict(_remote_config_enabled=False)):
        remoteconfig_poller.register("LIVE_DEBUGGER", MockPubsub())

        assert remoteconfig_poller.status == ServiceStatus.STOPPED


def test_remote_config_enable_validate_rc_disabled(remote_config_worker):
    remoteconfig_poller.disable()
    assert remoteconfig_poller.status == ServiceStatus.STOPPED

    with override_global_config(dict(_remote_config_enabled=False)):
        remoteconfig_poller.enable()

        assert remoteconfig_poller.status == ServiceStatus.STOPPED


@pytest.mark.skipif(
    sys.version_info >= (3, 12, 0),
    reason="Python 3.12 subprocess will raise deprecation warning for forking in a multi-threaded process",
)
@pytest.mark.subprocess(env=dict(DD_REMOTE_CONFIGURATION_ENABLED="true"))
def test_remote_config_forksafe():
    import os

    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
    from ddtrace.internal.service import ServiceStatus

    remoteconfig_poller.enable()

    parent_worker = remoteconfig_poller
    assert parent_worker.status == ServiceStatus.RUNNING

    client_id = remoteconfig_poller._client.id
    runtime_id = remoteconfig_poller._client._client_tracer["runtime_id"]

    parent_payload = remoteconfig_poller._client._build_payload(None)

    assert client_id == parent_payload["client"]["id"]
    assert runtime_id == parent_payload["client"]["client_tracer"]["runtime_id"]

    if os.fork() == 0:
        assert remoteconfig_poller.status == ServiceStatus.RUNNING
        assert remoteconfig_poller._worker is not parent_worker

        child_payload = remoteconfig_poller._client._build_payload(None)

        assert client_id != child_payload["client"]["id"]
        assert runtime_id != child_payload["client"]["client_tracer"]["runtime_id"]
        exit(0)


# TODO: split this test into smaller tests that operate independently from each other
@mock.patch.object(RemoteConfigClient, "_send_request")
def test_remote_configuration_1_click(mock_send_request, remote_config_worker):
    class Callback:
        features = {}

        def _reload_features(self, features, test_tracer=None):
            self.features = features

    callback = Callback()
    with override_global_config(dict(_remote_config_enabled=True, _remote_config_poll_interval=0.5)):
        with RemoteConfigPoller() as rc:
            mock_send_request.return_value = get_mock_encoded_msg(b'{"asm":{"enabled":true}}')
            mock_pubsub = RCMockPubSub(None, callback._reload_features)
            rc.register(ASM_FEATURES_PRODUCT, mock_pubsub)

            rc._online()
            mock_send_request.assert_called()
            sleep(0.5)
            assert callback.features == {
                "config": {"asm": {"enabled": True}},
                "metadata": {},
                "shared_data_counter": ANY,
            }

    class Callback:
        features = {}

        def _reload_features(self, features, test_tracer=None):
            self.features = dict(features)

    callback = Callback()

    with override_global_config(dict(_remote_config_enabled=True, _remote_config_poll_interval=0.1)):
        mock_send_request.return_value = get_mock_encoded_msg(
            b'{"rules_data": [{"data": [{"expiration": 1662804872, "value": "127.0.0.0"}, '
            b'{"expiration": 1662804872, "value": "52.80.198.1"}], "id": "blocking_ips", '
            b'"type": "ip_with_expiration"}]}'
        )
        with RemoteConfigPoller() as rc:
            mock_pubsub = RCMockPubSub(None, callback._reload_features)
            rc.register(ASM_FEATURES_PRODUCT, mock_pubsub)
            rc._online()
            mock_send_request.assert_called()
            sleep(0.5)
            assert callback.features == {
                "config": {
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
                },
                "metadata": {},
                "shared_data_counter": ANY,
            }

    class Callback:
        features = {}

        def _reload_features(self, features, test_tracer=None):
            self.features = features

    callback = Callback()
    with override_global_config(dict(_remote_config_enabled=True, _remote_config_poll_interval=0.5)):
        with RemoteConfigPoller() as rc:
            msg = b'{"asm":{"enabled":true}}'
            expires_date = datetime.datetime.strftime(
                datetime.datetime.now() + datetime.timedelta(days=1), "%Y-%m-%dT%H:%M:%SZ"
            )
            path = "datadog/2/%s/asm_features_activation/config" % ASM_FEATURES_PRODUCT
            # Signed data without version `spec_version`
            signed_errors = {
                "_type": "targets",
                "custom": {"opaque_backend_state": ""},
                "expires": expires_date,
                "targets": {
                    path: {
                        "custom": {"c": [""], "v": 0},
                        "hashes": {"sha256": hashlib.sha256(msg).hexdigest()},
                        "length": 24,
                    }
                },
                "version": 0,
            }
            mock_send_request.return_value = get_mock_encoded_msg_with_signed_errors(msg, path, signed_errors)
            mock_pubsub = RCMockPubSub(None, callback._reload_features)
            rc.register(ASM_FEATURES_PRODUCT, mock_pubsub)
            rc._online()
            mock_send_request.assert_called()
            sleep(0.5)
            assert callback.features == {}
            assert rc._client._last_error.startswith("invalid agent payload received")

    class Callback:
        features = {}

        def _reload_features(self, features, test_tracer=None):
            self.features = features

    callback = Callback()
    with override_global_config(dict(_remote_config_enabled=True, _remote_config_poll_interval=0.5)):
        with RemoteConfigPoller() as rc:
            mock_pubsub = RCMockPubSub(None, callback._reload_features)
            rc.register(ASM_FEATURES_PRODUCT, mock_pubsub)
            for _ in range(0, 2):
                msg = b'{"asm":{"enabled":true}}'
                expires_date = datetime.datetime.strftime(
                    datetime.datetime.now() + datetime.timedelta(days=1), "%Y-%m-%dT%H:%M:%SZ"
                )
                path = "datadog/2/%s/asm_features_activation/config" % ASM_FEATURES_PRODUCT
                # Signed data without version `spec_version`
                signed_errors = {
                    "_type": "targets",
                    "custom": {"opaque_backend_state": ""},
                    "expires": expires_date,
                    "targets": {
                        path: {
                            "custom": {"c": [""], "v": 0},
                            "hashes": {"sha256": hashlib.sha256(msg).hexdigest()},
                            "length": 24,
                        }
                    },
                    "version": 0,
                }
                mock_send_request.return_value = get_mock_encoded_msg_with_signed_errors(msg, path, signed_errors)
                rc._online()
                mock_send_request.assert_called()
                sleep(0.5)
                assert callback.features == {}
                assert rc._client._last_error.startswith("invalid agent payload received")

            mock_send_request.return_value = get_mock_encoded_msg(b'{"asm":{"enabled":true}}')
            rc._online()
            mock_send_request.assert_called()
            sleep(0.5)
            assert rc._client._last_error is None
            assert callback.features == {
                "config": {"asm": {"enabled": True}},
                "metadata": {},
                "shared_data_counter": ANY,
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
def test_remote_configuration_check_remote_config_enable_in_agent_errors(
    mock_info, result, expected, remote_config_worker
):
    mock_info.return_value = result

    worker = RemoteConfigPoller()

    # Check that the initial state is agent_check
    assert worker._state == worker._agent_check

    worker.periodic()

    # Check that the state is online if the agent supports remote config
    assert worker._state == worker._online if expected else worker._agent_check
    worker.stop_subscribers(True)
    worker.disable()


@pytest.mark.subprocess(
    parametrize=dict(
        DD_REMOTE_CONFIGURATION_ENABLED=["1", "0"],
    ),
)
def test_rc_default_products_registered():
    """
    By default, RC should be enabled. When RC is enabled, we will always
    enable the tracer flare feature as well. There should be three products
    registered when DD_REMOTE_CONFIGURATION_ENABLED is True
    """
    import os

    from ddtrace.internal.utils.formats import asbool

    rc_enabled = asbool(os.environ.get("DD_REMOTE_CONFIGURATION_ENABLED"))

    # Import this to trigger the preload
    from ddtrace import config
    import ddtrace.auto  # noqa:F401

    assert config._remote_config_enabled == rc_enabled

    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    assert bool(remoteconfig_poller._client._products.get("APM_TRACING")) == rc_enabled
    assert bool(remoteconfig_poller._client._products.get("AGENT_CONFIG")) == rc_enabled
    assert bool(remoteconfig_poller._client._products.get("AGENT_TASK")) == rc_enabled


@pytest.mark.parametrize(
    "rc_rules,expected_config_rules,expected_sampling_rules",
    [
        (
            [  # Test with all fields
                {
                    "service": "my-service",
                    "name": "web.request",
                    "resource": "*",
                    "provenance": "dynamic",
                    "sample_rate": 1.0,
                    "tags": [{"key": "care_about", "value_glob": "yes"}, {"key": "region", "value_glob": "us-*"}],
                }
            ],
            '[{"service": "my-service", "name": "web.request", "resource": "*", "provenance": "dynamic",'
            ' "sample_rate": 1.0, "tags": {"care_about": "yes", "region": "us-*"}}]',
            [
                SamplingRule(
                    sample_rate=1.0,
                    service="my-service",
                    name="web.request",
                    resource="*",
                    tags={"care_about": "yes", "region": "us-*"},
                    provenance="dynamic",
                )
            ],
        ),
        (  # Test with no service
            [
                {
                    "name": "web.request",
                    "resource": "*",
                    "provenance": "customer",
                    "sample_rate": 1.0,
                    "tags": [{"key": "care_about", "value_glob": "yes"}, {"key": "region", "value_glob": "us-*"}],
                }
            ],
            '[{"name": "web.request", "resource": "*", "provenance": "customer", "sample_rate": 1.0, "tags": '
            '{"care_about": "yes", "region": "us-*"}}]',
            [
                SamplingRule(
                    sample_rate=1.0,
                    service=SamplingRule.NO_RULE,
                    name="web.request",
                    resource="*",
                    tags={"care_about": "yes", "region": "us-*"},
                    provenance="customer",
                )
            ],
        ),
        (
            # Test with no tags
            [
                {
                    "service": "my-service",
                    "name": "web.request",
                    "resource": "*",
                    "provenance": "customer",
                    "sample_rate": 1.0,
                }
            ],
            '[{"service": "my-service", "name": "web.request", "resource": "*", "provenance": '
            '"customer", "sample_rate": 1.0}]',
            [
                SamplingRule(
                    sample_rate=1.0,
                    service="my-service",
                    name="web.request",
                    resource="*",
                    tags=SamplingRule.NO_RULE,
                    provenance="customer",
                )
            ],
        ),
        (
            # Test with no resource
            [
                {
                    "service": "my-service",
                    "name": "web.request",
                    "provenance": "customer",
                    "sample_rate": 1.0,
                    "tags": [{"key": "care_about", "value_glob": "yes"}, {"key": "region", "value_glob": "us-*"}],
                }
            ],
            '[{"service": "my-service", "name": "web.request", "provenance": "customer", "sample_rate": 1.0, "tags":'
            ' {"care_about": "yes", "region": "us-*"}}]',
            [
                SamplingRule(
                    sample_rate=1.0,
                    service="my-service",
                    name="web.request",
                    resource=SamplingRule.NO_RULE,
                    tags={"care_about": "yes", "region": "us-*"},
                    provenance="customer",
                )
            ],
        ),
        (
            # Test with no name
            [
                {
                    "service": "my-service",
                    "resource": "*",
                    "provenance": "customer",
                    "sample_rate": 1.0,
                    "tags": [{"key": "care_about", "value_glob": "yes"}, {"key": "region", "value_glob": "us-*"}],
                }
            ],
            '[{"service": "my-service", "resource": "*", "provenance": "customer", "sample_rate": 1.0, "tags":'
            ' {"care_about": "yes", "region": "us-*"}}]',
            [
                SamplingRule(
                    sample_rate=1.0,
                    service="my-service",
                    name=SamplingRule.NO_RULE,
                    resource="*",
                    tags={"care_about": "yes", "region": "us-*"},
                    provenance="customer",
                )
            ],
        ),
        (
            # Test with no sample rate
            [
                {
                    "service": "my-service",
                    "name": "web.request",
                    "resource": "*",
                    "provenance": "customer",
                    "tags": [{"key": "care_about", "value_glob": "yes"}, {"key": "region", "value_glob": "us-*"}],
                }
            ],
            None,
            None,
        ),
        (
            # Test with no service, name, resource, tags
            [
                {
                    "provenance": "customer",
                    "sample_rate": 1.0,
                }
            ],
            None,
            None,
        ),
    ],
)
def test_trace_sampling_rules_conversion(rc_rules, expected_config_rules, expected_sampling_rules):
    trace_sampling_rules = config.convert_rc_trace_sampling_rules(rc_rules)

    assert trace_sampling_rules == expected_config_rules
    if trace_sampling_rules is not None:
        parsed_rules = DatadogSampler._parse_rules_from_str(trace_sampling_rules)
        assert parsed_rules == expected_sampling_rules
