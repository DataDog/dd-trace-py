.. ddtrace documentation master file, created by
   sphinx-quickstart on Thu Jul  7 17:25:05 2016.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Datadog Trace Client
====================

`ddtrace` is Datadog's tracing client for Python. It is used to trace requests as
they flow across web servers, databases and microservices so that developers
have great visiblity into bottlenecks and troublesome requests.


Installation
------------

Install with :code:`pip` but point to Datadog's package repo::

    $ pip install ddtrace --find-links=https://s3.amazonaws.com/pypi.datadoghq.com/trace/index.html

If you are using a supported integration, proceed to the :ref:`relevant instructions <integrations>` next.

Quick Start
-----------

Adding tracing to your code is very simple. As an example, let's imagine we are adding
tracing from scratch to a small web app::

    from ddtrace import tracer

    service = 'my-web-site'

    @route("/home")
    def home(request):

        with tracer.trace('web.request') as span:
            # set some span metadata
            span.service = service
            span.resource = "home"
            span.set_tag('web.user', request.username)

            # trace a database request
            with tracer.trace('users.fetch'):
                user = db.fetch_user(request.username)

            # trace a template render
            with tracer.trace('template.render'):
                return render_template('/templates/user.html', user=user)


Glossary
--------

**Service**

The name of a set of processes that do the same job. Some examples are :code:`datadog-web-app` or :code:`datadog-metrics-db`.

**Resource**

A particular query to a service. For a web application, some
examples might be a URL stem like :code:`/user/home` or a handler function
like :code:`web.user.home`. For a sql database, a resource
would be the sql of the query itself like :code:`select * from users
where id = ?`.

You can track thousands (not millions or billions) of unique resources per services, so prefer
resources like :code:`/user/home` rather than :code:`/user/home?id=123456789`.

**App**

The name of the code that a service is running. Some common open source
examples are :code:`postgres`, :code:`rails` or :code:`redis`. If it's running
custom code, name it accordingly like :code:`datadog-metrics-db`.

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

.. toctree::
   :maxdepth: 2

.. _integrations:


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


Integrations
------------


Cassandra
~~~~~~~~~

.. automodule:: ddtrace.contrib.cassandra

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

Flask-cache
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

.. autofunction:: ddtrace.contrib.sqlite3.connection_factory


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

