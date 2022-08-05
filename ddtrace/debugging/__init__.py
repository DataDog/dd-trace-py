"""
DD Debugger
===========

Configuration
-------------

When using ``ddtrace-run``, the debugger can be enabled by setting the
``DD_DEBUGGER_ENABLED`` variable, or programmatically with::

    from ddtrace.debugging import Debugger

    # Enable the debugger
    Debugger.enable()

    ...

    # Disable the debugger
    Debugger.disable()
"""

from ddtrace.debugging._debugger import Debugger


__all__ = ["Debugger"]
