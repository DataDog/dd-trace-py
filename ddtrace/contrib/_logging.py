"""
Datadog APM traces can be integrated with the logs product by:

1. Having ``ddtrace`` patch the ``logging`` module. This will add trace
attributes to the log record.

2. Updating the log formatter used by the application. In order to inject
tracing information using the log the formatter must be updated to include the
tracing attributes from the log record.


Enabling
--------

Patch ``logging``
~~~~~~~~~~~~~~~~~

Datadog support for built-in logging is enabled by default when you either: run your application
with the ddtrace-run command, or Import ddtrace.auto in your code. If you are using the ddtrace
library directly, you can enable logging support by calling: ``ddtrace.patch(logging=True)``.
Note: Directly enabling integrations via ddtrace.patch(...) is not recommended.


Update Log Format
~~~~~~~~~~~~~~~~~

Make sure that your log format supports the following attributes: ``dd.trace_id``, ``dd.span_id``,
``dd.service``, ``dd.env``, ``dd.version``. These values will be automatically added to
the log record by the ``ddtrace`` library.

Example::

    import logging
    from ddtrace.trace import tracer

    FORMAT = ('%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] '
              '[dd.service=%(dd.service)s dd.env=%(dd.env)s '
              'dd.version=%(dd.version)s '
              'dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s] '
              '- %(message)s')
    logging.basicConfig(format=FORMAT)
    log = logging.getLogger()
    log.level = logging.INFO


    @tracer.wrap()
    def hello():
        log.info('Hello, World!')

    hello()

Note that most host based setups log by default to UTC time.
If the log timestamps aren't automatically in UTC, the formatter can be updated to use UTC::

    import time
    logging.Formatter.converter = time.gmtime

For more information, please see the attached guide on common timestamp issues:
https://docs.datadoghq.com/logs/guide/logs-not-showing-expected-timestamp/
"""
