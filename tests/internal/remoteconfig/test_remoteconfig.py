# -*- coding: utf-8 -*-
import base64
import datetime
import hashlib
import json
import os
import sys
from unittest.mock import patch

import mock
import pytest

from ddtrace._trace.product import _convert_rc_trace_sampling_rules
from ddtrace._trace.sampler import DatadogSampler
from ddtrace._trace.sampling_rule import SamplingRule
from ddtrace.internal.process_tags import ENTRYPOINT_BASEDIR_TAG
from ddtrace.internal.process_tags import ENTRYPOINT_NAME_TAG
from ddtrace.internal.process_tags import ENTRYPOINT_TYPE_SCRIPT
from ddtrace.internal.process_tags import ENTRYPOINT_TYPE_TAG
from ddtrace.internal.process_tags import ENTRYPOINT_WORKDIR_TAG
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import RCCallback
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.constants import ASM_FEATURES_PRODUCT
from ddtrace.internal.remoteconfig.constants import REMOTE_CONFIG_AGENT_ENDPOINT
from ddtrace.internal.remoteconfig.products.apm_tracing import APMTracingCallback
from ddtrace.internal.remoteconfig.products.apm_tracing import config_key
from ddtrace.internal.remoteconfig.worker import RemoteConfigPoller
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.service import ServiceStatus
from tests.internal.test_utils_version import _assert_and_get_version_agent_format
from tests.utils import override_global_config
from tests.utils import process_tag_reload


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
    class MockCallback(RCCallback):
        def __call__(self, payloads):
            pass

    mock_callback = MockCallback()
    remoteconfig_poller.disable()
    with override_global_config(dict(_remote_config_enabled=True)):
        assert remoteconfig_poller.status == ServiceStatus.STOPPED

        remoteconfig_poller.register_callback("LIVE_DEBUGGER", mock_callback)
        remoteconfig_poller.enable_product("LIVE_DEBUGGER")

        assert remoteconfig_poller.status == ServiceStatus.RUNNING
        assert remoteconfig_poller._client._product_callbacks["LIVE_DEBUGGER"] is not None

        remoteconfig_poller.disable()


def test_remote_config_register_validate_rc_disabled(remote_config_worker):
    remoteconfig_poller.disable()

    class MockCallback(RCCallback):
        def __call__(self, payloads):
            pass

    assert remoteconfig_poller.status == ServiceStatus.STOPPED

    with override_global_config(dict(_remote_config_enabled=False)):
        remoteconfig_poller.register_callback("LIVE_DEBUGGER", MockCallback())

        assert remoteconfig_poller.status == ServiceStatus.STOPPED


def test_remote_config_enable_validate_rc_disabled(remote_config_worker):
    remoteconfig_poller.disable()
    assert remoteconfig_poller.status == ServiceStatus.STOPPED

    with override_global_config(dict(_remote_config_enabled=False)):
        remoteconfig_poller.enable()

        assert remoteconfig_poller.status == ServiceStatus.STOPPED


@pytest.mark.subprocess(ddtrace_run=True, env=dict(DD_REMOTE_CONFIGURATION_ENABLED="true", PYTHONWARNINGS="ignore::DeprecationWarning:os"))
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

    with mock.patch.object(worker._client, "request", return_value=True):
        # mock request() to avoid flaky HTTP connection issues
        worker.periodic()

    # Check that the state is online if the agent supports remote config
    assert worker._state == worker._online if expected else worker._agent_check
    worker.stop_subscriber(True)
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

    assert bool(remoteconfig_poller._client._product_callbacks.get("APM_TRACING")) == rc_enabled
    assert bool(remoteconfig_poller._client._product_callbacks.get("AGENT_CONFIG")) == rc_enabled
    assert bool(remoteconfig_poller._client._product_callbacks.get("AGENT_TASK")) == rc_enabled


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
                    service=None,
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
                    tags=None,
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
                    resource=None,
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
                    name=None,
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
    trace_sampling_rules = _convert_rc_trace_sampling_rules(
        {"tracing_sampling_rate": None, "tracing_sampling_rules": rc_rules}
    )

    assert trace_sampling_rules == expected_config_rules
    if trace_sampling_rules is not None:
        sampler = DatadogSampler()
        sampler.set_sampling_rules(trace_sampling_rules)
        assert sampler.rules == expected_sampling_rules


