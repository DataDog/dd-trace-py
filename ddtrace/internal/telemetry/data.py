import platform
import sys

from ddtrace.internal.compat import PY3
from ddtrace.internal.runtime.container import get_container_info
from ddtrace.settings import _config as config

from ...version import get_version
from ..hostname import get_hostname


def _format_version_info(vi):
    # type: (sys._version_info) -> str
    """
    Helper function private to this module
    Converts sys.version_info into a string with the format x.x.x
    """
    return "%d.%d.%d" % (vi.major, vi.minor, vi.micro)


def _get_container_id():
    # type: () -> str
    """
    Helper function private to this module
    Get ID from docker container
    """
    container_info = get_container_info()
    if container_info:
        return container_info.container_id or ""
    return ""


def _get_os_version():
    # type: () -> str
    """
    Helper function private to this module
    Returns the os version for applications running on Unix, Mac or Windows 32-bit
    """
    mver, _, _ = platform.mac_ver()
    _, wver, _, _ = platform.win32_ver()
    _, lver = platform.libc_ver()

    return mver or wver or lver or ""


# A dictionary to store application data using ddtrace configurations and the System-Specific module
APPLICATION = {
    "service_name": config.service or "unnamed_python_service",
    "service_version": config.version or "",
    "env": config.env or "",
    "language_name": "python",
    "language_version": _format_version_info(sys.version_info),
    "tracer_version": get_version(),
    "runtime_name": platform.python_implementation(),
    "runtime_version": _format_version_info(sys.implementation.version) if PY3 else "",
}

# A dictionary to store host data using the platform module
HOST = {
    "os": platform.platform(aliased=True, terse=True),
    "hostname": get_hostname(),
    "os_version": _get_os_version(),
    "kernel_name": platform.system(),
    "kernel_release": platform.release(),
    "kernel_version": platform.version(),
    "container_id": _get_container_id(),
}
