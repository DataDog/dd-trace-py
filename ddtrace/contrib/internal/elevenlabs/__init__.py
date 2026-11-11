"""
The ElevenLabs integration instruments Python Agents conversations over WebSocket.

Enable it with ``ddtrace-run`` or ``ddtrace.patch(elevenlabs=True)`` and enable
LLM Observability to collect conversation turns, transcripts, client tools, and audio.
Synchronous and asynchronous conversations with default or custom audio interfaces are
supported with ElevenLabs 2.70.0 and later.

Negotiated mono PCM16 audio is wrapped as WAV. Audio has a shared 4 MiB encoded
budget per response. Audio that exceeds the budget or uses an unsupported format is omitted
while transcripts remain.
Timing describes audio sent or handed to the SDK, with estimated playback and interruption
truncation. These turns opt out of time-to-first-agent-audio measurements.

For improved user-speaking colors, enable the optional ``vad_score`` client event
in the ElevenLabs agent's Advanced settings under Client Events and save the agent.
Retain the application's other client events. The integration never changes agent
configuration or event subscriptions. Without usable VAD, existing audio playback
and phase-based colors remain available. VAD timing is estimated and does not enable
latency measurements.

Standalone speech-to-text, text-to-speech, and browser transports are not instrumented.

Conversation costs (unreleased)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``LLMObs.submit_conversation_cost(details)`` to submit USD billing from a
completed conversation-details response your application already obtained. For
application-verified post-call transcription webhook data, set
``source="post_call_transcription"``. This API requires Datadog backend support
and a released tracer version exposing the method; it performs no polling or
additional ElevenLabs requests.

The reported conversation total includes the provider's LLM and platform prices.
One dedicated workflow owns the charge. Missing provider amounts remain unknown;
``total_cost`` can supply a finite, nonnegative user override, including zero,
on the first submission. Late initial billing is supported. Delivery and updates
to missing values are supported for 24 hours after the first known total is
accepted; conflicting amounts and expired updates are rejected asynchronously.

``True`` means the observation was queued locally for every requested destination,
not that it was delivered or accepted for billing. With multiple routing
destinations, ``False`` can mean some queues accepted it while others were full.
Retrying the same conversation does not add charges. ``context_usage`` and audio
duration do not establish billable usage, and conversation charges are not allocated
to turns.

"""
