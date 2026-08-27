"""
FlagEvaluationWriter — SDK-native EVP `flagevaluation` writer for dd-trace-py.

Implements a two-tier aggregation design (full → degraded → drop-counted). Uses the same
PeriodicService + get_connection() transport path as the exposure writer in writer.py.

Key design properties:
- Async, best-effort recording: the finally_after hook invokes a bounded context snapshot
  and non-blocking enqueue. Aggregate/flush work happens in the background worker.
- Two-tier aggregation (full → degraded → drop-counted).
- Canonical context key: sorted, type-tagged, length-delimited — NOT a hash, so distinct
  contexts always produce distinct keys with no collisions.
- Context snapshotting: one bounded pass with inline field, key, value, container-width,
  depth, cycle, and visited-node caps.
- Caps: GLOBAL_CAP=131_072 (full-tier), PER_FLAG_CAP=10_000 (per-flag full-tier),
  DEGRADED_CAP=32_768 (degraded-tier). Beyond the degraded cap: drop-and-count.
- Eval-time from metadata key "dd.eval.timestamp_ms"; fallback to enqueue-time.
- First/last evaluation: min/max under lock.
- runtime_default_used: True when variant is None/absent.
- Killswitch: DD_FLAGGING_EVALUATION_COUNTS_ENABLED (default on); gates EVP path only.
- Non-blocking enqueue: queue.Queue(QUEUE_SIZE); drops + counts on queue.Full.
"""

from collections.abc import Mapping
from collections.abc import Sequence
from datetime import datetime
import http.client as httplib
import json
import queue
import struct
import time
from types import MappingProxyType
import typing

from ddtrace import config as ddconfig
from ddtrace.internal import forksafe
from ddtrace.internal.evp_proxy.constants import DEFAULT_EVP_PAYLOAD_SIZE_LIMIT
from ddtrace.internal.evp_proxy.constants import EVP_PROXY_AGENT_BASE_PATH
from ddtrace.internal.evp_proxy.constants import EVP_SUBDOMAIN_HEADER_EVENT_PLATFORM_VALUE
from ddtrace.internal.evp_proxy.constants import EVP_SUBDOMAIN_HEADER_NAME
from ddtrace.internal.logger import get_logger
from ddtrace.internal.openfeature._flageval_metrics import METADATA_ALLOCATION_KEY as METADATA_ALLOCATION_KEY
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

# Cross-SDK context snapshot limits.
MAX_CONTEXT_FIELDS = 256
MAX_KEY_LENGTH = 256
MAX_VALUE_LENGTH = 256
# Backward-compatible alias used by existing internal tests and benchmarks.
MAX_FIELD_LENGTH = MAX_VALUE_LENGTH
MAX_LIST_ELEMENTS = 256
MAX_STRUCTURE_PROPERTIES = 256
MAX_SNAPSHOT_DEPTH = 4
MAX_VISITED_NODES = MAX_CONTEXT_FIELDS * (MAX_SNAPSHOT_DEPTH + 1)
DEDICATED_TARGETING_KEY_CONTEXT_FIELDS = frozenset(("targetingKey", "targeting_key"))

CONTEXT_TRUNCATION_MAX_CONTEXT_FIELDS = "max_context_fields"
CONTEXT_TRUNCATION_MAX_KEY_LENGTH = "max_key_length"
CONTEXT_TRUNCATION_MAX_VALUE_LENGTH = "max_value_length"
CONTEXT_TRUNCATION_MAX_LIST_ELEMENTS = "max_list_elements"
CONTEXT_TRUNCATION_MAX_STRUCTURE_PROPERTIES = "max_structure_properties"
CONTEXT_TRUNCATION_MAX_SNAPSHOT_DEPTH = "max_snapshot_depth"
CONTEXT_TRUNCATION_MAX_VISITED_NODES = "max_visited_nodes"
CONTEXT_TRUNCATION_CYCLE = "cycle"
CONTEXT_TRUNCATION_SNAPSHOT_ERROR = "snapshot_error"
# A single unsupported key or value drops only its own field. Ruby's
# bounded_context_snapshot falls through its leaf type case the same way, so one
# caller value cannot discard the fields around it.
CONTEXT_TRUNCATION_UNSUPPORTED_VALUE = "unsupported_value"

