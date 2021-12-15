from ddtrace.internal.telemetry.data import create_integration
from ddtrace.internal.telemetry.telemetry_request import app_started_telemetry_request
from ddtrace.internal.telemetry.telemetry_writer import DEFAULT_TELEMETRY_ENDPOINT_TEST
from ddtrace.internal.telemetry.telemetry_writer import TelemetryWriter


def test_telemetry_writer_app_started():

    rb1 = app_started_telemetry_request()

    telemetry_writer = TelemetryWriter(endpoint=DEFAULT_TELEMETRY_ENDPOINT_TEST)
    telemetry_writer.add_request(rb1)

    telemetry_writer.periodic()
    assert len(telemetry_writer._requests_queue) == 0
    assert telemetry_writer.sequence == 1


def test_telemetry_writer_app_closed():
    telemetry_writer = TelemetryWriter(endpoint=DEFAULT_TELEMETRY_ENDPOINT_TEST)

    telemetry_writer.shutdown()
    assert len(telemetry_writer._requests_queue) == 0
    assert telemetry_writer.sequence == 1


def test_telemetry_writer_integration_changed():
    integration = create_integration("integration-1")

    telemetry_writer = TelemetryWriter(endpoint=DEFAULT_TELEMETRY_ENDPOINT_TEST)
    telemetry_writer.add_integration(integration)

    telemetry_writer.periodic()
    assert len(telemetry_writer._requests_queue) == 0
    assert telemetry_writer.sequence == 1
