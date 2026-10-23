---
name: llmobs-integrations
description: Create or modify LLMObs integration lifecycle, extraction, streaming, usage metrics, and tests.
---

# LLMObs integrations

Use for LLM/AI span lifecycle, message/tool/media extraction, metadata, usage metrics, and tests.
Use `apm-integrations` as well when changing contrib patching, registration, or suite plumbing.

## Ownership and references

Standard request spans use `LlmRequestEvent` and `core.context_with_event()`; the tracing
subscriber owns span creation, errors, extraction, and finishing. Specialized agent/tool/duplex
integrations may use direct spans. Follow the current integration's lifecycle deliberately.

- Standard requests: `ddtrace/contrib/internal/anthropic/patch.py` and
  `ddtrace/llmobs/_integrations/anthropic.py`.
- Agent/tool spans: `ddtrace/llmobs/_integrations/claude_agent_sdk.py`.
- Base contract: `ddtrace/llmobs/_integrations/base.py`.
- Streaming: `ddtrace/internal/utils/streaming.py`.
- Shared extraction: `ddtrace/llmobs/_integrations/utils.py`.
- Span annotation and structural types: `ddtrace/llmobs/_utils.py`, `_span.py`, and `types.py`.
- Metric and field keys: `ddtrace/llmobs/_constants.py`.

## Invariants

- Set `submit_to_llmobs` on the appropriate event/direct-span path.
- Complete deferred events on success and error; event-based wrappers must not independently
  finish the same span or repeat subscriber-owned extraction.
- Preserve stream consumption, cancellation, and context-manager behavior. Finish through the
  stream owner, not an ASGI sweep that could truncate an agent outliving its request.
- Stamp agent kind/name when the span starts so children can resolve their parent attribution.
- Reuse extraction and metadata sanitizers; retain provider-specific token accounting and media limits.

Read only the reference relevant to the change:
[implementation](references/implementation-guide.md), [failure modes](references/failure-modes.md),
[testing](references/testing-guide.md), or [Nova Sonic duplex streams](references/nova-sonic.md).

## Extraction checklist

Prefer `_annotate_llmobs_span_data()` over writing LLMObs payload fields through raw
`span._set_ctx_items()`. The helper updates the meta-struct and handles propagation-related
context fields. Integration-private state may still use context items; preserve the distinction.

Check the fields appropriate to the span kind and provider; do not invent unavailable data:

| Field | Expected content |
|-------|------------------|
| `kind` | Actual operation kind, such as `llm`, `agent`, `workflow`, or `tool` |
| `name` | Operation/agent display name when available; preserve start-time agent attribution |
| `model_name`, `model_provider` | Model identity for model operations, following the provider's extraction rules |
| `input_messages`, `output_messages` | Message lists with roles, text, tool calls/results, and supported media |
| `input_value`, `output_value` | Non-message payloads when the span kind uses them; follow the existing integration |
| `input_documents`, `output_documents` | Document payloads for operations such as embeddings or retrieval |
| `metadata` | Sanitized request parameters and response scalars such as `finish_reason` |
| `metrics` | Normalized usage, cache tokens and billable server-tool counts when reported |
| `tool_definitions` | Tools offered to the model, distinct from tool calls/results in messages |

Read `ddtrace/llmobs/types.py` for `Message`, `ToolCall`, `ToolResult`, `ToolDefinition`,
`AudioPart`, and `ImagePart`. Use the shared media guards and extraction rules in the
implementation guide rather than constructing unbounded inline payloads.

### Response Metadata

`metadata` is not request-only. Scalars the provider reports on the *response* belong there too, so
long as they are not already covered by `metrics` (usage counts) or `output_messages`.

The stop reason is the established case. Record it under the key `finish_reason` for every provider,
so one facet answers "why did generation stop" regardless of integration, and keep each provider's
own vocabulary as the value (`stop`/`length`/`content_filter`/`tool_calls` for the OpenAI family,
`end_turn`/`max_tokens`/`refusal`/`tool_use` for Anthropic). Read it from:

| Provider | Source |
|---|---|
| openai/litellm chat + legacy completions | `choice.finish_reason` (per choice) |
| openai responses API | `incomplete_details.reason` |
| anthropic | `response.stop_reason`, falling back to `response.finish_reason` for the streamed dict the aggregator rebuilds |

Rules:

- **Merge, do not replace.** Build the request metadata first, then update it with the
  response-derived keys (`parameters.update(...)`), so response data never clobbers request params.
- **Omit the key when absent.** If the provider reports no reason, leave `finish_reason` out
  entirely rather than writing `None` or `""`.
- **Keep the value a single string.** When a request returns multiple choices (`n > 1`), comma-join
  the per-choice reasons in choice order (`"stop,length"`) instead of emitting a list, so the key
  never changes type. `_openai_finish_reason_metadata()` in `_integrations/utils.py` does this.
- **Guard on the response, not `span.error`.** A response can exist on an errored span (see the AI
  Guard case in `_integrations/anthropic.py`); gate on `response is not None`.

Note two already-shipped integrations predate this key: bedrock and the claude-agent-sdk both write
`metadata["stop_reason"]`. Renaming those is a breaking change and has not been done — follow
`finish_reason` for new work.
