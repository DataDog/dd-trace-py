from ddtrace.internal import telemetry
from ddtrace.internal.service import ServiceStatus


def test_enable():
    telemetry.TELEMETRY_WRITER.enable()
    assert telemetry.TELEMETRY_WRITER.status == ServiceStatus.RUNNING

    # assert that calling enable twice does not raise an exception
    telemetry.TELEMETRY_WRITER.enable()
    assert telemetry.TELEMETRY_WRITER.status == ServiceStatus.RUNNING


def test_disable():
    telemetry.TELEMETRY_WRITER.disable()
    assert telemetry.TELEMETRY_WRITER.status == ServiceStatus.STOPPED

    # assert that calling disable twice does not raise an exception
    telemetry.TELEMETRY_WRITER.disable()
    assert telemetry.TELEMETRY_WRITER.status == ServiceStatus.STOPPED


def test_resart():
    telemetry.TELEMETRY_WRITER._restart()
    assert telemetry.TELEMETRY_WRITER.status == ServiceStatus.RUNNING
