Advanced Usage
==============

Agent Configuration
-------------------

If the Datadog Agent is on a separate host from your application, you can modify
the default ``ddtrace.tracer`` object to utilize another hostname and port. Here
is a small example showcasing this::

    from ddtrace import tracer

    tracer.configure(hostname=<YOUR_HOST>, port=<YOUR_PORT>)

By default, these will be set to localhost and 8126 respectively.

Distributed Tracing
-------------------

To trace requests across hosts, the spans on the secondary hosts must be linked together by setting `trace_id`, `parent_id` and `sampling_priority`.

- On the server side, it means to read propagated attributes and set them to the active tracing context.
- On the client side, it means to propagate the attributes, commonly as a header/metadata.

`ddtrace` already provides default propagators but you can also implement your own.

Web Frameworks
^^^^^^^^^^^^^^

Some web framework integrations support the distributed tracing out of the box, you just have to enable it.
For that, refer to the configuration of the given integration.
Supported web frameworks:

- Django
- Flask
- Tornado

For web servers not supported, you can extract the HTTP context from the headers using the `HTTPPropagator`.

.. autoclass:: ddtrace.propagation.http.HTTPPropagator
    :members: extract

HTTP Client
^^^^^^^^^^^

When calling a remote HTTP server part of the distributed trace, you have to propagate the HTTP headers.
This is not done automatically to prevent your system from leaking tracing information to external services.

.. autoclass:: ddtrace.propagation.http.HTTPPropagator
    :members: inject

Custom
^^^^^^

