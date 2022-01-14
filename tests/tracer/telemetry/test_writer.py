import json

import httpretty
import mock
import pytest

from ddtrace.internal.telemetry.data import get_application
from ddtrace.internal.telemetry.data import get_host_info
from ddtrace.internal.telemetry.writer import AGENT_URL
from ddtrace.internal.telemetry.writer import ENDPOINT
from ddtrace.internal.telemetry.writer import TelemetryWriter
from ddtrace.settings import _config as config


TEST_URL = "%s/%s" % (AGENT_URL, ENDPOINT)


@pytest.fixture
def mock_send_request_200():
    with httpretty.enabled():
        httpretty.register_uri(httpretty.POST, TEST_URL, status=202)
        yield


@pytest.fixture
def telemetry_writer():
    telemetry_writer = TelemetryWriter()
    telemetry_writer.enabled = True
    yield telemetry_writer


@pytest.fixture
def disabled_telemetry_writer():
    # TelemetryWriter should be disabled by default
    telemetry_writer = TelemetryWriter()
    assert telemetry_writer.enabled is False

    yield telemetry_writer


def test_add_app_started_event(telemetry_writer):
    """asserts that enabling the Telemetry writer queues and then sends an app-started telemetry request"""
    telemetry_writer.app_started_event()

    events = telemetry_writer._flush_events_queue()
    assert len(events) == 1
    assert events[0]["request_type"] == "app-started"


def test_add_app_closed_event(telemetry_writer):
    """asserts that app_closed_event() queues an app-closed telemetry request"""
    telemetry_writer.app_closed_event()
    events = telemetry_writer._flush_events_queue()
    assert len(events) == 1
    assert events[0]["request_type"] == "app-closed"


def test_add_integration_changed_event(telemetry_writer):
    """asserts that _app_integrations_changed_event() queues an app-integrations-changed telemetry request"""
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
    telemetry_writer._app_integrations_changed_event(integrations)

    events = telemetry_writer._flush_events_queue()
    assert len(events) == 1
    assert events[0]["request_type"] == "app-integrations-changed"


def test_add_event(telemetry_writer):
    """asserts that add_event() creates a telemetry request with a payload and payload type"""
    payload = {"test": "123"}
    payload_type = "test-event"
    telemetry_writer.add_event(payload, payload_type)
    events = telemetry_writer._flush_events_queue()
    assert len(events) == 1
    assert events[0]["request_type"] == "test-event"


def test_add_event_disabled_writer(disabled_telemetry_writer):
    """asserts that add_event() does not create a telemetry request with a payload and payload type"""
    payload = {"test": "123"}
    payload_type = "test-event"

    # Ensure events are NOT queued when telemetry is disabled
    disabled_telemetry_writer.add_event(payload, payload_type)
    assert len(disabled_telemetry_writer._flush_events_queue()) == 0


def test_add_integration(telemetry_writer):
    # Ensure integrations are queued when telemetry is enabled
    telemetry_writer.add_integration("integration-name", True)

    integrations = telemetry_writer._integrations_queue
    assert len(integrations) == 1
    assert integrations[0]["name"] == "integration-name"
    assert integrations[0]["auto_enabled"]


def test_add_integration_disabled_writer(disabled_telemetry_writer):
    """asserts that add_integration() does not queue an integration"""
    disabled_telemetry_writer.add_integration("integration-name", False)
    assert len(disabled_telemetry_writer._flush_integrations_queue()) == 0


def test_periodic(mock_send_request_200, telemetry_writer):
    """tests that periodic() sends all queued events and integrations to the agent"""
    # Ensure events queue is empty
    events = telemetry_writer._flush_events_queue()
    assert len(events) == 0

    # Add 2 events to the queue
    telemetry_writer.app_started_event()
    telemetry_writer.app_closed_event()
    # Queue two integrations
    telemetry_writer.add_integration("integration-1", False)
    telemetry_writer.add_integration("integration-2", False)

    # Ensure events queue has 2 events (app started and app closed)
    assert len(telemetry_writer._events_queue) == 2
    # Ensure two integrations have been queued
    assert len(telemetry_writer._integrations_queue) == 2

    with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
        # Send all events to the agent proxy
        telemetry_writer.periodic()
        # Assert no error logs were generated while sending telemetry requests
        log.error.assert_not_called()

    # Ensure all events have been sent
    assert len(telemetry_writer._flush_events_queue()) == 0
    assert len(telemetry_writer._flush_integrations_queue()) == 0


def test_shutdown(mock_send_request_200, telemetry_writer):
    """tests that shutdown() queues and attempts to send an app-closed event"""
    # Ensure events queue is empty
    assert len(telemetry_writer._flush_events_queue()) == 0

    with mock.patch.object(telemetry_writer, "app_closed_event") as ace:
        # Attempts to sends a shutdown event to the agent
        telemetry_writer.shutdown()
    # Ensure app-closed event was added to the queue
    ace.assert_called_once()

    # Ensure shutdown event was sent to the agent and flushed from the queue
    assert len(telemetry_writer._flush_events_queue()) == 0


def test_create_telemetry_request(telemetry_writer):
    """validates the return value of create_telemetry_request"""
    with mock.patch("ddtrace.internal.telemetry.writer.monotonic") as t:
        t.return_value = 888366600
        with mock.patch("ddtrace.internal.telemetry.writer.get_runtime_id") as get_rt_id:
            get_rt_id.return_value = "1234-567"

            telmetry_request = telemetry_writer._create_telemetry_request(
                payload={}, payload_type="app-closed", sequence_id=1
            )
            assert telmetry_request == {
                "tracer_time": 888366600,
                "runtime_id": "1234-567",
                "api_version": "v2",
                "seq_id": 1,
                "application": get_application(config.service, config.version, config.env),
                "host": get_host_info(),
                "payload": {},
                "request_type": "app-closed",
            }


def test_get_headers(telemetry_writer):
    """validates the return value of get_headers"""
    payload_type = "test-headers"
    telemetry_headers = telemetry_writer._create_headers(payload_type)

    assert telemetry_headers == {
        "Content-type": "application/json",
        "DD-Telemetry-Request-Type": payload_type,
        "DD-Telemetry-API-Version": "v2",
    }


def test_send_request(mock_send_request_200, telemetry_writer):
    """asserts that the agent proxy responds with a 202 when the agent is available"""
    payload_type = "test-request"
    request = telemetry_writer._create_telemetry_request({}, payload_type, 1)
    headers = telemetry_writer._create_headers(payload_type)

    # Send request to the agent mock
    resp = telemetry_writer._send_request(request)
    assert resp.status == 202

    # Ensure json encoded request was sent to the agent proxy
    httpretty.last_request().body == json.dumps(request)
    # Ensure request headers were set on the sent request
    httpretty.last_request().headers == headers


@httpretty.activate()
def test_send_request_raises_exception(telemetry_writer):
    """asserts that an error is logged when an exception is raised by the http client"""
    httpretty.register_uri(httpretty.POST, TEST_URL, status=400)

    payload_type = "test-request-exception"
    request = telemetry_writer._create_telemetry_request({}, payload_type, 1)

    with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
        # Send failing event
        resp = telemetry_writer._send_request(request)
        assert resp.status == 400

        # Assert an exception was raised when reading the response
        log.error.assert_called_with(
            "failed to send telemetry to the Datadog Agent at %s/%s", AGENT_URL, ENDPOINT, exc_info=True
        )
