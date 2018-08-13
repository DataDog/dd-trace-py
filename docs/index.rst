Datadog Python Trace Client
===========================

``ddtrace`` is Datadog's Python tracing client. It is used to trace requests as
they flow across web servers, databases and microservices. This enables
developers to have greater visibility into bottlenecks and troublesome requests
in their application.

Getting Started
---------------

For a basic product overview: check out the `setup documentation`_.

For details about developing and contributing: refer to the `development
guide`_.

For descriptions of the terminology of Datadog APM: take a look at the `official
documentation`_.


.. _setup documentation: https://docs.datadoghq.com/tracing/setup/python/

.. _official documentation: https://docs.datadoghq.com/tracing/visualization/

.. _development guide: https://github.com/datadog/dd-trace-py#development


Supported Libraries
-------------------

We officially support Python 2.7, 3.4 and above.

The versions listed are the versions that we have tested, but ``ddtrace`` can
still be compatible with other versions of these libraries. If a version of a
library you use is unsupported, feel free to contribute or request it by
contacting support. For deprecated library versions, the support is best-effort.

TODO
* link integrations to the integration doc
* check that each version is listed and supported


Instrumented libraries
======================


+---------------------+--------------------+------------------------+
| Integrations        | Supported versions | Autoinstrumented [1]_  |
+=====================+====================+========================+
| aiohttp             | >= 1.2             |          Yes [2]_      |
+---------------------+--------------------+------------------------+
| aiobotocore         | >= 0.2.3           |          No            |
+---------------------+--------------------+------------------------+
| aiopg               | >= 0.12.0          |          Yes           |
+---------------------+--------------------+------------------------+
| boto                | >= 2.29.0          |          Yes           |
+---------------------+--------------------+------------------------+
| botocore            | >= 1.4.51          |          Yes           |
+---------------------+--------------------+------------------------+
| bottle              | >= 0.11            |          No            |
+---------------------+--------------------+------------------------+
| celery              | >= 3.1             |          Yes           |
+---------------------+--------------------+------------------------+
| cassandra           | >= 3.5             |          Yes           |
+---------------------+--------------------+------------------------+
| djangorestframework | >= 3.4             |          No            |
+---------------------+--------------------+------------------------+
| django              | >= 1.8             |          No            |
+---------------------+--------------------+------------------------+
| elasticsearch       | >= 1.6             |          Yes           |
+---------------------+--------------------+------------------------+
| falcon              | >= 1.0             |          No            |
+---------------------+--------------------+------------------------+
| flask               | >= 0.10            |          No            |
+---------------------+--------------------+------------------------+
| flask_cache         | >= 0.12            |          No            |
+---------------------+--------------------+------------------------+
| gevent              | >= 1.0             |          No            |
+---------------------+--------------------+------------------------+
| mongoengine         | >= 0.11            |          Yes           |
+---------------------+--------------------+------------------------+
| mysql-connector     | >= 2.1             |          No            |
+---------------------+--------------------+------------------------+
| MySQL-python        | >= 1.2.3           |          No            |
+---------------------+--------------------+------------------------+
| mysqlclient         | >= 1.3             |          No            |
+---------------------+--------------------+------------------------+
| psycopg2            | >= 2.4             |          Yes           |
+---------------------+--------------------+------------------------+
| pylibmc             | >= 1.4             |          Yes           |
+---------------------+--------------------+------------------------+
| pylons              | >= 0.9.6           |          No            |
+---------------------+--------------------+------------------------+
| pymemcache          | >= 1.3             |          Yes           |
+---------------------+--------------------+------------------------+
| pymongo             | >= 3.0             |          Yes           |
+---------------------+--------------------+------------------------+
| pyramid             | >= 1.7             |          No            |
+---------------------+--------------------+------------------------+
| redis               | >= 2.6             |          Yes           |
+---------------------+--------------------+------------------------+
| sqlalchemy          | >= 1.0             |           No           |
+---------------------+--------------------+------------------------+
| tornado             | >= 4.0             |           No           |
+---------------------+--------------------+------------------------+


.. [1] Autoinstrumented libraries are automatically instrumented when the
  ``patch_all()`` method is called. Always use ``patch()`` and ``patch_all()`` as
  soon as possible in your Python entrypoint.

.. [2] only third-party modules such as aiohttp_jinja2


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

.. toctree::
    :hidden:

    installation
    quickstart
    auto_instrumentation
    web_integrations
    db_integrations
    async_integrations
    other_integrations
    auto_instrumentation
    basic_usage
    advanced_usage
