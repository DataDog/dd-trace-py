Datadog Trace Client
====================

`ddtrace` is Datadog's Python tracing client. It is used to trace requests as
they flow across web servers, databases and microservices so developers
have great visibility into bottlenecks and troublesome requests.

Installation
------------

Install with :code:`pip` but point to Datadog's package repo::

    $ pip install ddtrace --find-links=https://s3.amazonaws.com/pypi.datadoghq.com/trace/index.html

We strongly suggest pinning the version number you deploy while we are
in beta.

Get Started
-----------

Patching
~~~~~~~~

Datadog Tracing can automatically instrument many widely used Python libraries
and frameworks.

The easiest way to get started with tracing is to instrument your web server.
We support many `Web Frameworks`_. Install the middleware for yours.

Then let's patch all the widely used Python libraries that you are running::

    # Add the following a the main entry point of your application.
    from ddtrace import patch_all
    patch_all()

Start your web server and you should be off to the races.

If you want to restrict the set of instrumented libraries, you can either say
which ones to instrument, or which ones not to::

    from ddtrace import patch_all, patch

    # Patch all libraries, except mysql and pymongo
    patch_all(mysql=False, pymongo=False)

    # Only patch redis and elasticsearch, raising an exception if one fails
    patch(redis=True, elasticsearch=True, raise_errors=True)

Custom Tracing
~~~~~~~~~~~~~~

You can easily extend the spans we collect by adding your own traces. Here's a
small example that shows adding a custom span to a Flask application::

    from ddtrace import tracer

    # add the `wrap` decorator to trace an entire function.
    @tracer.wrap()
    def save_thumbnails(img, sizes):

        thumbnails = [resize_image(img, size) for size in sizes]

        # Or just trace part of a function with the `trace`
        # context manager.
        with tracer.trace("thumbnails.save") as span:
            span.set_meta("thumbnails.sizes", str(sizes))
            span.set_metric("thumbnails.count", len(span))

            image_server.store(thumbnails)


Read the full `API`_ for more details.


Sampling
--------

It is possible to sample traces with `ddtrace`.
While the Trace Agent already samples traces to reduce the bandwidth usage, this client sampling
reduces performance overhead.

`RateSampler` samples a ratio of the traces. Its usage is simple::

    from ddtrace.sampler import RateSampler

    # Sample rate is between 0 (nothing sampled) to 1 (everything sampled).
    # Sample 50% of the traces.
    sample_rate = 0.5
    tracer.sampler = RateSampler(sample_rate)



Glossary
~~~~~~~~

**Service**

The name of a set of processes that do the same job. Some examples are :code:`datadog-web-app` or :code:`datadog-metrics-db`. In general, you only need to set the
service in your application's top level entry point.

**Resource**

A particular query to a service. For a web application, some
examples might be a URL stem like :code:`/user/home` or a handler function
like :code:`web.user.home`. For a sql database, a resource
would be the sql of the query itself like :code:`select * from users
where id = ?`.

You can track thousands (not millions or billions) of unique resources per services, so prefer
resources like :code:`/user/home` rather than :code:`/user/home?id=123456789`.

**App**

Currently, an "app" doesn't provide much functionality and is subject to change in the future. For example, in the UI, hovering over the type icon (Web/Database/Custom) will display the “app” for a particular service. In the future the UI may use "app" as hints to group services together better and surface relevant metrics.

**Span**

A span tracks a unit of work in a service, like querying a database or
rendering a template. Spans are associated with a service and optionally a
resource. Spans have names, start times, durations and optional tags.

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

.. toctree::
   :maxdepth: 2

.. _integrations:


Web Frameworks
--------------

Django
~~~~~~

.. automodule:: ddtrace.contrib.django

Pylons
~~~~~~

.. automodule:: ddtrace.contrib.pylons

Falcon
~~~~~~

.. automodule:: ddtrace.contrib.falcon

Flask
~~~~~

.. automodule:: ddtrace.contrib.flask

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

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

