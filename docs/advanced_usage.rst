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


.. _context:


Context
-------

The :class:`ddtrace.context.Context` object is used to represent the state of
a trace at a point in time. This state includes the trace id, active span id,
distributed sampling decision and more. It is used to propagate the trace
across execution boundaries like processes
(:ref:`Distributed Tracing <disttracing>`), threads and tasks.

To retrieve the context of the currently active trace use::

        context = tracer.current_trace_context()

Note that if there is no active trace then ``None`` will be returned.


Tracing Context Management
--------------------------

In ``ddtrace`` "context management" is the management of which
:class:`ddtrace.Span` or :class:`ddtrace.context.Context` is active in an
execution (thread, task, etc). There can only be one active span or context
per execution at a time.

Context management enables parenting to be done implicitly when creating new
spans by using the active span as the parent of a new span. When an active span
finishes its parent becomes the new active span.

``tracer.trace()`` automatically creates new spans as the child of the active
context::

    # Here no span is active
    assert tracer.current_span() is None

    with tracer.trace("parent") as parent:
        # Here `parent` is active
        assert tracer.current_span() is parent

        with tracer.trace("child") as child:
            # Here `child` is active.
            # `child` automatically inherits from `parent`
            assert tracer.current_span() is child

        # `parent` is active again
        assert tracer.current_span() is parent

    # Here no span is active again
    assert tracer.current_span() is None


.. important::

    Span objects are owned by the execution in which they are created and must
    be finished in the same execution. The span context can be used to continue
    a trace in a different execution by passing it and activating it on the other
    end. See the sections below for how to propagate traces across task, thread or
    process boundaries.


Tracing Across Threads
^^^^^^^^^^^^^^^^^^^^^^

To continue a trace across threads the context needs to be passed between
threads::

    import threading, time
    from ddtrace import tracer

    def _target(trace_ctx):
        tracer.context_provider.activate(trace_ctx)
        with tracer.trace("second_thread"):
            # `second_thread`s parent will be the `main_thread` span
            time.sleep(1)

    with tracer.trace("main_thread"):
        thread = threading.Thread(target=_target, args=(tracer.current_trace_context(),))
        thread.start()
        thread.join()


Tracing Across Processes
^^^^^^^^^^^^^^^^^^^^^^^^

Just like the threading case, if tracing across processes is desired then the
span has to be propagated as a context::

    from multiprocessing import Process
    import time
    from ddtrace import tracer

    def _target(ctx):
        tracer.context_provider.activate(ctx)
        with tracer.trace("proc"):
            time.sleep(1)
        tracer.shutdown()

    with tracer.trace("work"):
        proc = Process(target=_target, args=(tracer.current_trace_context(),))
        proc.start()
        time.sleep(1)
        proc.join()


.. important::

   A :class:`ddtrace.Span` should only be accessed or modified in the process
   that it was created in. Using a :class:`ddtrace.Span` from within a child process
   could result in a deadlock or unexpected behavior.


fork
****
If using `fork()`, any open spans from the parent process must be finished by
the parent process. Any active spans from the original process will be converted
to contexts to avoid memory leaks.

Here's an example of tracing some work done in a child process::

    import os, sys, time
    from ddtrace import tracer

    span = tracer.trace("work")

    pid = os.fork()

    if pid == 0:
        with tracer.trace("child_work"):
            time.sleep(1)
        sys.exit(0)

    # Do some other work in the parent
    time.sleep(1)
    span.finish()
    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 0


Tracing Across Asyncio Tasks
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

By default the active context will by propagated across tasks on creation as
the `contextvars`_ context is copied between tasks. If this is not desirable
then ``None`` can be activated in the new task::

    tracer.context_provider.activate(None)

.. note:: For Python < 3.7 the asyncio integration must be used: :ref:`asyncio`

Manual Management
^^^^^^^^^^^^^^^^^

Parenting can be managed manually by using ``tracer.start_span()`` which by
default does not activate spans when they are created. See the documentation
for :meth:`ddtrace.Tracer.start_span`.


Context Providers
^^^^^^^^^^^^^^^^^

