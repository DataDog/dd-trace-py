import json
import logging
import platform
import typing as t

from ddtrace.internal.settings import env
from ddtrace.testing.internal.constants import DD_TEST_OPTIMIZATION_RUNTIME_TAGS


log = logging.getLogger(__name__)


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


_OVERRIDABLE_TAGS = frozenset(
    (
        PlatformTag.OS_ARCHITECTURE,
        PlatformTag.OS_PLATFORM,
        PlatformTag.OS_VERSION,
        PlatformTag.RUNTIME_NAME,
        PlatformTag.RUNTIME_VERSION,
    )
)


def get_platform_tags(runtime_tags_overrides: t.Optional[t.Mapping[str, str]] = None) -> dict[str, str]:
    """Extract configuration facet tags for OS and Python runtime, applying DD_TEST_OPTIMIZATION_RUNTIME_TAGS."""
    tags = {
        PlatformTag.OS_ARCHITECTURE: platform.machine(),
        PlatformTag.OS_PLATFORM: platform.system(),
        PlatformTag.OS_VERSION: platform.release(),
        PlatformTag.RUNTIME_NAME: platform.python_implementation(),
        PlatformTag.RUNTIME_VERSION: platform.python_version(),
    }
    tags.update(get_runtime_tags_overrides() if runtime_tags_overrides is None else runtime_tags_overrides)
    return tags


def get_runtime_tags_overrides() -> dict[str, str]:
    """Return the OS and runtime tag values set in DD_TEST_OPTIMIZATION_RUNTIME_TAGS, keyed by tag name."""
    value = env.get(DD_TEST_OPTIMIZATION_RUNTIME_TAGS, "")
    if not value.strip():
        return {}

    try:
        overrides = json.loads(value)
    except (ValueError, RecursionError):
        overrides = None
    if not isinstance(overrides, dict):
        log.warning("Ignoring %s: expected a JSON object, got %r", DD_TEST_OPTIMIZATION_RUNTIME_TAGS, value)
        return {}

    return {
        tag: tag_value.strip()
        for tag, tag_value in overrides.items()
        if tag in _OVERRIDABLE_TAGS and isinstance(tag_value, str) and tag_value.strip() and _is_encodable(tag_value)
    }


def _is_encodable(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
