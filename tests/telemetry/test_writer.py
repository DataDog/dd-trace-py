import time
from typing import Any
from typing import Dict

import httpretty
import mock
import pytest

from ddtrace.internal.telemetry.data import get_application
from ddtrace.internal.telemetry.data import get_dependencies
from ddtrace.internal.telemetry.data import get_host_info
from ddtrace.internal.telemetry.writer import TelemetryWriter
from ddtrace.internal.telemetry.writer import get_runtime_id
from ddtrace.internal.utils.version import _pep440_to_semver
from ddtrace.settings import _config as config

from .conftest import TelemetryTestSession


def test_add_event(telemetry_writer, test_agent_session, mock_time):
    """asserts that add_event queues a telemetry request with valid headers and payload"""
    payload = {"test": "123"}
    payload_type = "test-event"
    # add event to the queue
    telemetry_writer.add_event(payload, payload_type)
    # send request to the agent
    telemetry_writer.periodic()

    requests = test_agent_session.get_requests()
    assert len(requests) == 1
    assert requests[0]["headers"]["Content-Type"] == "application/json"
    assert requests[0]["headers"]["DD-Client-Library-Language"] == "python"
    assert requests[0]["headers"]["DD-Client-Library-Version"] == _pep440_to_semver()
    assert requests[0]["headers"]["DD-Telemetry-Request-Type"] == payload_type
    assert requests[0]["headers"]["DD-Telemetry-API-Version"] == "v1"
    assert requests[0]["headers"]["DD-Telemetry-Debug-Enabled"] == "False"
    assert requests[0]["headers"]["DD-Agent-Hostname"] == get_host_info()["hostname"]
    assert requests[0]["body"] == _get_request_body(payload, payload_type)


def test_add_event_disabled_writer(telemetry_writer, test_agent_session):
    """asserts that add_event() does not create a telemetry request when telemetry writer is disabled"""
    telemetry_writer._enabled = False

    payload = {"test": "123"}
    payload_type = "test-event"
    # ensure events are not queued when telemetry is disabled
    telemetry_writer.add_event(payload, payload_type)

    # ensure no request were sent
    telemetry_writer.periodic()
    assert len(test_agent_session.get_requests()) == 0


def test_app_started_event(telemetry_writer, test_agent_session, mock_time):
    """asserts that _app_started_event() queues a valid telemetry request which is then sent by periodic()"""
    # queue integrations
    telemetry_writer.add_integration("integration-t", True)
    telemetry_writer.add_integration("integration-f", False)
    # queue an app started event
    telemetry_writer._app_started_event()
    # force a flush
    telemetry_writer.periodic()

    requests = test_agent_session.get_requests()
    assert len(requests) == 1
    assert requests[0]["headers"]["DD-Telemetry-Request-Type"] == "app-started"

    events = test_agent_session.get_events()
    assert len(events) == 1

    # validate request body
    payload = {
        "dependencies": get_dependencies(),
        "integrations": [
            {
                "name": "integration-t",
                "version": "",
                "enabled": True,
                "auto_enabled": True,
                "compatible": True,
                "error": "",
            },
            {
                "name": "integration-f",
                "version": "",
                "enabled": True,
                "auto_enabled": False,
                "compatible": True,
                "error": "",
            },
        ],
        "configurations": [],
    }
    assert events[0] == _get_request_body(payload, "app-started")


def test_app_closing_event(telemetry_writer, test_agent_session, mock_time):
    """asserts that on_shutdown() queues and sends an app-closing telemetry request"""
    # send app closed event
    telemetry_writer.on_shutdown()

    requests = test_agent_session.get_requests()
    assert len(requests) == 1
    assert requests[0]["headers"]["DD-Telemetry-Request-Type"] == "app-closing"
    # ensure a valid request body was sent
    assert requests[0]["body"] == _get_request_body({}, "app-closing")


