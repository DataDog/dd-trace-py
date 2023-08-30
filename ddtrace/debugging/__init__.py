"""
Dynamic Instrumentation
=======================

Configuration
-------------

When using ``ddtrace-run``, dynamic instrumentation can be enabled by setting
the ``DD_DYNAMIC_INSTRUMENTATION_ENABLED`` variable, or programmatically with::

    from ddtrace.debugging import DynamicInstrumentation

    # Enable dynamic instrumentation
    DynamicInstrumentation.enable()

    ...

    # Disable the debugger
    DynamicInstrumentation.disable()

.. note::
    Dynamic Instrumentation is not supported with Python 3.12.

"""

from ddtrace.debugging._debugger import Debugger as DynamicInstrumentation


__all__ = ["DynamicInstrumentation"]
