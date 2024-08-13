import platform
import sys
import sysconfig
from typing import TYPE_CHECKING  # noqa:F401
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Tuple  # noqa:F401

from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.internal.packages import Distribution
from ddtrace.internal.packages import filename_to_package
from ddtrace.internal.runtime.container import get_container_info
from ddtrace.internal.utils.cache import cached
from ddtrace.version import get_version

from ...settings import _config as config  # noqa:F401
from ...settings.asm import config as asm_config
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
        "runtime_version": _format_version_info(sys.implementation.version),
        "products": _get_products(),
    }


def update_imported_dependencies(
    already_imported: Dict[str, Distribution], new_modules: List[str]
) -> List[Dict[str, str]]:
    deps = []

    for module_path in new_modules:
        if not module_path:
            continue
        try:
            package = filename_to_package(module_path)
            if not package or (package.name in already_imported) or package.name == "ddtrace":
                continue  # not third party or already imported
        except AttributeError:
            continue
        already_imported[package.name] = package
        deps.append({"name": package.name, "version": package.version})

    return deps


def get_application(service, version, env):
    # type: (str, str, str) -> Dict
    """Creates a dictionary to store application data using ddtrace configurations and the System-Specific module"""
    # We cache the application dict to reduce overhead since service, version, or env configurations
    # can change during runtime
    return _get_application((service, version, env))


def _get_products():
    # type: () -> Dict
    return {
        "appsec": {"version": get_version(), "enabled": asm_config._asm_enabled},
    }


_host_info = None


def get_host_info():
    # type: () -> Dict
    """Creates a dictionary to store host data using the platform module"""
    global _host_info
    if _host_info is None:
        _host_info = {
            "os": platform.system(),
            "hostname": get_hostname(),
            "os_version": _get_os_version(),
            "kernel_name": platform.system(),
            "kernel_release": platform.release(),
            "kernel_version": platform.version(),
            "container_id": _get_container_id(),
        }
    return _host_info


def _get_sysconfig_var(key: str) -> str:
    return sysconfig.get_config_var(key) or ""


def get_python_config_vars() -> List[Tuple[str, str, str]]:
    # DEV: Use "unknown" since these aren't user or dd defined values
    return [
        ("python_soabi", _get_sysconfig_var("SOABI"), "unknown"),
        ("python_host_gnu_type", _get_sysconfig_var("HOST_GNU_TYPE"), "unknown"),
        ("python_build_gnu_type", _get_sysconfig_var("BUILD_GNU_TYPE"), "unknown"),
    ]