def test_add_integration(telemetry_writer, test_agent_session, mock_time):
    """asserts that add_integration() queues a valid telemetry request"""
    # queue integrations
    telemetry_writer.add_integration("integration-t", True)
    telemetry_writer.add_integration("integration-f", False)
    # send integrations to the agent
    telemetry_writer.periodic()

    requests = test_agent_session.get_requests()
    assert len(requests) == 1

    # assert integration change telemetry request was sent
    assert requests[0]["headers"]["DD-Telemetry-Request-Type"] == "app-integrations-change"
    # assert that the request had a valid request body
    expected_payload = {
        "integrations": [
            {
                "name": "integration-t",
                "version": "",
                "enabled": True,
                "auto_enabled": True,
                "compatible": True,
                "error": "",
            },
            {
                "name": "integration-f",
                "version": "",
                "enabled": True,
                "auto_enabled": False,
                "compatible": True,
                "error": "",
            },
        ]
    }
    assert requests[0]["body"] == _get_request_body(expected_payload, "app-integrations-change")


def test_add_integration_disabled_writer(telemetry_writer, test_agent_session):
    """asserts that add_integration() does not queue an integration when telemetry is disabled"""
    telemetry_writer.disable()

    telemetry_writer.add_integration("integration-name", False)
    telemetry_writer.periodic()

    assert len(test_agent_session.get_requests()) == 0


@pytest.mark.parametrize("mock_status", [300, 400, 401, 403, 500])
def test_send_failing_request(mock_status, telemetry_writer):
    """asserts that a warning is logged when an unsuccessful response is returned by the http client"""

    with httpretty.enabled():
        httpretty.register_uri(httpretty.POST, telemetry_writer._client.url, status=mock_status)
        with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
            # sends failing app-closing event
            telemetry_writer.on_shutdown()
            # asserts unsuccessful status code was logged
            log.debug.assert_called_with(
                "failed to send telemetry to the Datadog Agent at %s. response: %s",
                telemetry_writer._client.url,
                mock_status,
            )
        # ensure one failing request was sent
        assert len(httpretty.latest_requests()) == 1


def test_telemetry_graceful_shutdown(telemetry_writer, test_agent_session, mock_time):
    telemetry_writer.start()
    telemetry_writer.stop()

    events = test_agent_session.get_events()
    assert len(events) == 2

    # Reverse chronological orger
    assert events[0]["request_type"] == "app-closing"
    assert events[0] == _get_request_body({}, "app-closing", 2)
    assert events[1]["request_type"] == "app-started"


def test_app_heartbeat_event_periodic(mock_time, telemetry_writer, test_agent_session):
    # type: (mock.Mock, Any, TelemetryWriter) -> None
    """asserts that we queue/send app-heartbeat when periodc() is called"""

    # Assert default hearbeat interval is 60 seconds
    assert telemetry_writer.interval == 60
    # Assert next flush contains app-heartbeat event
    telemetry_writer.periodic()
    _assert_app_heartbeat_event(1, test_agent_session)


def test_app_heartbeat_event(mock_time, telemetry_writer, test_agent_session):
    # type: (mock.Mock, Any, TelemetryWriter) -> None
    """asserts that we queue/send app-heartbeat event every 60 seconds when app_heartbeat_event() is called"""

    # Assert clean slate
    events = test_agent_session.get_events()
    assert len(events) == 0

    # Assert a maximum of one heartbeat is queued per flush
    telemetry_writer._app_heartbeat_event()
    telemetry_writer.periodic()
    events = test_agent_session.get_events()
    assert len(events) == 1


def _assert_app_heartbeat_event(seq_id, test_agent_session):
    # type: (int, TelemetryTestSession) -> None
    """used to test heartbeat events received by the testagent"""
    events = test_agent_session.get_events()
    assert len(events) == seq_id
    # The test_agent returns telemetry events in reverse chronological order
    # The first event in the list is last event sent by the Telemetry Client
    last_event = events[0]
    assert last_event["request_type"] == "app-heartbeat"
    assert last_event == _get_request_body({}, "app-heartbeat", seq_id=seq_id)


def _get_request_body(payload, payload_type, seq_id=1):
    # type: (Dict, str, int) -> Dict
    """used to test the body of requests received by the testagent"""
    return {
        "tracer_time": time.time(),
        "runtime_id": get_runtime_id(),
        "api_version": "v1",
        "debug": False,
        "seq_id": seq_id,
        "application": get_application(config.service, config.version, config.env),
        "host": get_host_info(),
        "payload": payload,
        "request_type": payload_type,
    }
