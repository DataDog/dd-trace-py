# Nova Sonic duplex audio integration

Nova 2 Sonic (aws_sdk_bedrock_runtime) uses a connection-local duplex adapter,
like OpenAI Realtime, rather than one response iterator and one span.
Do not consume ahead. Observe successful input sends, allow output drain after
input half-close, and finish once on output EOF, close, cancellation, or error.
Keep turn state separate from provider completion IDs, which can be shared by
many responses. Snapshot the caller's parent; do not activate long-lived turns.

Use explicit LLMObs parent/trace/session identity on the three direct children
of each audio-turn workflow. Speech offsets select the input WAV; speech-end
event receipt defines the initial latency boundary. Projected assistant playback
is an estimate, not a device acknowledgement. Keep total samples separate from
bounded retained bytes; share the audio payload budget across both roles.

Nova protocol state lives in llmobs/_integrations/aws_sdk_bedrock_runtime_utils.py.
The product supplies it through the Bedrock core event only while enabled. The
LLMObs service snapshots a private propagation context at invocation, including
application, sampling, and agent ancestry. Do not retain a mutable span as the
source of LLMObs identity: export can scrub its metadata. Activate the snapshot
only during turn emission, activate the turn root for each direct child, and
restore the receiver's prior context in a finally block. Audio span creation
and annotations live in AwsSdkBedrockRuntimeIntegration.

Keep audio retention separate from timing validity. Dropping an oversized WAV
must not remove a valid speech phase or its TTFA boundary. Never shorten gaps
inside a retained clip without a matching timestamped segment contract. End
interrupted generation at its observed interruption, even when emission waits
for the next turn. Explicit LLMObs identity must come from an LLMObs ancestor,
never from a plain APM span's identifiers.

Ignore post-interruption audio for playback validation and retention so late
malformed chunks cannot erase the already-trimmed clip or its speech phase.
Generated-byte accounting may continue independently for decodable chunks.
Late audio blocks during unfinished interrupting user speech retain the old
response's ownership. Late END_TURN events cannot extend its interruption time.
Bound attachments by the configured LLMObs event limit as well as the shared
audio cap, reserving serialized text, metadata, tags, and event overhead.

Decode input frames in bounded base64 chunks and trim retained bytes before
extending the buffer; preserve absolute sample counters for later speech offsets.
Inbound TOOL blocks start model responses just like ASSISTANT blocks. Capture
bounded promptStart tool definitions and reserve their serialized size before
attaching audio. Record model contentEnd reasons as finish_reason, keeping
INTERRUPTED ahead of late END_TURN and terminal reasons ahead of partial blocks.
User transcript endings do not describe model completion.

Nova 2 Sonic usageEvent audio counts come from details.total.input.speechTokens
and details.total.output.speechTokens. Difference these cumulative counters using
the same turn attribution as totalInputTokens/totalOutputTokens. Emit
input_audio_tokens/output_audio_tokens as subsets, never add them to total_tokens,
and preserve explicit zero. A missing or invalid breakdown makes that turn's
corresponding audio metric unknown; recover the baseline before pricing later
turns, without attributing earlier unknown usage to them.
Aggregate token counters are also unknown until valid usage is reported for that
turn. A later valid counter can recover a gap within the same turn. Across turns,
use the recovered counter only to reestablish the aggregate and audio baselines;
omit affected metrics and derived totals rather than charging the gap to a later
turn. Valid older cumulative counters remain stale events, not resets.
Preserve explicit zeros and derive total_tokens only when both input and output
counts are known. Audio fallback markers require per-turn, per-direction
audio evidence; empty failures and tool-only content must not invent audio.
When an audio part is retained, empty transcript content can remain empty.
