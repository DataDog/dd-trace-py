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
- The hook subscribes to the ``trace.span_finish`` core event LAZILY, on the
  first evaluation that actually records data (not on construction), and writes
  the tags when the *local root* span finishes, then deletes that span's state.
  Lazy subscription means a provider that is constructed but never used for a
  real evaluation (e.g. one built with ``initialization_timeout=0`` that fails
  to initialize and is discarded without ``shutdown()``) never leaves a global
  span-finish listener behind.
- ``destroy()`` unsubscribes that exact callback (symmetric subscribe<->
  unsubscribe -- prevents a duplicate subscription on provider reconfigure).
- Both the capture path and the write path swallow exceptions: span enrichment
  must NEVER break flag evaluation or span finish.
"""

import base64
import hashlib
import json
import threading
import typing
from weakref import WeakKeyDictionary

from openfeature.hook import Hook

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


def encode_delta_varint(serial_ids: typing.AbstractSet[int]) -> str:
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

    Thread-safety: the Node reference accumulates on a single-threaded event
    loop, but a root APM span in Python can be mutated by concurrent flag
    evaluations on multiple threads (and by child-span continuations) while the
    root-span-finish path snapshots/encodes it. A single per-state lock guards
    every read/mutate so adds never lose updates and ``to_span_tags`` always
    encodes a consistent snapshot (also correct under free-threaded CPython,
    where the set/dict ops are no longer GIL-atomic).
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
        self._lock = threading.Lock()

    def add_serial_id(self, serial_id: int) -> None:
        with self._lock:
            if serial_id in self._serial_ids:
                return
            if len(self._serial_ids) >= self.MAX_SERIAL_IDS:
                log.debug("span enrichment: serial id limit (%d) reached, dropping", self.MAX_SERIAL_IDS)
                return
            self._serial_ids.add(serial_id)

    def add_subject(self, targeting_key: str, serial_id: int) -> None:
        hashed = hash_targeting_key(targeting_key)
        with self._lock:
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
        # Truncate unconditionally: the 64-unit cap is measured in UTF-16 code
        # units (matching Node's ``slice(0, 64)``), NOT Python code points. A
        # ``len(value_str) > 64`` guard would skip truncation for astral-plane
        # values that are <=64 code points but >64 UTF-16 units (e.g. 40 emoji =
        # 80 units), emitting a value Node would have cut -- diverging the wire
        # format. ``_truncate_utf16`` is a no-op for values already within limit.
        value_str = self._truncate_utf16(self._coerce_default_value(value), self.MAX_DEFAULT_VALUE_LENGTH)
        with self._lock:
            # First-wins: an existing flag key is never overwritten.
            if flag_key in self._defaults:
                return
            if len(self._defaults) >= self.MAX_DEFAULTS:
                log.debug("span enrichment: runtime-default limit (%d) reached, dropping", self.MAX_DEFAULTS)
                return
            self._defaults[flag_key] = value_str

    @staticmethod
    def _coerce_default_value(value: typing.Any) -> str:
        """Render a runtime-default value to its wire string, byte-for-byte
        matching the FROZEN Node reference (dd-trace-js#8343):

            valueStr = (typeof value === "object" && value !== null)
                ? JSON.stringify(value)
                : String(value)

        Node's ``String()`` of a JS primitive differs from Python's ``str()``
        for exactly three cases that flow through this path -- ``null``/``None``
        and the two booleans -- so we coerce those to the JS spelling to keep
        ``ffe_runtime_defaults`` identical across SDKs (the L2 decoder compares
        these bytes). Numbers and strings already match ``str()``.

        - dict / list  -> ``json.dumps`` compact, mirroring ``JSON.stringify``
          (NOT ``str(dict)`` / ``"[object Object]"``).
        - None         -> ``"null"``   (Node ``String(null)``;  Python ``str`` -> "None")
        - True / False -> ``"true"`` / ``"false"`` (Node; Python ``str`` -> "True"/"False")
        - everything else (int, float, str) -> ``str(value)`` (already matches Node)
        """
        # bool is a subclass of int -- check it before the numeric str() fallback.
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (dict, list)):
            # separators omit the spaces JS JSON.stringify also omits.
            # ensure_ascii=False emits raw UTF-8 like JS JSON.stringify (which
            # never \uXXXX-escapes non-ASCII), so an object/array default with
            # Unicode content is byte-for-byte identical to the Node reference.
            return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        return str(value)

    @staticmethod
    def _truncate_utf16(value_str: str, max_units: int) -> str:
        """Truncate ``value_str`` to ``max_units`` UTF-16 code units, mirroring
        Node's ``String.prototype.slice(0, n)`` (which counts UTF-16 units, not
        Unicode code points -- Python's ``str[:n]`` counts code points).

        For astral-plane content (one code point == a UTF-16 surrogate pair ==
        two units) the two SDKs otherwise keep a different number of characters,
        so the truncated tag would diverge byte-for-byte. We encode to
        UTF-16-LE (2 bytes / unit), cut at ``max_units * 2`` bytes, and decode
        back; the ``"ignore"`` handler drops a trailing lone high surrogate left
        by a split pair so the result is always valid UTF-8 (no broken bytes).
        """
        u16 = value_str.encode("utf-16-le")
        return u16[: max_units * 2].decode("utf-16-le", "ignore")

    def has_data(self) -> bool:
        # Subjects are not checked: add_subject never runs without add_serial_id.
        with self._lock:
            return bool(self._serial_ids) or bool(self._defaults)

    def to_span_tags(self) -> dict[str, str]:
        # ffe_flags_enc is a BARE base64 string; ffe_subjects_enc and
        # ffe_runtime_defaults are JSON-stringified objects. The JSON is emitted
        # compact (no spaces) and with ensure_ascii=False to byte-match Node's
        # JSON.stringify (raw UTF-8, no \uXXXX escaping) -- the L2 decoder
        # json.loads() these, but keeping them compact + raw-UTF-8 preserves
        # strict cross-SDK byte parity (Pattern F).
        #
        # Snapshot under the lock so concurrent add_*() can't mutate the sets/
        # maps mid-encode (no "changed size during iteration", no lost update,
        # consistent snapshot at root-span finish).
        with self._lock:
            serial_ids = frozenset(self._serial_ids)
            subjects = {hashed: frozenset(ids) for hashed, ids in self._subjects.items()}
            defaults = dict(self._defaults)
        tags: dict[str, str] = {}
        if serial_ids:
            tags[TAG_FLAGS_ENC] = encode_delta_varint(serial_ids)
        if subjects:
            tags[TAG_SUBJECTS_ENC] = json.dumps(
                {hashed: encode_delta_varint(ids) for hashed, ids in subjects.items()},
                separators=(",", ":"),
                ensure_ascii=False,
            )
        if defaults:
            tags[TAG_RUNTIME_DEFAULTS] = json.dumps(defaults, separators=(",", ":"), ensure_ascii=False)
        return tags


class SpanEnrichmentHook(Hook):  # type: ignore[misc]
    """OpenFeature ``finally`` hook that accumulates feature-flag metadata per
    root span and writes the ``ffe_*`` tags when that root span finishes.

    Constructed ONLY when the span-enrichment gate is on, so when the gate is
    off nothing is allocated and nothing subscribes to span finish (DG-005).

    Subclasses ``openfeature.hook.Hook`` (like the sibling ``FlagEvalHook``) so
    the OpenFeature client can register it via ``get_provider_hooks()`` and
    invoke the full hook protocol -- the client calls
    ``supports_flag_value_type`` and the ``before``/``after``/``error`` stages on
    every registered hook, so the base-class defaults (accept all types, no-op
    stages) MUST be inherited. We override only ``finally_after``.
    """

    def __init__(self) -> None:
        # Keyed by span: state is GC'd with the span (zero idle leak, DG-005).
        self._span_states: "WeakKeyDictionary[typing.Any, SpanEnrichmentState]" = WeakKeyDictionary()
        # Guards _span_states get/create/pop so concurrent evaluations and the
        # root-span-finish callback never race on the dict (a WeakKeyDictionary
        # is not thread-safe, and free-threaded CPython drops the GIL atomicity
        # that masks this under the default build).
        self._states_lock = threading.Lock()
        # A PER-INSTANCE subscription name. The native event hub keys listeners
        # by name and REPLACES a same-name registration (event_hub.rs `on`), so
        # a shared name would let a second live provider/domain stomp this
        # hook's finish callback -- the displaced hook would keep accumulating
        # state in finally_after but never receive the finish event (state never
        # written, never cleaned). id(self) makes each hook's subscription
        # distinct; destroy() removes the exact callback by identity.
        self._subscription_name = "ffe.span_enrichment.%d" % id(self)
        # Subscribe LAZILY (see _ensure_subscribed), not here: a hook constructed
        # for a provider that never runs a real evaluation must not leak a global
        # listener. Guarded by _states_lock.
        self._subscribed = False

    def _ensure_subscribed(self) -> None:
        """Subscribe to ``trace.span_finish`` on first data capture (idempotent).

        Deferring the subscription out of ``__init__`` is what makes a
        discarded, never-used provider leak-free: ``set_provider`` never
        evaluates a flag, so ``finally_after`` never runs for a throwaway
        provider and this is never reached -- no listener is registered.
        """
        if self._subscribed:
            return
        with self._states_lock:
            # Re-check under the lock (another thread may have subscribed between
            # the fast-path read above and acquiring the lock).
            if not self._subscribed:
                core.on(_SPAN_FINISH_EVENT, self._on_span_finish, self._subscription_name)
                self._subscribed = True

    def _get_root_span(self) -> typing.Optional[typing.Any]:
        """O(1) local-root lookup -- mirrors Node's ``trace.started[0]``."""
        from ddtrace.trace import tracer

        return tracer.current_root_span()

    def _get_or_create_state(self, span: typing.Any) -> SpanEnrichmentState:
        with self._states_lock:
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

            # Only allocate per-root state once we know this evaluation has
            # something to record. An evaluation with neither a serial id nor a
            # runtime default (e.g. an older UFC payload that has a variant but
            # no serial_id) must NOT create empty state that lingers until span
            # GC -- it leaves no state to clean up on finish.
            if serial_id is not None:
                self._ensure_subscribed()
                state = self._get_or_create_state(root_span)
                state.add_serial_id(serial_id)
                if do_log and targeting_key:
                    state.add_subject(targeting_key, serial_id)
            elif getattr(details, "variant", None) is None:
                # Runtime-default detection = MISSING VARIANT (not a reason enum).
                self._ensure_subscribed()
                state = self._get_or_create_state(root_span)
                state.add_default(hook_context.flag_key, getattr(details, "value", None))
        except Exception as e:
            # Enrichment must NEVER break flag evaluation.
            log.warning("span enrichment capture failed: %s", e)

    def _on_span_finish(self, span: typing.Any) -> None:
        """Write the ``ffe_*`` tags when the (root) span finishes, then delete
        that span's accumulated state.
        """
        try:
            # Pop unconditionally so state is ALWAYS cleaned up on root-span
            # finish -- including empty state from a no-data evaluation -- rather
            # than lingering until span GC.
            with self._states_lock:
                state = self._span_states.pop(span, None)
            if state is None or not state.has_data():
                return
            for key, value in state.to_span_tags().items():
                if value:
                    span.set_tag(key, value)
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
        with self._states_lock:
            self._subscribed = False
        self._span_states = WeakKeyDictionary()
