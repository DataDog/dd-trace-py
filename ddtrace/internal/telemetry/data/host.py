import platform
from typing import TypedDict

from ...hostname import get_hostname
from ddtrace.internal.runtime.container import get_container_info


Host = TypedDict(
    "Host",
    {
        "os": str,
        "hostname": str,
        "os_version": str,
        "kernel_name": str,
        "kernel_release": str,
        "kernel_version": str,
        "container_id": str,
    },
)
"""
Stores info about the host an application is running on
"""


def get_containter_id():
    # type: () -> str
    """Get ID from docker container"""
    container_info = get_container_info()
    print(container_info.container_id)
    if container_info:
        return container_info.container_id or ""


def get_os_version():
    # type: () -> str
    ver, _, _ = platform.mac_ver()
    if ver:
        return ver
    _, ver, _, _ = platform.win32_ver()
    if ver:
        return ver
    return ""


def get_host():
    # type: () -> Host
    return {
        "os": platform.platform(aliased=1, terse=1),
        "hostname": get_hostname(),
        "os_version": get_os_version(),
        "kernel_name": platform.system(),
        "kernel_release": platform.release(),
        "kernel_version": platform.version(),
        "container_id": get_containter_id(),
    }  # type: Host


HOST = get_host()
