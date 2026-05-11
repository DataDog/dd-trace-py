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

Push Subscriptions
~~~~~~~~~~~~~~~~~~

Push subscriptions are also supported. When a push subscription delivers a message
via HTTP to your web server, the integration creates an inferred ``gcp.pubsub.receive``
span that captures subscription and message metadata.

For push subscription instrumentation to work, the subscription must be configured with:

- **Enable payload unwrapping** (``--push-no-wrapper``): delivers the raw message data
  as the HTTP request body instead of the default Pub/Sub JSON wrapper.
- **Write metadata** (``--push-no-wrapper-write-metadata``): writes Pub/Sub metadata
  (subscription name, message ID, and trace context) as HTTP headers on the push request.

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.google_cloud_pubsub["distributed_tracing_enabled"]

   Whether to enable distributed tracing between Pub/Sub messages.

   This option can also be set with the ``DD_GOOGLE_CLOUD_PUBSUB_PROPAGATION_ENABLED``
   environment variable.

   Default: ``True``

.. py:data:: ddtrace.config.google_cloud_pubsub["propagation_as_span_links"]

   Whether to attach propagated context as span links instead of re-parenting
   subscriber spans under the producer trace. When disabled (the default), the
   receive span becomes a child of the publishing span, linking producer and
   consumer traces. When enabled, the receive span starts a new trace and the
   producer context is attached as a span link. Requires
   ``distributed_tracing_enabled`` to be ``True``.

   This option can also be set with the ``DD_GOOGLE_CLOUD_PUBSUB_PROPAGATION_AS_SPAN_LINKS``
   environment variable.

   Default: ``False``

"""
