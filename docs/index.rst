Datadog Trace Client
====================

`ddtrace` is Datadog's Python tracing client. It is used to trace requests as
they flow across web servers, databases and microservices so developers
have great visibility into bottlenecks and troublesome requests.

Installation
------------

Install with :code:`pip`::

    $ pip install ddtrace

We strongly suggest pinning the version of the library you deploy.

Get Started
-----------

Datadog Tracing can automatically instrument many widely used Python libraries
and frameworks.

Once installed, the package will make the ``ddtrace-run`` command-line entrypoint
available in your Python environment.

``ddtrace-run`` will trace available web frameworks and database modules without the need
for changing your code::


    $ ddtrace-run -h

    Execute the given Python program, after configuring it
    to emit Datadog traces.

    Append command line arguments to your program as usual.

    Usage: [ENV_VARS] ddtrace-run <my_program>


The available environment variables for `ddtrace-run` are:

* ``DATADOG_TRACE_ENABLED=true|false`` (default: true): Enable web framework and library instrumentation. When false, your application code
  will not generate any traces.
* ``DATADOG_ENV``  (no default): Set an application's environment e.g. ``prod``, ``pre-prod``, ``stage``
* ``DATADOG_TRACE_DEBUG=true|false`` (default: false): Enable debug logging in the tracer
* ``DATADOG_SERVICE_NAME`` (no default): override the service name to be used for this program. This value is passed through when setting up middleware for web framework integrations (e.g. pylons, flask, django). For tracing without a web integration, prefer setting the service name in code.
* ``DATADOG_PATCH_MODULES=module:patch,module:patch...`` e.g. ``boto:true,redis:false`` : override the modules patched for this execution of the program (default: none)
* ``DATADOG_TRACE_AGENT_HOSTNAME=localhost`` : override the address of the trace agent host that the default tracer will attempt to submit to  (default: ``localhost``)
* ``DATADOG_TRACE_AGENT_PORT=8126`` : override the port that the default tracer will submit to  (default: 8126)

``ddtrace-run`` respects a variety of common entrypoints for web applications:

- ``ddtrace-run python my_app.py``
- ``ddtrace-run python manage.py runserver``
- ``ddtrace-run gunicorn myapp.wsgi:application``
- ``ddtrace-run uwsgi --http :9090 --wsgi-file my_app.py``


Pass along command-line arguments as your program would normally expect them::

    ddtrace-run gunicorn myapp.wsgi:application --max-requests 1000 --statsd-host localhost:8125

`For most users, this should be sufficient to see your application traces in Datadog.`

`Please read on if you are curious about further configuration, or
would rather set up Datadog Tracing explicitly in code.`


Instrumentation
---------------

Web
~~~

We support many `Web Frameworks`_. Install the middleware for yours.

Databases
~~~~~~~~~

Then let's patch widely used Python libraries::

    # Add the following at the main entry point of your application.
    from ddtrace import patch_all
    patch_all()

Start your web server and you should be off to the races. Here you can find
which `framework is automatically instrumented`_ with the ``patch_all()`` method.

.. _framework is automatically instrumented: #instrumented-libraries

Custom
~~~~~~

You can easily extend the spans we collect by adding your own traces. Here's a
small example that shows adding a custom span to a Flask application::

    from ddtrace import tracer

    # add the `wrap` decorator to trace an entire function.
    @tracer.wrap(service='my-app')
    def save_thumbnails(img, sizes):

        thumbnails = [resize_image(img, size) for size in sizes]

        # Or just trace part of a function with the `trace`
        # context manager.
        with tracer.trace("thumbnails.save") as span:
            span.set_meta("thumbnails.sizes", str(sizes))

            image_server.store(thumbnails)


Read the full `API`_ for more details.

Modifying the Agent hostname and port
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If the Datadog Agent is on a separate host from your application, you can modify the default ddtrace.tracer object to utilize another hostname and port. Here is a small example showcasing this::

    from ddtrace import tracer

    tracer.configure(hostname=<YOUR_HOST>, port=<YOUR_PORT>)

By default, these will be set to localhost and 8126 respectively.

Web Frameworks
--------------

Bottle
~~~~~~

.. automodule:: ddtrace.contrib.bottle

Django
~~~~~~

.. automodule:: ddtrace.contrib.django

Falcon
~~~~~~

.. automodule:: ddtrace.contrib.falcon

Flask
~~~~~

.. automodule:: ddtrace.contrib.flask

Pylons
~~~~~~

.. automodule:: ddtrace.contrib.pylons

