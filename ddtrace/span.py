import math
import pprint
import sys
import traceback
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import TYPE_CHECKING
from typing import Text
from typing import Union

import six

from . import config
from .constants import ERROR_MSG
from .constants import ERROR_STACK
from .constants import ERROR_TYPE
from .constants import MANUAL_DROP_KEY
from .constants import MANUAL_KEEP_KEY
from .constants import NUMERIC_TAGS
from .constants import SERVICE_KEY
from .constants import SERVICE_VERSION_KEY
from .constants import SPAN_MEASURED_KEY
from .constants import USER_KEEP
from .constants import USER_REJECT
from .constants import VERSION_KEY
from .context import Context
from .ext import SpanTypes
from .ext import http
from .ext import net
from .internal import _rand
from .internal.compat import NumericType
from .internal.compat import StringIO
from .internal.compat import ensure_text
from .internal.compat import is_integer
from .internal.compat import iteritems
from .internal.compat import numeric_types
from .internal.compat import stringify
from .internal.compat import time_ns
from .internal.logger import get_logger


if TYPE_CHECKING:
    from .tracer import Tracer


_TagNameType = Union[Text, bytes]
_MetaDictType = Dict[_TagNameType, Text]
_MetricDictType = Dict[_TagNameType, NumericType]

log = get_logger(__name__)


