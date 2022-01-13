import httpretty
import mock
import pytest

from ddtrace.internal.telemetry.writer import AGENT_URL
from ddtrace.internal.telemetry.writer import ENDPOINT
from ddtrace.internal.telemetry.writer import TelemetryWriter


TEST_URL = "%s/%s" % (AGENT_URL, ENDPOINT)


class FakeResponse:
    def __init__(self, status):
        self.status = status


@pytest.fixture
def mock_send_request_200():
    with httpretty.enabled():
        httpretty.register_uri(httpretty.POST, TEST_URL, status=202)
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
    assert events[0]["request_type"] == "app-started"


def test_add_app_closed_event():
    """asserts that app_closed_event() queues an app-closed telemetry request"""
    telemetry_writer = TelemetryWriter._instance
    TelemetryWriter.app_closed_event()
    events = telemetry_writer._events_queue
    assert len(events) == 1
    assert events[0]["request_type"] == "app-closed"


def test_add_integration_changed_event():
    """asserts that app_integrations_changed_event() queues an app-integrations-changed telemetry request"""
    telemetry_writer = TelemetryWriter._instance

    integrations = [
        {
            "name": "integration_name",
            "version": "",
            "enabled": True,
            "auto_enabled": True,
            "compatible": "",
            "error": "",
        }
    ]
    TelemetryWriter.app_integrations_changed_event(integrations)

    events = telemetry_writer._events_queue
    assert len(events) == 1
    assert events[0]["request_type"] == "app-integrations-changed"


def test_add_event():
    """asserts that add_event() creates a telemetry request with a payload and payload type"""
    telemetry_writer = TelemetryWriter._instance

    payload = {"test": "123"}
    payload_type = "test-event"
    TelemetryWriter.add_event(payload, payload_type)

    events = telemetry_writer._events_queue
    assert len(events) == 1
    assert events[0]["request_type"] == "test-event"


def test_add_integration():
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

    with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
        # Send all events to the agent proxy
        telemetry_writer.periodic()
        # Assert no error logs were generated while sending telemetry requests
        log.error.assert_not_called()

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


def test_send_request(mock_send_request_200):
    """asserts that the agent proxy responds with a 202 when the agent is available"""
    telemetry_writer = TelemetryWriter._instance
    request = {
        "tracer_time": 1678945683,
        "runtime_id": "123-4567-890-1234-5678999",
        "api_version": "v2",
        "seq_id": 0,
        "application": {},
        "host": {},
        "payload": {},
        "request_type": "test-request",
    }
    resp = telemetry_writer._send_request(request)
    assert resp.status == 202


@httpretty.activate()
def test_send_request_raises_exception():
    """asserts that an error is logged when an exception is raised by the http client"""
    httpretty.register_uri(httpretty.POST, TEST_URL, status=400, body=lambda _: Exception("client error"))

    telemetry_writer = TelemetryWriter._instance
    request = {
        "tracer_time": 1678945683,
        "runtime_id": "123-4567-890-1234-5678999",
        "api_version": "v2",
        "seq_id": 0,
        "application": {},
        "host": {},
        "payload": {},
        "request_type": "test-request-exception",
    }

    with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
        # Send all test event to the agent proxy
        telemetry_writer._send_request(request)
        # Assert an exception was raised when reading the response
        log.error.assert_called_with("failed to send telemetry to the Datadog Agent at %s", ENDPOINT, exc_info=True)