_EMPTY_CONTEXT: typing.Mapping[str, typing.Any] = MappingProxyType({})

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

# Bound on the shutdown wait for the drain worker. An unbounded join blocks process
# exit if the worker stalls in aggregation or in a caller __eq__ or __hash__ call.
# dd-trace-rb and dd-trace-java both bound this wait at 5 seconds.
DRAIN_WORKER_JOIN_TIMEOUT = 5.0

# Flag metadata key where the provider stamps the evaluation timestamp (ms).
EVAL_TIMESTAMP_METADATA_KEY = "dd.eval.timestamp_ms"

# Type-tag bytes for the canonical context key encoding (mirrors Go's ctxTag* constants).
_TAG_STR = b"s"
_TAG_BOOL = b"b"
_TAG_INT = b"i"
_TAG_FLOAT = b"f"
_TAG_OTHER = b"o"


class _JSONSafeOtherString(str):
    """Immutable JSON string retaining the prior canonical-key type distinction."""


FLAG_EVALUATION_DROPPED_METRIC = "flagevaluation.rows.dropped"
FLAG_EVALUATION_DEGRADED_METRIC = "flagevaluation.rows.degraded"
FLAG_EVALUATION_SPLITS_METRIC = "flagevaluation.payload.splits"
FLAG_EVALUATION_CONTEXT_TRUNCATED_METRIC = "flagevaluation.context.truncated"

FLAG_EVALUATION_REASON_PRE_QUEUE_OVERFLOW = "pre_queue_overflow"
FLAG_EVALUATION_REASON_QUEUE_OVERFLOW = "queue_overflow"
FLAG_EVALUATION_REASON_CLOSED = "closed"
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
    """Encode a validated context scalar with a type tag and bounded representation."""
    if type(v) is bool:
        tag = _TAG_BOOL
        raw = b"true" if v else b"false"
    elif type(v) is int:
        tag = _TAG_INT
        raw = str(v).encode("ascii")
    elif type(v) is float:
        tag = _TAG_FLOAT
        raw = float.__repr__(v).encode("ascii")
    elif v is None:
        tag = _TAG_OTHER
        raw = b"None"
    elif type(v) is _JSONSafeOtherString:
        tag = _TAG_OTHER
        raw = str.encode(v, "utf-8", errors="replace")
    elif type(v) is str:
        tag = _TAG_STR
        raw = str.encode(v, "utf-8", errors="replace")
    else:
        raise TypeError("context value was not validated")
    return tag + _length_delimited(raw)


def canonical_context_key(attrs: typing.Optional[typing.Mapping[str, typing.Any]]) -> str:
    """
    Build the EXACT, comparable canonical-context string key for a pruned context dict.

    Uses sorted(attrs.items()) so the encoding is deterministic regardless of Python
    dict insertion order. Each entry is encoded as:
        length_delimited(key_bytes) + type_tag_byte + length_delimited(value_bytes)

    Because the full encoding is used as the map key (not a hash), distinct contexts
    ALWAYS produce distinct keys — no hash collisions, no misattribution.

    Returns "" for empty/None attrs.
    """
    if attrs is None:
        return ""
    parts = []
    for k in sorted(attrs.keys()):
        parts.append(_length_delimited(k.encode("utf-8", errors="replace")))
        parts.append(_encode_context_value(attrs[k]))
    return b"".join(parts).decode("latin-1")  # lossless binary → str for dict key


def flatten_and_prune_context(
    attrs: typing.Optional[typing.Mapping[str, typing.Any]],
) -> tuple[typing.Mapping[str, typing.Any], frozenset[str]]:
    """Create an immutable, flattened context snapshot in one bounded traversal.

    Mapping insertion order determines which fields survive truncation. Every mapping
    property and sequence element is charged before its key or value is inspected.
    """
    if attrs is None:
        return _EMPTY_CONTEXT, frozenset()
    if not isinstance(attrs, Mapping):
        raise TypeError("evaluation context attributes must be a mapping")

    output: dict[str, typing.Any] = {}
    reasons: set[str] = set()
    budget = [MAX_VISITED_NODES]
    _flatten_mapping("", attrs, output, {id(attrs)}, 0, reasons, budget, root=True)
    return MappingProxyType(output), frozenset(reasons)


