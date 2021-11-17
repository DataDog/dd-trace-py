import platform
from typing import TypedDict

from ddtrace.internal.runtime.container import get_container_info

from ...hostname import get_hostname


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
    if container_info:
        return container_info.container_id or ""
    return ""


def get_os_version():
    # type: () -> str
    """
    Returns the os version for applications running on
    Mac or Windows 32-bit
    """
    ver, _, _ = platform.mac_ver()
    if ver:
        return ver
    _, ver, _, _ = platform.win32_ver()
    if ver:
        return ver
    return ""


def get_host():
    # type: () -> Host
    """
    Creates a Host dictionary using the platform module
    """
    return {
        "os": platform.platform(aliased=True, terse=True),
        "hostname": get_hostname(),
        "os_version": get_os_version(),
        "kernel_name": platform.system(),
        "kernel_release": platform.release(),
        "kernel_version": platform.version(),
        "container_id": get_containter_id(),
    }


HOST = get_host()
