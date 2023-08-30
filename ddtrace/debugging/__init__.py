"""
Dynamic Instrumentation
=======================

Enablement
----------

Dynamic Instrumentation can be enabled by setting the
``DD_DYNAMIC_INSTRUMENTATION_ENABLED`` variable to ``true`` in the environment,
when using the ``ddtrace-run`` command. Alternatively, when ``dtrace-run``
cannot be used, it can be enabled programmatically with::

    from ddtrace.debugging import DynamicInstrumentation

    # Enable dynamic instrumentation
    DynamicInstrumentation.enable()

    ...

    # Disable dynamic instrumentation
    DynamicInstrumentation.disable()


Configuration
-------------

See the :ref:`Configuration` page for more details on how to configure
Dynamic Instrumentation.


.. note::
    Dynamic Instrumentation is not supported with Python 3.12.

"""

from ddtrace.debugging._debugger import Debugger as DynamicInstrumentation


__all__ = ["DynamicInstrumentation"]