def _consume_budget(budget: list[int], reasons: set[str]) -> bool:
    if budget[0] <= 0:
        reasons.add(CONTEXT_TRUNCATION_MAX_VISITED_NODES)
        return False
    budget[0] -= 1
    return True


def _bounded_lookahead(
    iterator: typing.Iterator[typing.Any],
    output: dict[str, typing.Any],
    reasons: set[str],
    budget: list[int],
    width_reason: str,
    width_reached: bool,
) -> None:
    """Inspect at most one extra item to distinguish an exact cap from truncation."""
    budget_exhausted = budget[0] == 0
    field_limit_reached = len(output) >= MAX_CONTEXT_FIELDS
    try:
        next(iterator)
    except StopIteration:
        return
    except Exception:
        # A boundary lookahead must not erase the valid retained prefix. An
        # exhaustion probe is terminal even when the iterator fails, otherwise
        # every ancestor could invoke one more caller-owned iterator operation.
        if budget_exhausted:
            budget[0] = -1
            reasons.add(CONTEXT_TRUNCATION_MAX_VISITED_NODES)
            if field_limit_reached:
                reasons.add(CONTEXT_TRUNCATION_MAX_CONTEXT_FIELDS)
            if width_reached:
                reasons.add(width_reason)
            return
        if field_limit_reached:
            budget[0] = -2
            reasons.add(CONTEXT_TRUNCATION_MAX_CONTEXT_FIELDS)
            if width_reached:
                reasons.add(width_reason)
            return
        if width_reached:
            reasons.add(width_reason)
            return
        raise
    if budget_exhausted:
        # A successful exhaustion probe is terminal for the whole traversal.
        # Ancestors must not each inspect another caller-owned node while unwinding.
        budget[0] = -1
        reasons.add(CONTEXT_TRUNCATION_MAX_VISITED_NODES)
    elif field_limit_reached:
        # The field cap is global, so one successful probe is sufficient. Mark
        # the traversal terminal before unwinding through parent containers.
        budget[0] = -2
    else:
        budget[0] -= 1
    if width_reached:
        reasons.add(width_reason)
    if field_limit_reached:
        reasons.add(CONTEXT_TRUNCATION_MAX_CONTEXT_FIELDS)


def _flatten_mapping(
    prefix: str,
    value: Mapping[typing.Any, typing.Any],
    output: dict[str, typing.Any],
    seen: set[int],
    depth: int,
    reasons: set[str],
    budget: list[int],
    root: bool = False,
) -> None:
    iterator = iter(value)
    index = 0
    while True:
        if budget[0] < 0:
            return
        width_reached = index >= MAX_STRUCTURE_PROPERTIES
        if width_reached or len(output) >= MAX_CONTEXT_FIELDS:
            _bounded_lookahead(
                iterator,
                output,
                reasons,
                budget,
                CONTEXT_TRUNCATION_MAX_STRUCTURE_PROPERTIES,
                width_reached,
            )
            return
        if budget[0] == 0:
            _bounded_lookahead(
                iterator,
                output,
                reasons,
                budget,
                CONTEXT_TRUNCATION_MAX_STRUCTURE_PROPERTIES,
                False,
            )
            return
        try:
            child_key = next(iterator)
        except StopIteration:
            return
        if not _consume_budget(budget, reasons):
            return
        index += 1
        if not isinstance(child_key, str):
            # Skip this field only. Aborting the walk would discard every valid
            # field around it.
            reasons.add(CONTEXT_TRUNCATION_UNSUPPORTED_VALUE)
            continue
        # str.__str__ normalizes a str subclass without running a caller override.
        # StrEnum members and lazy translation strings arrive here routinely.
        lookup_key = child_key
        child_key = str.__str__(child_key)
        if root and child_key in DEDICATED_TARGETING_KEY_CONTEXT_FIELDS:
            continue
        separator_length = 0 if not prefix else 1
        if len(child_key) > MAX_KEY_LENGTH - len(prefix) - separator_length:
            reasons.add(CONTEXT_TRUNCATION_MAX_KEY_LENGTH)
            continue
        child_prefix = child_key if not prefix else f"{prefix}.{child_key}"
        child_value = value[lookup_key]
        _flatten_bounded(child_prefix, child_value, output, seen, depth, reasons, budget)


