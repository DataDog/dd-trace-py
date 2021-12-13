from ddtrace.internal.telemetry.data.integration import create_integration
from ddtrace.internal.telemetry.data.payload import AppStartedPayload
from ddtrace.internal.telemetry.data.telemetry_request import create_telemetry_request
from ddtrace.internal.telemetry.telemetry_writer import DEFAULT_TELEMETRY_ENDPOINT_TEST
from ddtrace.internal.telemetry.telemetry_writer import TelemetryWriter


def test_telemetry_writer_app_started():

    apps = AppStartedPayload()
    rb1 = create_telemetry_request(apps, 0)

    telemetry_writer = TelemetryWriter(endpoint=DEFAULT_TELEMETRY_ENDPOINT_TEST, interval=10000)
    telemetry_writer.add_request(rb1)

    telemetry_writer.periodic()
    assert len(telemetry_writer.requests) == 0
    assert telemetry_writer.sequence == 1


def test_telemetry_writer_app_closed():
    telemetry_writer = TelemetryWriter(endpoint=DEFAULT_TELEMETRY_ENDPOINT_TEST, interval=10000)

    telemetry_writer.shutdown()
    assert len(telemetry_writer.requests) == 0
    assert telemetry_writer.sequence == 1


def test_telemetry_writer_integration_changed():
    integration = create_integration("integration-1")

    telemetry_writer = TelemetryWriter(endpoint=DEFAULT_TELEMETRY_ENDPOINT_TEST, interval=10000)
    telemetry_writer.add_integration(integration)

    telemetry_writer.periodic()
    assert len(telemetry_writer.requests) == 0
    assert telemetry_writer.sequence == 1
