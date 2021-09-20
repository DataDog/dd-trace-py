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

    It is strongly suggested to pin the version of the library you deploy.


Installation on Alpine
~~~~~~~~~~~~~~~~~~~~~~

Binary distributions are not available for Alpine so build dependencies must be installed first.

.. code-block:: bash

    apk add gcc musl-dev linux-headers
    pip install ddtrace


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


If ``ddtrace-run`` isn't suitable for your application then :ref:`patch_all`
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
