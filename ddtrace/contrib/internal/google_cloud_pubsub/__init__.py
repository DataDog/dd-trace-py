"""
This integration instruments the `google-cloud-pubsub <https://github.com/googleapis/python-pubsub>`_
library to trace Cloud Pub/Sub publishing.

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

.. py:data:: ddtrace.config.google_cloud_pubsub["service"]

   The service name reported by default for your Pub/Sub spans.

   This option can also be set with the ``DD_GOOGLE_CLOUD_PUBSUB_SERVICE`` environment
   variable.

   Default: ``"google_cloud_pubsub"``

.. py:data:: ddtrace.config.google_cloud_pubsub["distributed_tracing_enabled"]

   Whether to inject trace context into message attributes during publish.
   When enabled, the publish span's context is injected into message attributes,
   allowing downstream consumers to continue the trace.

   This option can also be set with the ``DD_GOOGLE_CLOUD_PUBSUB_PROPAGATION_ENABLED``
   environment variable.

   Default: ``True``

"""
