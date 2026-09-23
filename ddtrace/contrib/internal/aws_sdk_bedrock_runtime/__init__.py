"""
The AWS SDK Bedrock Runtime integration traces Amazon Nova 2 Sonic voice
conversations made with the asynchronous ``aws-sdk-bedrock-runtime`` package
(version 0.11.0 or later, Python 3.12 or later).

Enabling
~~~~~~~~

Enable LLM Observability and use :ref:`ddtrace-run<ddtracerun>` or
:ref:`import ddtrace.auto<ddtraceauto>`. You can also enable it explicitly::

    from ddtrace import patch

    patch(aws_sdk_bedrock_runtime=True)

The integration observes ``AsyncBedrockRuntimeClient.invoke_model_with_bidirectional_stream``
for model ``amazon.nova-2-sonic-v1:0``. Other models and operations are unchanged.
It uses the application's existing AWS credentials.

Conversation turns
~~~~~~~~~~~~~~~~~~

Each response has a ``nova sonic audio turn`` workflow, with direct
``user speech``, ``nova sonic response`` (LLM), and ``agent speech`` children.
Turns share a session ID and preserve the caller's parent context. The LLM
span contains transcripts, token usage, tool calls/results, and bounded WAV
attachments.

Input clips use provider speech offsets across input content containers.
The initial latency boundary matches OpenAI Realtime: receipt of the speech-end
notification to receipt of the first output audio. It excludes speech-end
detection delay and device playback latency.

Assistant playback is projected from chunk arrival and PCM duration. Queued
chunks are serialized, and silence fills gaps when the audio queue empties. Interruptions
cut this projection at notification receipt. This is an estimate: the SDK
does not report how many samples the user's device actually played.
The span metadata labels this timing and retains speech/detection offsets.

Limitations
~~~~~~~~~~~

Only mono, base64-encoded 16-bit LPCM with a supported negotiated sample rate
is converted to WAV. Oversize, invalid, unavailable, or changed-format audio
falls back to text. Input and output share the inline audio payload budget.
Per-turn usage is attributed when events arrive; cumulative session counters
are converted to increments so they are not charged repeatedly.

Use a new connection for each prompt. Reusing input content containers within
one prompt preserves the sample offset origin. If a different prompt starts on
the same connection, tracing stops after flushing the first prompt because
the second prompt's offset origin has not been verified.

Consume or explicitly close the output stream (or use its async context
manager) to flush the last turn. Closing only the input half permits output
to drain. Final transcripts may arrive late, so a response is retained until
the next response begins or the stream ends.

This integration captures conversation text and audio when LLM Observability
is enabled. Audio stays in bounded in-memory buffers and is submitted
only as an inline WAV attachment.
"""
