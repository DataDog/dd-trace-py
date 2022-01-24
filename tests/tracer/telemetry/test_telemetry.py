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


def test_resart():
    telemetry.telemetry_writer._restart()
    assert telemetry.telemetry_writer.status == ServiceStatus.RUNNING
