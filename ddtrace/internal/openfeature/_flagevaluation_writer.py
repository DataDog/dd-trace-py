"""
FlagEvaluationWriter — SDK-native EVP `flagevaluation` writer for dd-trace-py.

Implements a two-tier aggregation design (full → degraded → drop-counted). Uses the same
PeriodicService + get_connection() transport path as the exposure writer in writer.py.

Key design properties:
- Async, best-effort recording: the finally_after hook does cheap capture + non-blocking
  enqueue. The writer bounds context before queueing; aggregate/flush work happens in the
  background worker.
- Two-tier aggregation (full → degraded → drop-counted).
- Canonical context key: sorted, type-tagged, length-delimited — NOT a hash, so distinct
  contexts always produce distinct keys with no collisions.
- Context pruning: ≤256 fields, string values ≤256 chars.
- Caps: GLOBAL_CAP=131_072 (full-tier), PER_FLAG_CAP=10_000 (per-flag full-tier),
  DEGRADED_CAP=32_768 (degraded-tier). Beyond the degraded cap: drop-and-count.
- Eval-time from metadata key "dd.eval.timestamp_ms"; fallback to enqueue-time.
- First/last evaluation: min/max under lock.
- runtime_default_used: True when variant is None/absent.
- Killswitch: DD_FLAGGING_EVALUATION_COUNTS_ENABLED (default on); gates EVP path only.
- Non-blocking enqueue: queue.Queue(QUEUE_SIZE); drops + counts on queue.Full.
"""

from collections.abc import Mapping
import http.client as httplib
import json
import queue
import struct
import threading
import time
import typing

from ddtrace import config as ddconfig
from ddtrace.internal.evp_proxy.constants import DEFAULT_EVP_PAYLOAD_SIZE_LIMIT
from ddtrace.internal.evp_proxy.constants import EVP_PROXY_AGENT_BASE_PATH
from ddtrace.internal.evp_proxy.constants import EVP_SUBDOMAIN_HEADER_EVENT_PLATFORM_VALUE
from ddtrace.internal.evp_proxy.constants import EVP_SUBDOMAIN_HEADER_NAME
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.threads import PeriodicThread
from ddtrace.internal.utils.http import get_connection


logger = get_logger(__name__)

# EVP endpoint for flag evaluation events.
FLAGEVALUATIONS_ENDPOINT = f"{EVP_PROXY_AGENT_BASE_PATH}/api/v2/flagevaluation"
EVP_SUBDOMAIN_VALUE = EVP_SUBDOMAIN_HEADER_EVENT_PLATFORM_VALUE
FLAGEVALUATIONS_PAYLOAD_SIZE_LIMIT = DEFAULT_EVP_PAYLOAD_SIZE_LIMIT
_JSON_SEPARATORS = (",", ":")

# Context pruning limits — mirror worker.ts MAX_EVALUATION_CONTEXT_FIELDS / MAX_FIELD_LENGTH.
MAX_CONTEXT_FIELDS = 256
MAX_FIELD_LENGTH = 256
DEDICATED_TARGETING_KEY_CONTEXT_FIELDS = frozenset(("targetingKey", "targeting_key"))

# Aggregation caps (sized for a >=2,500-flag scale target).
EVAL_SCALE_TARGET_FLAGS = 2_500
EVAL_SCALE_FULL_BUCKETS_PER_FLAG = 50
EVAL_SCALE_USERS_PER_FLAG = 1_000
EVAL_SCALE_PER_FLAG_HEADROOM_MULTIPLIER = 10
EVAL_SCALE_DEGRADED_BUCKETS_PER_FLAG = 10
EVAL_SCALE_FULL_BUCKET_TARGET = EVAL_SCALE_TARGET_FLAGS * EVAL_SCALE_FULL_BUCKETS_PER_FLAG
EVAL_SCALE_PER_FLAG_BUCKET_TARGET = EVAL_SCALE_PER_FLAG_HEADROOM_MULTIPLIER * EVAL_SCALE_USERS_PER_FLAG
EVAL_SCALE_DEGRADED_BUCKET_TARGET = EVAL_SCALE_TARGET_FLAGS * EVAL_SCALE_DEGRADED_BUCKETS_PER_FLAG
GLOBAL_CAP = 131_072  # bounds full-tier buckets
PER_FLAG_CAP = EVAL_SCALE_PER_FLAG_BUCKET_TARGET  # bounds full-tier buckets per flag
DEGRADED_CAP = 32_768  # bounds degraded-tier buckets; overflow is drop-counted

