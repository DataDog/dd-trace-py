"""
Datadog APM traces can be integrated with the logs product by:

1. Having ``ddtrace`` patch the ``logging`` module. This will add trace
attributes to the log record.

2. Updating the log formatter used by the application. In order to inject
tracing information into a log the formatter must be updated to include the
tracing attributes from the log record.


Enabling
--------

Patch ``logging``
~~~~~~~~~~~~~~~~~

If using :ref:`ddtrace-run<ddtracerun>` then set the environment variable ``DD_LOGS_INJECTION=true``.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(logging=True)


Update Log Format
~~~~~~~~~~~~~~~~~

Make sure that your log format exactly matches the following::

    import logging
    from ddtrace import tracer

    FORMAT = ('%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] '
              '[dd.service=%(dd.service)s dd.env=%(dd.env)s '
              'dd.version=%(dd.version)s '
              'dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s]'
              '- %(message)s')
    logging.basicConfig(format=FORMAT)
    log = logging.getLogger()
    log.level = logging.INFO


    @tracer.wrap()
    def hello():
        log.info('Hello, World!')

    hello()
"""

from ...internal.utils.importlib import require_modules


required_modules = ["logging"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch"]
