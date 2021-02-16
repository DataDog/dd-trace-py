"""
The Datadog WSGI middleware traces all WSGI requests.


Usage
~~~~~

The middleware can be used manually via the following command::


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

.. py:data:: ddtrace.config.wsgi["distributed_tracing"]

   Configuration that allows distributed tracing to be enabled.

   Default: ``True``


:ref:`All HTTP tags <http-tagging>` are supported for this integration.

"""
from .wsgi import DDWSGIMiddleware


__all__ = [
    "DDWSGIMiddleware",
]
