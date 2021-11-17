import platform

import mock

from ddtrace.internal.runtime.container import CGroupInfo
from ddtrace.internal.telemetry.data.host import HOST
from ddtrace.internal.telemetry.data.host import Host
from ddtrace.internal.telemetry.data.host import get_containter_id
from ddtrace.internal.telemetry.data.host import get_hostname
from ddtrace.internal.telemetry.data.host import get_os_version


def test_host_fields():
    """validates whether the HOST singleton contains the expected fields"""
    expected_host = {
        "os": platform.platform(aliased=1, terse=1),
        "hostname": get_hostname(),
        "os_version": get_os_version(),
        "kernel_name": platform.system(),
        "kernel_release": platform.release(),
        "kernel_version": platform.version(),
        "container_id": get_containter_id(),
    }  # type: Host

    assert HOST == expected_host


def test_get_os_version_macos():
    """test retreiving the os version on a mac machine"""
    with mock.patch("platform.mac_ver") as macos:
        macos.return_value = ("3.5.6", ("", "", ""), "")
        assert get_os_version() == "3.5.6"


def test_get_os_version_win32():
    """test retreiving the os version on a windows 32-bit machine"""
    with mock.patch("platform.win32_ver") as win32:
        win32.return_value = ("", "4.1.6", "", "")
        assert get_os_version() == "4.1.6"


def test_get_container_id_when_container_exists():
    """
    validates the return value of get_containter_id when get_container_info()
    can parse /proc/{pid}/cgroup
    """
    with mock.patch("ddtrace.internal.telemetry.data.host.get_container_info") as gci:
        cgroupInfo = CGroupInfo()
        cgroupInfo.container_id = "1641"
        gci.return_value = cgroupInfo
        assert get_containter_id() == "1641"


def test_get_container_id_when_container_does_not_exists():
    """
    validates the return value of get_containter_id when get_container_info() CAN NOT
    parse /proc/{pid}/cgroup
    """
    with mock.patch("ddtrace.internal.telemetry.data.host.get_container_info") as gci:
        gci.return_value = None
        assert get_containter_id() == ""