def test_apm_tracing_precedence_ordering(remote_config_worker):
    """Test that APM tracing configurations are applied in correct precedence order"""
    from ddtrace import config
    from ddtrace.internal.remoteconfig.products.apm_tracing import APMTracingCallback
    from ddtrace.internal.remoteconfig.products.apm_tracing import config_key
    from tests.utils import remote_config_build_payload as build_payload

    # Create an APM tracing callback instance
    callback = APMTracingCallback()

    # Mock current service and env
    original_service = config.service
    original_env = config.env
    config.service = "test-service"
    config.env = "test-env"

    try:
        # Create payloads with different levels of specificity
        # 1. Service-specific (highest precedence)
        service_payload = build_payload(
            "APM_TRACING",
            {
                "service_target": {"service": "test-service", "env": "*"},
                "lib_config": {"tracing_enabled": "service_config"},
            },
            "config1",
        )

        # 2. Environment-specific
        env_payload = build_payload(
            "APM_TRACING",
            {"service_target": {"service": "*", "env": "test-env"}, "lib_config": {"tracing_enabled": "env_config"}},
            "config2",
        )

        # 3. Cluster target
        cluster_payload = build_payload(
            "APM_TRACING",
            {"k8s_target_v2": {"cluster": "test-cluster"}, "lib_config": {"tracing_enabled": "cluster_config"}},
            "config3",
        )

        # 4. Wildcard (lowest precedence)
        wildcard_payload = build_payload(
            "APM_TRACING",
            {"service_target": {"service": "*", "env": "*"}, "lib_config": {"tracing_enabled": "wildcard_config"}},
            "config4",
        )

        # Test the config_key function for correct ordering
        assert config_key(service_payload) > config_key(env_payload)
        assert config_key(env_payload) > config_key(cluster_payload)
        assert config_key(cluster_payload) > config_key(wildcard_payload)

        # Send all payloads to the callback
        all_payloads = [service_payload, env_payload, cluster_payload, wildcard_payload]
        # Get the chained configuration
        chained_config = callback._process_payloads(all_payloads)

        # The first (highest precedence) config should be from the service-specific payload
        assert chained_config["tracing_enabled"] == "service_config"

        # Test that removing the service-specific config promotes the env config
        chained_config = callback._process_payloads([env_payload, cluster_payload, wildcard_payload])
        assert chained_config["tracing_enabled"] == "env_config"

        # Test that removing the env config promotes the cluster config
        chained_config = callback._process_payloads([cluster_payload, wildcard_payload])
        assert chained_config["tracing_enabled"] == "cluster_config"

        # Test that only wildcard config remains at the end
        chained_config = callback._process_payloads([wildcard_payload])
        assert chained_config["tracing_enabled"] == "wildcard_config"

    finally:
        # Restore original config
        config.service = original_service
        config.env = original_env


