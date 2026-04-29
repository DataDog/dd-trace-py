from io import StringIO
import math
import sys
import traceback
from types import TracebackType
from typing import Any
from typing import Callable
from typing import Mapping
from typing import Optional
from typing import Text
from typing import Union
from typing import cast

from ddtrace._trace._limits import MAX_SPAN_META_VALUE_LEN
from ddtrace._trace._span_link import SpanLink
from ddtrace._trace._span_pointer import _SpanPointerDirection
from ddtrace._trace.context import Context
from ddtrace._trace.types import _AttributeValueType
from ddtrace.constants import _SAMPLING_AGENT_DECISION
from ddtrace.constants import _SAMPLING_LIMIT_DECISION
from ddtrace.constants import _SAMPLING_RULE_DECISION
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import MANUAL_DROP_KEY
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.constants import SERVICE_KEY
from ddtrace.constants import SERVICE_VERSION_KEY
from ddtrace.constants import USER_KEEP
from ddtrace.constants import USER_REJECT
from ddtrace.constants import VERSION_KEY
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.compat import NumericType
from ddtrace.internal.compat import is_integer
from ddtrace.internal.constants import MAX_INT_64BITS as _MAX_INT_64BITS
from ddtrace.internal.constants import MAX_UINT_64BITS as _MAX_UINT_64BITS
from ddtrace.internal.constants import MIN_INT_64BITS as _MIN_INT_64BITS
from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
from ddtrace.internal.constants import SPAN_API_DATADOG
from ddtrace.internal.constants import SamplingMechanism
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.settings._config import config
from ddtrace.internal.utils.time import Time


log = get_logger(__name__)


def _get_64_lowest_order_bits_as_int(large_int: int) -> int:
    """Get the 64 lowest order bits from a 128bit integer"""
    return _MAX_UINT_64BITS & large_int


def _get_64_highest_order_bits_as_hex(large_int: int) -> str:
    """Get the 64 highest order bits from a 128bit integer"""
    return f"{large_int:032x}"[:16]


_INT_TYPES = frozenset([net.TARGET_PORT])