def _flatten_sequence(
    prefix: str,
    value: Sequence[typing.Any],
    output: dict[str, typing.Any],
    seen: set[int],
    depth: int,
    reasons: set[str],
    budget: list[int],
) -> None:
    iterator = iter(value)
    index = 0
    while True:
        if budget[0] < 0:
            return
        width_reached = index >= MAX_LIST_ELEMENTS
        if width_reached or len(output) >= MAX_CONTEXT_FIELDS:
            _bounded_lookahead(
                iterator,
                output,
                reasons,
                budget,
                CONTEXT_TRUNCATION_MAX_LIST_ELEMENTS,
                width_reached,
            )
            return
        if budget[0] == 0:
            _bounded_lookahead(
                iterator,
                output,
                reasons,
                budget,
                CONTEXT_TRUNCATION_MAX_LIST_ELEMENTS,
                False,
            )
            return
        try:
            child_value = next(iterator)
        except StopIteration:
            return
        if not _consume_budget(budget, reasons):
            return
        # AIDEV-NOTE: Keep Python's existing tags[0] list notation. Changing it
        # to tags.0 requires explicit backend-owner approval under FFL-3060.
        suffix_length = len(str(index)) + 2
        if suffix_length > MAX_KEY_LENGTH - len(prefix):
            reasons.add(CONTEXT_TRUNCATION_MAX_KEY_LENGTH)
            index += 1
            continue
        child_prefix = f"{prefix}[{index}]" if prefix else f"[{index}]"
        index += 1
        _flatten_bounded(child_prefix, child_value, output, seen, depth, reasons, budget)


def _validated_leaf(value: typing.Any) -> typing.Any:
    """Return an immutable OpenFeature scalar without invoking caller conversion hooks.

    Scalar subclasses are normalized through the unbound builtin method, so a caller
    override of __str__, __float__, or isoformat never runs. IntEnum, StrEnum, and
    bool-like subclasses are common in real evaluation context.
    """
    if value is None:
        return value
    # bool before int: bool is an int subclass, so the order preserves True/False
    # on the wire instead of encoding them as 1/0.
    if isinstance(value, bool):
        return bool.__bool__(value)
    if isinstance(value, int):
        # The cross-SDK contract applies MAX_VALUE_LENGTH to strings only. Numeric
        # scalars and null retain their existing wire representation without a new cap.
        return int.__int__(value)
    if isinstance(value, float):
        return float.__float__(value)
    if isinstance(value, str):
        normalized = str.__str__(value)
        if len(normalized) > MAX_VALUE_LENGTH:
            raise ValueError(CONTEXT_TRUNCATION_MAX_VALUE_LENGTH)
        return normalized
    if isinstance(value, datetime):
        serialized = datetime.isoformat(value)
        if len(serialized) > MAX_VALUE_LENGTH:
            raise ValueError(CONTEXT_TRUNCATION_MAX_VALUE_LENGTH)
        return _JSONSafeOtherString(serialized)
    raise ValueError(CONTEXT_TRUNCATION_UNSUPPORTED_VALUE)


