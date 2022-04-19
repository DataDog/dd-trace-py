"""
The foo integration instruments the bar and baz features of the
foo library.


Enabling
~~~~~~~~

The foo integration is enabled automatically when using
:ref:`ddtrace-run <ddtracerun>` or :func:`patch_all() <ddtrace.patch_all>`.

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
    from ddtrace import Pin

    myfoo = foo.Foo()
    Pin.override(myfoo, service="myfoo")
"""
from ...internal.utils.importlib import require_modules


required_modules = ["foo"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch"]
