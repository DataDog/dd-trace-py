import httpretty
import mock
import pytest

from ddtrace.internal.telemetry.data import get_application
from ddtrace.internal.telemetry.data import get_host_info
from ddtrace.internal.telemetry.writer import TelemetryWriter
from ddtrace.internal.telemetry.writer import get_runtime_id
from ddtrace.settings import _config as config


AGENT_URL = "http://localhost:8126"
TEST_URL = "%s/%s" % (AGENT_URL, TelemetryWriter.ENDPOINT)


@pytest.fixture
def mock_send_request_200():
    with httpretty.enabled():
        httpretty.register_uri(httpretty.POST, TEST_URL, status=202)
        yield


@pytest.fixture
def telemetry_writer():
    telemetry_writer = TelemetryWriter(AGENT_URL)
    telemetry_writer.enabled = True
    yield telemetry_writer


@pytest.fixture
def telemetry_writer_disabled():
    telemetry_writer = TelemetryWriter()
    # TelemetryWriter should be disabled by default
    assert telemetry_writer.enabled is False

    yield telemetry_writer


def test_add_event(telemetry_writer):
    """asserts that add_event() creates a telemetry request with a payload and payload type"""
    payload = {"test": "123"}
    payload_type = "test-event"

    # Adding an event to _events_queue should increment the sequence id by 1
    assert telemetry_writer._sequence == 1
    telemetry_writer.add_event(payload, payload_type)
    assert telemetry_writer._sequence == 2

    events = telemetry_writer._events_queue
    assert len(events) == 1
    assert events[0]["payload"] == payload
    assert events[0]["request_type"] == "test-event"


def test_add_app_started_event(mock_send_request_200, telemetry_writer):
    """asserts that app_started_event() queues a telemetry request which is then sent by periodic()"""
    assert telemetry_writer._sequence == 1
    telemetry_writer.app_started_event()
    assert telemetry_writer._sequence == 2

    telemetry_writer.periodic()

    headers = httpretty.last_request().headers
    assert "DD-Telemetry-Request-Type" in headers
    assert headers["DD-Telemetry-Request-Type"] == "app-started"


def test_add_app_closed_event(mock_send_request_200, telemetry_writer):
    """asserts that shutdown() queues and sends an app-closed telemetry request"""
    assert telemetry_writer._sequence == 1
    telemetry_writer.shutdown()
    assert telemetry_writer._sequence == 2

    headers = httpretty.last_request().headers
    assert "DD-Telemetry-Request-Type" in headers
    assert headers["DD-Telemetry-Request-Type"] == "app-closed"


def test_add_event_disabled_writer(telemetry_writer_disabled):
    """asserts that add_event() does not create a telemetry request telemetry writer is disabled"""
    payload = {"test": "123"}
    payload_type = "test-event"

    # Ensure events are NOT queued when telemetry is disabled
    telemetry_writer_disabled.add_event(payload, payload_type)
    assert len(telemetry_writer_disabled._events_queue) == 0
    assert telemetry_writer_disabled._sequence == 1


def test_add_integration(telemetry_writer):
    """ensure integrations are queued when telemetry is enabled"""
    telemetry_writer.add_integration("integration-t", True)
    telemetry_writer.add_integration("integration-f", False)

    assert telemetry_writer._integrations_queue == [
        {
            "name": "integration-t",
            "version": "",
            "enabled": True,
            "auto_enabled": True,
            "compatible": "",
            "error": "",
        },
        {
            "name": "integration-f",
            "version": "",
            "enabled": True,
            "auto_enabled": False,
            "compatible": "",
            "error": "",
        },
    ]


def test_add_integration_disabled_writer(telemetry_writer_disabled):
    """asserts that add_integration() does not queue an integration when telemetry is disabled"""
    telemetry_writer_disabled.add_integration("integration-name", False)
    assert len(telemetry_writer_disabled._integrations_queue) == 0


