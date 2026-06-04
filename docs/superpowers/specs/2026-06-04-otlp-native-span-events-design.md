# Design: Auto-enable native span events for OTLP export

**Date:** 2026-06-04
**Status:** Approved

## Problem

When `OTEL_TRACES_EXPORTER=otlp`, span events are emitted as a JSON string in the
`attributes["events"]` span attribute instead of as native OTLP events. This is because
`trace_native_span_events` defaults to `False`, causing the msgpack encoder to serialize
events via `json_dumps([dict(event) ...])` into `meta["events"]`, which libdatadog then
forwards as a plain string span attribute.

Native OTLP events (the `events[]` array on an OTLP span) are only produced when
`trace_native_span_events=True`, which forces v0.4 encoding with a structured
`span_events` top-level field that libdatadog converts to native OTLP events.

## Solution

Change `trace_native_span_events` in `AgentConfig` from a static `DDConfig.v()` with
`default=False` to a `DDConfig.d()` derived config. The derive function implements this
priority order:

1. If `DD_TRACE_NATIVE_SPAN_EVENTS` is explicitly set → honor it (user override wins).
   Setting it to `false` preserves the old behavior (events as JSON string attribute).
2. Else if `OTEL_TRACES_EXPORTER=otlp` → return `True` (auto-enable).
3. Else → return `False` (unchanged default behavior).

## Affected Files

- `ddtrace/internal/settings/_agent.py` — add derive function, change `DDConfig.v` → `DDConfig.d`
- `tests/` — add test cases for the auto-enable behavior

## Non-goals

- No changes to the encoder, writer, or libdatadog integration — they already handle
  `trace_native_span_events=True` correctly.
- No changes to how `DD_TRACE_NATIVE_SPAN_EVENTS=false` behaves — events degrade to
  JSON string attribute (same as today).
