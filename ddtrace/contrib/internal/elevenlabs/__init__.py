"""
The ElevenLabs integration instruments Python Agents conversations over WebSocket.

Enable it with ddtrace-run or ddtrace.patch(elevenlabs=True) and enable
LLM Observability to collect conversation turns, transcripts, client tools, and audio.
Synchronous and asynchronous conversations with default or custom audio interfaces are
supported with ElevenLabs 2.70.0 and later.

Negotiated mono PCM16 audio is wrapped as WAV. Audio has a shared 4 MiB encoded
budget per response; oversized or unsupported audio is omitted while transcripts remain.
Timing describes audio sent or handed to the SDK, with estimated playback and interruption
truncation. These turns opt out of time-to-first-agent-audio measurements.

Standalone speech-to-text, text-to-speech, and browser transports are not instrumented.
"""
