"""
The Coverage.py integration traces test code coverage when using `pytest` or `unittest`.


Enabling
~~~~~~~~

The Coverage.py integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Use DD_TRACE_<INTEGRATION>_ENABLED environment variable to enable or disable this integration.

Note: Coverage.py instrumentation is only enabled if `pytest` or `unittest` instrumentation is enabled.
"""
# Required to allow users to import from  `ddtrace.contrib.internal.coverage.patch` directly
import warnings as _w  # noqa:E402


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001

# Expose public methods
from ddtrace.contrib.internal.coverage.patch import get_version
from ddtrace.contrib.internal.coverage.patch import patch
from ddtrace.contrib.internal.coverage.patch import unpatch
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

__all__ = ["patch", "unpatch", "get_version"]