@mock.patch("ddtrace.internal.telemetry.writer.monotonic")
def test_sending_event(mock_monotonic, mock_send_request_200, telemetry_writer):
    """asserts that the telemetry writer sends a valid telemetry header and payload"""
    mock_monotonic.return_value = 1642544540

    payload_type = "app-closed"
    # Add event to the queue
    telemetry_writer.add_event({}, payload_type)
    # send request to the agent
    telemetry_writer.periodic()

    # Ensure request headers were set on the sent request
    headers = httpretty.last_request().headers
    assert headers["Content-type"] == "application/json"
    assert headers["DD-Telemetry-Request-Type"] == payload_type
    assert headers["DD-Telemetry-API-Version"] == "v1"

    # Ensure json encoded request was sent to the agent proxy
    assert httpretty.last_request().parsed_body == {
        "tracer_time": 1642544540,
        "runtime_id": get_runtime_id(),
        "api_version": "v1",
        "seq_id": 1,
        "application": get_application(config.service, config.version, config.env),
        "host": get_host_info(),
        "payload": {},
        "request_type": payload_type,
    }


def test_periodic(mock_send_request_200, telemetry_writer):
    """tests that periodic() sends all queued events and integrations to the agent"""
    # Ensure events queue is empty
    events = telemetry_writer._events_queue
    assert len(events) == 0

    # Add 1 event to the queue
    telemetry_writer.app_started_event()
    # Queue two integrations
    telemetry_writer.add_integration("integration-1", True)
    telemetry_writer.add_integration("integration-2", False)
    # Ensure two integrations have been queued
    assert len(telemetry_writer._integrations_queue) == 2
    # Ensure events queue has 2 events (app started and app closed)
    assert len(telemetry_writer._events_queue) == 1
    with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
        # Send all events to the agent proxy
        telemetry_writer.periodic()
        # Assert no error logs were generated while sending telemetry requests
        log.warning.assert_not_called()
    # Ensure all events have been sent
    assert len(telemetry_writer._events_queue) == 0
    assert len(telemetry_writer._integrations_queue) == 0

    # Queue 2 more integrations
    telemetry_writer.add_integration("integration-3", True)
    telemetry_writer.add_integration("integration-4", False)
    assert len(telemetry_writer._integrations_queue) == 2
    with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
        # Send both integrations to the agent proxy
        telemetry_writer.periodic()
        # Assert no error logs were generated while sending telemetry requests
        log.warning.assert_not_called()
    # Ensure all integrations were sent
    assert len(telemetry_writer._integrations_queue) == 0


@httpretty.activate()
def test_send_request_400(telemetry_writer):
    """asserts that an warning is logged when a 400 response is returned by the http client"""
    httpretty.register_uri(httpretty.POST, TEST_URL, status=400)
    with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
        # Sends failing app-closed event
        telemetry_writer.shutdown()
        # Assert 400 status code was logged
        log.warning.assert_called_with("failed to send telemetry to the Datadog Agent. response: %s", 400)


@httpretty.activate()
def test_send_request_500(telemetry_writer):
    """asserts that an warning is logged when a 400 response is returned by the http client"""
    httpretty.register_uri(httpretty.POST, TEST_URL, status=500)
    with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
        # Sends failing app-closed event
        telemetry_writer.shutdown()
        # Assert 500 status code was logged
        log.warning.assert_called_with("failed to send telemetry to the Datadog Agent. response: %s", 500)


def test_send_request_exception(telemetry_writer):
    """asserts that an error is logged when an exception is raised by the http client"""
    # Create a telemetry writer with an invalid agent url.
    # This will raise an HttpConnectionException on send_request
    telemetry_writer = TelemetryWriter("")
    telemetry_writer.enabled = True

    with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
        # Sends failing app-closed event
        telemetry_writer.shutdown()
        # Assert an exception was logged
        log.warning.assert_called_with(
            "failed to send telemetry to the Datadog Agent at %s/%s.",
            telemetry_writer.agent_url,
            telemetry_writer.ENDPOINT,
            exc_info=True,
        )
