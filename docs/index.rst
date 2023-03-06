.. include:: ./shared.rst

Datadog Python APM Client
=========================

``ddtrace`` is Datadog's Python APM client. It is used to profile code and
trace requests as they flow across web servers, databases and microservices.
This enables developers to have greater visibility into bottlenecks and
troublesome requests in their application.

Getting Started
---------------

For a basic product overview: check out the `setup documentation`_.

For details about developing and contributing: refer to the `development
guide`_.

For descriptions of the terminology of Datadog APM: take a look at the `official
documentation`_.


.. _`Supported Libraries`:

Supported Libraries
-------------------

We officially support Python 2.7, 3.5 and above.

The versions listed are the versions that we have tested, but ``ddtrace`` can
still be compatible with other versions of these libraries. If a version of a
library you use is unsupported, feel free to contribute or request it by
contacting support.


.. |SUPPVER| replace:: Supported Version
.. |AUTO| replace:: Automatically Instrumented

.. ddtrace-integration-version-support::
    ignore: ["asyncio", "aws_lambda", "boto", "dbapi", "futures", "gunicorn", "logging"]


.. _`Instrumentation Telemetry`:

Instrumentation Telemetry
-------------------------

Datadog may gather environmental and diagnostic information about instrumentation libraries; this includes information
about the host running an application, operating system, programming language and runtime, APM integrations used,
and application dependencies. Additionally, Datadog may collect information such as diagnostic logs, crash dumps
with obfuscated stack traces, and various system performance metrics.

To disable set ``DD_INSTRUMENTATION_TELEMETRY_ENABLED=false`` environment variable.

See our official `datadog documentation <https://docs.datadoghq.com/tracing/configure_data_security#telemetry-collection>`_ for more details.

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

.. toctree::
    :hidden:

    installation_quickstart
    configuration
    integrations
    basic_usage
    advanced_usage
    benchmarks
    contributing
    troubleshooting
    versioning
    upgrading
    api
    release_notes
