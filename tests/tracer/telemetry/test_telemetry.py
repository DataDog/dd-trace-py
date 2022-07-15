import os

import httpretty
import pytest

from ddtrace.internal import telemetry
from ddtrace.internal.service import ServiceStatus


@pytest.fixture
def telemetry_writer():
    yield telemetry.telemetry_writer


@pytest.fixture(autouse=True)
def mock_send_request(telemetry_writer):
    with httpretty.enabled():
        httpretty.register_uri(httpretty.POST, telemetry_writer.url, status=200)
        yield


def test_enable(telemetry_writer):
    telemetry_writer.enable()
    assert telemetry_writer.status == ServiceStatus.RUNNING

    # assert that calling enable twice does not raise an exception
    telemetry_writer.enable()
    assert telemetry_writer.status == ServiceStatus.RUNNING

    # send queued events
    telemetry_writer.periodic()
    # ensure an app-started telemetry request is sent
    assert len(httpretty.latest_requests()) == 1
    headers = httpretty.last_request().headers
    assert "DD-Telemetry-Request-Type" in headers
    assert headers["DD-Telemetry-Request-Type"] == "app-started"


def test_disable(telemetry_writer):
    telemetry_writer.disable()
    assert telemetry_writer.status == ServiceStatus.STOPPED

    # assert that calling disable twice does not raise an exception
    telemetry_writer.disable()
    assert telemetry_writer.status == ServiceStatus.STOPPED

    # send queued events
    telemetry_writer.periodic()
    # ensure no events were sent
    assert len(httpretty.latest_requests()) == 0


def test_fork():
    telemetry.telemetry_writer.enable()

    pid = os.fork()
    if pid > 0:
        assert telemetry.telemetry_writer.status == ServiceStatus.RUNNING
    else:
        assert telemetry.telemetry_writer._forked is True
        assert telemetry.telemetry_writer._integrations_queue == []
        assert telemetry.telemetry_writer._events_queue == []
        assert telemetry.telemetry_writer.status == ServiceStatus.RUNNING
        # Kill the process so it doesn't continue running the rest of the test suite
        os._exit(0)
