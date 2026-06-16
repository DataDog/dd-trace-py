"""
APM span enrichment for Feature Flagging and Experimentation (FFE) -- PY-01.

Ported VERBATIM from the FROZEN Node reference (dd-trace-js#8343). Behind
``DD_EXPERIMENTAL_FLAGGING_PROVIDER_SPAN_ENRICHMENT_ENABLED`` the provider
attaches three tags to the local root APM span when it finishes:

- ``ffe_flags_enc``       -- bare base64 ULEB128 delta-varint of the serial ids
- ``ffe_subjects_enc``    -- JSON object {sha256(targetingKey): base64(serial ids)}
- ``ffe_runtime_defaults``-- JSON object {flagKey: valueStr}

Tag names, encoding, and limits are FROZEN against the Node contract -- the
backend/Trino decode and the system-tests assertions depend on exact parity.
Do NOT re-derive; do NOT add per-SDK env-var knobs for the limits.

Lifecycle (mirrors the Node SpanEnrichmentHook):
- Per-root-span state lives in a ``WeakKeyDictionary`` keyed by the span, so it
  is garbage-collected with the span -- zero idle per-span overhead (DG-005).
- The hook subscribes to the ``trace.span_finish`` core event on construction
  and writes the tags when the *local root* span finishes, then deletes that
  span's state.
- ``destroy()`` unsubscribes that exact callback (symmetric subscribe<->
  unsubscribe -- prevents a duplicate subscription on provider reconfigure).
- Both the capture path and the write path swallow exceptions: span enrichment
  must NEVER break flag evaluation or span finish.
"""

import base64
import hashlib
import json
import typing
from weakref import WeakKeyDictionary

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# Core event dispatched by the tracer on every span finish (ddtrace/_trace/tracer.py).
_SPAN_FINISH_EVENT = "trace.span_finish"

# Flag-metadata keys threaded by the provider (FROZEN -- current keys only,
# no legacy ``doLog`` shim).
METADATA_SERIAL_ID = "__dd_split_serial_id"
METADATA_DO_LOG = "__dd_do_log"

# Span tag names (FROZEN -- bare names on span meta, not _dd.-prefixed).
TAG_FLAGS_ENC = "ffe_flags_enc"
TAG_SUBJECTS_ENC = "ffe_subjects_enc"
TAG_RUNTIME_DEFAULTS = "ffe_runtime_defaults"


def encode_delta_varint(serial_ids: set[int]) -> str:
    """ULEB128 delta-varint encode a set of serial ids -> bare base64 string.

    Dedupe is structural (the input is a set). Ids are sorted ascending and
    encoded as deltas from the previous id (the first delta is the first id).
    Each delta is ULEB128-encoded (7 bits/byte, MSB = continuation bit).

    An empty set encodes to the empty string (the caller omits the tag).

    Golden vector: ``{100, 108, 128, 130}`` -> deltas ``[100, 8, 20, 2]``
    -> ``"ZAgUAg=="``.
    """
    if not serial_ids:
        return ""
    buf = bytearray()
    prev = 0
    for id_ in sorted(serial_ids):
        delta = id_ - prev
        prev = id_
        while delta > 0x7F:
            buf.append((delta & 0x7F) | 0x80)
            delta >>= 7
        buf.append(delta & 0x7F)
    return base64.b64encode(bytes(buf)).decode("ascii")


def hash_targeting_key(targeting_key: str) -> str:
    """SHA256 lowercase hex digest of a targeting key (FROZEN contract)."""
    return hashlib.sha256(targeting_key.encode("utf-8")).hexdigest()


