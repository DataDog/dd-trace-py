"""
The OpenAI Agents integration instruments the openai-agents Python library to emit traces for agent workflows.

All traces submitted from the OpenAI Agents integration are tagged by:
- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.

Enabling
~~~~~~~~

The OpenAI Agents integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the OpenAI Agents integration::

    from ddtrace import patch

    patch(openai_agents=True)


Realtime agents
~~~~~~~~~~~~~~~

Realtime WebSocket conversations are instrumented independently of the SDK's provider tracing.
Realtime support requires ``openai-agents>=0.2.0`` and ``websockets>=14.0``; attaching to a SIP or WebRTC call through
``call_id`` requires ``openai-agents>=0.5.0``. Older non-Realtime Agents versions remain supported.
Enable LLM Observability to capture one workflow per turn, with model generation and speech
windows, transcripts, audio, tools, and provider-reported token usage.

On call attachments, server playback events determine the agent speech window. Audio is retrieved
from stored conversation items, preferring requests already made by the SDK or application.
Successful retrieval events remain visible to application listeners. Set
``DD_OPENAI_REALTIME_SIDEBAND_AUDIO_RETRIEVAL_ENABLED=false`` to disable additional audio requests;
passive observation of existing retrieval replies continues. Set ``DD_OPENAI_REALTIME_ENABLED=false``
to disable Realtime instrumentation while retaining ordinary Agents instrumentation.

Retained turns and retrieved audio are bounded. An incomplete, oversized, malformed, or
codec-inconsistent recording is omitted while available transcripts are retained. The integration
tracks at most 16 turns and 256 stored items, with a 12 MiB budget for retained encoded retrieved
audio and rebuilt clips. This budget excludes SDK history, parsed messages, codec scratch space,
exported span payloads, and total process memory. Audio and transcription waits expire, and
connection termination cancels the integration's outstanding work.

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.openai_agents["service"]

   The service name reported by default for OpenAI Agents requests.

   Alternatively, set this option with the ``DD_OPENAI_AGENTS_SERVICE`` environment variable.
"""  # noqa: E501