def test_apm_tracing_sampling_rules_none_override(remote_config_worker):
    """Test that setting tracing_sampling_rules to None correctly removes previously set sampling rules"""
    from ddtrace import config
    from ddtrace.internal.remoteconfig.products.apm_tracing import APMTracingCallback
    from tests.utils import remote_config_build_payload as build_payload

    # Test constants
    TEST_SERVICE = "test-service"
    rc_sampling_rule_rate_customer = 0.8
    rc_sampling_rule_rate_dynamic = 0.5

    # Create an APM tracing callback instance
    callback = APMTracingCallback()

    # Mock current service and env
    original_service = config.service
    original_env = config.env
    config.service = "test-service"
    config.env = "test-env"

    try:
        # Create payload with sampling rules
        sampling_rules_payload = build_payload(
            "APM_TRACING",
            {
                "service_target": {"service": TEST_SERVICE, "env": "*"},
                "lib_config": {
                    "tracing_sampling_rules": [
                        {
                            "sample_rate": rc_sampling_rule_rate_customer,
                            "service": TEST_SERVICE,
                            "resource": "*",
                            "provenance": "customer",
                        },
                        {
                            "sample_rate": rc_sampling_rule_rate_dynamic,
                            "service": "*",
                            "resource": "*",
                            "provenance": "dynamic",
                        },
                    ]
                },
            },
            "sampling_rules_config",
        )

        chained_config = callback._process_payloads([sampling_rules_payload])
        assert "tracing_sampling_rules" in chained_config
        sampling_rules = chained_config["tracing_sampling_rules"]
        assert len(sampling_rules) == 2
        assert sampling_rules[0]["sample_rate"] == rc_sampling_rule_rate_customer
        assert sampling_rules[0]["service"] == TEST_SERVICE
        assert sampling_rules[0]["provenance"] == "customer"
        assert sampling_rules[1]["sample_rate"] == rc_sampling_rule_rate_dynamic
        assert sampling_rules[1]["service"] == "*"
        assert sampling_rules[1]["provenance"] == "dynamic"

        # Create payload that sets sampling rules to None
        none_sampling_rules_payload = build_payload(
            "APM_TRACING",
            {
                "service_target": {"service": TEST_SERVICE, "env": "*"},
                "lib_config": {"tracing_sampling_rules": None},
            },
            "none_sampling_rules_config",
        )

        # Same service_target → tie-break by payload index: higher index sorts first in ChainMap
        # (see APMTracingCallback._get_chained_lib_config), so put the None override last.
        chained_config = callback._process_payloads([sampling_rules_payload, none_sampling_rules_payload])
        assert "tracing_sampling_rules" in chained_config
        assert chained_config["tracing_sampling_rules"] is None

    finally:
        # Restore original config
        config.service = original_service
        config.env = original_env


def test_remote_config_payload_includes_process_tags():
    with (
        patch.object(sys, "argv", ["/path/to/test_script.py"]),
        patch.object(os, "getcwd", return_value="/path/to/workdir"),
    ):
        process_tag_reload()

        client = RemoteConfigClient()
        payload = client._build_payload({})

        assert "process_tags" in payload["client"]["client_tracer"]

        process_tags = payload["client"]["client_tracer"]["process_tags"]

        assert isinstance(process_tags, list)
        assert f"{ENTRYPOINT_BASEDIR_TAG}:to" in process_tags
        assert f"{ENTRYPOINT_NAME_TAG}:test_script" in process_tags
        assert f"{ENTRYPOINT_TYPE_TAG}:{ENTRYPOINT_TYPE_SCRIPT}" in process_tags
        assert f"{ENTRYPOINT_WORKDIR_TAG}:workdir" in process_tags


