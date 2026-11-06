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

The observer uses the effective response audio format when the server supplies an
output override. Unsupported observation policies retain the existing late-audio
truncation behavior after the client has been seen to truncate. Automatic SDK
reconnects establish fresh observation state and a fresh conversation session ID.
Successful audio, text, and tool-result sends queued during recovery are replayed
into the replacement observer before it is published. Recovery observation is
bounded to 1 MiB of serialized events and 1024 events, independently of the SDK's
transport queue. Exceeding either limit, or an ambiguous failed send, leaves the
input clock unknown for the recovered session: later speech onsets use receipt
time and do not trim audio against an invented origin. Ambiguous audio from the
first recovered turn uses the existing oversize placeholder. These observation
limits do not discard traffic from the SDK's transport queue.

Settings uncertainty is recorded before sending a session update. Only the nested
`error.event_id` can correlate an error with a client request; the top-level ID
identifies the server event. Uncorrelated errors remain conservative, and relevant
updates with unresolved outcomes omit counts. Failed sends roll back local pending
records but remain uncertain until a server snapshot because the SDK can queue them
for retransmission. Overlapping updates are tracked as a bounded batch without
assigning acknowledgement identities. An overflow, or overlap involving a queued
failed send, keeps counts unavailable until a fresh connection. Existing incomplete
turns are never promoted to complete later.

A response prompt override cannot be correlated from the server response schema.
After observing one, the connection omits session-derived prompt ID/version tags
for subsequent turns; explicit annotation tags remain available. Rearmed enabled
observers also omit session-derived prompt tags because the SDK flushes queued
recovery sends before observation restarts, and those sends can contain an unseen
prompt override. Normal initial-session attribution is unchanged. No outbound IDs,
provider requests, or model/provider guesses are added.

Span creation, completion callbacks, and export run outside the observation lock.
Turn span creation restores the caller's LLMObs and routing contexts even when a
prior turn remains held or is finishing on another thread. Consecutive turn roots
remain siblings under the caller's workflow, or independent roots without one;
turns do not become ambient parents of later caller work.
The concurrency regression uses Events and a blocking finish fake to verify that
microphone input can progress and concurrent close still finishes each turn once.
The complete serialized marker metadata envelope is capped independently of the
count, including fields added by a truncation acknowledgement.

Provider omission of `speech_stopped` across buffer clear or manual commit has not
been established. Commit does not reset an episode. Policy transitions/invalidation
reset it, and an unexpected distinct start during an active episode invalidates the
observation without inventing another count. Exact event replays remain deduplicated.

Benchmark discovery currently has no matching Realtime event-path scenario; the
concurrency regression establishes progress, not throughput or latency bounds.
