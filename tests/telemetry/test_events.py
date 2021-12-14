from ddtrace.internal.telemetry.data import create_integration
from ddtrace.internal.telemetry.data import Dependency
from ddtrace.internal.telemetry.events import AppClosedEvent
from ddtrace.internal.telemetry.events import AppIntegrationsChangedEvent
from ddtrace.internal.telemetry.events import AppStartedEvent


def test_app_started_event():
    """validates the fields of AppStartedEvent typed dict"""

    dependencies = [Dependency(name="dependency", version="0.0.0"), Dependency(name="dependency2", version="0.1.0")]
    configurations = {"DD_PARTIAL_FLUSHING_ENABLED": True}

    asp = AppStartedEvent(dependencies=dependencies, configurations=configurations)

    assert asp == {"dependencies": dependencies, "configurations": configurations}


def test_app_closed_event():
    """validates the fields of AppClosedEvent typed dict"""

    ace = AppClosedEvent()

    assert ace == {}


def test_app_integrations_changed_event():
    """validates the fields of AppIntegrationsChangedEvent typed dict"""

    integrations = [create_integration("integration-1"), create_integration("integration-2")]
    ace = AppIntegrationsChangedEvent(integrations=integrations)

    assert ace == {"integrations": integrations}
