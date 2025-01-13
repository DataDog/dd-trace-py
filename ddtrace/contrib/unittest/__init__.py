"""
The unittest integration traces test executions.


Enabling
~~~~~~~~

The unittest integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternately, use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(unittest=True)

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.unittest["operation_name"]

   The operation name reported by default for unittest traces.

   This option can also be set with the ``DD_UNITTEST_OPERATION_NAME`` environment
   variable.

   Default: ``"unittest.test"``

   .. py:data:: ddtrace.config.unittest["strict_naming"]

   Requires all ``unittest`` tests to start with ``test`` as stated in the Python documentation

   This option can also be set with the ``DD_CIVISIBILITY_UNITTEST_STRICT_NAMING`` environment
   variable.

   Default: ``True``
"""


from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate

from .patch import get_version  # noqa: F401
from .patch import patch  # noqa: F401
from .patch import unpatch  # noqa: F401


deprecate(
    ("%s is deprecated" % (__name__)),
    message="Avoid using this package directly. "
    "Use ``ddtrace.auto`` or the ``ddtrace-run`` command to enable and configure this integration.",
    category=DDTraceDeprecationWarning,
    removal_version="3.0.0",
)