Pyramid
~~~~~~~

.. automodule:: ddtrace.contrib.pyramid

aiohttp
~~~~~~~

.. automodule:: ddtrace.contrib.aiohttp

aiobotocore
~~~~~~~~~~~

.. automodule:: ddtrace.contrib.aiobotocore

aiopg
~~~~~

.. automodule:: ddtrace.contrib.aiopg

Tornado
~~~~~~~

.. automodule:: ddtrace.contrib.tornado


Other Libraries
---------------

Boto2
~~~~~~~~~

.. automodule:: ddtrace.contrib.boto

Botocore
~~~~~~~~~

.. automodule:: ddtrace.contrib.botocore

Cassandra
~~~~~~~~~

.. automodule:: ddtrace.contrib.cassandra

Elasticsearch
~~~~~~~~~~~~~

.. automodule:: ddtrace.contrib.elasticsearch

Flask Cache
~~~~~~~~~~~

.. automodule:: ddtrace.contrib.flask_cache

Celery
~~~~~~

.. automodule:: ddtrace.contrib.celery

MongoDB
~~~~~~~

**Mongoengine**

.. automodule:: ddtrace.contrib.mongoengine

**Pymongo**

.. automodule:: ddtrace.contrib.pymongo

Memcached
~~~~~~~~~

**pylibmc**

.. automodule:: ddtrace.contrib.pylibmc

MySQL
~~~~~

.. automodule:: ddtrace.contrib.mysql

Postgres
~~~~~~~~

.. automodule:: ddtrace.contrib.psycopg

Redis
~~~~~

.. automodule:: ddtrace.contrib.redis

Requests
~~~~~

.. automodule:: ddtrace.contrib.requests

SQLAlchemy
~~~~~~~~~~

.. automodule:: ddtrace.contrib.sqlalchemy

SQLite
~~~~~~

.. automodule:: ddtrace.contrib.sqlite3

Asynchronous Libraries
----------------------

asyncio
~~~~~~~

.. automodule:: ddtrace.contrib.asyncio

gevent
~~~~~~

.. automodule:: ddtrace.contrib.gevent


Distributed Tracing
-------------------

To trace requests across hosts, the spans on the secondary hosts must be linked together by setting `trace_id`, `parent_id` and `sampling_priority`.

- On the server side, it means to read propagated attributes and set them to the active tracing context.
- On the client side, it means to propagate the attributes, commonly as a header/metadata.

`ddtrace` already provides default propagators but you can also implement your own.

Web frameworks
~~~~~~~~~~~~~~

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
~~~~~~~~~~~

When calling a remote HTTP server part of the distributed trace, you have to propagate the HTTP headers.
This is not done automatically to prevent your system from leaking tracing information to external services.

.. autoclass:: ddtrace.propagation.http.HTTPPropagator
    :members: inject

Custom
~~~~~~

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
~~~~~~~~~~~~~~~~~

Priority sampling consists in deciding if a trace will be kept by using a `priority` attribute that will be propagated
for distributed traces. Its value gives indication to the Agent and to the backend on how important the trace is.

- -1: The user asked not to keep the trace.
- 0: The sampler automatically decided not to keep the trace.
- 1: The sampler automatically decided to keep the trace.
- 2: The user asked to keep the trace.

For now, priority sampling is disabled by default. Enabling it ensures that your sampled distributed traces will be complete.
To enable the priority sampling::

    tracer.configure(priority_sampling=True)

Once enabled, the sampler will automatically assign a priority of 0 or 1 to traces, depending on their service and volume.

You can also set this priority manually to either drop a non-interesting trace or to keep an important one.
For that, set the `context.sampling_priority` to -1 or 2.
It has to be done before any context propagation (fork, RPC calls) to be effective.
For example, it is possible to select some traces based on some existing bit of information such as a user or transaction ID.
But it is generally not possible to select a trace based on its error status,
as this information typically happens later in the process, when context has already been propagated::

    context = tracer.context_provider.active()
    # Indicate to not keep the trace
    context.sampling_priority = -1

    # Indicate to keep the trace
    span.context.sampling_priority = 2


Pre-sampling
~~~~~~~~~~~~

Pre-sampling will completely disable instrumentation of some transactions and drop the trace at the client level.
Information will be lost but it allows to control any potential performance impact.

`RateSampler` ramdomly samples a percentage of traces. Its usage is simple::

    from ddtrace.sampler import RateSampler

    # Sample rate is between 0 (nothing sampled) to 1 (everything sampled).
    # Keep 20% of the traces.
    sample_rate = 0.2
    tracer.sampler = RateSampler(sample_rate)