# Async hand-off queue size.
QUEUE_SIZE = 4_096

# Flush interval: dedicated 10 s timer, separate from ExposureWriter's 1 s interval.
DEFAULT_FLUSH_INTERVAL = 10.0

# Queue drain interval. This keeps the hand-off queue bounded while allowing a flush
# window to accumulate more buckets than QUEUE_SIZE.
DRAIN_INTERVAL = 0.1

# Flag metadata key where the provider stamps the evaluation timestamp (ms).
EVAL_TIMESTAMP_METADATA_KEY = "dd.eval.timestamp_ms"

# Metadata key for allocation_key (same as _flageval_metrics.py METADATA_ALLOCATION_KEY).
METADATA_ALLOCATION_KEY = "allocation_key"

# Type-tag bytes for the canonical context key encoding (mirrors Go's ctxTag* constants).
_TAG_STR = b"s"
_TAG_BOOL = b"b"
_TAG_INT = b"i"
_TAG_FLOAT = b"f"
_TAG_OTHER = b"o"

FLAG_EVALUATION_DROPPED_METRIC = "flagevaluation.rows.dropped"
FLAG_EVALUATION_DEGRADED_METRIC = "flagevaluation.rows.degraded"
FLAG_EVALUATION_SPLITS_METRIC = "flagevaluation.payload.splits"

FLAG_EVALUATION_REASON_QUEUE_OVERFLOW = "queue_overflow"
FLAG_EVALUATION_REASON_DEGRADED_CAP = "degraded_cap"
FLAG_EVALUATION_REASON_PAYLOAD_LIMIT = "payload_limit"
FLAG_EVALUATION_REASON_CARDINALITY_CAP = "cardinality_cap"


def _json_dumps(obj: typing.Any) -> bytes:
    return json.dumps(obj, default=_json_default, separators=_JSON_SEPARATORS).encode("utf-8")


def _json_default(value: typing.Any) -> str:
    if hasattr(value, "isoformat"):
        try:
            return str(value.isoformat())
        except Exception:
            return str(value)
    return str(value)


def _count_metric(name: str, value: int, reason: typing.Optional[str] = None) -> None:
    if value <= 0:
        return
    tags = (("reason", reason),) if reason else tuple()
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, name, value, tags)


# ---------------------------------------------------------------------------
# Canonical context key — type-tagged, length-delimited, sorted
# ---------------------------------------------------------------------------


def _length_delimited(data: bytes) -> bytes:
    """Prepend a fixed 8-byte big-endian length to data."""
    return struct.pack(">Q", len(data)) + data


def _encode_context_value(v: typing.Any) -> bytes:
    """Encode a single context value with a type tag + length-delimited value."""
    if isinstance(v, bool):
        # bool must be checked before int because bool is a subclass of int in Python.
        tag = _TAG_BOOL
        raw = b"true" if v else b"false"
    elif isinstance(v, int):
        tag = _TAG_INT
        raw = str(v).encode()
    elif isinstance(v, float):
        tag = _TAG_FLOAT
        raw = repr(v).encode()
    elif isinstance(v, str):
        tag = _TAG_STR
        raw = v.encode("utf-8", errors="replace")
    else:
        tag = _TAG_OTHER
        raw = str(v).encode("utf-8", errors="replace")
    return tag + _length_delimited(raw)


def canonical_context_key(attrs: dict[str, typing.Any]) -> str:
    """
    Build the EXACT, comparable canonical-context string key for a pruned context dict.

    Uses sorted(attrs.items()) so the encoding is deterministic regardless of Python
    dict insertion order. Each entry is encoded as:
        length_delimited(key_bytes) + type_tag_byte + length_delimited(value_bytes)

    Because the full encoding is used as the map key (not a hash), distinct contexts
    ALWAYS produce distinct keys — no hash collisions, no misattribution.

    Returns "" for empty/None attrs.
    """
    if not attrs:
        return ""
    parts = []
    for k in sorted(attrs.keys()):
        parts.append(_length_delimited(k.encode("utf-8", errors="replace")))
        parts.append(_encode_context_value(attrs[k]))
    return b"".join(parts).decode("latin-1")  # lossless binary → str for dict key


