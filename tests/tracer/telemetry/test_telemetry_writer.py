import mock
import pytest

from ddtrace.internal.telemetry.telemetry_writer import TelemetryWriter


@pytest.fixture
def mock_send_request_400():
    class Response400:
        status = 401

    with mock.patch.object(TelemetryWriter, "_send_request", return_value=Response400()):
        yield


@pytest.fixture
def mock_send_request_200():
    class Response200:
        status = 200

    with mock.patch.object(TelemetryWriter, "_send_request", return_value=Response200()):
        yield


@pytest.fixture(autouse=True)
def reset_telemetry_writer():
    # Enable telemetry_writer
    TelemetryWriter.disable()
    TelemetryWriter.enable()
    telemetry_writer = TelemetryWriter._instance
    # Flush app-started event from the queue
    telemetry_writer.flush_events_queue()
    assert len(telemetry_writer._events_queue) == 0

    yield


def test_add_app_started_event():
    """asserts that enabling the Telemetry writer queues and then sends an app-started telemetry request"""
    TelemetryWriter.disable()
    TelemetryWriter.enable()

    telemetry_writer = TelemetryWriter._instance

    events = telemetry_writer._events_queue
    assert len(events) == 1
    assert events[0].request["request_type"] == "app-started"


def test_add_app_closed_event():
    """asserts that app_closed_event() queues an app-closed telemetry request"""
    telemetry_writer = TelemetryWriter._instance
    TelemetryWriter.app_closed_event()
    events = telemetry_writer._events_queue
    assert len(events) == 1
    assert events[0].request["request_type"] == "app-closed"


def test_add_integration_changed_event():
    """asserts that add_integration() queues an integration"""

    telemetry_writer = TelemetryWriter._instance
    TelemetryWriter.add_integration("integration-name")
    integrations = telemetry_writer._integrations_queue
    assert len(integrations) == 1
    assert integrations[0]["name"] == "integration-name"


def test_periodic(mock_send_request_200):
    """tests that periodic() sends all queued events and integrations to the agent"""
    telemetry_writer = TelemetryWriter._instance

    # Ensure events queue is empty
    events = telemetry_writer._events_queue
    assert len(events) == 0

    # Add 2 events to the queue
    TelemetryWriter.app_started_event()
    TelemetryWriter.app_closed_event()
    # Queue two integrations
    TelemetryWriter.add_integration("integration-1")
    TelemetryWriter.add_integration("integration-2")

    # Ensure events queue has 2 events (app started and app closed)
    events = telemetry_writer._events_queue
    assert len(events) == 2
    # Ensure two integrations have been queued
    integrations = telemetry_writer._integrations_queue
    assert len(integrations) == 2

    # Send all events to the agent proxy
    telemetry_writer.periodic()

    # Ensure all events have been sent
    assert len(telemetry_writer._events_queue) == 0
    assert len(telemetry_writer._integrations_queue) == 0


def test_shutdown(mock_send_request_200):
    """tests that shutdown() queues and attempts to send an app-closed event"""
    telemetry_writer = TelemetryWriter._instance

    # Ensure events queue is empty
    events = telemetry_writer._events_queue
    assert len(events) == 0

    with mock.patch.object(telemetry_writer, "app_closed_event") as ace:
        # Attempts to sends a shutdown event to the agent
        telemetry_writer.shutdown()
    # Ensure app-closed event was added to the queue
    ace.assert_called_once()

    # Ensure shutdown event was sent to the agent and flushed from the queue
    assert len(telemetry_writer._events_queue) == 0


def test_fail_count(mock_send_request_400):
    telemetry_writer = TelemetryWriter._instance

    # Add failing event
    TelemetryWriter.add_event(payload={}, payload_type="failing-request")

    for i in range(1, TelemetryWriter.MAX_FAIL_COUNT):
        # Attempt to send telemetry request
        telemetry_writer.periodic()
        # Ensure the fail count is incremented
        events = telemetry_writer._events_queue
        assert len(events) == 1
        assert events[0].fail_count == i

    # Attempt to send failing-request one last time
    telemetry_writer.periodic()
    # Ensure the failing event is no longer in the queue
    assert len(telemetry_writer._events_queue) == 0