def test_callback_error_isolation():
    """
    Test that when one product's callback throws an exception, it's caught and logged,
    and other products still receive their payloads (error isolation).
    """
    rc_client = RemoteConfigClient()

    # Track which callbacks were called
    callback_calls = {"PRODUCT_A": [], "PRODUCT_B": []}

    def failing_callback(payloads):
        """Callback that always throws an exception"""
        callback_calls["PRODUCT_A"].append(payloads)
        raise ValueError("Simulated callback error")

    def working_callback(payloads):
        """Callback that works correctly"""
        callback_calls["PRODUCT_B"].append(payloads)

    # Register both products
    rc_client.register_callback("PRODUCT_A", failing_callback)
    rc_client.register_callback("PRODUCT_B", working_callback)

    # Create payloads for both products
    payloads = [
        Payload(
            metadata=ConfigMetadata(
                id="config-a",
                product_name="PRODUCT_A",
                sha256_hash="hash1",
                length=10,
                tuf_version=1,
                apply_state=2,
                apply_error=None,
            ),
            path="test/PRODUCT_A/config-a",
            content={"test": "data-a"},
        ),
        Payload(
            metadata=ConfigMetadata(
                id="config-b",
                product_name="PRODUCT_B",
                sha256_hash="hash2",
                length=10,
                tuf_version=1,
                apply_state=2,
                apply_error=None,
            ),
            path="test/PRODUCT_B/config-b",
            content={"test": "data-b"},
        ),
    ]

    # Dispatch should catch the error from PRODUCT_A but still call PRODUCT_B
    rc_client._dispatch_to_products(payloads)

    # Verify both callbacks were called despite PRODUCT_A failing
    assert len(callback_calls["PRODUCT_A"]) == 1
    assert len(callback_calls["PRODUCT_B"]) == 1

    # Verify each received the correct payload
    assert callback_calls["PRODUCT_A"][0][0].metadata.product_name == "PRODUCT_A"
    assert callback_calls["PRODUCT_B"][0][0].metadata.product_name == "PRODUCT_B"


def test_apm_config_precedence():
    """
    Test that APM tracing configs are applied in the correct precedence order.
    More specific configs (service+env) should take precedence over wildcards.
    """
    # Create a callback with its own state
    apm_callback = APMTracingCallback()

    # Test precedence calculation using config_key with Payload objects
    # Higher precedence = more specific targeting
    def make_payload(service, env):
        return Payload(
            metadata=ConfigMetadata(
                id="test",
                product_name="APM_TRACING",
                sha256_hash="hash",
                length=10,
                tuf_version=1,
            ),
            path="test/path",
            content={
                "lib_config": {},
                "service_target": {"service": service, "env": env},
            },
        )

    precedence_wildcard = config_key(make_payload("*", "*"))
    precedence_service_only = config_key(make_payload("my-service", "*"))
    precedence_env_only = config_key(make_payload("*", "prod"))
    precedence_both = config_key(make_payload("my-service", "prod"))

    # More specific should have higher precedence value
    assert precedence_wildcard < precedence_service_only
    assert precedence_wildcard < precedence_env_only
    assert precedence_service_only < precedence_both
    assert precedence_env_only < precedence_both

    # Test that configs are applied in precedence order
    with override_global_config(dict(service="my-service", env="prod")):
        # Create payloads with different precedence levels
        payloads = [
            Payload(
                metadata=ConfigMetadata(
                    id="config-wildcard",
                    product_name="APM_TRACING",
                    sha256_hash="hash1",
                    length=10,
                    tuf_version=1,
                ),
                path="datadog/2/APM_TRACING/wildcard/config",
                content={
                    "lib_config": {"tracing_sampling_rate": 0.1},
                    "service_target": {"service": "*", "env": "*"},
                },
            ),
            Payload(
                metadata=ConfigMetadata(
                    id="config-specific",
                    product_name="APM_TRACING",
                    sha256_hash="hash2",
                    length=10,
                    tuf_version=1,
                ),
                path="datadog/2/APM_TRACING/specific/config",
                content={
                    "lib_config": {"tracing_sampling_rate": 0.9},
                    "service_target": {"service": "my-service", "env": "prod"},
                },
            ),
        ]

        # Mock the dispatch to capture what gets sent
        # Need to patch where dispatch is imported in apm_tracing module
        with mock.patch("ddtrace.internal.remoteconfig.products.apm_tracing.dispatch") as mock_dispatch:
            apm_callback(payloads)

            # Verify dispatch was called
            mock_dispatch.assert_called_once()
            call_args = mock_dispatch.call_args[0]

            # The lib_config should have the more specific value (0.9) taking precedence
            lib_config = call_args[1][0]
            assert lib_config["tracing_sampling_rate"] == 0.9


