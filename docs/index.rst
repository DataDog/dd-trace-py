.. include:: ./shared.rst

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


.. _`Supported Libraries`:

Supported Libraries
-------------------

We officially support Python 2.7, 3.4 and above.

The versions listed are the versions that we have tested, but ``ddtrace`` can
still be compatible with other versions of these libraries. If a version of a
library you use is unsupported, feel free to contribute or request it by
contacting support.


.. |SUPPVER| replace:: Supported Version
.. |AUTO| replace:: Automatically Instrumented


+--------------------------------------------------+---------------+----------------+
|                   Integration                    |  |SUPPVER|    | |AUTO|    [1]_ |
+==================================================+===============+================+
| :ref:`aiobotocore`                               | >= 0.2.3      | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`aiohttp`                                   | >= 1.2        | Yes [2]_       |
+--------------------------------------------------+---------------+----------------+
| :ref:`aiopg`                                     | >= 0.12.0     | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`boto2`                                     | >= 2.29.0     | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`botocore`                                  | >= 1.4.51     | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`bottle`                                    | >= 0.11       | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`celery`                                    | >= 3.1        | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`cassandra`                                 | >= 3.5        | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`django`                                    | >= 1.8        | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`djangorestframework <djangorestframework>` | >= 3.4        | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`elasticsearch`                             | >= 1.6        | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`falcon`                                    | >= 1.0        | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`flask`                                     | >= 0.10       | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`flask_cache`                               | >= 0.12       | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`gevent`                                    | >= 1.0        | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`grpc`                                      | >= 1.8.0      | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`jinja2`                                    | >= 2.7        | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`mako`                                      | >= 0.1.0      | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`kombu`                                     | >= 4.0        | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`molten`                                    | >= 0.7.0      | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`mongoengine`                               | >= 0.11       | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`mysql-connector`                           | >= 2.1        | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`MySQL-python <MySQL-python>`               | >= 1.2.3      | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`mysqlclient <mysqlclient>`                 | >= 1.3        | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`psycopg2`                                  | >= 2.4        | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`pylibmc`                                   | >= 1.4        | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`pylons`                                    | >= 0.9.6      | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`pymemcache`                                | >= 1.3        | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`pymongo`                                   | >= 3.0        | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`pyramid`                                   | >= 1.7        | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`redis`                                     | >= 2.6        | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`rediscluster`                              | >= 1.3.5      | Yes            |
+--------------------------------------------------+---------------+----------------+
| :ref:`requests`                                  | >= 2.08       | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`sqlalchemy`                                | >= 1.0        | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`tornado`                                   | >= 4.0        | No             |
+--------------------------------------------------+---------------+----------------+
| :ref:`vertica`                                   | >= 0.6        | Yes            |
+--------------------------------------------------+---------------+----------------+


.. [1] Libraries that are automatically instrumented when the
  :ref:`ddtrace-run<ddtracerun>` command is used or the ``patch_all()`` method
  is called. Always use ``patch()`` and ``patch_all()`` as soon as possible in
  your Python entrypoint.

.. [2] only third-party modules such as aiohttp_jinja2


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

.. toctree::
    :hidden:

    installation_quickstart
    web_integrations
    db_integrations
    async_integrations
    other_integrations
    basic_usage
    advanced_usage
