"""
The RQ__ integration will trace your jobs.


Usage
~~~~~

The rq integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(rq=True)


Worker Usage
~~~~~~~~~~~~

``ddtrace-run`` can be used to easily trace your workers::

    DD_SERVICE=myworker ddtrace-run rq worker



Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To override the service name for a queue::

    from ddtrace.trace import Pin

    connection = redis.Redis()
    queue = rq.Queue(connection=connection)
    Pin.override(queue, service="custom_queue_service")


To override the service name for a particular worker::

    worker = rq.SimpleWorker([queue], connection=queue.connection)
    Pin.override(worker, service="custom_worker_service")


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.rq['distributed_tracing_enabled']
.. py:data:: ddtrace.config.rq_worker['distributed_tracing_enabled']

   If ``True`` the integration will connect the traces sent between the enqueuer
   and the RQ worker.

   This option can also be set with the ``DD_RQ_DISTRIBUTED_TRACING_ENABLED``
   environment variable on either the enqueuer or worker applications.

   Default: ``True``

.. py:data:: ddtrace.config.rq['service']

   The service name reported by default for RQ spans from the app.

   This option can also be set with the ``DD_SERVICE`` or ``DD_RQ_SERVICE``
   environment variables.

   Default: ``rq``

.. py:data:: ddtrace.config.rq_worker['service']

   The service name reported by default for RQ spans from workers.

   This option can also be set with the ``DD_SERVICE`` environment
   variable.

   Default: ``rq-worker``

.. __: https://python-rq.org/

"""
