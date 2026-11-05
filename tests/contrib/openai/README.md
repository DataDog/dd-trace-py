# Realtime interruption tests

The experimental interruption observer is opt-in through the private integration
setting `config.openai["_realtime_barge_in_enabled"] = True`. It is disabled by
default and introduces no new environment variable or provider calls.

With acknowledged server VAD allowing interruptions, the observer retains an
audio-producing turn through its estimated playback window, including the first
turn after connection. Generation end remains the model-work end. State is bounded
and flushed by subsequent observations or connection cleanup; there is no timer
thread that promises submission at an idle connection's deadline.

Completed eligible turn roots carry `metrics.barge_in_count` and versioned
`meta.metadata.voice_barge_in` marker metadata. Incomplete observation omits the
numeric count; known zero is distinct. A response cancelled before producing audio
is outside this population. Counts describe detector episodes during attributable
estimated playback, not user intent or hardware audibility. Client truncation and
server acknowledgement enrich the episode without counting it again.

An explicit nonempty `model_provider` tag on the turn root takes precedence over
endpoint detection. Use `LLMObs.annotation_context(tags={"model_provider": "openai"})`
around the conversation to supply it. Finalization reads that root's stored tags,
including annotations added after creation, rather than the later active context.
Without an override, opaque endpoints retain `unknown`. This affects turn-root
attribution; canonical model/provider fields on child LLM spans are unchanged.

The tests cover the state machine and real patched sync/async connection classes
over fake sockets. Discover environments with `scripts/run-tests --list` and run
the returned hashes through `scripts/run-tests`; do not invoke pytest directly.
Use synthetic audio and text when extending the fixtures.