class Span(SpanData):
    __slots__ = [
        # Public span attributes
        "_meta",
        "context",
        "_metrics",
        "_store",
        # Internal attributes
        "_parent_context",
        "_local_root_value",
        "_service_entry_span_value",
        "_parent",
        "_ignored_exceptions",
        "_on_finish_callbacks",
        "__weakref__",
    ]

    def __init__(
        self,
        name: str,
        service: Optional[str] = None,
        resource: Optional[str] = None,
        span_type: Optional[str] = None,
        trace_id: Optional[int] = None,
        span_id: Optional[int] = None,
        parent_id: Optional[int] = None,
        start: Optional[int] = None,
        context: Optional[Context] = None,
        on_finish: Optional[list[Callable[["Span"], None]]] = None,
        span_api: str = SPAN_API_DATADOG,
        links: Optional[list[SpanLink]] = None,
    ) -> None:
        """
        Create a new span. Call `finish` once the traced operation is over.

        **Note:** A ``Span`` should only be accessed or modified in the process
        that it was created in. Using a ``Span`` from within a child process
        could result in a deadlock or unexpected behavior.

        :param str name: the name of the traced operation.

        :param str service: the service name
        :param str resource: the resource name
        :param str span_type: the span type

        :param int trace_id: the id of this trace's root span.
        :param int parent_id: the id of this span's direct parent span.
        :param int span_id: the id of this span.

        :param int start: the start time of request as a unix epoch in seconds
        :param object context: the Context of the span.
        :param on_finish: list of functions called when the span finishes.
        """
        self._meta: dict[str, str] = {}  # ast-grep-ignore: span-meta-access
        self._metrics: dict[str, NumericType] = {}  # ast-grep-ignore: span-metrics-access

        self._on_finish_callbacks = [] if on_finish is None else on_finish

        self._parent_context: Optional[Context] = context
        # PERF: cache trace_id/span_id to avoid repeated Rust property calls
        _trace_id = self.trace_id
        _span_id = self.span_id
        self.context: Context = (
            context.copy(_trace_id, _span_id)
            if context
            else Context(trace_id=_trace_id, span_id=_span_id, is_remote=False)
        )

        if links:
            for link in links:
                self._set_link(link.trace_id, link.span_id, link.tracestate, link.flags, link.attributes)

        self._parent: Optional["Span"] = None
        self._ignored_exceptions: Optional[list[type[BaseException]]] = None
        self._local_root_value: Optional["Span"] = None  # None means this is the root span.
        self._service_entry_span_value: Optional["Span"] = None  # None means this is the service entry span.
        self._store: Optional[dict[str, Any]] = None

    def _update_tags_from_context(self) -> None:
        with self.context:
            for tag in self.context._meta:
                self._meta.setdefault(tag, self.context._meta[tag])  # ast-grep-ignore: span-meta-access
            for metric in self.context._metrics:
                self._metrics.setdefault(metric, self.context._metrics[metric])  # ast-grep-ignore: span-metrics-access

    def _ignore_exception(self, exc: type[BaseException]) -> None:
        if self._ignored_exceptions is None:
            self._ignored_exceptions = [exc]
        else:
            self._ignored_exceptions.append(exc)

    def _set_ctx_item(self, key: str, val: Any) -> None:
        if not self._store:
            self._store = {}
        self._store[key] = val

    def _set_ctx_items(self, items: dict[str, Any]) -> None:
        if not self._store:
            self._store = {}
        self._store.update(items)

    def _get_ctx_item(self, key: str) -> Optional[Any]:
        if not self._store:
            return None
        return self._store.get(key)

    def finish(self, finish_time: Optional[float] = None) -> None:
        """Mark the end time of the span and submit it to the tracer.
        If the span has already been finished don't do anything.

        :param finish_time: The end time of the span, in seconds. Defaults to ``now``.
        """
        if finish_time is None:
            self._finish_ns(Time.time_ns())
        else:
            self._finish_ns(int(finish_time * 1e9))

    def _finish_ns(self, finish_time_ns: int) -> None:
        if self.duration_ns is not None:
            return

        # be defensive so we don't die if start isn't set
        self.duration_ns = finish_time_ns - (self.start_ns or finish_time_ns)

        for cb in self._on_finish_callbacks:
            cb(self)

    def _override_sampling_decision(self, decision: Optional[NumericType]):
        self.context.sampling_priority = decision
        self._set_sampling_decision_maker(SamplingMechanism.MANUAL)
        if self._local_root:
            for key in (_SAMPLING_RULE_DECISION, _SAMPLING_AGENT_DECISION, _SAMPLING_LIMIT_DECISION):
                if self._local_root._has_attribute(key):
                    self._local_root._remove_attribute(key)

    def _set_sampling_decision_maker(
        self,
        sampling_mechanism: int,
    ) -> Optional[Text]:
        value = "-%d" % sampling_mechanism
        self.context._meta[SAMPLING_DECISION_TRACE_TAG_KEY] = value
        return value

    def set_tag(self, key: str, value: Optional[str] = None) -> None:
        """Set a tag key/value pair on the span."""
        # Special case, force `http.status_code` as a string
        # DEV: `http.status_code` *has* to be in `meta` for metrics
        #   calculated in the trace agent
        if key == http.STATUS_CODE:
            value = str(value)

        # Determine once up front
        val_is_an_int = is_integer(value)

        # Explicitly try to convert expected integers to `int`
        # DEV: Some integrations parse these values from strings, but don't call `int(value)` themselves
        if key in _INT_TYPES and not val_is_an_int:
            try:
                value = int(value)  # type: ignore
                val_is_an_int = True
            except (ValueError, TypeError):
                pass

        # Set integers that are less than equal to 2^53 as metrics
        if value is not None and val_is_an_int and abs(value) <= 2**53:  # type: ignore
            self.set_metric(key, value)  # type: ignore  # ast-grep-ignore: span-set-metric
            return

        # All floats should be set as a metric
        elif isinstance(value, float):
            self.set_metric(key, value)  # ast-grep-ignore: span-set-metric
            return

        elif key == MANUAL_KEEP_KEY:
            self._override_sampling_decision(USER_KEEP)
            return
        elif key == MANUAL_DROP_KEY:
            self._override_sampling_decision(USER_REJECT)
            return
        elif key == SERVICE_KEY:
            self.service = value
        elif key == SERVICE_VERSION_KEY:
            # Also set the `version` tag to the same value
            # DEV: Note that we do no return, we want to set both
            self.set_tag(VERSION_KEY, value)
        elif key == _SPAN_MEASURED_KEY:
            # Set `_dd.measured` tag as a metric
            # DEV: `set_metric` will ensure it is an integer 0 or 1
            if value is None:
                value = 1  # type: ignore
            self.set_metric(key, value)  # type: ignore  # ast-grep-ignore: span-set-metric
            return

        try:
            self._meta[key] = str(value)  # ast-grep-ignore: span-meta-access
            if key in self._metrics:  # ast-grep-ignore: span-metrics-access
                del self._metrics[key]  # ast-grep-ignore: span-metrics-access
        except Exception:
            log.warning("error setting tag %s, ignoring it", key, exc_info=True)

    def _set_attribute(self, key: str, value: Union[str, int, float]) -> None:
        """Set a tag key/value pair on the span. Values must be either strings or numbers."""
        # DEV: `http.status_code` must be stored in `meta` as a string for
        #   metrics calculated in the trace agent
        if key == http.STATUS_CODE:
            value = str(value)
        if isinstance(value, str):
            self._meta[key] = value  # ast-grep-ignore: span-meta-access
            if key in self._metrics:  # ast-grep-ignore: span-metrics-access
                del self._metrics[key]  # ast-grep-ignore: span-metrics-access
        elif isinstance(value, (int, float)):
            if math.isnan(value) or math.isinf(value):
                log.debug("ignoring not real attribute %s:%s", key, value)
                return
            self._metrics[key] = value  # ast-grep-ignore: span-metrics-access
            if key in self._meta:  # ast-grep-ignore: span-meta-access
                del self._meta[key]  # ast-grep-ignore: span-meta-access
        elif isinstance(value, bytes):
            self._meta[key] = value.decode("utf-8", errors="replace")  # ast-grep-ignore: span-meta-access
            if key in self._metrics:  # ast-grep-ignore: span-metrics-access
                del self._metrics[key]  # ast-grep-ignore: span-metrics-access
        else:
            try:
                self._meta[key] = str(value)  # ast-grep-ignore: span-meta-access
            except Exception:
                if config._raise:
                    raise
                log.warning("Failed to convert attribute '%s' to str, ignoring it", key, exc_info=True)
                return
            if key in self._metrics:  # ast-grep-ignore: span-metrics-access
                del self._metrics[key]  # ast-grep-ignore: span-metrics-access

    def _has_attribute(self, key: str) -> bool:
        """Return whether the given attribute exists."""
        return key in self._meta or key in self._metrics  # ast-grep-ignore: span-meta-access,span-metrics-access

    def _remove_attribute(self, key: str) -> None:
        """Remove the given attribute if it exists."""
        self._meta.pop(key, None)  # ast-grep-ignore: span-meta-access
        self._metrics.pop(key, None)  # ast-grep-ignore: span-metrics-access

    def _get_attribute(self, key: str) -> Optional[Union[str, int, float]]:
        """Return the given attribute or None if it doesn't exist."""
        if key in self._meta:  # ast-grep-ignore: span-meta-access
            return self._meta[key]  # ast-grep-ignore: span-meta-access
        elif key in self._metrics:  # ast-grep-ignore: span-metrics-access
            return self._metrics[key]  # ast-grep-ignore: span-metrics-access
        else:
            return None

    def _get_str_attribute(self, key: str) -> Optional[str]:
        """Return the string attribute for the given key, or None if it doesn't exist."""
        return self._meta.get(key)  # ast-grep-ignore: span-meta-access

    def _get_numeric_attribute(self, key: str) -> Optional[NumericType]:
        """Return the numeric attribute for the given key, or None if it doesn't exist."""
        return self._metrics.get(key)  # ast-grep-ignore: span-metrics-access

    def _get_attributes(self) -> Mapping[str, Union[str, NumericType]]:
        """Return all attributes (both string and numeric) as a single mapping."""
        return {**self._meta, **self._metrics}  # ast-grep-ignore: span-meta-access,span-metrics-access

    def _get_str_attributes(self) -> Mapping[str, str]:
        """Return all string attributes."""
        return self._meta  # ast-grep-ignore: span-meta-access

    def _get_numeric_attributes(self) -> Mapping[str, NumericType]:
        """Return all numeric attributes."""
        return self._metrics  # ast-grep-ignore: span-metrics-access

    def get_tag(self, key: str) -> Optional[str]:
        """Return the given tag or None if it doesn't exist."""
        return self._meta.get(key, None)  # ast-grep-ignore: span-meta-access

    def get_tags(self) -> dict[str, str]:
        """Return all tags."""
        return self._meta.copy()  # ast-grep-ignore: span-meta-access

    def set_tags(self, tags: dict[str, str]) -> None:
        """Set a dictionary of tags on the given span. Keys and values
        must be strings (or stringable)
        """
        if tags:
            for k, v in iter(tags.items()):
                self.set_tag(k, v)

    def set_metric(self, key: str, value: NumericType) -> None:
        """This method sets a numeric tag value for the given key."""
        # Enforce a specific constant for `_dd.measured`
        if key == _SPAN_MEASURED_KEY:
            try:
                value = int(bool(value))
            except (ValueError, TypeError):
                log.warning("failed to convert %r tag to an integer from %r", key, value)
                return

        # FIXME[matt] we could push this check to serialization time as well.
        # only permit types that are commonly serializable (don't use
        # isinstance so that we convert unserializable types like numpy
        # numbers)
        if not isinstance(value, (int, float)):
            try:
                value = float(value)
            except (ValueError, TypeError):
                log.debug("ignoring not number metric %s:%s", key, value)
                return

        # don't allow nan or inf
        if math.isnan(value) or math.isinf(value):
            log.debug("ignoring not real metric %s:%s", key, value)
            return

        if key in self._meta:  # ast-grep-ignore: span-meta-access
            del self._meta[key]  # ast-grep-ignore: span-meta-access
        self._metrics[key] = value  # ast-grep-ignore: span-metrics-access

    def set_metrics(self, metrics: dict[str, NumericType]) -> None:
        """Set a dictionary of metrics on the given span. Keys must be
        must be strings (or stringable). Values must be numeric.
        """
        if metrics:
            for k, v in metrics.items():
                self.set_metric(k, v)  # ast-grep-ignore: span-set-metric

    def get_metric(self, key: str) -> Optional[NumericType]:
        """Return the given metric or None if it doesn't exist."""
        return self._metrics.get(key)  # ast-grep-ignore: span-metrics-access

    def _add_on_finish_exception_callback(self, callback: Callable[["Span"], None]):
        """Add an errortracking related callback to the on_finish_callback array"""
        self._on_finish_callbacks.insert(0, callback)

    def get_metrics(self) -> dict[str, NumericType]:
        """Return all metrics."""
        return self._metrics.copy()  # ast-grep-ignore: span-metrics-access

    def set_traceback(self, limit: Optional[int] = None):
        """If the current stack has an exception, tag the span with the
        relevant error info. If not, tag it with the current python stack.
        """
        (exc_type, exc_val, exc_tb) = sys.exc_info()

        if exc_type and exc_val and exc_tb:
            if limit:
                limit = -abs(limit)
            self.set_exc_info(exc_type, exc_val, exc_tb, limit=limit)
        else:
            if limit is None:
                limit = config._span_traceback_max_size
            tb = "".join(traceback.format_stack(limit=limit + 1)[:-1])
            self._meta[ERROR_STACK] = tb  # ast-grep-ignore: span-meta-access

    def _get_traceback(
        self,
        exc_type: type[BaseException],
        exc_val: BaseException,
        exc_tb: Optional[TracebackType],
        limit: Optional[int] = None,
    ) -> str:
        """
        Return a formatted traceback as a string.
        If the traceback is too long, it will be truncated to the limit parameter,
        but from the end of the traceback (keeping the most recent frames).

        If the traceback surpasses the MAX_SPAN_META_VALUE_LEN limit, it will
        try to reduce the traceback size by half until it fits
        within this limit (limit for tag values).

        :param exc_type: the exception type
        :param exc_val: the exception value
        :param exc_tb: the exception traceback
        :param limit: the maximum number of frames to keep
        :return: the formatted traceback as a string
        """
        # If limit is None, use the default value from the configuration
        if limit is None:
            limit = config._span_traceback_max_size
        # Ensure the limit is negative for traceback.print_exception (to keep most recent frames)
        limit: int = -abs(limit)  # type: ignore[no-redef]

        # Create a buffer to hold the traceback
        buff = StringIO()
        # Print the exception traceback to the buffer with the specified limit
        traceback.print_exception(exc_type, exc_val, exc_tb, file=buff, limit=limit)
        tb = buff.getvalue()

        # Check if the traceback exceeds the maximum allowed length
        while len(tb) > MAX_SPAN_META_VALUE_LEN and abs(limit) > 1:
            # Reduce the limit by half and print the traceback again
            limit //= 2
            buff = StringIO()
            traceback.print_exception(exc_type, exc_val, exc_tb, file=buff, limit=limit)
            tb = buff.getvalue()

        return tb

    def set_exc_info(
        self,
        exc_type: type[BaseException],
        exc_val: BaseException,
        exc_tb: Optional[TracebackType],
        limit: Optional[int] = None,
    ) -> None:
        """Tag the span with an error tuple as from `sys.exc_info()`."""
        if not (exc_type and exc_val and exc_tb):
            return  # nothing to do

        # SystemExit(0) is not an error
        if issubclass(exc_type, SystemExit) and cast(SystemExit, exc_val).code == 0:
            return

        if self._ignored_exceptions and any([issubclass(exc_type, e) for e in self._ignored_exceptions]):
            return

        self.error = 1
        tb = self._get_traceback(exc_type, exc_val, exc_tb, limit=limit)

        # readable version of type (e.g. exceptions.ZeroDivisionError)
        exc_type_str = "%s.%s" % (exc_type.__module__, exc_type.__name__)
        self._meta[ERROR_TYPE] = exc_type_str  # ast-grep-ignore: span-meta-access

        try:
            self._meta[ERROR_MSG] = str(exc_val)  # ast-grep-ignore: span-meta-access
        except Exception:
            # An exception can occur if a custom Exception overrides __str__
            # If this happens str(exc_val) won't work, so best we can do is print the class name
            # Otherwise, don't try to set an error message
            if exc_val and hasattr(exc_val, "__class__"):
                self._meta[ERROR_MSG] = exc_val.__class__.__name__  # ast-grep-ignore: span-meta-access

        self._meta[ERROR_STACK] = tb  # ast-grep-ignore: span-meta-access

        # some web integrations like bottle rely on set_exc_info to get the error tags, so we need to dispatch
        # this event such that the additional tags for inferred aws api gateway spans can be appended here.
        core.dispatch("web.request.final_tags", (self,))

        core.dispatch("span.exception", (self, exc_type, exc_val, exc_tb))

    def record_exception(
        self,
        exception: BaseException,
        attributes: Optional[dict[str, _AttributeValueType]] = None,
    ) -> None:
        """
        Records an exception as a span event. Multiple exceptions can be recorded on a span.

        :param exception: The exception to record.
        :param attributes: Optional dictionary of additional attributes to add to the exception event.
            These attributes will override the default exception attributes if they contain the same keys.
            Valid attribute values include (homogeneous array of) strings, booleans, integers, floats.
        """
        tb = self._get_traceback(type(exception), exception, exception.__traceback__)

        attrs: dict[str, _AttributeValueType] = {
            "exception.type": "%s.%s" % (exception.__class__.__module__, exception.__class__.__name__),
            "exception.message": str(exception),
            "exception.stacktrace": tb,
        }
        if attributes:
            attributes = {k: v for k, v in attributes.items() if self._validate_attribute(k, v)}

            # User provided attributes must take precedence over attrs
            attrs.update(attributes)

        self._add_event(name="exception", attributes=attrs, time_unix_nano=Time.time_ns())

    def _validate_attribute(self, key: str, value: object) -> bool:
        if isinstance(value, (str, bool, int, float)):
            return self._validate_scalar(key, value)

        if not isinstance(value, list):
            log.warning("record_exception: Attribute %s must be a string, number, or boolean: %s.", key, value)
            return False

        if len(value) == 0:
            return True

        if not isinstance(value[0], (str, bool, int, float)):
            log.warning("record_exception: List values %s must be string, number, or boolean: %s.", key, value)
            return False

        first_type = type(value[0])
        for val in value:
            if not isinstance(val, first_type) or not self._validate_scalar(key, val):
                log.warning("record_exception: Attribute %s array must be homogeneous: %s.", key, value)
                return False
        return True

    def _validate_scalar(self, key: str, value: Union[bool, str, int, float]) -> bool:
        if isinstance(value, (bool, str)):
            return True

        if isinstance(value, int):
            if value < _MIN_INT_64BITS or value > _MAX_INT_64BITS:
                log.warning(
                    "record_exception: Attribute %s must be within the range of a signed 64-bit integer: %s.",
                    key,
                    value,
                )
                return False
            return True

        if isinstance(value, float):
            if not math.isfinite(value):
                log.warning("record_exception: Attribute %s must be a finite number: %s.", key, value)
                return False
            return True

        return False

    @property
    def _local_root(self) -> "Span":
        return self._local_root_value or self

    @_local_root.setter
    def _local_root(self, value: "Span") -> None:
        self._local_root_value = value if value is not self else None

    @_local_root.deleter
    def _local_root(self) -> None:
        del self._local_root_value

    @property
    def _service_entry_span(self) -> "Span":
        return self._service_entry_span_value or self

    @_service_entry_span.setter
    def _service_entry_span(self, span: "Span") -> None:
        self._service_entry_span_value = None if span is self else span

    @_service_entry_span.deleter
    def _service_entry_span(self) -> None:
        del self._service_entry_span_value

    def link_span(self, context: Context, attributes: Optional[Mapping[str, Any]] = None) -> None:
        """Defines a causal relationship between two spans"""
        if not context.trace_id or not context.span_id:
            msg = f"Invalid span or trace id. trace_id:{context.trace_id} span_id:{context.span_id}"
            if config._raise:
                raise ValueError(msg)
            else:
                log.warning(msg)

        if context.trace_id and context.span_id:
            self.set_link(
                trace_id=context.trace_id,
                span_id=context.span_id,
                tracestate=context._tracestate,
                flags=int(context._traceflags),
                attributes=attributes,
            )

    def set_link(
        self,
        trace_id: int,
        span_id: int,
        tracestate: Optional[str] = None,
        flags: Optional[int] = None,
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._set_link(
            trace_id,
            span_id,
            tracestate=tracestate,
            flags=flags,
            attributes=attributes,
        )

    def _add_span_pointer(
        self,
        pointer_kind: str,
        pointer_direction: _SpanPointerDirection,
        pointer_hash: str,
        extra_attributes: Optional[dict[str, Any]] = None,
    ) -> None:
        # This is a Private API for now.
        attrs: dict[str, Any] = {}
        if extra_attributes is not None:
            attrs.update(extra_attributes)
        # Set required ptr.* keys after extra_attributes so they cannot be overridden
        attrs["link.kind"] = "span-pointer"
        attrs["ptr.kind"] = pointer_kind
        attrs["ptr.dir"] = pointer_direction.value
        attrs["ptr.hash"] = pointer_hash

        self._set_link(0, 0, attributes=attrs)

    def _finish_with_ancestors(self) -> None:
        """Finish this span along with all (accessible) ancestors of this span.

        This method is useful if a sudden program shutdown is required and finishing
        the trace is desired.
        """
        span: Optional["Span"] = self
        while span is not None:
            span.finish()
            span = span._parent

    def __enter__(self) -> "Span":
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        try:
            if exc_type:
                self.set_exc_info(exc_type, exc_val, exc_tb)  # type: ignore
            self.finish()
        except Exception:
            log.exception("error closing trace")

    def __repr__(self) -> str:
        """Return a detailed string representation of a span."""
        meta = {
            k: v.keys() if isinstance(v, dict) else f"wrong type [{type(v).__name__}]"
            for k, v in self._get_meta_structs().items()
        }
        return (
            f"Span(name='{self.name}', "
            f"span_id={self.span_id}, "
            f"parent_id={self.parent_id}, "
            f"trace_id={self.trace_id}, "
            f"service='{self.service}', "
            f"resource='{self.resource}', "
            f"type='{self.span_type}', "
            f"start={self.start_ns}, "
            f"end={self.duration_ns and self.start_ns and self.start_ns + self.duration_ns}, "
            f"duration={self.duration_ns}, "
            f"error={self.error}, "
            f"tags={self._meta}, "  # ast-grep-ignore: span-meta-access
            f"metrics={self._metrics}, "  # ast-grep-ignore: span-metrics-access
            f"links={self._get_links()}, "
            f"events={self._get_events()}, "
            f"context={self.context}, "
            f"service_entry_span_name={self._service_entry_span.name}), "
            f"metastruct={meta}"
        )

    def __str__(self) -> str:
        """Return a concise string representation of a span."""
        return "<Span(id=%s,trace_id=%s,parent_id=%s,name=%s)>" % (
            self.span_id,
            self.trace_id,
            self.parent_id,
            self.name,
        )

    @property
    def _is_top_level(self) -> bool:
        """Return whether the span is a "top level" span.

        Top level meaning the root of the trace or a child span
        whose service is different from its parent.
        """
        return (self._local_root is self) or (
            self._parent is not None and self._parent.service != self.service and self.service is not None
        )
