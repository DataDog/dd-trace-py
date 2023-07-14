import os
import platform
import sys

import mock
import pytest

import ddtrace
from ddtrace.internal.compat import PY3
from ddtrace.internal.packages import get_distributions
from ddtrace.internal.runtime.container import CGroupInfo
from ddtrace.internal.telemetry.data import _format_version_info
from ddtrace.internal.telemetry.data import _get_container_id
from ddtrace.internal.telemetry.data import _get_os_version
from ddtrace.internal.telemetry.data import get_application
from ddtrace.internal.telemetry.data import get_dependencies
from ddtrace.internal.telemetry.data import get_host_info
from ddtrace.internal.telemetry.data import get_hostname
from ddtrace.settings import _config as config


def test_get_application():
    """validates return value of get_application"""

    runtime_v = ""
    if PY3:
        runtime_v = _format_version_info(sys.implementation.version)

    expected_application = {
        "service_name": "unnamed_python_service",
        "service_version": "",
        "env": "",
        "language_name": "python",
        "language_version": _format_version_info(sys.version_info),
        "tracer_version": ddtrace.__version__,
        "runtime_name": platform.python_implementation(),
        "runtime_version": runtime_v,
        "products": {"appsec": {"version": ddtrace.__version__, "enabled": config._appsec_enabled}},
    }

    assert get_application("", "", "") == expected_application


def test_get_application_with_values():
    """validates return value of get_application when service, version, and environment configurations are set"""

    application = get_application("munirs-service", "1.1.1", "staging")
    assert application["service_name"] == "munirs-service"
    assert application["service_version"] == "1.1.1"
    assert application["env"] == "staging"


def test_application_with_setenv(run_python_code_in_subprocess, monkeypatch):
    """
    validates the return value of get_application when DD_SERVICE, DD_VERSION, and DD_ENV environment variables are set
    """
    monkeypatch.setenv("DD_SERVICE", "test_service")
    monkeypatch.setenv("DD_VERSION", "12.34.56")
    monkeypatch.setenv("DD_ENV", "prod")

    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace.internal.telemetry.data import get_application
from ddtrace import config

application = get_application(config.service, config.version, config.env)
assert application["service_name"] == "test_service"
assert application["service_version"] == "12.34.56"
assert application["env"] == "prod"
"""
    )

    assert status == 0, (out, err)


def test_get_application_is_cached():
    """ensures get_application() returns cached dictionary when it's called with the same arguments"""
    with mock.patch("platform.python_implementation") as gv:
        get_application("is_cached-service", "1.2.3", "test")
        get_application("is_cached-service", "1.2.3", "test")
        get_application("is_cached-service", "1.2.3", "test")

    gv.assert_called_once()


def test_format_version_info():
    """ensures the return value of _format_version_info() has the format x.x.x"""
    sys_vi = sys.version_info

    version_str = _format_version_info(sys_vi)
    assert version_str == "{}.{}.{}".format(sys_vi.major, sys_vi.minor, sys_vi.micro)


def test_get_host_info():
    """validates whether the HOST singleton contains the expected fields"""
    expected_host = {
        "os": platform.system(),
        "hostname": get_hostname(),
        "os_version": _get_os_version(),
        "kernel_name": platform.system(),
        "kernel_release": platform.release(),
        "kernel_version": platform.version(),
        "container_id": _get_container_id(),
    }

    assert get_host_info() == expected_host


@pytest.mark.parametrize(
    "mac_ver,win32_ver,libc_ver,expected",
    [
        (("", "", ""), ("", "4.1.6", "", ""), ("", ""), "4.1.6"),
        (("3.5.6", "", ""), ("", "", "", ""), ("", ""), "3.5.6"),
        (("", "", ""), ("", "", "", ""), ("", "1.2.7"), ""),
        (("", "", ""), ("", "", "", ""), ("", ""), ""),
    ],
)
def test_get_os_version(mac_ver, win32_ver, libc_ver, expected):
    """test retrieving the os version on a mac, linux and windows 32-bit operating systems"""
    with mock.patch("platform.mac_ver") as macos:
        macos.return_value = mac_ver
        with mock.patch("platform.win32_ver") as win32:
            win32.return_value = win32_ver
            with mock.patch("platform.libc_ver") as libc:
                # platform.libc_ver is used to retrieve the version of Linux platforms. This value is NOT the
                # os version. This value should NOT be sent in telemetry payloads.
                # TODO: Find a way to get the OS version on Linux
                libc.return_value = libc_ver
                assert _get_os_version() == expected


@pytest.mark.parametrize(
    "mac_ver,win32_ver,expected",
    [
        # We are on a unix system that raised an OSError
        (("", "", ""), ("", "", "", ""), ""),
        # We are on macOS, we never call platform.libc_ver(), so success
        (("3.5.6", "", ""), ("", "", "", ""), "3.5.6"),
        # We are on a Windows machine, we never call platform.libc_ver(), so success
        (("", "", ""), ("", "4.1.6", "", ""), "4.1.6"),
    ],
)
def test_get_os_version_os_error(mac_ver, win32_ver, expected):
    """regression test for platform.libc_ver() raising an OSError on Windows/unsupported machine"""
    with mock.patch("platform.mac_ver") as macos:
        macos.return_value = mac_ver

        with mock.patch("platform.win32_ver") as win32:
            win32.return_value = win32_ver

            # Cause platform.libc_ver() to raise an OSError
            with mock.patch("platform.libc_ver") as libc:
                libc.side_effect = OSError

                assert _get_os_version() == expected


def test_get_container_id_when_container_exists():
    """
    validates the return value of _get_container_id when get_container_info()
    can parse /proc/{pid}/cgroup
    """
    # mocks ddtrace.internal.runtime.container.get_container_info import in data.py
    with mock.patch("ddtrace.internal.telemetry.data.get_container_info") as gci:
        cgroupInfo = CGroupInfo()
        cgroupInfo.container_id = "d39b145254d1f9c337fdd2be132f6650c6f5bc274bfa28aaa204a908a1134096"
        gci.return_value = cgroupInfo
        assert _get_container_id() == "d39b145254d1f9c337fdd2be132f6650c6f5bc274bfa28aaa204a908a1134096"


def test_get_container_id_when_container_does_not_exists():
    """
    validates the return value of _get_container_id when get_container_info() CAN NOT
    parse /proc/{pid}/cgroup
    """
    # mocks ddtrace.internal.runtime.container.get_container_info import in data.py
    with mock.patch("ddtrace.internal.telemetry.data.get_container_info") as gci:
        gci.return_value = None
        assert _get_container_id() == ""


def test_get_dependencies():
    """asserts that get_dependencies and get_distributions return the same packages"""
    pkgs_as_dicts = {(dep["name"], dep["version"]) for dep in get_dependencies()}
    pkgs_as_distributions = {(dist.name, dist.version) for dist in get_distributions()}
    assert pkgs_as_dicts == pkgs_as_distributions


def test_enable_products(run_python_code_in_subprocess):
    env = os.environ.copy()
    env["DD_APPSEC_ENABLED"] = "true"

    out, err, status, _ = run_python_code_in_subprocess(
        """
import ddtrace
from ddtrace.internal.telemetry.data import get_application

application = get_application("service-x", "1.1.1", "staging")
assert "products" in application

assert application["products"] == {
    "appsec": {
        "version": ddtrace.__version__,
        "enabled": True,
    },
}
""",
        env=env,
    )
    assert status == 0, out + err
