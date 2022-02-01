import httpretty
import mock
import pkg_resources
import pytest

from ddtrace.internal.telemetry.data import get_application
from ddtrace.internal.telemetry.data import get_host_info
from ddtrace.internal.telemetry.writer import TelemetryWriter
from ddtrace.internal.telemetry.writer import get_runtime_id
from ddtrace.settings import _config as config


AGENT_URL = "http://localhost:8126"
TEST_URL = "%s/%s" % (AGENT_URL, TelemetryWriter.ENDPOINT)


@pytest.fixture
def mock_status():
    return 202


@pytest.fixture
def mock_send_request(mock_status):
    with httpretty.enabled():
        httpretty.register_uri(httpretty.POST, TEST_URL, status=mock_status)
        yield


@pytest.fixture
def mock_time():
    with mock.patch("time.time") as mt:
        mt.return_value = 1642544540
        yield


@pytest.fixture
def telemetry_writer():
    telemetry_writer = TelemetryWriter(AGENT_URL)
    telemetry_writer._enabled = True
    yield telemetry_writer


@pytest.fixture
def telemetry_writer_disabled():
    telemetry_writer = TelemetryWriter()
    # TelemetryWriter should be disabled by default
    assert telemetry_writer._enabled is False
    yield telemetry_writer


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


def test_add_app_started_event(mock_time, mock_send_request, telemetry_writer):
    """asserts that app_started_event() queues a valid telemetry request which is then sent by periodic()"""
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
        "dependencies": [{"name": pkg.project_name, "version": pkg.version} for pkg in pkg_resources.working_set],
        "configurations": [],
    }
    assert httpretty.last_request().parsed_body == _get_request_body(payload, "app-started")


def test_add_app_closing_event(mock_time, mock_send_request, telemetry_writer):
    """asserts that shutdown() queues and sends an app-closing telemetry request"""
    # send app closed event
    telemetry_writer.shutdown()
    # assert that one request was sent
    assert len(httpretty.latest_requests()) == 1
    # ensure an app closing event was sent
    headers = httpretty.last_request().headers
    assert "DD-Telemetry-Request-Type" in headers
    assert headers["DD-Telemetry-Request-Type"] == "app-closing"
    # ensure a valid request body was sent
    assert len(httpretty.latest_requests()) == 1
    assert httpretty.last_request().parsed_body == _get_request_body({}, "app-closing")


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

    with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
        # send all events to the agent proxy
        telemetry_writer.periodic()
        # assert no warning logs were generated while sending telemetry requests
        log.warning.assert_not_called()
    # ensure one app-started and one app-integrations-change event was sent
    assert len(httpretty.latest_requests()) == 2

    # queue 2 more integrations
    telemetry_writer.add_integration("integration-3", True)
    telemetry_writer.add_integration("integration-4", False)
    with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
        # send both integrations to the agent proxy
        telemetry_writer.periodic()
        # assert no warning logs were generated while sending telemetry requests
        log.warning.assert_not_called()
    # ensure one more app-integrations-change events was sent
    # 2 requests were sent in the previous flush
    assert len(httpretty.latest_requests()) == 3


@pytest.mark.parametrize("mock_status", [300, 400, 401, 403, 500])
def test_send_failing_request(mock_status, mock_send_request, telemetry_writer):
    """asserts that a warning is logged when an unsuccessful response is returned by the http client"""
    with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
        # sends failing app-closing event
        telemetry_writer.shutdown()
        # asserts unsuccessful status code was logged
        log.warning.assert_called_with(
            "failed to send telemetry to the Datadog Agent at %s/%s. response: %s",
            telemetry_writer._agent_url,
            telemetry_writer.ENDPOINT,
            mock_status,
        )
    # ensure one failing request was sent
    assert len(httpretty.latest_requests()) == 1


def test_send_request_exception(telemetry_writer):
    """asserts that an error is logged when an exception is raised by the http client"""
    # create a telemetry writer with an invalid agent url.
    # this will raise an Exception on _send_request
    telemetry_writer = TelemetryWriter("http://hostthatdoesntexist:1234")
    telemetry_writer._enabled = True

    with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
        # sends failing app-closing event
        telemetry_writer.shutdown()
        # assert an exception was logged
        log.warning.assert_called_with(
            "failed to send telemetry to the Datadog Agent at %s/%s.",
            "http://hostthatdoesntexist:1234",
            telemetry_writer.ENDPOINT,
            exc_info=True,
        )


def _get_request_body(payload, payload_type, seq_id=1):
    """used to test the body of requests intercepted by httpretty"""
    return {
        "tracer_time": 1642544540,
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