Advanced Usage
--------------

Trace Filtering
~~~~~~~~~~~~~~~

It is possible to filter or modify traces before they are sent to the agent by
configuring the tracer with a filters list. For instance, to filter out
all traces of incoming requests to a specific url::

    Tracer.configure(settings={
        'FILTERS': [
            FilterRequestsOnUrl(r'http://test\.example\.com'),
        ],
    })

All the filters in the filters list will be evaluated sequentially
for each trace and the resulting trace will either be sent to the agent or
discarded depending on the output.

**Use the standard filters**

The library comes with a FilterRequestsOnUrl filter that can be used to
filter out incoming requests to specific urls:

.. autoclass:: ddtrace.filters.FilterRequestsOnUrl
    :members:

**Write a custom filter**

Creating your own filters is as simple as implementing a class with a
process_trace method and adding it to the filters parameter of
Tracer.configure. process_trace should either return a trace to be fed to the
next step of the pipeline or None if the trace should be discarded::

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
~~~

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

.. _integrations:

Glossary
~~~~~~~~

**Service**

The name of a set of processes that do the same job. Some examples are :code:`datadog-web-app` or :code:`datadog-metrics-db`. In general, you only need to set the
service in your application's top level entry point.

**Resource**

A particular query to a service. For a web application, some
examples might be a URL stem like :code:`/user/home` or a handler function
like :code:`web.user.home`. For a SQL database, a resource
would be the sql of the query itself like :code:`select * from users
where id = ?`.

You can track thousands (not millions or billions) of unique resources per services, so prefer
resources like :code:`/user/home` rather than :code:`/user/home?id=123456789`.

**App**

Currently, an "app" doesn't provide much functionality and is subject to change in the future. For example, in the UI, hovering over the type icon (Web/Database/Custom) will display the “app” for a particular service. In the future the UI may use "app" as hints to group services together better and surface relevant metrics.

**Span**

A span tracks a unit of work in a service, like querying a database or
rendering a template. Spans are associated with a service and optionally a
resource. A span has a name, start time, duration and optional tags.

Supported versions
==================

We officially support Python 2.7, 3.4 and above.

+-----------------+--------------------+
| Integrations    | Supported versions |
+=================+====================+
| aiohttp         | >= 1.2             |
+-----------------+--------------------+
| aiobotocore     | >= 0.2.3           |
+-----------------+--------------------+
| aiopg           | >= 0.12.0          |
+-----------------+--------------------+
| boto            | >= 2.29.0          |
+-----------------+--------------------+
| botocore        | >= 1.4.51          |
+-----------------+--------------------+
| bottle          | >= 0.12            |
+-----------------+--------------------+
| celery          | >= 3.1             |
+-----------------+--------------------+
| cassandra       | >= 3.5             |
+-----------------+--------------------+
| django          | >= 1.8             |
+-----------------+--------------------+
| elasticsearch   | >= 1.6             |
+-----------------+--------------------+
| falcon          | >= 1.0             |
+-----------------+--------------------+
| flask           | >= 0.10            |
+-----------------+--------------------+
| flask_cache     | >= 0.12            |
+-----------------+--------------------+
| gevent          | >= 1.0             |
+-----------------+--------------------+
| mongoengine     | >= 0.11            |
+-----------------+--------------------+
| mysql-connector | >= 2.1             |
+-----------------+--------------------+
| psycopg2        | >= 2.5             |
+-----------------+--------------------+
| pylibmc         | >= 1.4             |
+-----------------+--------------------+
| pylons          | >= 1.0             |
+-----------------+--------------------+
| pymongo         | >= 3.0             |
+-----------------+--------------------+
| pyramid         | >= 1.7             |
+-----------------+--------------------+
| redis           | >= 2.6             |
+-----------------+--------------------+
| sqlalchemy      | >= 1.0             |
+-----------------+--------------------+


These are the fully tested versions but `ddtrace` can be compatible with lower versions.
If some versions are missing, you can contribute or ask for it by contacting our support.
For deprecated library versions, the support is best-effort.

Instrumented libraries
======================

The following is the list of libraries that are automatically instrumented when the
``patch_all()`` method is called. Always use ``patch()`` and ``patch_all()`` as
soon as possible in your Python entrypoint.

* sqlite3
* mysql
* psycopg
* redis
* cassandra
* pymongo
* mongoengine
* elasticsearch
* pylibmc
* celery
* aiopg
* aiohttp (only third-party modules such as ``aiohttp_jinja2``)

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
