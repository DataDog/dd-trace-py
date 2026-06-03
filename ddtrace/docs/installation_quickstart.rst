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

For example, if you start your application with ``python app.py`` then run (replacing
placeholders with actual values for your environment variables)::

    DD_SERVICE=<app_name> DD_ENV=<environment> DD_VERSION=<version> ddtrace-run python app.py

For more advanced usage of ``ddtrace-run`` refer to the documentation
:ref:`here<ddtracerun>`.

To verify the environment configuration for your application run the command ``ddtrace-run --info``.
This command prints useful information for debugging, ensuring that your environment variable configurations
are recognized and that the tracer will be able to connect to the Datadog agent with them.
Note: ``--info`` only reflects configurations set via environment variables, not those set within code.


When ``ddtrace-run`` cannot be used, a similar start-up behavior can be achieved
with the import of ``ddtrace.auto``. This should normally be imported as the
first thing during the application start-up.


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