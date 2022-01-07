import mock

from ddtrace.internal.telemetry.telemetry_writer import TelemetryWriter


def test_telemetry_writer_app_started():
    """asserts that enabling the Telemetry writer queues and then sends an app-started telemetry request"""
    TelemetryWriter.disable()
    TelemetryWriter.enable()

    telemetry_writer = TelemetryWriter._instance

    events = telemetry_writer.queued_events()
    assert len(events) == 1
    assert events[0].request["request_type"] == "app-started"

    # telemetry_writer.periodic()
    # assert len(telemetry_writer.queued_events()) == 0


def test_telemetry_writer_app_closed():
    """asserts that app_closed_event() queues an app-closed telemetry request"""
    TelemetryWriter.disable()
    TelemetryWriter.enable()

    telemetry_writer = TelemetryWriter._instance
    telemetry_writer.flush_events_queue()
    assert len(telemetry_writer.queued_events()) == 0

    TelemetryWriter.app_closed_event()
    events = telemetry_writer.queued_events()
    assert len(events) == 1
    assert events[0].request["request_type"] == "app-closed"


def test_telemetry_writer_integration_changed():
    """asserts that integration_event() queues an integration dictionary"""
    TelemetryWriter.disable()
    TelemetryWriter.enable()

    telemetry_writer = TelemetryWriter._instance
    telemetry_writer.flush_events_queue()

    TelemetryWriter.integration_event("integration-name")
    integrations = telemetry_writer.queued_integrations()
    assert len(integrations) == 1
    assert integrations[0]["name"] == "integration-name"

    # telemetry_writer.periodic()
    # assert len(telemetry_writer.queued_events()) == 0
    # assert len(telemetry_writer.queued_integrations()) == 0


def test_fail_count():
    class FakeResponse:
        status = 401

    with mock.patch.object(TelemetryWriter, "_send_request", return_value=FakeResponse()):
        # Enable telemetry_writer
        TelemetryWriter.disable()
        TelemetryWriter.enable()
        telemetry_writer = TelemetryWriter._instance
        # Flush app-started event from the queue
        telemetry_writer.flush_events_queue()
        assert len(telemetry_writer.queued_events()) == 0

        # Add failing event
        TelemetryWriter.add_event({"request_type": "failing-request"})

        for i in range(1, TelemetryWriter.MAX_FAIL_COUNT):
            # Attempt to send telemetry request
            telemetry_writer.periodic()
            # Ensure the fail count is incremented
            events = telemetry_writer.queued_events()
            assert len(events) == 1
            assert events[0].fail_count == i

        # Attempt to send failing-request one last time
        telemetry_writer.periodic()
        # Ensure the failing event is no longer in the queue
        assert len(telemetry_writer.queued_events()) == 0