def flatten_and_prune_context(attrs: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """
    Flatten nested dicts (dot-notation) and apply 256-field / 256-char prune.

    Returns a new dict with at most MAX_CONTEXT_FIELDS entries, skipping string values
    that exceed MAX_FIELD_LENGTH. Keys are chosen deterministically (sorted order) so
    identical contexts always produce identical pruned maps.

    Returns {} when the input is empty or all values are pruned.
    """
    if not attrs:
        return {}

    flat: dict[str, typing.Any] = {}
    _flatten_recursive("", attrs, flat)
    if not flat:
        return {}

    # Fast path: no pruning needed.
    needs_prune = len(flat) > MAX_CONTEXT_FIELDS
    if not needs_prune:
        for v in flat.values():
            if isinstance(v, str) and len(v) > MAX_FIELD_LENGTH:
                needs_prune = True
                break
    if not needs_prune:
        return flat

    # Deterministic prune: sort keys, keep first MAX_CONTEXT_FIELDS non-oversized values.
    out: dict[str, typing.Any] = {}
    count = 0
    for k in sorted(flat.keys()):
        if count >= MAX_CONTEXT_FIELDS:
            break
        v = flat[k]
        if isinstance(v, str) and len(v) > MAX_FIELD_LENGTH:
            continue
        out[k] = v
        count += 1
    return out


def _json_safe_context_value(value: typing.Any) -> typing.Any:
    return json.loads(_json_dumps(value).decode("utf-8"))


def _json_safe_context(attrs: dict[str, typing.Any]) -> dict[str, typing.Any]:
    return {k: _json_safe_context_value(v) for k, v in attrs.items()}


def _flatten_recursive(prefix: str, attrs: typing.Any, out: dict[str, typing.Any]) -> None:
    """Recursively flatten nested dicts into dot-notation keys."""
    if not isinstance(attrs, Mapping):
        if prefix:
            out[prefix] = attrs
        return
    for k, v in attrs.items():
        if not prefix and k in DEDICATED_TARGETING_KEY_CONTEXT_FIELDS:
            continue
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, Mapping):
            _flatten_recursive(full_key, v, out)
        else:
            out[full_key] = v


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------


class _Entry:
    """Per-bucket aggregation state."""

    __slots__ = (
        "count",
        "first_evaluation",
        "last_evaluation",
        "runtime_default",
        "targeting_key",
        "context_attrs",
        "error_message",
    )

    def __init__(
        self,
        eval_time_ms: int,
        runtime_default: bool,
        targeting_key: str,
        context_attrs: dict[str, typing.Any],
        error_message: str,
    ) -> None:
        self.count: int = 1
        self.first_evaluation: int = eval_time_ms
        self.last_evaluation: int = eval_time_ms
        self.runtime_default: bool = runtime_default
        # Full-tier only:
        self.targeting_key: str = targeting_key
        self.context_attrs: dict[str, typing.Any] = context_attrs
        self.error_message: str = error_message

    def observe(self, eval_time_ms: int) -> None:
        """Update count and first/last bounds for a repeated evaluation."""
        self.count += 1
        if eval_time_ms < self.first_evaluation:
            self.first_evaluation = eval_time_ms
        if eval_time_ms > self.last_evaluation:
            self.last_evaluation = eval_time_ms


class _EvalEvent(typing.NamedTuple):
    """Minimal snapshot handed from finally_after to the background worker."""

    flag_key: str
    variant: str  # "" when absent (= runtime_default)
    allocation_key: str
    targeting_key: str
    attrs: dict[str, typing.Any]  # flattened and pruned context snapshot
    runtime_default: bool
    error_message: str
    eval_time_ms: int


class _FlagEvaluationConnection(typing.Protocol):
    def request(self, method: str, url: str, body: bytes, headers: dict[str, str]) -> None:
        pass

    def getresponse(self) -> httplib.HTTPResponse:
        pass

    def close(self) -> None:
        pass


class _PayloadEventResult(typing.NamedTuple):
    encoded: typing.Optional[bytes]
    degraded_payload_limit: bool = False
    dropped_payload_limit: bool = False


