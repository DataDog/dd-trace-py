from ddtrace.internal.telemetry.data.application import get_version
from ddtrace.internal.telemetry.data.dependency import Dependency
from ddtrace.internal.telemetry.data.integration import create_integration
from ddtrace.internal.telemetry.data.metrics import Series
from ddtrace.internal.telemetry.data.payload import AppClosedPayload
from ddtrace.internal.telemetry.data.payload import AppGenerateMetricsPayload
from ddtrace.internal.telemetry.data.payload import AppIntegrationsChangedPayload
from ddtrace.internal.telemetry.data.payload import AppStartedPayload


def test_app_started_payload():
    payload = AppStartedPayload()

    assert payload.request_type() == "app-started"
    assert len(payload.dependencies) > 0
    assert payload.to_dict() == {"dependencies": payload.dependencies}


def test_app_closed():
    payload = AppClosedPayload()

    assert payload.request_type() == "app-closed"
    assert payload.to_dict() == {}


def test_app_integrations_changed():
    integrations = [
        create_integration("integration-1"),
        create_integration("integration-2", enabled=False),
        create_integration("integration-3", error="error"),
    ]

    payload = AppIntegrationsChangedPayload(integrations)

    assert payload.request_type() == "app-integrations-changed"
    assert len(payload.integrations) == 3

    assert payload.to_dict() == {"integrations": integrations}


def test_generate_metrics():
    series_array = [Series("test.metric")]

    payload = AppGenerateMetricsPayload(series_array)

    assert payload.request_type() == "generate-metrics"
    assert len(payload.series) == 1

    assert payload.to_dict() == {
        "namespace": "tracers",
        "lib_language": "python",
        "lib_version": get_version(),
        "series": [s.to_dict() for s in series_array],
    }
