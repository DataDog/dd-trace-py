import platform
import sys
import sysconfig

from ddtrace.internal import process_tags
from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.internal.runtime.container import get_container_info
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.utils.cache import callonce
from ddtrace.version import __version__

from ..hostname import get_hostname
from ..logger import get_logger


log = get_logger(__name__)


def _format_version_info(vi: "sys._version_info") -> str:
    """Converts sys.version_info into a string with the format x.x.x"""
    return "%d.%d.%d" % (vi.major, vi.minor, vi.micro)


def _get_container_id() -> str:
    """Get ID from docker container"""
    container_info = get_container_info()
    if container_info:
        return container_info.container_id or ""
    return ""


def _get_os_version() -> str:
    """Returns the os version for applications running on Mac or Windows 32-bit"""
    try:
        mver, _, _ = platform.mac_ver()
        if mver:
            return mver

        _, wver, _, _ = platform.win32_ver()
        if wver:
            return wver
    except OSError:
        # We were unable to lookup the proper version
        pass

    return ""


@cached()
def _get_application(key: tuple[str, str, str]) -> dict:
    """
    This helper packs and unpacks get_application arguments to support caching.
    Cached() annotation only supports functions with one argument
    """
    service, version, env = key

    application = {
        "service_name": service or DEFAULT_SERVICE_NAME,  # mandatory field, can not be empty
        "service_version": version or "",
        "env": env or "",
        "language_name": "python",
        "language_version": _format_version_info(sys.version_info),
        "tracer_version": __version__,
        "runtime_name": platform.python_implementation(),
        "runtime_version": _format_version_info(sys.implementation.version),
    }

    if p_tags := process_tags.process_tags:
        application["process_tags"] = p_tags

    return application


def get_application(service: str, version: str, env: str) -> dict:
    """Creates a dictionary to store application data using ddtrace configurations and the System-Specific module"""
    # We cache the application dict to reduce overhead since service, version, or env configurations
    # can change during runtime
    return _get_application((service, version, env))


@callonce
def get_host_info() -> dict:
    """Creates a dictionary to store host data using the platform module."""
    return {
        "os": platform.system(),
        "hostname": get_hostname(),
        "os_version": _get_os_version(),
        "kernel_name": platform.system(),
        "kernel_release": platform.release(),
        "kernel_version": platform.version(),
        "container_id": _get_container_id(),
    }


def _get_sysconfig_var(key: str) -> str:
    return sysconfig.get_config_var(key) or ""


def get_python_config_vars() -> list[tuple[str, str, str]]:
    # DEV: Use "unknown" since these aren't user or dd defined values
    return [
        ("python_soabi", _get_sysconfig_var("SOABI"), "unknown"),
        ("python_host_gnu_type", _get_sysconfig_var("HOST_GNU_TYPE"), "unknown"),
        ("python_build_gnu_type", _get_sysconfig_var("BUILD_GNU_TYPE"), "unknown"),
    ]
