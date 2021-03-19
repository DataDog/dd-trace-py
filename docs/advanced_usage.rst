Advanced Usage
==============

.. _agentconfiguration:

Agent Configuration
-------------------

If the Datadog Agent is on a separate host from your application, you can modify
the default ``ddtrace.tracer`` object to utilize another hostname and port. Here
is a small example showcasing this::

    from ddtrace import tracer

    tracer.configure(hostname=<YOUR_HOST>, port=<YOUR_PORT>, https=<True/False>)

By default, these will be set to ``localhost``, ``8126``, and ``False`` respectively.

You can also use a Unix Domain Socket to connect to the agent::

    from ddtrace import tracer

    tracer.configure(uds_path="/path/to/socket")


Distributed Tracing
-------------------

To trace requests across hosts, the spans on the secondary hosts must be linked together by setting `trace_id` and `parent_id`.

- On the server side, it means to read propagated attributes and set them to the active tracing context.
- On the client side, it means to propagate the attributes, commonly as a header/metadata.

`ddtrace` already provides default propagators but you can also implement your own.

Web Frameworks
^^^^^^^^^^^^^^

Some web framework integrations support distributed tracing out of the box.

Supported web frameworks:


+-------------------+---------+
| Framework/Library | Enabled |
+===================+=========+
| :ref:`aiohttp`    | True    |
+-------------------+---------+
| :ref:`bottle`     | True    |
+-------------------+---------+
| :ref:`django`     | True    |
+-------------------+---------+
| :ref:`falcon`     | True    |
+-------------------+---------+
| :ref:`flask`      | True    |
+-------------------+---------+
| :ref:`pylons`     | True    |
+-------------------+---------+
| :ref:`pyramid`    | True    |
+-------------------+---------+
| :ref:`requests`   | True    |
+-------------------+---------+
| :ref:`tornado`    | True    |
+-------------------+---------+


HTTP Client
^^^^^^^^^^^

For distributed tracing to work, necessary tracing information must be passed
alongside a request as it flows through the system. When the request is handled
on the other side, the metadata is retrieved and the trace can continue.

To propagate the tracing information, HTTP headers are used to transmit the
required metadata to piece together the trace.

.. autoclass:: ddtrace.propagation.http.HTTPPropagator
    :members:

Custom
^^^^^^

You can manually propagate your tracing context over your RPC protocol. Here is
an example assuming that you have `rpc.call` function that call a `method` and
propagate a `rpc_metadata` dictionary over the wire::


    # Implement your own context propagator
    class MyRPCPropagator(object):
        def inject(self, span_context, rpc_metadata):
            rpc_metadata.update({
                'trace_id': span_context.trace_id,
                'span_id': span_context.span_id,
            })

        def extract(self, rpc_metadata):
            return Context(
                trace_id=rpc_metadata['trace_id'],
                span_id=rpc_metadata['span_id'],
            )

    # On the parent side
    def parent_rpc_call():
        with tracer.trace("parent_span") as span:
            rpc_metadata = {}
            propagator = MyRPCPropagator()
            propagator.inject(span.context, rpc_metadata)
            method = "<my rpc method>"
            rpc.call(method, metadata)

    # On the child side
    def child_rpc_call(method, rpc_metadata):
        propagator = MyRPCPropagator()
        context = propagator.extract(rpc_metadata)
        tracer.context_provider.activate(context)

        with tracer.trace("child_span") as span:
            span.set_meta('my_rpc_method', method)


Sampling
--------

Client Sampling
^^^^^^^^^^^^^^^

Client sampling enables the sampling of traces before they are sent to the
Agent. This can provide some performance benefit as the traces will be
dropped in the client.

The ``RateSampler`` randomly samples a percentage of traces::

    from ddtrace.sampler import RateSampler

    # Sample rate is between 0 (nothing sampled) to 1 (everything sampled).
    # Keep 20% of the traces.
    sample_rate = 0.2
    tracer.sampler = RateSampler(sample_rate)


