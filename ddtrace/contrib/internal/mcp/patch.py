from types import ModuleType

import mcp

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations.mcp import MCPIntegration


log = get_logger(__name__)

config._add(
    "mcp",
    {
        "distributed_tracing": asbool(env.get("DD_MCP_DISTRIBUTED_TRACING", default=True)),
        "capture_intent": asbool(env.get("DD_MCP_CAPTURE_INTENT", default=False)),
    },
)


def get_version() -> str:
    from importlib.metadata import version

    try:
        return version("mcp")
    except Exception:
        return ""


def _supported_versions() -> dict[str, str]:
    return {"mcp": ">=1.10.0"}


def _instrumentation() -> ModuleType:
    """Return the instrumentation module for the installed mcp major version.

    Each module imports the mcp classes it wraps at import time, so only the one matching the installed
    version can be imported. Raises ImportError when mcp is not the MCP SDK.
    """
    if parse_version(get_version()) >= (2, 0, 0):
        from ddtrace.contrib.internal.mcp import _patch_v2

        return _patch_v2

    from ddtrace.contrib.internal.mcp import _patch_v1

    return _patch_v1


def patch():
    if getattr(mcp, "__datadog_patch", False):
        return

    # Claimed before the imports below so a concurrent patch() cannot clear the
    # guard above and wrap everything a second time.
    mcp.__datadog_patch = True

    try:
        instrumentation = _instrumentation()
    except ImportError:
        mcp.__datadog_patch = False
        log.debug("mcp is importable but is not the MCP SDK, skipping instrumentation")
        return

    mcp._datadog_integration = MCPIntegration(integration_config=config.mcp)
    instrumentation.patch()


def unpatch():
    if not getattr(mcp, "__datadog_patch", False):
        return

    mcp.__datadog_patch = False

    # Only reachable with __datadog_patch set, which patch() leaves set only
    # when the import succeeded, so it cannot fail here.
    _instrumentation().unpatch()

    delattr(mcp, "_datadog_integration")
