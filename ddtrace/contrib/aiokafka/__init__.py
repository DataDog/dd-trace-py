"""
This integration instruments the ``aiokafka<https://github.com/aio-libs/aiokafka>``
library to trace event streaming.

Enabling
~~~~~~~~

The aiokafka integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(aiokafka=True)
    import aiokafka
    ...

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.aiokafka["service"]

   The service name reported by default for your kafka spans.

   This option can also be set with the ``DD_AIOKAFKA_SERVICE`` environment
   variable.

   Default: ``"kafka"``


To configure the aiokafka integration using the
``Pin`` API::

    from ddtrace import Pin
    from ddtrace import patch

    # Make sure to patch before importing confluent_kafka
    patch(aiokafka=True)

    import aiokafka

    Pin.override(aiokafka, service="custom-service-name")
"""

from ...internal.utils.importlib import require_modules


required_modules = ["aiokafka"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import get_version
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch", "get_version"]
