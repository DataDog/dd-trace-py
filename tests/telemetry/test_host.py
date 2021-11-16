import platform

from ddtrace.internal.telemetry.data.host import HOST
from ddtrace.internal.telemetry.data.host import Host
from ddtrace.internal.telemetry.data.host import get_containter_id
from ddtrace.internal.telemetry.data.host import get_hostname
from ddtrace.internal.telemetry.data.host import get_os_version


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


def test_get_os_version():
    pass


def test_get_container_id():
    pass
