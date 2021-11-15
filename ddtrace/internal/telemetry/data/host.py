import platform
from typing import TypedDict

from ...hostname import get_hostname
from ...runtime.container import get_container_info


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


def get_containter_id():
    # type: () -> str
    container_info = get_container_info()
    if container_info and container_info.container_id:
        return container_info.container_id
    return ""


def get_os_version():
    ver, _, _ = platform.mac_ver()
    if ver:
        return ver
    ver, _, _ = platform.win32_ver()
    if ver:
        return ver
    return ""


HOST = {
    "os": platform.platform(aliased=1, terse=1),
    "hostname": get_hostname(),
    "os_version": get_os_version(),
    "kernel_name": platform.system(),
    "kernel_release": platform.release(),
    "kernel_version": platform.version(),
    "container_id": get_containter_id(),
}  # type: Host
