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
        "patches": str,
        "runtime_name": str,
        "runtime_version": str,
    },
)


def format_version_info(vi):
    # type: (sys.version_info) -> str
    return "%d.%d.%d" % (vi.major, vi.minor, vi.micro)


APPLICATION = {
    "service_name": config.service or "unnamed_python_service",
    "service_version": config.version or "",
    "env": config.env or "",
    "language_name": "python",
    "language_version": format_version_info(sys.version_info),
    "tracer_version": get_version(),
    "patches": "",
    "runtime_name": sys.implementation.name,
    "runtime_version": format_version_info(sys.implementation.version),
}  # type: Application
