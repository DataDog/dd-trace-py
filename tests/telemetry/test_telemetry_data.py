import platform
import sys

import mock
import pytest

from ddtrace.internal.compat import PY3
from ddtrace.internal.runtime.container import CGroupInfo
from ddtrace.internal.telemetry.data import APPLICATION
from ddtrace.internal.telemetry.data import Application
from ddtrace.internal.telemetry.data import Dependency
from ddtrace.internal.telemetry.data import HOST
from ddtrace.internal.telemetry.data import Host
from ddtrace.internal.telemetry.data import Integration
from ddtrace.internal.telemetry.data import create_integration
from ddtrace.internal.telemetry.data import format_version_info
from ddtrace.internal.telemetry.data import get_container_id
from ddtrace.internal.telemetry.data import get_hostname
from ddtrace.internal.telemetry.data import get_os_version
from ddtrace.internal.telemetry.data import get_version


def test_create_dependency():
    """tests creating a Dependency dictionary"""
    name = "dependency_name"
    version = "0.0.0"
    dependency = Dependency(name=name, version=version)

    assert dependency == {
        "name": name,
        "version": version,
    }  # type: Dependency


def test_create_integration():
    """tests create_integration and validates return type"""
    integration = create_integration("integration_name", "0.0.0", False, False, "no", "error")

    assert integration == {
        "name": "integration_name",
        "version": "0.0.0",
        "enabled": False,
        "auto_enabled": False,
        "compatible": "no",
        "error": "error",
    }  # type: Integration


def test_create_integration_with_default_args():
    """validates the return value of create_integration when default arguments are used"""
    name = "integration_name"
    integration = create_integration(name)

    assert integration == {
        "name": name,
        "version": "",
        "enabled": True,
        "auto_enabled": True,
        "compatible": "",
        "error": "",
    }  # type: Integration


def test_application():
    """validates whether the APPLICATION singleton contains the expected fields"""

    runtime_v = ""
    if PY3:
        runtime_v = format_version_info(sys.implementation.version)

    expected_application = {
        "service_name": "unnamed_python_service",
        "service_version": "",
        "env": "",
        "language_name": "python",
        "language_version": format_version_info(sys.version_info),
        "tracer_version": get_version(),
        "runtime_name": platform.python_implementation(),
        "runtime_version": runtime_v,
    }  # type: Application

    assert APPLICATION == expected_application


def test_application_with_setenv(run_python_code_in_subprocess, monkeypatch):
    """tests the APPLICATION singleton when DD_SERVICE, DD_VERSION, and DD_ENV environment variables"""
    monkeypatch.setenv("DD_SERVICE", "test_service")
    monkeypatch.setenv("DD_VERSION", "12.34.56")
    monkeypatch.setenv("DD_ENV", "prod")

    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace.internal.telemetry.data import APPLICATION

assert APPLICATION["service_name"] == "test_service"
assert APPLICATION["service_version"] == "12.34.56"
assert APPLICATION["env"] == "prod"
"""
    )

    assert status == 0, (out, err)


def test_format_version_info():
    """ensures the return value of format_version_info() has the format x.x.x"""
    sys_vi = sys.version_info

    version_str = format_version_info(sys_vi)
    assert version_str == "{}.{}.{}".format(sys_vi.major, sys_vi.minor, sys_vi.micro)


def test_host_fields():
    """validates whether the HOST singleton contains the expected fields"""
    expected_host = {
        "os": platform.platform(aliased=1, terse=1),
        "hostname": get_hostname(),
        "os_version": get_os_version(),
        "kernel_name": platform.system(),
        "kernel_release": platform.release(),
        "kernel_version": platform.version(),
        "container_id": get_container_id(),
    }  # type: Host

    assert HOST == expected_host


@pytest.mark.parametrize(
    "mac_ver,win32_ver,libc_ver,expected",
    [
        ((None, None, None), (None, "4.1.6", None, None), (None, None), "4.1.6"),
        (("3.5.6", None, None), (None, "", None, None), (None, None), "3.5.6"),
        ((None, None, None), (None, None, None, None), (None, "1.2.7"), "1.2.7"),
        ((None, None, None), (None, None, None, None), (None, None), ""),
    ],
)
def test_get_os_version(mac_ver, win32_ver, libc_ver, expected):
    """test retrieving the os version on a mac and windows 32-bit operating systems"""
    with mock.patch("platform.mac_ver") as macos:
        macos.return_value = mac_ver
        with mock.patch("platform.win32_ver") as win32:
            win32.return_value = win32_ver
            with mock.patch("platform.libc_ver") as libc:
                libc.return_value = libc_ver
                assert get_os_version() == expected


def test_get_container_id_when_container_exists():
    """
    validates the return value of get_container_id when get_container_info()
    can parse /proc/{pid}/cgroup
    """
    with mock.patch("ddtrace.internal.telemetry.data.get_container_info") as gci:
        cgroupInfo = CGroupInfo()
        cgroupInfo.container_id = "1641"
        gci.return_value = cgroupInfo
        assert get_container_id() == "1641"


def test_get_container_id_when_container_does_not_exists():
    """
    validates the return value of get_container_id when get_container_info() CAN NOT
    parse /proc/{pid}/cgroup
    """
    with mock.patch("ddtrace.internal.telemetry.data.get_container_info") as gci:
        gci.return_value = None
        assert get_container_id() == ""