def test_apm_sampling_rules_override():
    """
    Test that APM tracing sampling rules can be properly overridden via remote config.
    """
    with override_global_config(dict(service="test-service", env="test")):
        # Create a callback with its own state (must be created inside override context)
        apm_callback = APMTracingCallback()

        # Create a payload with sampling rules
        payloads = [
            Payload(
                metadata=ConfigMetadata(
                    id="sampling-config",
                    product_name="APM_TRACING",
                    sha256_hash="hash1",
                    length=10,
                    tuf_version=1,
                ),
                path="datadog/2/APM_TRACING/sampling/config",
                content={
                    "lib_config": {
                        "tracing_sampling_rate": 0.5,
                        "tracing_sampling_rules": [
                            {"service": "test-service", "sample_rate": 1.0},
                            {"service": "other-service", "sample_rate": 0.1},
                        ],
                    },
                    "service_target": {"service": "test-service", "env": "test"},
                },
            ),
        ]

        # Mock the dispatch to capture what gets sent
        # Need to patch where dispatch is imported in apm_tracing module
        with mock.patch("ddtrace.internal.remoteconfig.products.apm_tracing.dispatch") as mock_dispatch:
            apm_callback(payloads)

            # Verify dispatch was called with the sampling rules
            mock_dispatch.assert_called_once()
            call_args = mock_dispatch.call_args[0]

            lib_config = call_args[1][0]
            assert lib_config["tracing_sampling_rate"] == 0.5
            assert "tracing_sampling_rules" in lib_config
            assert len(lib_config["tracing_sampling_rules"]) == 2

        # Test that sending an empty payload removes the rules
        empty_payloads = []

        with mock.patch("ddtrace.internal.remoteconfig.products.apm_tracing.dispatch") as mock_dispatch:
            apm_callback(empty_payloads)

            # Verify dispatch was called
            if mock_dispatch.call_count > 0:
                # Config should be empty or default
                call_args = mock_dispatch.call_args[0]
                lib_config = call_args[1][0]
                # After removing configs, we should get an empty dict
                assert isinstance(lib_config, dict)


@pytest.mark.subprocess(env={"DD_REMOTE_CONFIGURATION_ENABLED": "false"})
def test_apm_tracing_rc_handlers_dispatched_from_products():
    """APMTracingCallback.dispatch routes lib_config to handlers registered via on()."""
    from ddtrace.internal.core.event_hub import on
    from ddtrace.internal.remoteconfig import ConfigMetadata
    from ddtrace.internal.remoteconfig import Payload
    from ddtrace.internal.remoteconfig.products.apm_tracing import APMTracingCallback

    received: list = []

    def fake_handler(lib_config, dd_config):
        received.append(dict(lib_config))

    on("apm-tracing.rc", fake_handler, "test-product")
    callback = APMTracingCallback()
    payload = Payload(
        metadata=ConfigMetadata(
            id="cfg1",
            product_name="APM_TRACING",
            sha256_hash="abc123",
            length=10,
            tuf_version=1,
        ),
        path="datadog/2/APM_TRACING/cfg1/hash",
        content={
            "service_target": {"service": "*", "env": "*"},
            "lib_config": {"llmobs": {"enabled": True, "ml_app_name": "my-app"}},
        },
    )
    callback([payload])

    assert len(received) == 1
    assert received[0]["llmobs"] == {"enabled": True, "ml_app_name": "my-app"}


