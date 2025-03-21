"""
The foo integration instruments the bar and baz features of the
foo library.


Enabling
~~~~~~~~

The foo integration is enabled automatically when using
:ref:`ddtrace-run <ddtracerun>` or :ref:`import ddtrace.auto <ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(foo=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.foo["service"]

   The service name reported by default for foo instances.

   This option can also be set with the ``DD_FOO_SERVICE`` environment
   variable.

   Default: ``"foo"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the foo integration on an per-instance basis use the
``Pin`` API::

    import foo
    from ddtrace.trace import Pin

    myfoo = foo.Foo()
    Pin.override(myfoo, service="myfoo")
"""

from .patch import get_version
from .patch import patch
from .patch import unpatch


__all__ = ["patch", "unpatch", "get_version"]
