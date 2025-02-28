"""
Dynamic Instrumentation
=======================

Enablement
----------

Dynamic Instrumentation can be enabled by setting the
``DD_DYNAMIC_INSTRUMENTATION_ENABLED`` variable to ``true`` in the environment,
when using the ``ddtrace-run`` command. Alternatively, when ``ddtrace-run``
cannot be used, it can be enabled programmatically with::

    import ddtrace.auto

Note that the ``DD_DYNAMIC_INSTRUMENTATION_ENABLED`` still needs to be set to
``true`` in the environment.

Alternatively, Dynamic Instrumentation can be enabled programmatically with::

    from ddtrace.debugging import DynamicInstrumentation

    # Enable dynamic instrumentation
    DynamicInstrumentation.enable()

    ...

    # Disable dynamic instrumentation
    DynamicInstrumentation.disable()

However, this method requires that Dynamic Instrumentation is started manually
in every forked process.


Configuration
-------------

See the :ref:`Configuration` page for more details on how to configure
Dynamic Instrumentation.
"""

from ddtrace.internal.module import lazy


@lazy
def _():
    from ddtrace.debugging._debugger import Debugger as DynamicInstrumentation  # noqa
