from ddtrace.internal.telemetry.telemetry_writer import TelemetryWriter


# Telemetry writer integration tests
# Currently these tests send telemetry payloads to the backend (and fail since an API Key is not set)


def test_telemetry_writer_app_started():
    """asserts that enabling the Telemetry writer queues and then sends an app-started telemetry request"""
    TelemetryWriter.disable()
    TelemetryWriter.enable()

    telemetry_writer = TelemetryWriter._instance

    events = telemetry_writer.queued_events()
    assert len(events) == 1
    assert events[0]["body"]["request_type"] == "app-started"

    telemetry_writer.periodic()
    assert len(telemetry_writer.queued_events()) == 0


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
    assert events[0]["body"]["request_type"] == "app-closed"


def test_telemetry_writer_integration_changed():
    """asserts that integration_event() queues an integration dictionary"""
    TelemetryWriter.disable()
    TelemetryWriter.enable()

    telemetry_writer = TelemetryWriter._instance
    telemetry_writer.flush_events_queue()

    integration = {
        "name": "integration-name",
        "version": "",
        "enabled": True,
        "auto_enabled": True,
        "compatible": "",
        "error": "",
    }
    TelemetryWriter.integration_event(integration)
    assert len(telemetry_writer.queued_integrations()) == 1

    telemetry_writer.periodic()
    assert len(telemetry_writer.queued_events()) == 0
    assert len(telemetry_writer.queued_integrations()) == 0
