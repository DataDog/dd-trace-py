"""
Legacy import path for ``ddtrace.internal.settings._agentless``, which moved to ``ddtrace_base.settings._agentless``.

This module is a thin re-export shim so external consumers of the historical
``ddtrace.internal.settings._agentless`` import path keep working..
Import from ````ddtrace_base.settings._agentless```` instead.
"""

from ddtrace_base.settings._agentless import AgentlessConfig as AgentlessConfig  # noqa: F401
from ddtrace_base.settings._agentless import config as config  # noqa: F401