Resolving deprecation warnings
------------------------------
Before upgrading, it’s a good idea to resolve any deprecation warnings raised by your project.
These warnings must be fixed before upgrading, otherwise the ``ddtrace`` library
will not work as expected. Our deprecation messages include the version where
the behavior is altered or removed.

In Python, deprecation warnings are silenced by default. To enable them you may
add the following flag or environment variable::

    $ python -Wall app.py

    # or

    $ PYTHONWARNINGS=all python app.py


Trace Filtering
---------------

It is possible to filter or modify traces before they are sent to the Agent by
configuring the tracer with a filters list. For instance, to filter out
all traces of incoming requests to a specific url::

    from ddtrace import tracer

    tracer.configure(settings={
        'FILTERS': [
            FilterRequestsOnUrl(r'http://test\.example\.com'),
        ],
    })

The filters in the filters list will be applied sequentially to each trace
and the resulting trace will either be sent to the Agent or discarded.

**Built-in filters**

The library comes with a ``FilterRequestsOnUrl`` filter that can be used to
filter out incoming requests to specific urls:

.. autoclass:: ddtrace.filters.FilterRequestsOnUrl
    :members:

**Writing a custom filter**

Create a filter by implementing a class with a ``process_trace`` method and
providing it to the filters parameter of :meth:`ddtrace.Tracer.configure()`.
``process_trace`` should either return a trace to be fed to the next step of
the pipeline or ``None`` if the trace should be discarded::

    from ddtrace import Span, tracer
    from ddtrace.filters import TraceFilter

    class FilterExample(TraceFilter):
        def process_trace(self, trace):
            # type: (List[Span]) -> Optional[List[Span]]
            ...

    # And then configure it with
    tracer.configure(settings={'FILTERS': [FilterExample()]})

(see filters.py for other example implementations)

.. _`Logs Injection`:

Logs Injection
--------------

.. automodule:: ddtrace.contrib.logging

..  _http-tagging:

HTTP tagging
------------

Query String Tracing
^^^^^^^^^^^^^^^^^^^^

It is possible to store the query string of the URL — the part after the ``?``
in your URL — in the ``url.query.string`` tag.

Configuration can be provided both at the global level and at the integration level.

Examples::

    from ddtrace import config

    # Global config
    config.http.trace_query_string = True

    # Integration level config, e.g. 'falcon'
    config.falcon.http.trace_query_string = True

..  _http-headers-tracing:

Headers tracing
^^^^^^^^^^^^^^^


For a selected set of integrations, it is possible to store http headers from both requests and responses in tags.

Configuration can be provided both at the global level and at the integration level.

Examples::

    from ddtrace import config

    # Global config
    config.trace_headers([
        'user-agent',
        'transfer-encoding',
    ])

    # Integration level config, e.g. 'falcon'
    config.falcon.http.trace_headers([
        'user-agent',
        'some-other-header',
    ])

The following rules apply:
  - headers configuration is based on a whitelist. If a header does not appear in the whitelist, it won't be traced.
  - headers configuration is case-insensitive.
  - if you configure a specific integration, e.g. 'requests', then such configuration overrides the default global
    configuration, only for the specific integration.
  - if you do not configure a specific integration, then the default global configuration applies, if any.
  - if no configuration is provided (neither global nor integration-specific), then headers are not traced.

Once you configure your application for tracing, you will have the headers attached to the trace as tags, with a
structure like in the following example::

    http {
      method  GET
      request {
        headers {
          user_agent  my-app/0.0.1
        }
      }
      response {
        headers {
          transfer_encoding  chunked
        }
      }
      status_code  200
      url  https://api.github.com/events
    }

..  _http-custom-error:

Custom Error Codes
^^^^^^^^^^^^^^^^^^
It is possible to have a custom mapping of which HTTP status codes are considered errors.
By default, 500-599 status codes are considered errors.
Configuration is provided both at the global level.

