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

Web
~~~

The easiest way to get started with tracing is to instrument your web server.
We support many `Web Frameworks`_. Install the middleware for yours.

Databases
~~~~~~~~~

Then let's patch widely used Python libraries::

    # Add the following at the main entry point of your application.
    from ddtrace import patch_all
    patch_all()

Start your web server and you should be off to the races.

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
            span.set_metric("thumbnails.count", len(span))

            image_server.store(thumbnails)


Read the full `API`_ for more details.

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


Other Libraries
---------------

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

Tutorials
---------

Sampling
~~~~~~~~

It is possible to sample traces with `ddtrace`.
While the Trace Agent already samples traces to reduce the bandwidth usage, this client sampling
reduces performance overhead.

`RateSampler` samples a ratio of the traces. Its usage is simple::

    from ddtrace.sampler import RateSampler

    # Sample rate is between 0 (nothing sampled) to 1 (everything sampled).
    # Sample 50% of the traces.
    sample_rate = 0.5
    tracer.sampler = RateSampler(sample_rate)

Distributed Tracing
~~~~~~~~~~~~~~~~~~~

To trace requests across hosts, the spans on the secondary hosts must be linked together by setting `trace_id` and `parent_id`::

    def trace_request_on_secondary_host(parent_trace_id, parent_span_id):
        with tracer.trace("child_span") as span:
            span.parent_id = parent_span_id
            span.trace_id = parent_trace_id


Users can pass along the parent_trace_id and parent_span_id via whatever method best matches the RPC framework. For example, with HTTP headers (Using Python Flask)::

    def parent_rpc_call():
        with tracer.trace("parent_span") as span:
            import requests
            headers = {'x-ddtrace-parent_trace_id':span.trace_id,
                       'x-ddtrace-parent_span_id':span.span_id}
            url = "<some RPC endpoint>"
            r = requests.get(url, headers=headers)


    from flask import request
    parent_trace_id = request.headers.get(‘x-ddtrace-parent_trace_id‘)
    parent_span_id = request.headers.get(‘x-ddtrace-parent_span_id‘)
    child_rpc_call(parent_trace_id, parent_span_id)


    def child_rpc_call(parent_trace_id, parent_span_id):
        with tracer.trace("child_span") as span:
            span.parent_id = parent_span_id
            span.trace_id = parent_trace_id

Advanced Usage
--------------

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
| bottle          | >= 1.2             |
+-----------------+--------------------+
| celery          | >= 3.1             |
+-----------------+--------------------+
| cassandra       | >= 3.5             |
+-----------------+--------------------+
| django          | >= 1.8             |
+-----------------+--------------------+
| elasticsearch   | >= 2.3             |
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
| psycopg2        | >= 2.4             |
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

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

