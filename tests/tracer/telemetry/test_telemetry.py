import mock

from ddtrace.internal import telemetry
from ddtrace.internal.service import ServiceStatus


def test_enable():
    telemetry.enable()
    assert telemetry.TELEMETRY_WRITER.status == ServiceStatus.RUNNING

    # assert that calling enable twice does not raise an exception
    telemetry.enable()
    assert telemetry.TELEMETRY_WRITER.status == ServiceStatus.RUNNING


def test_disable():
    telemetry.disable()
    assert telemetry.TELEMETRY_WRITER.status == ServiceStatus.STOPPED

    # assert that calling disable twice does not raise an exception
    telemetry.disable()
    assert telemetry.TELEMETRY_WRITER.status == ServiceStatus.STOPPED


def test_resart():
    telemetry._restart()
    assert telemetry.TELEMETRY_WRITER.status == ServiceStatus.RUNNING


def test_add_integration():
    with mock.patch("ddtrace.internal.telemetry.TELEMETRY_WRITER") as tw:
        telemetry.add_integration("integration_name", False)
        tw.add_integration.assert_called_once_with("integration_name", False)
