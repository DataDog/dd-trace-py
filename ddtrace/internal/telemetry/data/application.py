import sys
from typing import TypedDict

from ....settings import _config as config  # noqa: E402
from ....version import get_version


Application = TypedDict(
    "Application",
    {
        "service_name": str,
        "service_version": str,
        "env": str,
        "language_name": str,
        "language_version": str,
        "tracer_version": str,
        "runtime_name": str,
        "runtime_version": str,
    },
)
"""
Stores information to uniquely identify a service
"""


def format_version_info(vi):
    # type: (sys.version_info) -> str
    """
    Converts sys.version_info into a string with the format x.x.x
    """
    return "%d.%d.%d" % (vi.major, vi.minor, vi.micro)


def get_application():
    # type: () -> Application
    """
    Creates an Application Dictionary using ddtrace configurations
    and the System-Specific module
    """
    return {
        "service_name": config.service or "unnamed_python_service",
        "service_version": config.version or "",
        "env": config.env or "",
        "language_name": "python",
        "language_version": format_version_info(sys.version_info),
        "tracer_version": get_version(),
        "runtime_name": sys.implementation.name,
        "runtime_version": format_version_info(sys.implementation.version),
    }  # type: Application


APPLICATION = get_application()
