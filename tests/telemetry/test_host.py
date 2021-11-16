import platform
import mock

from ddtrace.internal.telemetry.data.host import HOST
from ddtrace.internal.telemetry.data.host import Host
from ddtrace.internal.telemetry.data.host import get_containter_id
from ddtrace.internal.telemetry.data.host import get_hostname
from ddtrace.internal.telemetry.data.host import get_os_version
from ddtrace.internal.runtime.container import CGroupInfo


def test_host_fields():
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
    with mock.patch("platform.mac_ver") as macos:
        macos.return_value = ("3.5.6", ("", "", ""), "")
        assert get_os_version() == "3.5.6"


def test_get_os_version_win32():
    with mock.patch("platform.win32_ver") as win32:
        win32.return_value = ("", "4.1.6", "", "")
        assert get_os_version() == "4.1.6"


def test_get_container_id():
    # not sure how to mock get_container_info()

    # cgroupinfo = CGroupInfo()
    # cgroupinfo.container_id = "1641"

    # with mock.patch("ddtrace.internal.runtime.container.get_container_info") as gc_info:
    #     gc_info.return_value = cgroupinfo
    #     assert get_containter_id() == "1641"
    pass
