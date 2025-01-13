"""
This integration provides context management for tracing the execution flow
of concurrent execution of ``asyncio.Task``.
"""
# Required to allow users to import from  `ddtrace.contrib.asyncio.patch` directly
# Expose public methods
import warnings as _w  # noqa:E402


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001
from ddtrace._trace.provider import DefaultContextProvider
from ddtrace.contrib.internal.asyncio.helpers import ensure_future  # noqa: F401,E402
from ddtrace.contrib.internal.asyncio.helpers import run_in_executor  # noqa: F401,E402
from ddtrace.contrib.internal.asyncio.helpers import set_call_context  # noqa: F401,E402
from ddtrace.contrib.internal.asyncio.patch import get_version  # noqa: F401,E402
from ddtrace.contrib.internal.asyncio.patch import patch  # noqa: F401,E402
from ddtrace.contrib.internal.asyncio.patch import unpatch  # noqa: F401,E402
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


context_provider = DefaultContextProvider()


deprecate(
    ("%s is deprecated" % (__name__)),
    message="Avoid using this package directly. "
    "Use ``ddtrace.auto`` or the ``ddtrace-run`` command to enable and configure this integration.",
    category=DDTraceDeprecationWarning,
    removal_version="3.0.0",
)