The default context provider used in the tracer uses contextvars_ to store
the active context per execution. This means that any asynchronous library
that uses `contextvars`_ will have support for automatic context management.

If there is a case where the default is insufficient then a custom context
provider can be used. It must implement the
:class:`ddtrace.provider.BaseContextProvider` interface and can be configured
with::

    tracer.configure(context_provider=MyContextProvider)


.. _contextvars: https://docs.python.org/3/library/contextvars.html


.. _disttracing:

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

See :py:class:`HTTPPropagator <ddtrace.propagation.http.HTTPPropagator>` for details.

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
            span.set_tag('my_rpc_method', method)


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

    # Global config
    config.http.trace_query_string = True

    # Integration level config, e.g. 'falcon'
    config.falcon.http.trace_query_string = True

The sensitive query strings (e.g: token, password) are obfuscated by default.

It is possible to configure the obfuscation regexp by setting the ``DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN`` environment variable.

To disable query string obfuscation, set the ``DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN`` environment variable to empty string ("")

If the ``DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN`` environment variable is set to an invalid regexp, the query strings will not be traced.

..  _http-headers-tracing:

Headers tracing
^^^^^^^^^^^^^^^


For a selected set of integrations, it is possible to store http headers from both requests and responses in tags.

The recommended method is to use the ``DD_TRACE_HEADER_TAGS`` environment variable.

Alternatively, configuration can be provided both at the global level and at the integration level in your application code.

Examples::

    from ddtrace import config

    # Global config
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

`--info`: This argument prints an easily readable tracer health check and configurations. It does not reflect configuration changes made at the code level,
only environment variable configurations.

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
$ DD_TRACE_DEBUG=true ddtrace-run ipython

Because iPython uses SQLite, it will be automatically instrumented and your
traces should be sent off. If an error occurs, a message will be displayed in
the console, and changes can be made as needed.


.. _uwsgi:

uWSGI
-----

**Note:** ``ddtrace-run`` is not supported with uWSGI.

``ddtrace`` only supports `uWSGI <https://uwsgi-docs.readthedocs.io/>`__ when configured with each of the following:

- Threads must be enabled with the `enable-threads <https://uwsgi-docs.readthedocs.io/en/latest/Options.html#enable-threads>`__ or `threads <https://uwsgi-docs.readthedocs.io/en/latest/Options.html#threads>`__ options.
- Lazy apps must be enabled with the `lazy-apps <https://uwsgi-docs.readthedocs.io/en/latest/Options.html#lazy-apps>`__ option.
- For automatic instrumentation (like ``ddtrace-run``) set the `import <https://uwsgi-docs.readthedocs.io/en/latest/Options.html#import>`__ option to ``ddtrace.bootstrap.sitecustomize``.

Example with CLI arguments:

.. code-block:: bash

  uwsgi --enable-threads --lazy-apps --import=ddtrace.bootstrap.sitecustomize --master --processes=5 --http 127.0.0.1:8000 --module wsgi:app


Example with uWSGI ini file:

.. code-block:: ini

  ;; uwsgi.ini
  [uwsgi]
  module = wsgi:app
  http = 127.0.0.1:8000

  master = true
  processes = 5

  ;; ddtrace required options
  enable-threads = 1
  lazy-apps = 1
  import=ddtrace.bootstrap.sitecustomize


.. code-block:: bash

  uwsgi --ini uwsgi.ini


.. _gunicorn:

Gunicorn
--------

``ddtrace`` supports `Gunicorn <https://gunicorn.org>`__.

However, if you are using the ``gevent`` worker class, you have to make sure
``gevent`` monkey patching is done before loading the ``ddtrace`` library.

There are different options to make that happen:

- If you rely on ``ddtrace-run``, you must set ``DD_GEVENT_PATCH_ALL=1`` in
  your environment to have gevent patched first-thing.

- Replace ``ddtrace-run`` by using ``import ddtrace.bootstrap.sitecustomize``
  as the first import of your application.

- Use a `post_worker_init <https://docs.gunicorn.org/en/stable/settings.html#post-worker-init>`_
  hook to import ``ddtrace.bootstrap.sitecustomize``.
