"""
Datadog APM traces can be integrated with the logs product by:

1. Having ``ddtrace`` patch the ``logging`` module. This will add trace
attributes to the log record.

2. Updating the log formatter used by the application. In order to inject
tracing information into a log the formatter must be updated to include the
tracing attributes from the log record. ``ddtrace-run`` will do this
automatically for you by specifying a format. For more detail or instructions
for how to do this manually see the manual section below.

With these in place the trace information will be injected into a log entry
which can be used to correlate the log and trace in Datadog.


ddtrace-run
-----------

When using ``ddtrace-run``, enable patching by setting the environment variable
``DD_LOGS_INJECTION=true``. The logger by default will have a format that
includes trace information::

    import logging
    from ddtrace import tracer

    log = logging.getLogger()
    log.level = logging.INFO


    @tracer.wrap()
    def hello():
        log.info('Hello, World!')

    hello()

Manual Instrumentation
----------------------

If you prefer to instrument manually, patch the logging library then update the
log formatter as in the following example

Make sure that your log format exactly matches the following::

    from ddtrace import patch_all; patch_all(logging=True)
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
