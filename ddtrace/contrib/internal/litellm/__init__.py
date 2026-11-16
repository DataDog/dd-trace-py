"""
The LiteLLM integration instruments the LiteLLM Python SDK and proxy server.

All traces submitted from the LiteLLM integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- ``litellm.request.model``: Model used in the request. This may be just the model name (e.g. ``gpt-3.5-turbo``) or the model name with the route defined (e.g. ``openai/gpt-3.5-turbo``).
- ``litellm.request.host``: Host where the request is sent (if specified).


Enabling
~~~~~~~~

The LiteLLM integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the LiteLLM integration::

    from ddtrace import patch

    patch(litellm=True)


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.litellm["service"]

   The service name reported by default for LiteLLM requests.

   Alternatively, set this option with the ``DD_LITELLM_SERVICE`` environment variable.


Usage and cost metrics
~~~~~~~~~~~~~~~~~~~~~~

Usage and cost metrics for LiteLLM proxy calls are off by default. See
`AI Costs <https://docs.datadoghq.com/cloud_cost_management/ai_costs/>`_ for the metrics and how to use them.

.. py:data:: ddtrace.config.litellm["usage_metrics_enabled"]

   Emit usage and cost metrics for LiteLLM proxy calls.

   Alternatively, set this option with the ``DD_LITELLM_USAGE_METRICS_ENABLED`` environment variable.

   Default: ``False``

.. py:data:: ddtrace.config.litellm["usage_metrics_exporter"]

   Where to send the metrics: ``dogstatsd`` (the Datadog Agent), or ``otlp`` for backends other than Datadog.

   Alternatively, set this option with the ``DD_LITELLM_USAGE_METRICS_EXPORTER`` environment variable.

   Default: ``dogstatsd``

.. py:data:: ddtrace.config.litellm["usage_metrics_tags"]

   Comma-separated optional tags to add: ``user``, ``team``, ``key_alias``, ``route``, ``destination``,
   ``provider_account``, ``project``, ``service`` and ``host``.

   Alternatively, set this option with the ``DD_LITELLM_USAGE_METRICS_TAGS`` environment variable.

   Default: ``None``

.. py:data:: ddtrace.config.litellm["usage_metrics_client_source"]

   The client application name, sent as ``trajectory.client_source``.

   Alternatively, set this option with the ``DD_LITELLM_USAGE_METRICS_CLIENT_SOURCE`` environment variable.

   Default: ``None``
"""  # noqa: E501
