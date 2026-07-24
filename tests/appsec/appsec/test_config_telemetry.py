"""Tests for AppSec configuration telemetry."""

import os

import pytest


@pytest.mark.parametrize(
    "env_var,value,expected_value",
    [
        ("DD_APPSEC_SCA_ENABLED", "true", True),
        ("DD_APPSEC_SCA_ENABLED", "True", True),
        ("DD_APPSEC_SCA_ENABLED", "1", True),
        ("DD_APPSEC_SCA_ENABLED", "false", False),
        ("DD_APPSEC_SCA_ENABLED", "False", False),
        ("DD_APPSEC_SCA_ENABLED", "0", False),
        ("DD_API_SECURITY_ENDPOINT_COLLECTION_ENABLED", "false", False),
        ("DD_API_SECURITY_ENDPOINT_COLLECTION_MESSAGE_LIMIT", "42", 42),
    ],
)
def test_app_started_event_configuration_override(
    test_agent_session, run_python_code_in_subprocess, env_var, value, expected_value
):
    """Assert that AppSec configuration overrides are included in telemetry."""
    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"
    env["DD_APPSEC_ENABLED"] = "true"
    env[env_var] = value
    _, stderr, status, _ = run_python_code_in_subprocess("import ddtrace.auto", env=env)
    assert status == 0, stderr

    configuration = test_agent_session.get_configurations(name=env_var, remove_seq_id=True, effective=True)
    assert len(configuration) == 1, configuration
    assert configuration[0] == {"name": env_var, "origin": "env_var", "value": expected_value}


def test_app_started_event_fleet_config_id(test_agent_session, run_python_code_in_subprocess, tmpdir):
    managed_config = tmpdir.join("application_monitoring.yaml")
    managed_config.write(
        """
config_id: sca-policy
apm_configuration_default:
  DD_APPSEC_SCA_ENABLED: true
"""
    )

    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"
    env["_DD_SC_MANAGED_FILE_OVERRIDE"] = str(managed_config)
    _, stderr, status, _ = run_python_code_in_subprocess("import ddtrace.auto", env=env)
    assert status == 0, stderr

    sca_configuration = test_agent_session.get_configurations(
        name="DD_APPSEC_SCA_ENABLED", remove_seq_id=True, effective=True
    )
    assert sca_configuration == [
        {
            "name": "DD_APPSEC_SCA_ENABLED",
            "origin": "fleet_stable_config",
            "value": True,
            "config_id": "sca-policy",
        }
    ]

    endpoint_configuration = test_agent_session.get_configurations(
        name="DD_API_SECURITY_ENDPOINT_COLLECTION_ENABLED", remove_seq_id=True, effective=True
    )
    assert endpoint_configuration == [
        {
            "name": "DD_API_SECURITY_ENDPOINT_COLLECTION_ENABLED",
            "origin": "default",
            "value": True,
        }
    ]


@pytest.mark.parametrize("onboarding_value", ["true", "false", "1", "0", ""])
def test_app_started_event_agentic_onboarding_reported_verbatim(
    test_agent_session, run_python_code_in_subprocess, onboarding_value
):
    """RFC-1113: DD_APPSEC_AGENTIC_ONBOARDING is reported verbatim as an ordinary config key."""
    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"
    env["DD_APPSEC_AGENTIC_ONBOARDING"] = onboarding_value
    _, stderr, status, _ = run_python_code_in_subprocess("import ddtrace.auto", env=env)
    assert status == 0, stderr

    configuration = test_agent_session.get_configurations(
        name="DD_APPSEC_AGENTIC_ONBOARDING", remove_seq_id=True, effective=True
    )
    assert len(configuration) == 1, configuration
    assert configuration[0] == {
        "name": "DD_APPSEC_AGENTIC_ONBOARDING",
        "origin": "env_var",
        "value": onboarding_value,
    }


def test_app_started_event_agentic_onboarding_absent(test_agent_session, run_python_code_in_subprocess):
    """RFC-1113: when unset, the key is still reported with an empty value and origin=default."""
    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"
    env["DD_APPSEC_ENABLED"] = "true"
    env.pop("DD_APPSEC_AGENTIC_ONBOARDING", None)
    _, stderr, status, _ = run_python_code_in_subprocess("import ddtrace.auto", env=env)
    assert status == 0, stderr

    configuration = test_agent_session.get_configurations(
        name="DD_APPSEC_AGENTIC_ONBOARDING", remove_seq_id=True, effective=True
    )
    assert len(configuration) == 1, configuration
    assert configuration[0] == {
        "name": "DD_APPSEC_AGENTIC_ONBOARDING",
        "origin": "default",
        "value": "",
    }
