import os
<<<<<<< HEAD
=======
import time
from typing import Any
from typing import Dict
>>>>>>> 6f3d2cfc (chore(telemetry): add support for app-heartbeat telemetry events (#3979))

import httpretty
import mock
import pytest

from ddtrace.internal.telemetry.data import get_application
from ddtrace.internal.telemetry.data import get_dependencies
from ddtrace.internal.telemetry.data import get_host_info
from ddtrace.internal.telemetry.writer import TelemetryWriter
from ddtrace.internal.telemetry.writer import get_runtime_id
from ddtrace.settings import _config as config

<<<<<<< HEAD

AGENT_URL = "http://localhost:8126"
TEST_URL = "%s/%s" % (AGENT_URL, TelemetryWriter.ENDPOINT)


@pytest.fixture
def mock_status():
    return 202
=======
from .conftest import TelemetryTestSession
>>>>>>> 6f3d2cfc (chore(telemetry): add support for app-heartbeat telemetry events (#3979))


@pytest.fixture
def mock_send_request(mock_status):
    with httpretty.enabled():
        httpretty.register_uri(httpretty.POST, TEST_URL, status=mock_status)
        yield


@pytest.fixture
def mock_time():
    with mock.patch("time.time") as mt:
        mt.return_value = 1642544540
<<<<<<< HEAD
        yield


@pytest.fixture
def telemetry_writer():
    telemetry_writer = TelemetryWriter(AGENT_URL)
    # Enable the TelemetryWriter without queuing an app-started event
    # and setting up exit hooks
    telemetry_writer._enabled = True
    yield telemetry_writer


@pytest.fixture
def telemetry_writer_disabled():
    telemetry_writer = TelemetryWriter()
    # TelemetryWriter _enabled should be None by default
    assert telemetry_writer._enabled is None
    telemetry_writer._enabled = False
    yield telemetry_writer
=======
        yield mt
>>>>>>> 6f3d2cfc (chore(telemetry): add support for app-heartbeat telemetry events (#3979))


def test_add_event(mock_time, mock_send_request, telemetry_writer):
    """asserts that add_event queues a telemetry request with valid headers and payload"""
    payload = {"test": "123"}
    payload_type = "test-event"
    # add event to the queue
    telemetry_writer.add_event(payload, payload_type)
    # send request to the agent
    telemetry_writer.periodic()
    # Ensure one request was sent
    assert len(httpretty.latest_requests()) == 1
    # validate request headers
    headers = httpretty.last_request().headers
    assert headers["Content-type"] == "application/json"
    assert headers["DD-Telemetry-Request-Type"] == payload_type
    assert headers["DD-Telemetry-API-Version"] == "v1"
    # validate request body
    assert httpretty.last_request().parsed_body == _get_request_body(payload, payload_type)


def test_add_event_disabled_writer(mock_send_request, telemetry_writer_disabled):
    """asserts that add_event() does not create a telemetry request when telemetry writer is disabled"""
    payload = {"test": "123"}
    payload_type = "test-event"
    # ensure events are not queued when telemetry is disabled
    telemetry_writer_disabled.add_event(payload, payload_type)
    # ensure no request were sent
    telemetry_writer_disabled.periodic()
    assert len(httpretty.latest_requests()) == 0


def test_app_started_event(mock_time, mock_send_request, telemetry_writer):
    """asserts that app_started_event() queues a valid telemetry request which is then sent by periodic()"""
    # queue integrations
    telemetry_writer.add_integration("integration-t", True)
    telemetry_writer.add_integration("integration-f", False)
    # queue an app started event
    telemetry_writer.app_started_event()
    # send app-started event to the agent
    telemetry_writer.periodic()
    # assert that an app-started event was sent
    assert len(httpretty.latest_requests()) == 1
    # ensure an app-started telemetry request is sent
    headers = httpretty.last_request().headers
    assert "DD-Telemetry-Request-Type" in headers
    assert headers["DD-Telemetry-Request-Type"] == "app-started"
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
    assert httpretty.last_request().parsed_body == _get_request_body(payload, "app-started")


def test_app_started_event_on_fork(telemetry_writer):
    """asserts that app_started_event() is not sent after a fork"""
    if os.fork() == 0:
        # queue an app started event
        telemetry_writer.app_started_event()
        # send all queued events to the agent
        telemetry_writer.periodic()
        # ensure an app_started event was not sent
        assert len(httpretty.latest_requests()) == 0
        # Kill the process so it doesn't continue running the rest of the test suite
        os._exit(0)


def test_app_closing_event(mock_time, mock_send_request, telemetry_writer):
    """asserts that on_shutdown() queues and sends an app-closing telemetry request"""
    # send app closed event
    telemetry_writer.on_shutdown()
    # assert that one request was sent
    assert len(httpretty.latest_requests()) == 1
    # ensure an app closing event was sent
    headers = httpretty.last_request().headers
    assert "DD-Telemetry-Request-Type" in headers
    assert headers["DD-Telemetry-Request-Type"] == "app-closing"
    # ensure a valid request body was sent
    assert len(httpretty.latest_requests()) == 1
    assert httpretty.last_request().parsed_body == _get_request_body({}, "app-closing")


def test_app_closing_on_fork(telemetry_writer):
    """asserts that on_shutdown() does not send an app-closing telemetry request after a fork"""
    if os.fork() == 0:
        # send app closed event
        telemetry_writer.on_shutdown()
        # assert that no requests were sent
        assert len(httpretty.latest_requests()) == 0
        # Kill the process so it doesn't continue running the rest of the test suite
        os._exit(0)


def test_add_integration(mock_time, mock_send_request, telemetry_writer):
    """asserts that add_integration() queues a valid telemetry request"""
    # queue integrations
    telemetry_writer.add_integration("integration-t", True)
    telemetry_writer.add_integration("integration-f", False)
    # send integrations to the agent
    telemetry_writer.periodic()
    # assert integration change telemetry request was sent
    assert len(httpretty.latest_requests()) == 1
    headers = httpretty.last_request().headers
    assert "DD-Telemetry-Request-Type" in headers
    assert headers["DD-Telemetry-Request-Type"] == "app-integrations-change"
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
    assert httpretty.last_request().parsed_body == _get_request_body(expected_payload, "app-integrations-change")


def test_add_integration_disabled_writer(mock_send_request, telemetry_writer_disabled):
    """asserts that add_integration() does not queue an integration when telemetry is disabled"""
    telemetry_writer_disabled.add_integration("integration-name", False)
    telemetry_writer_disabled.periodic()
    assert len(httpretty.latest_requests()) == 0


def test_periodic(mock_send_request, telemetry_writer):
    """tests that periodic() sends queued app-started and integration events to the agent"""
    # add 1 event to the queue
    telemetry_writer.app_started_event()
    # queue two integrations
    telemetry_writer.add_integration("integration-1", True)
    telemetry_writer.add_integration("integration-2", False)
    telemetry_writer.periodic()
    # ensure one app-started and one app-integrations-change event was sent
    assert len(httpretty.latest_requests()) == 2

    # queue 2 more integrations
    telemetry_writer.add_integration("integration-3", True)
    telemetry_writer.add_integration("integration-4", False)
    # send both integrations to the agent proxy
    telemetry_writer.periodic()
    # ensure one more app-integrations-change events was sent
    # 2 requests were sent in the previous flush
    assert len(httpretty.latest_requests()) == 3


@pytest.mark.parametrize("mock_status", [300, 400, 401, 403, 500])
def test_send_failing_request(mock_status, telemetry_writer):
    """asserts that a warning is logged when an unsuccessful response is returned by the http client"""

    with httpretty.enabled():
        httpretty.register_uri(httpretty.POST, telemetry_writer.url, status=mock_status)
        with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
            # sends failing app-closing event
            telemetry_writer.on_shutdown()
            # asserts unsuccessful status code was logged
            log.debug.assert_called_with(
                "failed to send telemetry to the Datadog Agent at %s/%s. response: %s",
                telemetry_writer._agent_url,
                telemetry_writer.ENDPOINT,
                mock_status,
            )
        # ensure one failing request was sent
        assert len(httpretty.latest_requests()) == 1


def test_send_request_exception():
    """asserts that an error is logged when an exception is raised by the http client"""
    # create a telemetry writer with an invalid agent url.
    # this will raise an Exception on _send_request
    telemetry_writer = TelemetryWriter("http://hostthatdoesntexist:1234")
    telemetry_writer._enabled = True

    with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
        # sends failing app-closing event
        telemetry_writer.on_shutdown()
        # assert an exception was logged
        log.debug.assert_called_with(
            "failed to send telemetry to the Datadog Agent at %s/%s.",
            "http://hostthatdoesntexist:1234",
            telemetry_writer.ENDPOINT,
            exc_info=True,
        )


def test_telemetry_graceful_shutdown(mock_time, mock_send_request, telemetry_writer):
    telemetry_writer.start()
    telemetry_writer.stop()
    assert len(httpretty.latest_requests()) == 2
    assert httpretty.last_request().parsed_body == _get_request_body({}, "app-closing", 2)


def test_app_heartbeat_event_periodic(mock_time, telemetry_writer, test_agent_session):
    # type: (mock.Mock, Any, TelemetryWriter) -> None
    """asserts that we queue/send app-heartbeat event every 60 seconds when periodc() is called"""

    # Assert clean slate
    events = test_agent_session.get_events()
    assert len(events) == 0

    # Assert first flush does not queue any events
    events = test_agent_session.get_events()
    assert len(events) == 0

    # Advance time 60 seconds
    mock_time.return_value += 60

    # Assert next flush contains app-heartbeat event
    telemetry_writer.periodic()
    _assert_app_heartbeat_event(1, test_agent_session)

    # Advance time 59 seconds
    mock_time.return_value += 59

    # Assert next flush does not contain an app-heartbeat event
    telemetry_writer.periodic()
    events = test_agent_session.get_events()
    assert len(events) == 1

    # Advance time 1 second
    mock_time.return_value += 1

    # Assert next flush contains app-heartbeat event
    telemetry_writer.periodic()
    _assert_app_heartbeat_event(2, test_agent_session)

    # Advance time 120 seconds (2 intervals)
    mock_time.return_value += 120

    # Assert next flush contains only one app-heartbeat event
    telemetry_writer.periodic()
    _assert_app_heartbeat_event(3, test_agent_session)


def test_app_heartbeat_event(mock_time, telemetry_writer, test_agent_session):
    # type: (mock.Mock, Any, TelemetryWriter) -> None
    """asserts that we queue/send app-heartbeat event every 60 seconds when app_heartbeat_event() is called"""

    # Assert clean slate
    events = test_agent_session.get_events()
    assert len(events) == 0

    # Within 60 seconds of telemetry writer creation, no event is queued
    telemetry_writer.app_heartbeat_event()
    telemetry_writer.periodic()
    events = test_agent_session.get_events()
    assert len(events) == 0

    # Advance time 60 seconds
    mock_time.return_value += 60

    # Manual queueing and calling periodic will only queue and flush 1 event
    telemetry_writer.app_heartbeat_event()
    telemetry_writer.periodic()
    _assert_app_heartbeat_event(1, test_agent_session)

    # Advance time 120 seconds (2 intervals)
    mock_time.return_value += 120

    # Calling multiple times will only enqueue one event
    telemetry_writer.app_heartbeat_event()
    telemetry_writer.app_heartbeat_event()
    telemetry_writer.app_heartbeat_event()
    telemetry_writer.app_heartbeat_event()
    telemetry_writer.periodic()
    _assert_app_heartbeat_event(2, test_agent_session)


def test_app_heartbeat_event_fork(mock_time, telemetry_writer, test_agent_session):
    # type: (mock.Mock, TelemetryWriter) -> None
    """asserts that we do not queue/send app-heartbeat events on forks"""
    if os.fork() == 0:
        # Advance time 120 seconds (2 intervals)
        mock_time.return_value += 120

        telemetry_writer.app_heartbeat_event()
        telemetry_writer.app_heartbeat_event()
        telemetry_writer.app_heartbeat_event()
        telemetry_writer.app_heartbeat_event()
        telemetry_writer.periodic()
        # Kill the process so it doesn't continue running the rest of the test suite
        events = test_agent_session.get_events()
        assert len(events) == 0
        os._exit(0)


def _assert_app_heartbeat_event(seq_id, test_agent_session):
    # type: (int, TelemetryWriter, TelemetryTestSession) -> None
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
<<<<<<< HEAD
        "tracer_time": 1642544540,
=======
        "tracer_time": time.time(),
>>>>>>> 6f3d2cfc (chore(telemetry): add support for app-heartbeat telemetry events (#3979))
        "runtime_id": get_runtime_id(),
        "api_version": "v1",
        "seq_id": seq_id,
        "application": get_application(config.service, config.version, config.env),
        "host": get_host_info(),
        "payload": payload,
        "request_type": payload_type,
    }


def FakeModule():
    """Used to mock working_set entries in pkg_resources"""

    def __init__(self, project_name, version):
        self.project_name = project_name
        self.version = version