Examples::

    from ddtrace import config

    config.http_server.error_statuses = '500-599'

Certain status codes can be excluded by providing a list of ranges. Valid options:
    - ``400-400``
    - ``400-403,405-499``
    - ``400,401,403``

.. _adv_opentracing:

OpenTracing
-----------


The Datadog opentracer can be configured via the ``config`` dictionary
parameter to the tracer which accepts the following described fields. See below
for usage.

+---------------------+----------------------------------------+---------------+
|  Configuration Key  |              Description               | Default Value |
+=====================+========================================+===============+
| `enabled`           | enable or disable the tracer           | `True`        |
+---------------------+----------------------------------------+---------------+
| `debug`             | enable debug logging                   | `False`       |
+---------------------+----------------------------------------+---------------+
| `agent_hostname`    | hostname of the Datadog agent to use   | `localhost`   |
+---------------------+----------------------------------------+---------------+
| `agent_https`       | use https to connect to the agent      | `False`       |
+---------------------+----------------------------------------+---------------+
| `agent_port`        | port the Datadog agent is listening on | `8126`        |
+---------------------+----------------------------------------+---------------+
| `global_tags`       | tags that will be applied to each span | `{}`          |
+---------------------+----------------------------------------+---------------+
| `sampler`           | see `Sampling`_                        | `AllSampler`  |
+---------------------+----------------------------------------+---------------+
| `uds_path`          | unix socket of agent to connect to     | `None`        |
+---------------------+----------------------------------------+---------------+
| `settings`          | see `Advanced Usage`_                  | `{}`          |
+---------------------+----------------------------------------+---------------+


Usage
^^^^^

**Manual tracing**

To explicitly trace::

  import time
  import opentracing
  from ddtrace.opentracer import Tracer, set_global_tracer

  def init_tracer(service_name):
      config = {
        'agent_hostname': 'localhost',
        'agent_port': 8126,
      }
      tracer = Tracer(service_name, config=config)
      set_global_tracer(tracer)
      return tracer

  def my_operation():
    span = opentracing.tracer.start_span('my_operation_name')
    span.set_tag('my_interesting_tag', 'my_interesting_value')
    time.sleep(0.05)
    span.finish()

  init_tracer('my_service_name')
  my_operation()

**Context Manager Tracing**

To trace a function using the span context manager::

  import time
  import opentracing
  from ddtrace.opentracer import Tracer, set_global_tracer

  def init_tracer(service_name):
      config = {
        'agent_hostname': 'localhost',
        'agent_port': 8126,
      }
      tracer = Tracer(service_name, config=config)
      set_global_tracer(tracer)
      return tracer

  def my_operation():
    with opentracing.tracer.start_span('my_operation_name') as span:
      span.set_tag('my_interesting_tag', 'my_interesting_value')
      time.sleep(0.05)

  init_tracer('my_service_name')
  my_operation()

See our tracing trace-examples_ repository for concrete, runnable examples of
the Datadog opentracer.

.. _trace-examples: https://github.com/DataDog/trace-examples/tree/master/python

See also the `Python OpenTracing`_ repository for usage of the tracer.

.. _Python OpenTracing: https://github.com/opentracing/opentracing-python


**Alongside Datadog tracer**

The Datadog OpenTracing tracer can be used alongside the Datadog tracer. This
provides the advantage of providing tracing information collected by
``ddtrace`` in addition to OpenTracing.  The simplest way to do this is to use
the :ref:`ddtrace-run<ddtracerun>` command to invoke your OpenTraced
application.


Examples
^^^^^^^^

**Celery**

Distributed Tracing across celery tasks with OpenTracing.

1. Install Celery OpenTracing::

    pip install Celery-OpenTracing

