"""
The Datadog WSGI middleware traces all WSGI requests.


Usage
~~~~~

The middleware can be used manually via the following command::


    from ddtrace.contrib.wsgi import DDWSGIMiddleware

    # application is a WSGI application
    application = DDWSGIMiddleware(application)


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
from ddtrace.contrib.internal.wsgi.wsgi import DDWSGIMiddleware
from ddtrace.contrib.internal.wsgi.wsgi import get_version  # noqa: F401
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


def __getattr__(name):
    if name in ("get_version",):
        deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            message="Use ``import ddtrace.auto`` or the ``ddtrace-run`` command to configure this integration.",
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )

    if name in globals():
        return globals()[name]
    raise AttributeError("%s has no attribute %s", __name__, name)


__all__ = ["DDWSGIMiddleware"]
