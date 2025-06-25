"""
Datadog APM traces can be integrated with the logs produced by structlog by:

1. Having ``ddtrace`` patch the ``structlog`` module. This will add a
processor in the beginning of the chain that adds trace attributes
to the event_dict

2. For log correlation between APM and logs, the easiest format is via JSON
so that no further configuration needs to be done in the Datadog UI assuming
that the Datadog trace values are at the top level of the JSON

Enabling
--------

Patch ``structlog``
~~~~~~~~~~~~~~~~~~~

If using :ref:`ddtrace-run<ddtracerun>` then set the environment variable ``DD_LOGS_INJECTION=true``.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(structlog=True)

Proper Formatting
~~~~~~~~~~~~~~~~~

The trace attributes are injected via a processor in the processor block of the configuration
whether that be the default processor chain or a user-configured chain.

An example of a configuration that outputs to a file that can be injected into is as below::

    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=structlog.WriteLoggerFactory(file=Path("app").with_suffix(".log").open("wt")))

For more information, please see the attached guide for the Datadog Logging Product:
https://docs.datadoghq.com/logs/log_collection/python/
"""