2. Replace your Celery app with the version that comes with Celery-OpenTracing::

    from celery_opentracing import CeleryTracing
    from ddtrace.opentracer import set_global_tracer, Tracer

    ddtracer = Tracer()
    set_global_tracer(ddtracer)

    app = CeleryTracing(app, tracer=ddtracer)


Opentracer API
^^^^^^^^^^^^^^

.. autoclass:: ddtrace.opentracer.Tracer
    :members:
    :special-members: __init__


.. _ddtracerun:

``ddtrace-run``
---------------

``ddtrace-run`` will trace :ref:`supported<Supported Libraries>` web frameworks
and database modules without the need for changing your code::

  $ ddtrace-run -h

  Execute the given Python program, after configuring it
  to emit Datadog traces.

  Append command line arguments to your program as usual.

  Usage: ddtrace-run <my_program>


The environment variables for ``ddtrace-run`` used to configure the tracer are
detailed in :ref:`Configuration`.

``ddtrace-run`` respects a variety of common entrypoints for web applications:

- ``ddtrace-run python my_app.py``
- ``ddtrace-run python manage.py runserver``
- ``ddtrace-run gunicorn myapp.wsgi:application``


Pass along command-line arguments as your program would normally expect them::

$ ddtrace-run gunicorn myapp.wsgi:application --max-requests 1000 --statsd-host localhost:8125

If you're running in a Kubernetes cluster and still don't see your traces, make
sure your application has a route to the tracing Agent. An easy way to test
this is with a::

$ pip install ipython
$ DATADOG_TRACE_DEBUG=true ddtrace-run ipython

Because iPython uses SQLite, it will be automatically instrumented and your
traces should be sent off. If an error occurs, a message will be displayed in
the console, and changes can be made as needed.


.. _uwsgi:

uWSGI
-----

The tracer and profiler support uWSGI when configured with the following:

- Threads must be enabled with `enable-threads <https://uwsgi-docs.readthedocs.io/en/latest/Options.html#enable-threads>`_ or with `threads <https://uwsgi-docs.readthedocs.io/en/latest/Options.html#threads>`_ if running uWSGI in multithreaded mode.
- If manual instrumentation and configuration is used, `lazy-apps <https://uwsgi-docs.readthedocs.io/en/latest/Options.html#lazy-apps>`_ must be used.

To enable tracing with automatic instrumentation and configuration with environment variables, use `import <https://uwsgi-docs.readthedocs.io/en/latest/Options.html#import>`_ option with the setting ``ddtrace.bootstrap.customize``. For example, add the following to the uWSGI configuration file::

  import=ddtrace.bootstrap.sitecustomize

**Note:** Automatic instrumentation and configuration using ``ddtrace-run`` is not supported with uWSGI.

To enable tracing with manual instrumentation and configuration, configure uWSGI with the ``lazy-apps`` option and use :ref:`patch_all()<patch_all>` and :ref:`agent configuration<agentconfiguration>` to a WSGI app::

  from ddtrace import patch_all
  from ddtrace import tracer


  patch_all()
  tracer.configure(collect_metrics=True)

  def application(env, start_response):
      with tracer.trace("uwsgi-app"):
          start_response('200 OK', [('Content-Type','text/html')])
          return [b"Hello World"]


API
---

``Tracer``
^^^^^^^^^^
.. autoclass:: ddtrace.Tracer
    :members:
    :special-members: __init__


``Span``
^^^^^^^^
.. autoclass:: ddtrace.Span
    :members:
    :special-members: __init__

``Pin``
^^^^^^^

.. _Pin:

.. autoclass:: ddtrace.Pin
    :members:
    :special-members: __init__

.. _patch_all:

``patch_all``
^^^^^^^^^^^^^

.. autofunction:: ddtrace.monkey.patch_all

.. _patch:

``patch``
^^^^^^^^^
.. autofunction:: ddtrace.monkey.patch

.. toctree::
   :maxdepth: 2
