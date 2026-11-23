"""Catalog of the signal kinds that probes can emit.

Each signal class declares the probe types it handles, and registers itself
when its module is imported. This module imports every signal module, so
signal_for_probe can resolve every probe type once this module is loaded.
When adding a new signal kind, import its module here.
"""

from ddtrace.debugging._signal import metric_sample  # noqa: F401
from ddtrace.debugging._signal import snapshot  # noqa: F401
from ddtrace.debugging._signal import tracing  # noqa: F401
from ddtrace.debugging._signal import trigger  # noqa: F401
from ddtrace.debugging._signal.model import _signal_for_probe as signal_for_probe


__all__ = ["signal_for_probe"]
