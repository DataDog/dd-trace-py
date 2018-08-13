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

Web frameworks
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

HTTP client
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

Priority sampling
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


API
---

.. autoclass:: ddtrace.Tracer
    :members:
    :special-members: __init__


.. autoclass:: ddtrace.Span
    :members:
    :special-members: __init__

.. autoclass:: ddtrace.Pin
    :members:
    :special-members: __init__

.. autofunction:: ddtrace.monkey.patch_all

.. autofunction:: ddtrace.monkey.patch

.. toctree::
   :maxdepth: 2