class Span(object):

    __slots__ = [
        # Public span attributes
        "service",
        "name",
        "_resource",
        "span_id",
        "trace_id",
        "parent_id",
        "meta",
        "error",
        "metrics",
        "_span_type",
        "start_ns",
        "duration_ns",
        "tracer",
        # Sampler attributes
        "sampled",
        # Internal attributes
        "_context",
        "_local_root",
        "_parent",
        "_ignored_exceptions",
        "_on_finish_callbacks",
        "__weakref__",
    ]

    def __init__(
        self,
        tracer,  # type: Optional[Tracer]
        name,  # type: str
        service=None,  # type: Optional[str]
        resource=None,  # type: Optional[str]
        span_type=None,  # type: Optional[str]
        trace_id=None,  # type: Optional[int]
        span_id=None,  # type: Optional[int]
        parent_id=None,  # type: Optional[int]
        start=None,  # type: Optional[int]
        context=None,  # type: Optional[Context]
        on_finish=None,  # type: Optional[List[Callable[[Span], None]]]
    ):
        # type: (...) -> None
        """
        Create a new span. Call `finish` once the traced operation is over.

        **Note:** A ``Span`` should only be accessed or modified in the process
        that it was created in. Using a ``Span`` from within a child process
        could result in a deadlock or unexpected behavior.

        :param ddtrace.Tracer tracer: the tracer that will submit this span when
            finished.
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
        # pre-conditions
        if not (span_id is None or isinstance(span_id, six.integer_types)):
            raise TypeError("span_id must be an integer")
        if not (trace_id is None or isinstance(trace_id, six.integer_types)):
            raise TypeError("trace_id must be an integer")
        if not (parent_id is None or isinstance(parent_id, six.integer_types)):
            raise TypeError("parent_id must be an integer")

        # required span info
        self.name = name
        self.service = service
        self._resource = [resource or name]
        self._span_type = None
        self.span_type = span_type

        # tags / metadata
        self.meta = {}  # type: _MetaDictType
        self.error = 0
        self.metrics = {}  # type: _MetricDictType

        # timing
        self.start_ns = time_ns() if start is None else int(start * 1e9)  # type: int
        self.duration_ns = None  # type: Optional[int]

        # tracing
        self.trace_id = trace_id or _rand.rand64bits()  # type: int
        self.span_id = span_id or _rand.rand64bits()  # type: int
        self.parent_id = parent_id  # type: Optional[int]
        self.tracer = tracer  # type: Optional[Tracer]
        self._on_finish_callbacks = [] if on_finish is None else on_finish

        # sampling
        self.sampled = True  # type: bool

        self._context = context._with_span(self) if context else None  # type: Optional[Context]
        self._parent = None  # type: Optional[Span]
        self._ignored_exceptions = None  # type: Optional[List[Exception]]
        self._local_root = None  # type: Optional[Span]

    def _ignore_exception(self, exc):
        # type: (Exception) -> None
        if self._ignored_exceptions is None:
            self._ignored_exceptions = [exc]
        else:
            self._ignored_exceptions.append(exc)

    @property
    def start(self):
        # type: () -> float
        """The start timestamp in Unix epoch seconds."""
        return self.start_ns / 1e9

    @start.setter
    def start(self, value):
        # type: (Union[int, float]) -> None
        self.start_ns = int(value * 1e9)

    @property
    def resource(self):
        return self._resource[0]

    @resource.setter
    def resource(self, value):
        self._resource[0] = value

    @property
    def span_type(self):
        return self._span_type

    @span_type.setter
    def span_type(self, value):
        self._span_type = value.value if isinstance(value, SpanTypes) else value

    @property
    def finished(self):
        # type: () -> bool
        return self.duration_ns is not None

    @finished.setter
    def finished(self, value):
        # type: (bool) -> None
        """Finishes the span if set to a truthy value.

        If the span is already finished and a truthy value is provided
        no action will occur.
        """
        if value:
            if not self.finished:
                self.duration_ns = time_ns() - self.start_ns
        else:
            self.duration_ns = None

    @property
    def duration(self):
        # type: () -> Optional[float]
        """The span duration in seconds."""
        if self.duration_ns is not None:
            return self.duration_ns / 1e9
        return None

    @duration.setter
    def duration(self, value):
        # type: (float) -> None
        self.duration_ns = int(value * 1e9)

    def finish(self, finish_time=None):
        # type: (Optional[float]) -> None
        """Mark the end time of the span and submit it to the tracer.
        If the span has already been finished don't do anything.

        :param finish_time: The end time of the span, in seconds. Defaults to ``now``.
        """
        if self.duration_ns is not None:
            return

        ft = time_ns() if finish_time is None else int(finish_time * 1e9)
        # be defensive so we don't die if start isn't set
        self.duration_ns = ft - (self.start_ns or ft)

        for cb in self._on_finish_callbacks:
            cb(self)

    def set_tag(self, key, value=None):
        # type: (_TagNameType, Any) -> None
        """Set a tag key/value pair on the span.

        Keys must be strings, values must be ``stringify``-able.

        :param key: Key to use for the tag
        :type key: str
        :param value: Value to assign for the tag
        :type value: ``stringify``-able value
        """

        if not isinstance(key, six.string_types):
            log.warning("Ignoring tag pair %s:%s. Key must be a string.", key, value)
            return

        # Special case, force `http.status_code` as a string
        # DEV: `http.status_code` *has* to be in `meta` for metrics
        #   calculated in the trace agent
        if key == http.STATUS_CODE:
            value = str(value)

        # Determine once up front
        val_is_an_int = is_integer(value)

        # Explicitly try to convert expected integers to `int`
        # DEV: Some integrations parse these values from strings, but don't call `int(value)` themselves
        INT_TYPES = (net.TARGET_PORT,)
        if key in INT_TYPES and not val_is_an_int:
            try:
                value = int(value)
                val_is_an_int = True
            except (ValueError, TypeError):
                pass

        # Set integers that are less than equal to 2^53 as metrics
        if value is not None and val_is_an_int and abs(value) <= 2 ** 53:
            self.set_metric(key, value)
            return

        # All floats should be set as a metric
        elif isinstance(value, float):
            self.set_metric(key, value)
            return

        # Key should explicitly be converted to a float if needed
        elif key in NUMERIC_TAGS:
            if value is None:
                log.debug("ignoring not number metric %s:%s", key, value)
                return

            try:
                # DEV: `set_metric` will try to cast to `float()` for us
                self.set_metric(key, value)
            except (TypeError, ValueError):
                log.warning("error setting numeric metric %s:%s", key, value)

            return

        elif key == MANUAL_KEEP_KEY:
            self.context.sampling_priority = USER_KEEP
            return
        elif key == MANUAL_DROP_KEY:
            self.context.sampling_priority = USER_REJECT
            return
        elif key == SERVICE_KEY:
            self.service = value
        elif key == SERVICE_VERSION_KEY:
            # Also set the `version` tag to the same value
            # DEV: Note that we do no return, we want to set both
            self.set_tag(VERSION_KEY, value)
        elif key == SPAN_MEASURED_KEY:
            # Set `_dd.measured` tag as a metric
            # DEV: `set_metric` will ensure it is an integer 0 or 1
            if value is None:
                value = 1
            self.set_metric(key, value)
            return

        try:
            self.meta[key] = stringify(value)
            if key in self.metrics:
                del self.metrics[key]
        except Exception:
            log.warning("error setting tag %s, ignoring it", key, exc_info=True)

    def _set_str_tag(self, key, value):
        # type: (_TagNameType, Text) -> None
        """Set a value for a tag. Values are coerced to unicode in Python 2 and
        str in Python 3, with decoding errors in conversion being replaced with
        U+FFFD.
        """
        try:
            self.meta[key] = ensure_text(value, errors="replace")
        except Exception as e:
            if config._raise:
                raise e
            log.warning("Failed to set text tag '%s'", key, exc_info=True)

    def _remove_tag(self, key):
        # type: (_TagNameType) -> None
        if key in self.meta:
            del self.meta[key]

    def get_tag(self, key):
        # type: (_TagNameType) -> Optional[Text]
        """Return the given tag or None if it doesn't exist."""
        return self.meta.get(key, None)

    def set_tags(self, tags):
        # type: (_MetaDictType) -> None
        """Set a dictionary of tags on the given span. Keys and values
        must be strings (or stringable)
        """
        if tags:
            for k, v in iter(tags.items()):
                self.set_tag(k, v)

    def set_meta(self, k, v):
        # type: (_TagNameType, NumericType) -> None
        self.set_tag(k, v)

    def set_metas(self, kvs):
        # type: (_MetaDictType) -> None
        self.set_tags(kvs)

    def set_metric(self, key, value):
        # type: (_TagNameType, NumericType) -> None
        # This method sets a numeric tag value for the given key. It acts
        # like `set_meta()` and it simply add a tag without further processing.

        # Enforce a specific connstant for `_dd.measured`
        if key == SPAN_MEASURED_KEY:
            try:
                value = int(bool(value))
            except (ValueError, TypeError):
                log.warning("failed to convert %r tag to an integer from %r", key, value)
                return

        # FIXME[matt] we could push this check to serialization time as well.
        # only permit types that are commonly serializable (don't use
        # isinstance so that we convert unserializable types like numpy
        # numbers)
        if type(value) not in numeric_types:
            try:
                value = float(value)
            except (ValueError, TypeError):
                log.debug("ignoring not number metric %s:%s", key, value)
                return

        # don't allow nan or inf
        if math.isnan(value) or math.isinf(value):
            log.debug("ignoring not real metric %s:%s", key, value)
            return

        if key in self.meta:
            del self.meta[key]
        self.metrics[key] = value

    def set_metrics(self, metrics):
        # type: (_MetricDictType) -> None
        if metrics:
            for k, v in iteritems(metrics):
                self.set_metric(k, v)

    def get_metric(self, key):
        # type: (_TagNameType) -> Optional[NumericType]
        return self.metrics.get(key)

    def to_dict(self):
        # type: () -> Dict[str, Any]
        d = {
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "span_id": self.span_id,
            "service": self.service,
            "resource": self.resource,
            "name": self.name,
            "error": self.error,
        }

        # a common mistake is to set the error field to a boolean instead of an
        # int. let's special case that here, because it's sure to happen in
        # customer code.
        err = d.get("error")
        if err and type(err) == bool:
            d["error"] = 1

        if self.start_ns:
            d["start"] = self.start_ns

        if self.duration_ns:
            d["duration"] = self.duration_ns

        if self.meta:
            d["meta"] = self.meta

        if self.metrics:
            d["metrics"] = self.metrics

        if self.span_type:
            d["type"] = self.span_type

        return d

    def set_traceback(self, limit=20):
        # type: (int) -> None
        """If the current stack has an exception, tag the span with the
        relevant error info. If not, set the span to the current python stack.
        """
        (exc_type, exc_val, exc_tb) = sys.exc_info()

        if exc_type and exc_val and exc_tb:
            self.set_exc_info(exc_type, exc_val, exc_tb)
        else:
            tb = "".join(traceback.format_stack(limit=limit + 1)[:-1])
            self.meta[ERROR_STACK] = tb

    def set_exc_info(self, exc_type, exc_val, exc_tb):
        # type: (Any, Any, Any) -> None
        """Tag the span with an error tuple as from `sys.exc_info()`."""
        if not (exc_type and exc_val and exc_tb):
            return  # nothing to do

        if self._ignored_exceptions and any([issubclass(exc_type, e) for e in self._ignored_exceptions]):  # type: ignore[arg-type]  # noqa
            return

        self.error = 1

        # get the traceback
        buff = StringIO()
        traceback.print_exception(exc_type, exc_val, exc_tb, file=buff, limit=20)
        tb = buff.getvalue()

        # readable version of type (e.g. exceptions.ZeroDivisionError)
        exc_type_str = "%s.%s" % (exc_type.__module__, exc_type.__name__)

        self.meta[ERROR_MSG] = stringify(exc_val)
        self.meta[ERROR_TYPE] = exc_type_str
        self.meta[ERROR_STACK] = tb

    def _remove_exc_info(self):
        # type: () -> None
        """Remove all exception related information from the span."""
        self.error = 0
        self._remove_tag(ERROR_MSG)
        self._remove_tag(ERROR_TYPE)
        self._remove_tag(ERROR_STACK)

    def pprint(self):
        # type: () -> str
        """Return a human readable version of the span."""
        data = [
            ("name", self.name),
            ("id", self.span_id),
            ("trace_id", self.trace_id),
            ("parent_id", self.parent_id),
            ("service", self.service),
            ("resource", self.resource),
            ("type", self.span_type),
            ("start", self.start),
            ("end", None if not self.duration else self.start + self.duration),
            ("duration", self.duration),
            ("error", self.error),
            ("tags", dict(sorted(self.meta.items()))),
            ("metrics", dict(sorted(self.metrics.items()))),
        ]
        return " ".join(
            # use a large column width to keep pprint output on one line
            "%s=%s" % (k, pprint.pformat(v, width=1024 ** 2).strip())
            for (k, v) in data
        )

    @property
    def context(self):
        # type: () -> Context
        """Return the trace context for this span."""
        if self._context is None:
            self._context = Context(trace_id=self.trace_id, span_id=self.span_id)
        return self._context

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type:
                self.set_exc_info(exc_type, exc_val, exc_tb)
            self.finish()
        except Exception:
            log.exception("error closing trace")

    def __repr__(self):
        return "<Span(id=%s,trace_id=%s,parent_id=%s,name=%s)>" % (
            self.span_id,
            self.trace_id,
            self.parent_id,
            self.name,
        )