@pytest.mark.subprocess(env={"DD_REMOTE_CONFIGURATION_ENABLED": "false"})
def test_apm_tracing_same_config_id_delete_is_not_skipped():
    """A content=None payload with the same config_id as a prior content payload deletes it.

    Verifies via the dispatch→handler chain: the deletion payload must not be skipped,
    so the lib_config reaching downstream handlers is empty (not the content from the
    first payload).
    """
    import ddtrace
    from ddtrace._trace.product import apm_tracing_rc as tracer_rc
    from ddtrace.internal.core.event_hub import on
    from ddtrace.internal.remoteconfig import ConfigMetadata
    from ddtrace.internal.remoteconfig import Payload
    from ddtrace.internal.remoteconfig.products.apm_tracing import APMTracingCallback

    # Register the real tracer handler so config changes are applied to ddtrace.config
    on("apm-tracing.rc", tracer_rc, "tracer")

    shared_metadata = dict(
        product_name="APM_TRACING",
        sha256_hash="abc123",
        length=10,
        tuf_version=1,
    )

    payload_with_content = Payload(
        metadata=ConfigMetadata(id="cfg1", **shared_metadata),
        path="datadog/2/APM_TRACING/cfg1/hash",
        content={
            "service_target": {"service": "*", "env": "*"},
            "lib_config": {"tracing_enabled": False},
        },
    )
    payload_deleted = Payload(
        metadata=ConfigMetadata(id="cfg1", **shared_metadata),
        path="datadog/2/APM_TRACING/cfg1/hash",
        content=None,
    )

    APMTracingCallback()([payload_with_content, payload_deleted])

    # deletion was processed — tracer handler received empty lib_config,
    # so tracing_enabled=False from the first payload must not have been applied
    assert ddtrace.config._tracing_enabled is True, (
        "content=None payload was skipped — tracing_enabled=False from the first payload leaked through"
    )


@pytest.mark.subprocess(env={"DD_REMOTE_CONFIGURATION_ENABLED": "false"})
def test_apm_tracing_start_collects_product_apm_capabilities():
    """start() aggregates APMCapabilities from all registered products into RC registration."""
    import enum
    import types

    import mock

    from ddtrace.internal.remoteconfig.products import apm_tracing

    class FakeCapabilities(enum.IntFlag):
        MY_CAP = 1 << 10

    fake_product = types.SimpleNamespace(APMCapabilities=FakeCapabilities, apm_tracing_rc=None)

    with mock.patch("ddtrace.internal.products.manager") as mock_manager:
        mock_manager.__products__ = {"fake": fake_product}
        with mock.patch("ddtrace.internal.remoteconfig.worker.remoteconfig_poller") as mock_poller:
            apm_tracing.start()

            mock_poller.register_callback.assert_called_once()
            _, kwargs = mock_poller.register_callback.call_args
            assert FakeCapabilities.MY_CAP in kwargs["capabilities"]


def test_send_request_retries_once_on_oserror():
    """First attempt raises OSError; the retry decorator should produce a successful second call."""
    client = RemoteConfigClient()
    attempts = []

    def fake_get_connection(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("timed out")
        mock_conn = mock.MagicMock()
        mock_conn.getresponse.return_value.status = 200
        mock_conn.getresponse.return_value.headers.get.return_value = None
        mock_conn.getresponse.return_value.read.return_value = b'{"ok": true}'
        return mock_conn

    with mock.patch("ddtrace.internal.agent.get_connection", side_effect=fake_get_connection):
        result = client._send_request("{}")

    assert len(attempts) == 2
    assert result == {"ok": True}


def test_online_tolerates_transient_failures_before_bouncing(remote_config_worker):
    """_online should stay in _online for N-1 consecutive failures, and bounce on the Nth."""
    worker = RemoteConfigPoller()
    worker._state = worker._online
    threshold = worker._MAX_CONSECUTIVE_FAILURES

    with mock.patch.object(worker._client, "request", return_value=False):
        for i in range(threshold - 1):
            worker._online()
            assert worker._state == worker._online, f"bounced too early at failure {i + 1}"
            assert worker._consecutive_failures == i + 1

        worker._online()
        assert worker._state == worker._agent_check
        assert worker._consecutive_failures == 0

    worker.stop_subscriber(True)
    worker.disable()
