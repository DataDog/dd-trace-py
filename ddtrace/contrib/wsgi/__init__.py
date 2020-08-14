"""
The Datadog WSGI Middleware provides tracing, error tracking and automatic RUM
injection.


Enabling
~~~~~~~~

The WSGI instrumentation will be automatically added to a Django or Flask
application when using :ref:`ddtrace-run<ddtracerun>` or
:ref:`patch_all()<patch_all>`.


Usage
~~~~~

If the auto instrumentation does not work or finer grained control is desired,
the middleware can be used manually::


    from ddtrace.contrib.wsgi import DDTraceMiddleware

    # application is a WSGI application
    application = DDTraceMiddleware(application)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.wsgi["service"]

   The service name reported for the WSGI application.

   This option can also be set with the ``DD_SERVICE`` environment
   variable.

   Default: ``"wsgi"``

.. py:data:: ddtrace.config.rum_header_injection

   Enable injecting RUM headers to HTTP responses.

   Default: ``True``
"""
from .wsgi import DDWSGIMiddleware

__all__ = [
    "DDWSGIMiddleware",
]
