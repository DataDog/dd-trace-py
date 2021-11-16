import sys

from ddtrace.internal.telemetry.data.application import APPLICATION
from ddtrace.internal.telemetry.data.application import Application
from ddtrace.internal.telemetry.data.application import get_version


def test_application():
    vi = sys.version_info
    language_version = "{}.{}.{}".format(vi.major, vi.minor, vi.micro)

    vi = sys.implementation.version
    runtime_version = "{}.{}.{}".format(vi.major, vi.minor, vi.micro)

    expected_application = {
        "service_name": "unnamed_python_service",
        "service_version": "",
        "env": "",
        "language_name": "python",
        "language_version": language_version,
        "tracer_version": get_version(),
        "runtime_name": sys.implementation.name,
        "runtime_version": runtime_version,
    }  # type: Application

    assert APPLICATION == expected_application


def test_application_with_setenv(run_python_code_in_subprocess, monkeypatch):

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
