import sys

from ddtrace.internal.telemetry.data.application import APPLICATION
from ddtrace.internal.telemetry.data.application import Application
from ddtrace.internal.telemetry.data.application import format_version_info
from ddtrace.internal.telemetry.data.application import get_version


def test_application():
    """tests Application values when no enviorment variables are set"""
    expected_application = {
        "service_name": "unnamed_python_service",
        "service_version": "",
        "env": "",
        "language_name": "python",
        "language_version": format_version_info(sys.version_info),
        "tracer_version": get_version(),
        "runtime_name": sys.implementation.name,
        "runtime_version": format_version_info(sys.implementation.version),
    }  # type: Application

    assert APPLICATION == expected_application


def test_application_with_setenv(run_python_code_in_subprocess, monkeypatch):
    """tests Application values when DD_SERVICE, DD_VERSION, and DD_ENV enviorment variables"""
    monkeypatch.setenv("DD_SERVICE", "test_service")
    monkeypatch.setenv("DD_VERSION", "12.34.56")
    monkeypatch.setenv("DD_ENV", "prod")

    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace.internal.telemetry.data.application import APPLICATION

assert APPLICATION["service_name"] == "test_service"
assert APPLICATION["service_version"] == "12.34.56"
assert APPLICATION["env"] == "prod"
"""
    )

    assert status == 0, (out, err)


def test_format_version_info():
    """validates the return value of format_version_info() has the format: x.x.x"""
    sys_vi = sys.version_info

    version_str = format_version_info(sys_vi)
    assert version_str == "{}.{}.{}".format(sys_vi.major, sys_vi.minor, sys_vi.micro)