def _flatten_bounded(
    prefix: str,
    value: typing.Any,
    output: dict[str, typing.Any],
    seen: set[int],
    depth: int,
    reasons: set[str],
    budget: list[int],
) -> None:
    """Flatten one already-charged mapping property or sequence element."""
    if isinstance(value, Mapping):
        if depth >= MAX_SNAPSHOT_DEPTH:
            reasons.add(CONTEXT_TRUNCATION_MAX_SNAPSHOT_DEPTH)
            return
        value_id = id(value)
        if value_id in seen:
            reasons.add(CONTEXT_TRUNCATION_CYCLE)
            return
        seen.add(value_id)
        try:
            _flatten_mapping(prefix, value, output, seen, depth + 1, reasons, budget)
        finally:
            seen.remove(value_id)
        return

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if depth >= MAX_SNAPSHOT_DEPTH:
            reasons.add(CONTEXT_TRUNCATION_MAX_SNAPSHOT_DEPTH)
            return
        value_id = id(value)
        if value_id in seen:
            reasons.add(CONTEXT_TRUNCATION_CYCLE)
            return
        seen.add(value_id)
        try:
            _flatten_sequence(prefix, value, output, seen, depth + 1, reasons, budget)
        finally:
            seen.remove(value_id)
        return

    try:
        output[prefix] = _validated_leaf(value)
    except ValueError as exc:
        # Both reasons drop this one field and leave the rest of the walk intact.
        if exc.args in (
            (CONTEXT_TRUNCATION_MAX_VALUE_LENGTH,),
            (CONTEXT_TRUNCATION_UNSUPPORTED_VALUE,),
        ):
            reasons.add(exc.args[0])
            return
        raise


def _json_safe_context(attrs: typing.Mapping[str, typing.Any]) -> dict[str, typing.Any]:
    return dict(attrs)


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
        context_attrs: typing.Mapping[str, typing.Any],
        error_message: str,
    ) -> None:
        self.count: int = 1
        self.first_evaluation: int = eval_time_ms
        self.last_evaluation: int = eval_time_ms
        self.runtime_default: bool = runtime_default
        # Full-tier only:
        self.targeting_key: str = targeting_key
        self.context_attrs: dict[str, typing.Any] = dict(context_attrs)
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
    attrs: typing.Mapping[str, typing.Any]  # immutable, flattened context snapshot once queued
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


class _FlagEvaluationQueue(queue.Queue[_EvalEvent]):
    """No-argument queue type used by ResetObject after a fork."""

    def __init__(self) -> None:
        super().__init__(maxsize=QUEUE_SIZE)


