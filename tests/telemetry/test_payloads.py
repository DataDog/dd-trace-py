import pytest

from ddtrace.internal.telemetry.data.application import get_version
from ddtrace.internal.telemetry.data.integration import create_integration
from ddtrace.internal.telemetry.data.payload import AppClosedPayload
from ddtrace.internal.telemetry.data.payload import AppIntegrationsChangedPayload
from ddtrace.internal.telemetry.data.payload import AppStartedPayload
from ddtrace.internal.telemetry.data.payload import Payload


def test_payload_request_type():
    """validates the return value of Payload.request_type"""
    assert AppClosedPayload().request_type() == "app-closed"
    assert AppStartedPayload().request_type() == "app-started"
    assert AppIntegrationsChangedPayload([]).request_type() == "app-integrations-changed"


def test_app_started_payload_to_dict():
    """validates the return value of AppStartedPayload.to_dict"""
    asp = AppStartedPayload()
    assert asp.to_dict() == {"dependencies": asp.dependencies}


def test_app_closed_payload_to_dict():
    """validates the return value of AppClosedPayload.to_dict"""
    assert AppClosedPayload().to_dict() == {}


def test_app_integrations_changed_to_dict():
    """validates the return value of AppIntegrationsChangedPayload.to_dict"""
    integrations = [
        create_integration("integration-1", "0.0.0", False, False, "no", "error"),
    ]
    payload = AppIntegrationsChangedPayload(integrations)

    assert len(payload.integrations) == 1
    assert payload.to_dict() == {
        "integrations": [
            {
                "name": "integration-1",
                "version": "0.0.0",
                "enabled": False,
                "auto_enabled": False,
                "compatible": "no",
                "error": "error",
            },
        ],
    }


def test_app_started_payload_dependencies():
    """
    validates that dependencies are set after an AppStartedPayload object
    is created
    """
    payload = AppStartedPayload()

    assert len(payload.dependencies) > 0

    for dep in payload.dependencies:
        assert "name" in dep
        assert "version" in dep

        assert dep["name"]
        assert dep["version"]


def test_integrations_changed_payload_integrations():
    """
    validates that integrations are set after an AppIntegrationsChangedPayload
    object is created
    """
    integrations = [
        create_integration("integration-1"),
        create_integration("integration-2"),
    ]
    payload = AppIntegrationsChangedPayload(integrations)

    assert len(payload.integrations) == 2

    assert "name" in payload.integrations[0]
    assert payload.integrations[0]["name"] == "integration-1"

    assert "name" in payload.integrations[1]
    assert payload.integrations[1]["name"] == "integration-2"


def test_base_payload():
    """validates that the base Payload class can not be instantiated"""
    with pytest.raises(TypeError) as type_err:
        Payload()

    assert "Can't instantiate abstract class Payload with abstract methods request_type, to_dict" in str(type_err)
