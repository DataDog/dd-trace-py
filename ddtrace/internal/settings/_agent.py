"""
Compatibility shim exposing ``ddtrace.settings._agent`` via the legacy
``ddtrace.internal.settings._agent`` import path.
"""

from ddtrace.settings._agent import *  # noqa: F401,F403
