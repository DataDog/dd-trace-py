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

If neither ``ddtrace-run`` nor ``import ddtrace.auto`` are suitable for your application, then
:py:func:`ddtrace.patch_all` can be used to configure the tracer::

    from ddtrace import config, patch_all

    config.env = "dev"      # the environment the application is in
    config.service = "app"  # name of your application
    config.version = "0.1"  # version of your application
    patch_all()


.. note::
    We recommend the use of ``ddtrace-run`` when possible. If you are importing
    ``ddtrace.auto`` as a programmatic replacement for ``ddtrace``, then note
    that integrations will take their configuration from the environment
    variables. A call to :py:func:`ddtrace.patch_all` cannot be used to disable
    an integration at this point.


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

OpenTracing
-----------

``ddtrace`` also provides an OpenTracing API to the Datadog tracer so
that you can use the Datadog tracer in your OpenTracing-compatible
applications.

Installation
~~~~~~~~~~~~

Include OpenTracing with ``ddtrace``::

  $ pip install ddtrace[opentracing]

To include the OpenTracing dependency in your project with ``ddtrace``, ensure
you have the following in ``setup.py``::

    install_requires=[
        "ddtrace[opentracing]",
    ],

Configuration
~~~~~~~~~~~~~

The OpenTracing convention for initializing a tracer is to define an
initialization method that will configure and instantiate a new tracer and
overwrite the global ``opentracing.tracer`` reference.

Typically this method looks something like::

    from ddtrace.opentracer import Tracer, set_global_tracer

    def init_tracer(service_name):
        """
        Initialize a new Datadog opentracer and set it as the
        global tracer.

        This overwrites the opentracing.tracer reference.
        """
        config = {
          'agent_hostname': 'localhost',
          'agent_port': 8126,
        }
        tracer = Tracer(service_name, config=config)
        set_global_tracer(tracer)
        return tracer

For more advanced usage of OpenTracing in ``ddtrace`` refer to the
documentation :ref:`here<adv_opentracing>`.
