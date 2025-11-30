import platform
import typing as t


class PlatformTag:
    # Architecture
    OS_ARCHITECTURE = "os.architecture"

    # Platform
    OS_PLATFORM = "os.platform"

    # Version
    OS_VERSION = "os.version"

    # Runtime Name
    RUNTIME_NAME = "runtime.name"

    # Runtime Version
    RUNTIME_VERSION = "runtime.version"


def get_platform_tags() -> t.Dict[str, str]:
    """Extract configuration facet tags for OS and Python runtime."""
    return {
        PlatformTag.OS_ARCHITECTURE: platform.machine(),
        PlatformTag.OS_PLATFORM: platform.system(),
        PlatformTag.OS_VERSION: platform.release(),
        PlatformTag.RUNTIME_NAME: platform.python_implementation(),
        PlatformTag.RUNTIME_VERSION: platform.python_version(),
    }