class _WriterProcessState:
    """Aggregation and counters that must start empty in a fork child."""

    def __init__(self) -> None:
        self.full: dict[tuple[typing.Any, ...], _Entry] = {}
        self.degraded: dict[tuple[typing.Any, ...], _Entry] = {}
        self.per_flag_count: dict[str, int] = {}
        self.global_count = 0
        self.dropped_pre_queue = 0
        self.dropped_queue = 0
        self.dropped_degraded_overflow = 0
        self.context_truncated: dict[str, int] = {}
        self.context_snapshot_error_logged = False


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

        # Queue, aggregation, and counters are process-local. ResetObject replaces
        # them before PeriodicThread restarts in a fork child, so inherited locks,
        # queued events, and aggregate rows cannot deadlock or be emitted twice.
        self._queue = typing.cast("queue.Queue[_EvalEvent]", forksafe.ResetObject(_FlagEvaluationQueue))
        self._process_state = typing.cast(_WriterProcessState, forksafe.ResetObject(_WriterProcessState))

        # Aggregation is isolated from the short application-thread lifecycle/counter
        # critical sections. All locks reset unlocked in a fork child.
        self._lock = forksafe.Lock()
        self._lifecycle_lock = forksafe.Lock()
        self._counter_lock = forksafe.Lock()

        self._drain_worker: typing.Optional[PeriodicThread] = None
        self._accepting_events = True

    @property
    def _full(self) -> dict[tuple[typing.Any, ...], _Entry]:
        return self._process_state.full

    @_full.setter
    def _full(self, value: dict[tuple[typing.Any, ...], _Entry]) -> None:
        self._process_state.full = value

    @property
    def _degraded(self) -> dict[tuple[typing.Any, ...], _Entry]:
        return self._process_state.degraded

    @_degraded.setter
    def _degraded(self, value: dict[tuple[typing.Any, ...], _Entry]) -> None:
        self._process_state.degraded = value

    @property
    def _per_flag_count(self) -> dict[str, int]:
        return self._process_state.per_flag_count

    @_per_flag_count.setter
    def _per_flag_count(self, value: dict[str, int]) -> None:
        self._process_state.per_flag_count = value

    @property
    def _global_count(self) -> int:
        return self._process_state.global_count

    @_global_count.setter
    def _global_count(self, value: int) -> None:
        self._process_state.global_count = value

    @property
    def _dropped_pre_queue(self) -> int:
        return self._process_state.dropped_pre_queue

    @_dropped_pre_queue.setter
    def _dropped_pre_queue(self, value: int) -> None:
        self._process_state.dropped_pre_queue = value

    @property
    def _dropped_queue(self) -> int:
        return self._process_state.dropped_queue

    @_dropped_queue.setter
    def _dropped_queue(self, value: int) -> None:
        self._process_state.dropped_queue = value

    @property
    def _dropped_degraded_overflow(self) -> int:
        return self._process_state.dropped_degraded_overflow

    @_dropped_degraded_overflow.setter
    def _dropped_degraded_overflow(self, value: int) -> None:
        self._process_state.dropped_degraded_overflow = value

    @property
    def _context_truncated(self) -> dict[str, int]:
        return self._process_state.context_truncated

    @_context_truncated.setter
    def _context_truncated(self, value: dict[str, int]) -> None:
        self._process_state.context_truncated = value

    @property
    def _context_snapshot_error_logged(self) -> bool:
        return self._process_state.context_snapshot_error_logged

    @_context_snapshot_error_logged.setter
    def _context_snapshot_error_logged(self, value: bool) -> None:
        self._process_state.context_snapshot_error_logged = value

    # ------------------------------------------------------------------
    # Public API used by FlagEvalEVPHook
    # ------------------------------------------------------------------

    def enqueue(self, event: _EvalEvent) -> None:
        """
        Non-blocking enqueue from the finally_after hook thread.

        An O(1) full check avoids context work under backpressure. Otherwise context is
        bounded, flattened, and made immutable before it enters the queue. A queue.Full
        race at put_nowait is counted separately; this method never blocks.
        """
        closed = False
        with self._lifecycle_lock:
            if not self._accepting_events:
                closed = True
            elif self._queue.full():
                with self._counter_lock:
                    self._dropped_pre_queue += 1
                return
        if closed:
            self._count_closed_drop()
            return

        # Snapshot outside the lifecycle lock. Shutdown remains prompt even if a caller's
        # bounded iterator is slow; the accepting state is rechecked before commit.
        try:
            bounded_attrs, truncation_reasons = flatten_and_prune_context(event.attrs)
        except Exception as exc:
            bounded_attrs = _EMPTY_CONTEXT
            truncation_reasons = frozenset((CONTEXT_TRUNCATION_SNAPSHOT_ERROR,))
            with self._counter_lock:
                should_log_snapshot_error = not self._context_snapshot_error_logged
                self._context_snapshot_error_logged = True
            if should_log_snapshot_error:
                # Log the exception type only. The traversal calls __iter__ and
                # __getitem__ on the caller's own context object, so an exception
                # message or traceback can carry customer context data. That data is
                # consent-gated in the payload, so it must not reach the log sink.
                logger.debug("FlagEvaluationWriter: context snapshot error (%s)", type(exc).__name__)

        bounded_event = _EvalEvent(
            flag_key=event.flag_key,
            variant=event.variant,
            allocation_key=event.allocation_key,
            targeting_key=event.targeting_key,
            attrs=bounded_attrs,
            runtime_default=event.runtime_default,
            error_message=event.error_message,
            eval_time_ms=event.eval_time_ms,
        )

        closed = False
        with self._lifecycle_lock:
            if not self._accepting_events:
                closed = True
            else:
                try:
                    self._queue.put_nowait(bounded_event)
                except queue.Full:
                    with self._counter_lock:
                        self._dropped_queue += 1
                    logger.debug(
                        "FlagEvaluationWriter: queue full race — dropped flag evaluation event for %s",
                        bounded_event.flag_key,
                    )
                    return
                if truncation_reasons:
                    with self._counter_lock:
                        for reason in truncation_reasons:
                            self._context_truncated[reason] = self._context_truncated.get(reason, 0) + 1
        if closed:
            self._count_closed_drop()

    # ------------------------------------------------------------------
    # PeriodicService implementation
    # ------------------------------------------------------------------

    def _start_service(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        with self._lifecycle_lock:
            self._accepting_events = True
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
        with self._lifecycle_lock:
            self._accepting_events = False
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

        # 2. Atomically snapshot/reset aggregation and hook-path counters under their
        # independent locks. Enqueue never contends on aggregation serialization.
        with self._lock:
            dropped_degraded = self._dropped_degraded_overflow
            full = self._full
            degraded = self._degraded
            self._full = {}
            self._degraded = {}
            self._per_flag_count = {}
            self._global_count = 0
            self._dropped_degraded_overflow = 0
        with self._counter_lock:
            dropped_pre_queue = self._dropped_pre_queue
            dropped_queue = self._dropped_queue
            context_truncated = self._context_truncated
            self._dropped_pre_queue = 0
            self._dropped_queue = 0
            self._context_truncated = {}

        if dropped_pre_queue:
            logger.warning(
                "FlagEvaluationWriter: queue full before snapshot — dropped %d evaluation(s) under backpressure",
                dropped_pre_queue,
            )
            _count_metric(
                FLAG_EVALUATION_DROPPED_METRIC,
                dropped_pre_queue,
                FLAG_EVALUATION_REASON_PRE_QUEUE_OVERFLOW,
            )
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
        for reason, count in context_truncated.items():
            _count_metric(FLAG_EVALUATION_CONTEXT_TRUNCATED_METRIC, count, reason)

        if not full and not degraded:
            return

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
        """Close intake under the enqueue commit lock, then perform the final drain."""
        with self._lifecycle_lock:
            self._accepting_events = False
        self._stop_drain_worker()
        self.periodic()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _count_closed_drop(self) -> None:
        _count_metric(FLAG_EVALUATION_DROPPED_METRIC, 1, FLAG_EVALUATION_REASON_CLOSED)
        logger.debug("FlagEvaluationWriter: dropped flag evaluation event after shutdown started")

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
        # The worker can already own a dequeued event. Wait until aggregation
        # completes so the final periodic() call cannot snapshot before it.
        #
        # The wait is bounded. Aggregation calls __eq__ and __hash__ on the caller's
        # own canonical context key, so a caller implementation that blocks would
        # otherwise hang process exit.
        #
        # PeriodicThread.join returns None whether the worker stopped or the wait
        # expired, so the outcome is not observable here. A timeout is still safe:
        # _aggregate and the periodic() snapshot both hold self._lock, so a worker
        # that is still running writes into the post-swap maps rather than
        # corrupting the flush. Such an event flushes late instead of never.
        worker.join(timeout=DRAIN_WORKER_JOIN_TIMEOUT)

    def _aggregate(self, event: _EvalEvent) -> None:
        """
        Aggregate a single evaluation event into the two-tier maps.

        Implements: full-tier → degraded-tier → drop-counted cascade.
        Canonical key computation happens here (off the hot path). Context was already
        flattened, pruned, and made immutable before enqueue.
        """
        context_attrs = event.attrs if event.attrs is not None else _EMPTY_CONTEXT

        # Build the full-tier key tuple. A valid OpenFeature number can exceed
        # Python's configured integer-to-decimal conversion limit. Keep numeric
        # values uncapped, but isolate an unencodable context off the hook path so
        # one dequeued event cannot abort the drain or final flush.
        try:
            ctx_key = canonical_context_key(context_attrs)
        except (TypeError, ValueError, OverflowError) as exc:
            context_attrs = _EMPTY_CONTEXT
            ctx_key = ""
            with self._counter_lock:
                self._context_truncated[CONTEXT_TRUNCATION_SNAPSHOT_ERROR] = (
                    self._context_truncated.get(CONTEXT_TRUNCATION_SNAPSHOT_ERROR, 0) + 1
                )
                should_log_snapshot_error = not self._context_snapshot_error_logged
                self._context_snapshot_error_logged = True
            if should_log_snapshot_error:
                # Log the exception type only. Traceback frames here hold context
                # values in their locals, and those values are consent-gated in the
                # payload. They must not reach the log sink.
                logger.debug(
                    "FlagEvaluationWriter: context canonicalization error (%s)", type(exc).__name__
                )
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
