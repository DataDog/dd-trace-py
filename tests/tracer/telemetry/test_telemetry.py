import os

from ddtrace.internal import telemetry
from ddtrace.internal.service import ServiceStatus


def test_enable():
    telemetry.telemetry_writer.enable()
    assert telemetry.telemetry_writer.status == ServiceStatus.RUNNING

    # assert that calling enable twice does not raise an exception
    telemetry.telemetry_writer.enable()
    assert telemetry.telemetry_writer.status == ServiceStatus.RUNNING


def test_disable():
    telemetry.telemetry_writer.disable()
    assert telemetry.telemetry_writer.status == ServiceStatus.STOPPED

    # assert that calling disable twice does not raise an exception
    telemetry.telemetry_writer.disable()
    assert telemetry.telemetry_writer.status == ServiceStatus.STOPPED


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
