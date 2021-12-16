from ddtrace.internal.telemetry.data import create_integration
from ddtrace.internal.telemetry.telemetry_request import app_started_telemetry_request
from ddtrace.internal.telemetry.telemetry_writer import DEFAULT_TELEMETRY_ENDPOINT_TEST
from ddtrace.internal.telemetry.telemetry_writer import TelemetryWriter


def test_telemetry_writer_app_started():
    TelemetryWriter.disable()
    TelemetryWriter.enable(endpoint=DEFAULT_TELEMETRY_ENDPOINT_TEST)

    telemetry_writer = TelemetryWriter._instance

    # TelemetryWriter.sequence is incremented everytime TelemetryWriter.add_request is used to add
    # a TelemetryRequest to the payload buffer. This test ensures that adding one payload increases
    # this value by one.
    prev_sequence = TelemetryWriter.sequence
    rb1 = app_started_telemetry_request()
    TelemetryWriter.add_request(rb1)
    assert telemetry_writer.sequence - prev_sequence == 1

    telemetry_writer.periodic()
    assert len(telemetry_writer._requests_queue) == 0


def test_telemetry_writer_app_closed():
    TelemetryWriter.disable()
    TelemetryWriter.enable(endpoint=DEFAULT_TELEMETRY_ENDPOINT_TEST)

    telemetry_writer = TelemetryWriter._instance
    prev_sequence = TelemetryWriter.sequence
    telemetry_writer.shutdown()

    assert len(telemetry_writer._requests_queue) == 0
    assert telemetry_writer.sequence - prev_sequence == 1


def test_telemetry_writer_integration_changed():
    TelemetryWriter.disable()
    TelemetryWriter.enable(endpoint=DEFAULT_TELEMETRY_ENDPOINT_TEST)

    integration = create_integration("integration-1")

    TelemetryWriter.add_integration(integration)

    telemetry_writer = TelemetryWriter._instance
    prev_sequence = TelemetryWriter.sequence
    telemetry_writer.periodic()

    assert len(telemetry_writer._requests_queue) == 0
    assert telemetry_writer.sequence - prev_sequence == 1
