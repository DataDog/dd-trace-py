.. include:: ./shared.rst


.. _Installation:

Installation + Quickstart
=========================

Before installing be sure to read through the `setup documentation`_ to ensure
your environment is ready to receive traces.


Installation
------------

Install with :code:`pip`::

    pip install ddtrace

.. important::

    pip version 18 and above is required to install the library.


Quickstart
----------

.. important::


    Using `gevent <https://www.gevent.org/>`__? Read our :ref:`gevent documentation<gevent>`.

    Using `Gunicorn <https://gunicorn.org>`__? Read the :ref:`Gunicorn documentation<gunicorn>`.

    Using `uWSGI <https://uwsgi-docs.readthedocs.io>`__? Read our :ref:`uWSGI documentation<uwsgi>`.


Tracing
~~~~~~~

Getting started for tracing is as easy as prefixing your python entry-point
command with ``ddtrace-run``.

For example if you start your application with ``python app.py`` then run (with
your desired settings in place of the example environment variables)::

    DD_SERVICE=app DD_ENV=dev DD_VERSION=0.1 ddtrace-run python app.py

For more advanced usage of ``ddtrace-run`` refer to the documentation
:ref:`here<ddtracerun>`.

To verify the environment configuration for your application run the command ``ddtrace-run --info``. 
This will print out info useful for debugging to make sure your environment variable configurations are 
being picked up correctly and that the tracer will be able to connect to the Datadog agent with them. 
Note: ``--info`` Only reflects configurations made via environment variables, not those made in code.


If ``ddtrace-run`` isn't suitable for your application then :py:func:`ddtrace.patch_all`
can be used::

    from ddtrace import config, patch_all

    config.env = "dev"      # the environment the application is in
    config.service = "app"  # name of your application
    config.version = "0.1"  # version of your application
    patch_all()


Service names also need to be configured for libraries that query other
services (``requests``, ``grpc``, database libraries, etc).  Check out the
:ref:`integration documentation<integrations>` for each to set them up.


For additional configuration see the :ref:`configuration <Configuration>`
documentation.

To learn how to manually instrument check out the :ref:`basic usage <basic
usage>` documentation.


Profiling
~~~~~~~~~

Profiling can also be auto enabled with :ref:`ddtracerun` by providing the
``DD_PROFILING_ENABLED`` environment variable::

    DD_PROFILING_ENABLED=true ddtrace-run python app.py

If ``ddtrace-run`` isn't suitable for your application then
``ddtrace.profiling.auto`` can be used::

    import ddtrace.profiling.auto


Configuration
~~~~~~~~~~~~~

Almost all configuration of ``ddtrace`` can be done via environment
variable. See the full list in :ref:`Configuration`.

OpenTelemetry
-------------

With the `ddtrace.opentelemetry` package, Opentelemetry users can use the `Opentelemetry API <https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-api>` to generate and submit traces to the Datadog Agent. Below are instructions to install and configure opentelemetry support.


Support
~~~~~~~

The `ddtrace.opentelemetry` supports tracing applications using all operations defined in the [opentelmetry python api](https://opentelemetry.io/docs/instrumentation/python/) except for creating span links, generating span events, and using the Metrics API. These operations are not yet supported. Below is a non-exhaustive list of supported operations:
 - Creating a span
 - Activating a span
 - Setting attributes on Span
 - Setting error types and error messages on spans
 - Manual span parenting
 - Distributed tracing (Injecting/Extracting trace headers)

Configuration
~~~~~~~~~~~~~

No additional configurations are required to enable opentelemetry support when ``ddtrace-run`` is used. To enable support for applications using manual instrumentation
follow the instructions below:

1. Set the following environment variable. Note, this environment variable must be set before the first span is generated::
    
    OTEL_PYTHON_CONTEXT=ddcontextvars_context

2. In your application code set the following tracer provider::
    
    import opentelemetry
    from ddtrace.opentelemetry import TracerProvider

    opentelemetry.trace.set_tracer_provider(TracerProvider())


For more advanced usage of OpenTelemetry in ``ddtrace`` refer to the
documentation :ref:`here<adv_opentelemetry>`.
