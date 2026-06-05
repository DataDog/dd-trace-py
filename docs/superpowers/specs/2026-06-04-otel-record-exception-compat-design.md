# Design: Suppress DD error tags in `record_exception` when OTel compatibility mode is enabled

**Date:** 2026-06-04
**Status:** Approved

## Problem

`OtelSpan.record_exception()` unconditionally sets three Datadog-specific span tags:
- `error.message` (ERROR_MSG)
- `error.type` (ERROR_TYPE)
- `error.stack` (ERROR_STACK)

When `DD_TRACE_OTEL_COMPATIBILITY_ENABLED=true`, spans are exported via OTLP as native
OpenTelemetry spans. In that mode, error information should live exclusively in the span
event (`exception.*` event attributes), not duplicated into DD-specific span tags which
have no meaning in the OTLP model and would appear as unexpected span attributes.

Additionally, in compat mode the stacktrace is currently omitted from the span event
(only added when the caller provides it via the `attributes` parameter). In OTLP, the
`exception.stacktrace` event attribute is the canonical location for the traceback, so
it must always be present on the event when compat mode is on.

## Solution

Split `record_exception()` behavior on `config._otel_trace_compatibility_enabled`,
following the existing pattern used at lines 109 and 191 of
`ddtrace/internal/opentelemetry/span.py`.

### When `_otel_trace_compatibility_enabled` is False (default, unchanged)

1. Build `attrs` dict with `exception.type`, `exception.message`, `exception.escaped`.
2. Apply caller-supplied `attributes` (take precedence).
3. Set `error.message` and `error.type` on `_ddspan` via `_set_attribute`.
4. Set `error.stack` on `_ddspan`: use `attrs["exception.stacktrace"]` if present,
   otherwise compute via `traceback.format_exception`.
5. Add span event with `attrs` (no `exception.stacktrace` unless caller provided it).

### When `_otel_trace_compatibility_enabled` is True (new behavior)

1. Build `attrs` dict with `exception.type`, `exception.message`, `exception.escaped`.
2. Apply caller-supplied `attributes` (take precedence).
3. **Do not** set `error.message`, `error.type`, or `error.stack` on `_ddspan`.
4. If `exception.stacktrace` is not already in `attrs`, compute it via
   `traceback.format_exception` and add it to `attrs`.
5. Add span event with `attrs` (which now always includes `exception.stacktrace`).

## Affected Files

- `ddtrace/internal/opentelemetry/span.py` — modify `record_exception()` method
- `tests/opentelemetry/test_span.py` — add two new unit tests

## Tests

Two new unit tests using `patch.object(config, "_otel_trace_compatibility_enabled", True)`,
following the pattern of `test_otel_span_attribute_remapping_disabled_with_otel_compatibility`:

1. **`test_otel_record_exception_suppresses_dd_tags_with_otel_compatibility`**
   — Assert that after calling `record_exception(exc)` with compat mode on, `_ddspan`
   has no `error.message`, `error.type`, or `error.stack` tag.

2. **`test_otel_record_exception_adds_stacktrace_to_event_with_otel_compatibility`**
   — Assert that after calling `record_exception(exc)` with compat mode on, the span
   event's attributes contain `exception.stacktrace`.

## Non-goals

- No change to behavior when compat mode is off.
- No change to how `exception.stacktrace` is handled when the caller provides it
  explicitly in the `attributes` parameter (caller value still takes precedence in
  both modes).
- No change to `set_status()` — it has its own separate handling of `error.message`.