You can manually propagate your tracing context over your RPC protocol. Here is an example assuming that you have `rpc.call`
function that call a `method` and propagate a `rpc_metadata` dictionary over the wire::


    # Implement your own context propagator
    MyRPCPropagator(object):
        def inject(self, span_context, rpc_metadata):
            rpc_metadata.update({
                'trace_id': span_context.trace_id,
                'span_id': span_context.span_id,
                'sampling_priority': span_context.sampling_priority,
            })

        def extract(self, rpc_metadata):
            return Context(
                trace_id=rpc_metadata['trace_id'],
                span_id=rpc_metadata['span_id'],
                sampling_priority=rpc_metadata['sampling_priority'],
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

.. _`Priority Sampling`:

Priority Sampling
^^^^^^^^^^^^^^^^^

Priority sampling gives you control over whether or not a trace will be
propagated. This is done by associating a priority attribute on a trace that
will be propagated along with the trace. The priority value informs the Agent
and the backend about how to deal with the trace. By default priorities are set
on a trace by a sampler.

The sampler can set the priority to the following values:

- ``AUTO_REJECT``: the sampler automatically rejects the trace
- ``AUTO_KEEP``: the sampler automatically keeps the trace

For now, priority sampling is disabled by default. Enabling it ensures that your
sampled distributed traces will be complete.  To enable priority sampling::

    tracer.configure(priority_sampling=True)

Once enabled, the sampler will automatically assign a priority to your traces,
depending on their service and volume.

You can also set this priority manually to either drop an uninteresting trace or
to keep an important one.
To do this, set the ``context.sampling_priority`` to one of the following:

- ``USER_REJECT``: the user asked to reject the trace
- ``USER_KEEP``: the user asked to keep the trace

When not using distributed tracing, you may change the priority at any time, as
long as the trace is not finished yet.
But it has to be done before any context propagation (fork, RPC calls) to be
effective in a distributed context.
Changing the priority after context has been propagated causes different parts
of a distributed trace to use different priorities. Some parts might be kept,
some parts might be rejected, and this can cause the trace to be partially
stored and remain incomplete.

If you change the priority, we recommend you do it as soon as possible, when the
root span has just been created::

    from ddtrace.ext.priority import USER_REJECT, USER_KEEP

    context = tracer.context_provider.active()

    # indicate to not keep the trace
    context.sampling_priority = USER_REJECT

    # indicate to keep the trace
    span.context.sampling_priority = USER_KEEP


Pre-sampling
^^^^^^^^^^^^

Pre-sampling will completely disable instrumentation of some transactions and
drop the trace at the client level. Information will be lost but it allows to
control any potential performance impact.

``RateSampler`` randomly samples a percentage of traces::

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

    Tracer.configure(settings={
        'FILTERS': [
            FilterRequestsOnUrl(r'http://test\.example\.com'),
        ],
    })

All the filters in the filters list will be evaluated sequentially
for each trace and the resulting trace will either be sent to the Agent or
discarded depending on the output.

**Use the standard filters**

The library comes with a ``FilterRequestsOnUrl`` filter that can be used to
filter out incoming requests to specific urls:

.. autoclass:: ddtrace.filters.FilterRequestsOnUrl
    :members:

**Write a custom filter**

Creating your own filters is as simple as implementing a class with a
``process_trace`` method and adding it to the filters parameter of
Tracer.configure. process_trace should either return a trace to be fed to the
next step of the pipeline or ``None`` if the trace should be discarded::

    class FilterExample(object):
        def process_trace(self, trace):
            # write here your logic to return the `trace` or None;
            # `trace` instance is owned by the thread and you can alter
            # each single span or the whole trace if needed

    # And then instantiate it with
    filters = [FilterExample()]
    Tracer.configure(settings={'FILTERS': filters})

(see filters.py for other example implementations)


.. _ddtracerun:

``ddtrace-run``
---------------

``ddtrace-run`` will trace :ref:`supported<Supported Libraries>` web frameworks
and database modules without the need for changing your code::

  $ ddtrace-run -h

  Execute the given Python program, after configuring it
  to emit Datadog traces.

  Append command line arguments to your program as usual.

  Usage: [ENV_VARS] ddtrace-run <my_program>


The available environment variables for ``ddtrace-run`` are:

* ``DATADOG_TRACE_ENABLED=true|false`` (default: true): Enable web framework and
  library instrumentation. When false, your application code will not generate
  any traces.
* ``DATADOG_ENV`` (no default): Set an application's environment e.g. ``prod``,
  ``pre-prod``, ``stage``
* ``DATADOG_TRACE_DEBUG=true|false`` (default: false): Enable debug logging in
  the tracer
* ``DATADOG_SERVICE_NAME`` (no default): override the service name to be used
  for this program. This value is passed through when setting up middleware for
  web framework integrations (e.g. pylons, flask, django). For tracing without a
  web integration, prefer setting the service name in code.
* ``DATADOG_PATCH_MODULES=module:patch,module:patch...`` e.g.
  ``boto:true,redis:false``: override the modules patched for this execution of
  the program (default: none)
* ``DATADOG_TRACE_AGENT_HOSTNAME=localhost``: override the address of the trace
  agent host that the default tracer will attempt to submit to  (default:
  ``localhost``)
* ``DATADOG_TRACE_AGENT_PORT=8126``: override the port that the default tracer
  will submit to  (default: 8126)
* ``DATADOG_PRIORITY_SAMPLING`` (default: false): enables :ref:`Priority Sampling`

``ddtrace-run`` respects a variety of common entrypoints for web applications:

- ``ddtrace-run python my_app.py``
- ``ddtrace-run python manage.py runserver``
- ``ddtrace-run gunicorn myapp.wsgi:application``
- ``ddtrace-run uwsgi --http :9090 --wsgi-file my_app.py``


Pass along command-line arguments as your program would normally expect them::

$ ddtrace-run gunicorn myapp.wsgi:application --max-requests 1000 --statsd-host localhost:8125

*As long as your application isn't running in* ``DEBUG`` *mode, this should be
enough to see your application traces in Datadog.*

If you're running in a Kubernetes cluster, and still don't see your traces, make
sure your application has a route to the tracing Agent. An easy way to test this
is with a::


$ pip install ipython
$ DATADOG_TRACE_DEBUG=true ddtrace-run ipython

Because iPython uses SQLite, it will be automatically instrumented, and your
traces should be sent off. If there's an error, you'll see the message in the
console, and can make changes as needed.


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
.. autoclass:: ddtrace.Pin
    :members:
    :special-members: __init__

.. _patch_all:

``patch_all``
^^^^^^^^^^^^^

.. autofunction:: ddtrace.monkey.patch_all

``patch``
^^^^^^^^^
.. autofunction:: ddtrace.monkey.patch

.. toctree::
   :maxdepth: 2
