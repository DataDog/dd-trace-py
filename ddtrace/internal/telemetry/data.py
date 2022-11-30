import platform
import sys
from typing import Dict
from typing import List
from typing import Tuple

from ddtrace.internal.compat import PY3
from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.internal.packages import get_distributions
from ddtrace.internal.runtime.container import get_container_info
from ddtrace.internal.utils.cache import cached

from ...settings import _config as config
from ...version import get_version
from ..hostname import get_hostname


def _format_version_info(vi):
    # type: (sys._version_info) -> str
    """Converts sys.version_info into a string with the format x.x.x"""
    return "%d.%d.%d" % (vi.major, vi.minor, vi.micro)


def _get_container_id():
    # type: () -> str
    """Get ID from docker container"""
    container_info = get_container_info()
    if container_info:
        return container_info.container_id or ""
    return ""


def _get_os_version():
    # type: () -> str
    """Returns the os version for applications running on Unix, Mac or Windows 32-bit"""
    mver, _, _ = platform.mac_ver()
    _, wver, _, _ = platform.win32_ver()
    _, lver = platform.libc_ver()

    return mver or wver or lver or ""


@cached()
def _get_application(key):
    # type: (Tuple[str, str, str]) -> Dict
    """
    This helper packs and unpacks get_application arguments to support caching.
    Cached() annotation only supports functions with one argument
    """
    service, version, env = key

    return {
        "service_name": service or DEFAULT_SERVICE_NAME,  # mandatory field, can not be empty
        "service_version": version or "",
        "env": env or "",
        "language_name": "python",
        "language_version": _format_version_info(sys.version_info),
        "tracer_version": get_version(),
        "runtime_name": platform.python_implementation(),
        "runtime_version": _format_version_info(sys.implementation.version) if PY3 else "",
        "products": _get_products(),
    }


def get_dependencies():
    # type: () -> List[Dict[str, str]]
    """Returns a unique list of the names and versions of all installed packages"""
    dependencies = {(dist.name, dist.version) for dist in get_distributions()}
    return [{"name": name, "version": version} for name, version in dependencies]


def get_application(service, version, env):
    # type: (str, str, str) -> Dict
    """Creates a dictionary to store application data using ddtrace configurations and the System-Specific module"""
    # We cache the application dict to reduce overhead since service, version, or env configurations
    # can change during runtime
    return _get_application((service, version, env))


def _get_products():
    # type: () -> Dict
    products = {}
    if config._appsec_enabled:
        products["appsec"] = {"version": get_version()}

    return products


_host_info = None


def get_host_info():
    # type: () -> Dict
    """Creates a dictionary to store host data using the platform module"""
    global _host_info
    if _host_info is None:
        _host_info = {
            "os": platform.platform(aliased=True, terse=True),
            "hostname": get_hostname(),
            "os_version": _get_os_version(),
            "kernel_name": platform.system(),
            "kernel_release": platform.release(),
            "kernel_version": platform.version(),
            "container_id": _get_container_id(),
        }
    return _host_info