class _PayloadBuildResult(typing.NamedTuple):
    payloads: list[tuple[bytes, int]]
    degraded_payload_limit: int = 0
    dropped_payload_limit: int = 0


# ---------------------------------------------------------------------------
# FlagEvaluationWriter
# ---------------------------------------------------------------------------


class FlagEvaluationWriter(PeriodicService):
    """
    SDK-native EVP `flagevaluation` writer.

    Two-tier aggregation design:
    - full-tier: keyed by schema-visible dimensions only: flag, variant, allocation,
      runtime_default_used, error.message, targeting_key, canonical_context
    - degraded-tier: keyed by schema-visible retained dimensions: flag, variant, allocation,
      runtime_default_used, error.message
    - drop-counted: beyond degradedCap, increment _dropped_degraded_overflow

    The finally_after hook enqueues _EvalEvent snapshots through enqueue(), which bounds
    context before buffering; the PeriodicService background thread drains the queue,
    aggregates, and flushes via HTTP every 10 s.
    """

    def __init__(self, interval: float = DEFAULT_FLUSH_INTERVAL, timeout: float = 2.0) -> None:
        super().__init__(interval=interval)
        self._timeout = timeout
        self._intake: str = agent_config.trace_agent_url
        self._endpoint: str = FLAGEVALUATIONS_ENDPOINT
        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
            EVP_SUBDOMAIN_HEADER_NAME: EVP_SUBDOMAIN_VALUE,
        }

        # Async hand-off queue: non-blocking, bounded.
        self._queue: "queue.Queue[_EvalEvent]" = queue.Queue(maxsize=QUEUE_SIZE)

        # Aggregation maps (drained under _lock on each periodic() call). Keys are tuples of
        # the enumerable dimensions plus (full tier) the canonical context string.
        self._lock = threading.Lock()
        self._full: dict[tuple[typing.Any, ...], _Entry] = {}
        self._degraded: dict[tuple[typing.Any, ...], _Entry] = {}
        self._per_flag_count: dict[str, int] = {}  # flag_key → full-tier bucket count
        self._global_count: int = 0

        # Observable drop counters.
        self._dropped_queue: int = 0  # queue.Full drops (hook path)
        self._dropped_degraded_overflow: int = 0  # degraded-cap overflow drops

        self._drain_worker: typing.Optional[PeriodicThread] = None

    # ------------------------------------------------------------------
    # Public API used by FlagEvalEVPHook
    # ------------------------------------------------------------------

    def enqueue(self, event: _EvalEvent) -> None:
        """
        Non-blocking enqueue from the finally_after hook thread.

        Context is flattened/pruned before it enters the queue so queue length is not the
        only memory bound. On queue.Full, increments _dropped_queue (observable) and
        returns immediately — never blocks the evaluation hot path.
        """
        bounded_event = _EvalEvent(
            flag_key=event.flag_key,
            variant=event.variant,
            allocation_key=event.allocation_key,
            targeting_key=event.targeting_key,
            attrs=flatten_and_prune_context(event.attrs) if event.attrs else {},
            runtime_default=event.runtime_default,
            error_message=event.error_message,
            eval_time_ms=event.eval_time_ms,
        )

        try:
            self._queue.put_nowait(bounded_event)
        except queue.Full:
            with self._lock:
                self._dropped_queue += 1
            logger.debug(
                "FlagEvaluationWriter: queue full — dropped flag evaluation event for %s",
                bounded_event.flag_key,
            )

    # ------------------------------------------------------------------
    # PeriodicService implementation
    # ------------------------------------------------------------------

    def _start_service(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        self._drain_worker = PeriodicThread(
            DRAIN_INTERVAL,
            target=self._drain_queue,
            name="%s:%s:drain" % (self.__class__.__module__, self.__class__.__name__),
            no_wait_at_start=False,
        )
        self._drain_worker.start()
        try:
            super()._start_service(*args, **kwargs)
        except Exception:
            self._stop_drain_worker()
            raise

    def _stop_service(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        self._stop_drain_worker()
        super()._stop_service(*args, **kwargs)

    def periodic(self) -> None:
        """
        Drain the queue, aggregate, and flush to the EVP proxy.

        Called periodically by the PeriodicService thread (every DEFAULT_FLUSH_INTERVAL).
        Also callable directly in tests.
        """
        # 1. Drain the queue into the aggregation maps.
        self._drain_queue()

        # 2. Snapshot and reset under lock.
        with self._lock:
            dropped_queue = self._dropped_queue
            dropped_degraded = self._dropped_degraded_overflow
            full = self._full
            degraded = self._degraded
            if not full and not degraded:
                if dropped_queue:
                    logger.warning(
                        "FlagEvaluationWriter: queue full — dropped %d evaluation(s) under backpressure",
                        dropped_queue,
                    )
                    _count_metric(
                        FLAG_EVALUATION_DROPPED_METRIC,
                        dropped_queue,
                        FLAG_EVALUATION_REASON_QUEUE_OVERFLOW,
                    )
                    self._dropped_queue = 0
                if dropped_degraded:
                    logger.warning(
                        "FlagEvaluationWriter: degraded cap full — dropped %d evaluation(s)",
                        dropped_degraded,
                    )
                    _count_metric(
                        FLAG_EVALUATION_DROPPED_METRIC,
                        dropped_degraded,
                        FLAG_EVALUATION_REASON_DEGRADED_CAP,
                    )
                    self._dropped_degraded_overflow = 0
                return
            # Reset maps.
            self._full = {}
            self._degraded = {}
            self._per_flag_count = {}
            self._global_count = 0
            self._dropped_queue = 0
            self._dropped_degraded_overflow = 0

        if dropped_queue:
            logger.warning(
                "FlagEvaluationWriter: queue full — dropped %d evaluation(s) under backpressure",
                dropped_queue,
            )
            _count_metric(FLAG_EVALUATION_DROPPED_METRIC, dropped_queue, FLAG_EVALUATION_REASON_QUEUE_OVERFLOW)
        if dropped_degraded:
            logger.warning(
                "FlagEvaluationWriter: degraded cap full — dropped %d evaluation(s)",
                dropped_degraded,
            )
            _count_metric(FLAG_EVALUATION_DROPPED_METRIC, dropped_degraded, FLAG_EVALUATION_REASON_DEGRADED_CAP)

        # 3. Build payload.
        flush_time_ms = int(time.time() * 1000)
        events = []

        # Full-tier events: all optional fields present.
        for key, entry in full.items():
            flag_key = key[0]
            variant = key[1]
            allocation_key = key[2]
            ev = _base_event(flag_key, entry, flush_time_ms)
            if entry.runtime_default:
                ev["runtime_default_used"] = True
            if entry.targeting_key:
                ev["targeting_key"] = entry.targeting_key
            if variant:
                ev["variant"] = {"key": variant}
            if allocation_key:
                ev["allocation"] = {"key": allocation_key}
            if entry.error_message:
                ev["error"] = {"message": entry.error_message}
            if entry.context_attrs:
                ev["context"] = {"evaluation": entry.context_attrs}
            events.append(ev)

        # Degraded-tier events: no targeting_key, no context.
        degraded_count = 0
        for key, entry in degraded.items():
            degraded_count += entry.count
            flag_key = key[0]
            variant = key[1]
            allocation_key = key[2]
            ev = _base_event(flag_key, entry, flush_time_ms)
            if entry.runtime_default:
                ev["runtime_default_used"] = True
            if variant:
                ev["variant"] = {"key": variant}
            if allocation_key:
                ev["allocation"] = {"key": allocation_key}
            if entry.error_message:
                ev["error"] = {"message": entry.error_message}
            events.append(ev)
        _count_metric(FLAG_EVALUATION_DEGRADED_METRIC, degraded_count, FLAG_EVALUATION_REASON_CARDINALITY_CAP)

        if not events:
            return

        # 4. Encode under the EVP payload limit and POST.
        context: dict[str, str] = {}
        if ddconfig.service:
            context["service"] = ddconfig.service
        if ddconfig.env:
            context["env"] = ddconfig.env
        if ddconfig.version:
            context["version"] = ddconfig.version

        result = _build_payloads_with_stats(events, context, FLAGEVALUATIONS_PAYLOAD_SIZE_LIMIT)
        _count_metric(
            FLAG_EVALUATION_DEGRADED_METRIC,
            result.degraded_payload_limit,
            FLAG_EVALUATION_REASON_PAYLOAD_LIMIT,
        )
        _count_metric(
            FLAG_EVALUATION_DROPPED_METRIC,
            result.dropped_payload_limit,
            FLAG_EVALUATION_REASON_PAYLOAD_LIMIT,
        )
        if len(result.payloads) > 1:
            _count_metric(FLAG_EVALUATION_SPLITS_METRIC, len(result.payloads) - 1)

        for payload, num_events in result.payloads:
            self._send_payload(payload, num_events)

    def on_shutdown(self) -> None:  # type: ignore[override]
        """Final flush on service shutdown — drains the queue and flushes before exit."""
        self._stop_drain_worker()
        self.periodic()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _drain_queue(self) -> None:
        """Drain all pending events from the queue and aggregate them."""
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break
            self._aggregate(event)

    def _stop_drain_worker(self) -> None:
        worker = self._drain_worker
        if worker is None:
            return
        self._drain_worker = None
        worker.stop()
        worker.join(timeout=1.0)

    def _aggregate(self, event: _EvalEvent) -> None:
        """
        Aggregate a single evaluation event into the two-tier maps.

        Implements: full-tier → degraded-tier → drop-counted cascade.
        Canonical key computation happens here (off the hot path). Context was already
        flattened and pruned before enqueue.
        """
        context_attrs = event.attrs or {}

        # Build the full-tier key tuple.
        ctx_key = canonical_context_key(context_attrs)
        full_key = (
            event.flag_key,
            event.variant,
            event.allocation_key,
            event.runtime_default,
            event.error_message,
            event.targeting_key,
            ctx_key,
        )

        with self._lock:
            # Fast path: existing full-tier bucket.
            if full_key in self._full:
                self._full[full_key].observe(event.eval_time_ms)
                return

            # Per-flag cap check.
            per_flag = self._per_flag_count.get(event.flag_key, 0)
            if per_flag >= PER_FLAG_CAP:
                self._add_to_degraded(event)
                return

            # Increment per-flag attempt count before checking globalCap (matches Go design).
            self._per_flag_count[event.flag_key] = per_flag + 1

            # Global cap check.
            if self._global_count >= GLOBAL_CAP:
                self._add_to_degraded(event)
                return

            # New full-tier bucket.
            self._full[full_key] = _Entry(
                eval_time_ms=event.eval_time_ms,
                runtime_default=event.runtime_default,
                targeting_key=event.targeting_key,
                context_attrs=_json_safe_context(context_attrs),
                error_message=event.error_message,
            )
            self._global_count += 1

    def _add_to_degraded(self, event: _EvalEvent) -> None:
        """
        Add to the degraded-tier map (drops targeting_key + context).
        Must be called with self._lock held.
        """
        deg_key = (
            event.flag_key,
            event.variant,
            event.allocation_key,
            event.runtime_default,
            event.error_message,
        )
        if deg_key in self._degraded:
            self._degraded[deg_key].observe(event.eval_time_ms)
            return

        if len(self._degraded) >= DEGRADED_CAP:
            self._dropped_degraded_overflow += 1
            return

        self._degraded[deg_key] = _Entry(
            eval_time_ms=event.eval_time_ms,
            runtime_default=event.runtime_default,
            targeting_key="",
            context_attrs={},
            error_message=event.error_message,
        )

    def _send_payload(self, payload: bytes, num_events: int) -> None:
        """POST the encoded payload to the EVP proxy."""
        conn = typing.cast(_FlagEvaluationConnection, get_connection(self._intake, timeout=self._timeout))
        try:
            conn.request("POST", self._endpoint, payload, self._headers)
            resp = conn.getresponse()
            if resp.status >= 300:
                logger.debug(
                    "FlagEvaluationWriter: failed to send %d events to %s, status=%d: %s",
                    num_events,
                    self._intake,
                    resp.status,
                    resp.read(),
                )
            else:
                logger.debug(
                    "FlagEvaluationWriter: sent %d flag evaluation events to %s",
                    num_events,
                    self._intake,
                )
        except Exception:
            logger.debug(
                "FlagEvaluationWriter: error sending %d events to %s",
                num_events,
                self._intake,
                exc_info=True,
            )
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def _base_event(flag_key: str, entry: "_Entry", flush_time_ms: int) -> dict[str, typing.Any]:
    """Build the required-fields-only event dict for a single aggregation entry."""
    return {
        "timestamp": flush_time_ms,
        "flag": {"key": flag_key},
        "first_evaluation": entry.first_evaluation,
        "last_evaluation": entry.last_evaluation,
        "evaluation_count": entry.count,
    }


def _degraded_payload_event(event: dict[str, typing.Any]) -> dict[str, typing.Any]:
    degraded = event.copy()
    degraded.pop("context", None)
    degraded.pop("targeting_key", None)
    return degraded


def _encode_payload_event(
    event: dict[str, typing.Any],
    single_event_payload_limit: int,
) -> _PayloadEventResult:
    try:
        encoded = _json_dumps(event)
    except (TypeError, ValueError):
        logger.debug("FlagEvaluationWriter: failed to encode event", exc_info=True)
        return _PayloadEventResult(None)

    if len(encoded) <= single_event_payload_limit:
        return _PayloadEventResult(encoded)

    degraded_event = _degraded_payload_event(event)
    if degraded_event != event:
        try:
            encoded = _json_dumps(degraded_event)
        except (TypeError, ValueError):
            logger.debug("FlagEvaluationWriter: failed to encode degraded event", exc_info=True)
            return _PayloadEventResult(None)
        if len(encoded) <= single_event_payload_limit:
            logger.warning(
                "FlagEvaluationWriter: degraded oversized flag evaluation event for %s before sending",
                event.get("flag", {}).get("key", ""),
            )
            return _PayloadEventResult(encoded, degraded_payload_limit=True)

    logger.warning(
        "FlagEvaluationWriter: dropped oversized flag evaluation event for %s",
        event.get("flag", {}).get("key", ""),
    )
    return _PayloadEventResult(None, dropped_payload_limit=True)


def _build_payloads(
    events: list[dict[str, typing.Any]],
    context: dict[str, str],
    payload_size_limit: int = FLAGEVALUATIONS_PAYLOAD_SIZE_LIMIT,
) -> typing.Iterator[tuple[bytes, int]]:
    for payload in _build_payloads_with_stats(events, context, payload_size_limit).payloads:
        yield payload


def _build_payloads_with_stats(
    events: list[dict[str, typing.Any]],
    context: dict[str, str],
    payload_size_limit: int = FLAGEVALUATIONS_PAYLOAD_SIZE_LIMIT,
) -> _PayloadBuildResult:
    context_suffix = b""
    if context:
        context_suffix = b',"context":' + _json_dumps(context)
    prefix = b'{"flagEvaluations":['
    suffix = b"]" + context_suffix + b"}"
    single_event_payload_limit = payload_size_limit - len(prefix) - len(suffix)
    if single_event_payload_limit <= 0:
        logger.warning("FlagEvaluationWriter: EVP payload size limit is too small to encode flagevaluation payloads")
        return _PayloadBuildResult([])

    payload = bytearray(prefix)
    num_events = 0
    payloads: list[tuple[bytes, int]] = []
    degraded_payload_limit = 0
    dropped_payload_limit = 0

    for event in events:
        event_result = _encode_payload_event(event, single_event_payload_limit)
        encoded_event = event_result.encoded
        if event_result.dropped_payload_limit:
            dropped_payload_limit += int(event.get("evaluation_count", 1) or 1)
        if event_result.degraded_payload_limit:
            degraded_payload_limit += int(event.get("evaluation_count", 1) or 1)
        if encoded_event is None:
            continue

        separator_size = 1 if num_events else 0
        candidate_size = len(payload) + separator_size + len(encoded_event) + len(suffix)
        if num_events and candidate_size > payload_size_limit:
            payload.extend(suffix)
            payloads.append((bytes(payload), num_events))
            payload = bytearray(prefix)
            num_events = 0

        if num_events:
            payload.extend(b",")
        payload.extend(encoded_event)
        num_events += 1

    if num_events:
        payload.extend(suffix)
        payloads.append((bytes(payload), num_events))

    return _PayloadBuildResult(payloads, degraded_payload_limit, dropped_payload_limit)
