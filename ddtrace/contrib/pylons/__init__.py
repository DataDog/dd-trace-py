"""
The Pylons__ integration traces requests and template rendering in a Pylons
application.


Enabling
~~~~~~~~

To enable the Pylons integration, wrap a Pylons application with the provided
``PylonsTraceMiddleware``::

    from pylons.wsgiapp import PylonsApp

    from ddtrace import tracer
    from ddtrace.contrib.pylons import PylonsTraceMiddleware

    app = PylonsApp(...)

    traced_app = PylonsTraceMiddleware(app, tracer, service="my-pylons-app")


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.pylons["service"]

   The service name reported by default for Pylons requests.

   This option can also be set with the ``DD_SERVICE`` environment
   variable.

   Default: ``"pylons"``


:ref:`All HTTP tags <http-tagging>` are supported for this integration.

.. __: https://pylonsproject.org/about-pylons-framework.html
"""

from ...utils.importlib import require_modules


required_modules = ["pylons.wsgiapp"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .middleware import PylonsTraceMiddleware
        from .patch import patch
        from .patch import unpatch

        __all__ = [
            "patch",
            "unpatch",
            "PylonsTraceMiddleware",
        ]
