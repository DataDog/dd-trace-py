"""
This integration instruments the `google-cloud-pubsub <https://github.com/googleapis/python-pubsub>`_
library to trace Cloud Pub/Sub publishing and consuming.

Enabling
~~~~~~~~

The google_cloud_pubsub integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(google_cloud_pubsub=True)
    from google.cloud import pubsub_v1
    ...

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.google_cloud_pubsub["distributed_tracing_enabled"]

   Whether to enable distributed tracing between Pub/Sub messages.

   This option can also be set with the ``DD_GOOGLE_CLOUD_PUBSUB_PROPAGATION_ENABLED``
   environment variable.

   Default: ``True``

.. py:data:: ddtrace.config.google_cloud_pubsub["reparent_enabled"]

   Whether to re-parent subscriber spans under the producer trace context.
   When enabled, the receive span becomes a child of the publishing span,
   linking producer and consumer traces. Requires ``distributed_tracing_enabled``
   to be ``True``.

   This option can also be set with the ``DD_GOOGLE_CLOUD_PUBSUB_REPARENT_ENABLED``
   environment variable.

   Default: ``True``

"""
