"""
Datadog APM traces can be integrated with the logs produced by structlog by:

1. Having ``ddtrace`` patch the ``structlog`` module. This will add a
processor in the beginning of the chain that adds trace attributes
to the event_dict

2. Ensure the configuration has at the minimum a processor chain that outputs
to a log location without errors. Automatic Log injection will not occur if a
processor chain is not configured.

3. For log correlation between APM and logs, the easiest format is via JSON
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
However, because the last processor in the chain must be a renderer that can be outputted to
a log, the integration will not add attributes if no pre-existing processor chain exists.

An example of a proper configuration that can be injected into is as below:

    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=structlog.WriteLoggerFactory(file=Path("app").with_suffix(".log").open("wt")))

For more information, please see the attached guide for the Datadog Logging Product:
https://docs.datadoghq.com/logs/log_collection/python/
"""

from ...internal.utils.importlib import require_modules


required_modules = ["structlog"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import get_version
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch", "get_version"]