class SpanEnrichmentState:
    """Per-root-span accumulator. Created lazily; never allocated when the gate
    is off (DG-005). Enforces the FROZEN limits, dedupes, and truncates.
    """

    MAX_SERIAL_IDS = 200
    MAX_SUBJECTS = 10
    MAX_EXPERIMENTS_PER_SUBJECT = 20
    MAX_DEFAULTS = 5
    MAX_DEFAULT_VALUE_LENGTH = 64

    def __init__(self) -> None:
        self._serial_ids: set[int] = set()
        self._subjects: dict[str, set[int]] = {}
        self._defaults: dict[str, str] = {}

    def add_serial_id(self, serial_id: int) -> None:
        if serial_id in self._serial_ids:
            return
        if len(self._serial_ids) >= self.MAX_SERIAL_IDS:
            log.debug("span enrichment: serial id limit (%d) reached, dropping", self.MAX_SERIAL_IDS)
            return
        self._serial_ids.add(serial_id)

    def add_subject(self, targeting_key: str, serial_id: int) -> None:
        hashed = hash_targeting_key(targeting_key)
        existing = self._subjects.get(hashed)
        if existing is not None:
            if len(existing) >= self.MAX_EXPERIMENTS_PER_SUBJECT:
                log.debug(
                    "span enrichment: experiments-per-subject limit (%d) reached, dropping",
                    self.MAX_EXPERIMENTS_PER_SUBJECT,
                )
                return
            existing.add(serial_id)
            return
        if len(self._subjects) >= self.MAX_SUBJECTS:
            log.debug("span enrichment: subject limit (%d) reached, dropping", self.MAX_SUBJECTS)
            return
        self._subjects[hashed] = {serial_id}

    def add_default(self, flag_key: str, value: typing.Any) -> None:
        # First-wins: an existing flag key is never overwritten.
        if flag_key in self._defaults:
            return
        if len(self._defaults) >= self.MAX_DEFAULTS:
            log.debug("span enrichment: runtime-default limit (%d) reached, dropping", self.MAX_DEFAULTS)
            return
        # Object/array default -> JSON (NOT str(dict)); scalars -> str.
        if isinstance(value, (dict, list)):
            value_str = json.dumps(value)
        else:
            value_str = str(value)
        if len(value_str) > self.MAX_DEFAULT_VALUE_LENGTH:
            # Slicing a Python str truncates by code point, so it can never
            # split a multi-byte character (UTF-8-safe).
            value_str = value_str[: self.MAX_DEFAULT_VALUE_LENGTH]
        self._defaults[flag_key] = value_str

    def has_data(self) -> bool:
        # Subjects are not checked: add_subject never runs without add_serial_id.
        return bool(self._serial_ids) or bool(self._defaults)

    def to_span_tags(self) -> dict[str, str]:
        tags: dict[str, str] = {}
        if self._serial_ids:
            tags[TAG_FLAGS_ENC] = encode_delta_varint(self._serial_ids)
        if self._subjects:
            tags[TAG_SUBJECTS_ENC] = json.dumps(
                {hashed: encode_delta_varint(ids) for hashed, ids in self._subjects.items()}
            )
        if self._defaults:
            tags[TAG_RUNTIME_DEFAULTS] = json.dumps(self._defaults)
        return tags


class SpanEnrichmentHook:
    """OpenFeature ``finally`` hook that accumulates feature-flag metadata per
    root span and writes the ``ffe_*`` tags when that root span finishes.

    Constructed ONLY when the span-enrichment gate is on, so when the gate is
    off nothing is allocated and nothing subscribes to span finish (DG-005).
    """

    def __init__(self) -> None:
        # Keyed by span: state is GC'd with the span (zero idle leak, DG-005).
        self._span_states: "WeakKeyDictionary[typing.Any, SpanEnrichmentState]" = WeakKeyDictionary()
        # A stable subscription name makes re-subscription idempotent and lets
        # destroy() remove this exact callback.
        self._subscription_name = "ffe.span_enrichment"
        core.on(_SPAN_FINISH_EVENT, self._on_span_finish, self._subscription_name)

    def _get_root_span(self) -> typing.Optional[typing.Any]:
        """O(1) local-root lookup -- mirrors Node's ``trace.started[0]``."""
        from ddtrace.trace import tracer

        return tracer.current_root_span()

    def _get_or_create_state(self, span: typing.Any) -> SpanEnrichmentState:
        state = self._span_states.get(span)
        if state is None:
            state = SpanEnrichmentState()
            self._span_states[span] = state
        return state

    def finally_after(
        self,
        hook_context: typing.Any,
        details: typing.Any,
        hints: typing.Any = None,
    ) -> None:
        """Runs after every evaluation (success or error). Accumulates serial
        ids / subjects / runtime defaults onto the current root span's state.
        """
        try:
            root_span = self._get_root_span()
            if root_span is None:
                return

            metadata = getattr(details, "flag_metadata", None) or {}
            serial_id = metadata.get(METADATA_SERIAL_ID)
            do_log = bool(metadata.get(METADATA_DO_LOG, False))

            evaluation_context = getattr(hook_context, "evaluation_context", None)
            targeting_key = getattr(evaluation_context, "targeting_key", None)

            state = self._get_or_create_state(root_span)

            if serial_id is not None:
                state.add_serial_id(serial_id)
                if do_log and targeting_key:
                    state.add_subject(targeting_key, serial_id)
            elif getattr(details, "variant", None) is None:
                # Runtime-default detection = MISSING VARIANT (not a reason enum).
                state.add_default(hook_context.flag_key, getattr(details, "value", None))
        except Exception as e:
            # Enrichment must NEVER break flag evaluation.
            log.warning("span enrichment capture failed: %s", e)

    def _on_span_finish(self, span: typing.Any) -> None:
        """Write the ``ffe_*`` tags when the (root) span finishes, then delete
        that span's accumulated state.
        """
        try:
            state = self._span_states.get(span)
            if state is None or not state.has_data():
                return
            try:
                for key, value in state.to_span_tags().items():
                    if value:
                        span.set_tag(key, value)
            finally:
                # Cleanup on root-span finish (mirrors Node's spanStates.delete).
                self._span_states.pop(span, None)
        except Exception as e:
            log.warning("span enrichment write failed: %s", e)

    def destroy(self) -> None:
        """Unsubscribe the span-finish callback (provider-close cleanup).

        Removing exactly this callback (not a blanket reset of the event) keeps
        any other ``trace.span_finish`` listeners intact and prevents a
        duplicate subscription on provider reconfigure.
        """
        try:
            core.reset_listeners(_SPAN_FINISH_EVENT, self._on_span_finish)
        except Exception as e:
            log.debug("span enrichment destroy failed: %s", e)
        self._span_states = WeakKeyDictionary()
