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
from ...internal.utils.importlib import require_modules


required_modules = ["unittest"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        # Required to allow users to import from `ddtrace.contrib.unittest.patch` directly
        from . import patch as _  # noqa: F401, I001

        # Expose public methods
        from ..internal.unittest.patch import get_version
        from ..internal.unittest.patch import patch
        from ..internal.unittest.patch import unpatch

        __all__ = ["patch", "unpatch", "get_version"]
