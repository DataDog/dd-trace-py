import abc
from enum import Enum
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

import attr

from ddtrace.debugging._expressions import DDExpression
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import _resolve
from ddtrace.internal.rate_limiter import BudgetRateLimiterWithJitter as RateLimiter
from ddtrace.internal.safety import _isinstance
from ddtrace.internal.utils.cache import cached


log = get_logger(__name__)

DEFAULT_PROBE_RATE = 5000.0
DEFAULT_SNAPSHOT_PROBE_RATE = 1.0
DEFAULT_PROBE_CONDITION_ERROR_RATE = 1.0 / 60 / 5


@cached()
def _resolve_source_file(_path: str) -> Optional[Path]:
    """Resolve the source path for the given path.

    This recursively strips parent directories until it finds a file that
    exists according to sys.path.
    """
    path = Path(_path)
    if path.is_file():
        return path.resolve()

    for relpath in (path.relative_to(_) for _ in path.parents):
        resolved_path = _resolve(relpath)
        if resolved_path is not None:
            return resolved_path

    return None


MAXLEVEL = 2
MAXSIZE = 100
MAXLEN = 255
MAXFIELDS = 20


@attr.s
class CaptureLimits(object):
    max_level = attr.ib(type=int, default=MAXLEVEL)
    max_size = attr.ib(type=int, default=MAXSIZE)
    max_len = attr.ib(type=int, default=MAXLEN)
    max_fields = attr.ib(type=int, default=MAXFIELDS)


DEFAULT_CAPTURE_LIMITS = CaptureLimits()


@attr.s
class Probe(abc.ABC):
    __context_creator__ = False

    probe_id = attr.ib(type=str)
    version = attr.ib(type=int)
    tags = attr.ib(type=dict, eq=False)

    def update(self, other: "Probe") -> None:
        """Update the mutable fields from another probe."""
        if self.probe_id != other.probe_id:
            log.error("Probe ID mismatch when updating mutable fields")
            return

        if self.version == other.version:
            return

        for attrib in (_.name for _ in self.__attrs_attrs__ if _.eq):
            setattr(self, attrib, getattr(other, attrib))

    def __hash__(self):
        return hash(self.probe_id)


@attr.s
class RateLimitMixin(abc.ABC):
    rate = attr.ib(type=float, eq=False)
    limiter = attr.ib(type=RateLimiter, init=False, repr=False, eq=False)

    @limiter.default
    def _(self):
        return RateLimiter(
            limit_rate=self.rate,
            tau=1.0 / self.rate if self.rate else 1.0,
            on_exceed=lambda: log.warning("Rate limit exceeeded for %r", self),
            call_once=True,
            raise_on_exceed=False,
        )


@attr.s
class ProbeConditionMixin(object):
    """Conditional probe.

    If the condition is ``None``, then this is equivalent to a non-conditional
    probe.
    """

    condition = attr.ib(type=Optional[DDExpression])
    condition_error_rate = attr.ib(type=float, eq=False)
    condition_error_limiter = attr.ib(type=RateLimiter, init=False, repr=False, eq=False)

    @condition_error_limiter.default
    def _(self):
        return RateLimiter(
            limit_rate=self.condition_error_rate,
            tau=1.0 / self.condition_error_rate if self.condition_error_rate else 1.0,
            on_exceed=lambda: log.debug("Condition error rate limit exceeeded for %r", self),
            call_once=True,
            raise_on_exceed=False,
        )


@attr.s
class ProbeLocationMixin(object):
    def location(self) -> Tuple[Optional[str], Optional[Union[str, int]]]:
        """return a tuple of (location,sublocation) for the probe.
        For example, line probe returns the (file,line) and method probe return (module,method)
        """
        return (None, None)


@attr.s
class LineLocationMixin(ProbeLocationMixin):
    source_file = attr.ib(type=Path, converter=_resolve_source_file, eq=False)  # type: ignore[misc]
    line = attr.ib(type=int, eq=False)

    def location(self):
        return (str(self.source_file) if self.source_file is not None else None, self.line)


class ProbeEvaluateTimingForMethod(str, Enum):
    DEFAULT = "DEFAULT"
    ENTER = "ENTER"
    EXIT = "EXIT"


@attr.s
class FunctionLocationMixin(ProbeLocationMixin):
    module = attr.ib(type=str, eq=False)
    func_qname = attr.ib(type=str, eq=False)
    evaluate_at = attr.ib(type=ProbeEvaluateTimingForMethod)

    def location(self):
        return (self.module, self.func_qname)


class MetricProbeKind(str, Enum):
    COUNTER = "COUNT"
    GAUGE = "GAUGE"
    HISTOGRAM = "HISTOGRAM"
    DISTRIBUTION = "DISTRIBUTION"


@attr.s
class MetricProbeMixin(object):
    kind = attr.ib(type=str)
    name = attr.ib(type=str)
    value = attr.ib(type=Optional[DDExpression])


@attr.s
class MetricLineProbe(Probe, LineLocationMixin, MetricProbeMixin, ProbeConditionMixin):
    pass


@attr.s
class MetricFunctionProbe(Probe, FunctionLocationMixin, MetricProbeMixin, ProbeConditionMixin):
    pass


@attr.s
class TemplateSegment(abc.ABC):
    @abc.abstractmethod
    def eval(self, _locals: Dict[str, Any]) -> str:
        pass


@attr.s
class LiteralTemplateSegment(TemplateSegment):
    str_value = attr.ib(type=str, default=None)

    def eval(self, _locals: Dict[str, Any]) -> Any:
        return self.str_value


@attr.s
class ExpressionTemplateSegment(TemplateSegment):
    expr = attr.ib(type=DDExpression, default=None)

    def eval(self, _locals: Dict[str, Any]) -> Any:
        return self.expr.eval(_locals)


@attr.s
class StringTemplate(object):
    template = attr.ib(type=str)
    segments = attr.ib(type=List[TemplateSegment])

    def render(self, _locals: Dict[str, Any], serializer: Callable[[Any], str]) -> str:
        def _to_str(value):
            return value if _isinstance(value, str) else serializer(value)

        return "".join([_to_str(s.eval(_locals)) for s in self.segments])


@attr.s
class LogProbeMixin(object):
    template = attr.ib(type=str)
    segments = attr.ib(type=List[TemplateSegment])
    take_snapshot = attr.ib(type=bool)
    limits = attr.ib(type=CaptureLimits, eq=False)


@attr.s
class LogLineProbe(Probe, LineLocationMixin, LogProbeMixin, ProbeConditionMixin, RateLimitMixin):
    pass


@attr.s
class LogFunctionProbe(Probe, FunctionLocationMixin, LogProbeMixin, ProbeConditionMixin, RateLimitMixin):
    pass


@attr.s
class SpanProbeMixin(object):
    pass


@attr.s
class SpanFunctionProbe(Probe, FunctionLocationMixin, SpanProbeMixin, ProbeConditionMixin):
    __context_creator__ = True


class SpanDecorationTargetSpan(object):
    ROOT = "ROOT"
    ACTIVE = "ACTIVE"


@attr.s
class SpanDecorationTag(object):
    name = attr.ib(type=str)
    value = attr.ib(type=StringTemplate)


@attr.s
class SpanDecoration(object):
    when = attr.ib(type=Optional[DDExpression])
    tags = attr.ib(type=List[SpanDecorationTag])


@attr.s
class SpanDecorationMixin(object):
    target_span = attr.ib(type=SpanDecorationTargetSpan)
    decorations = attr.ib(type=List[SpanDecoration])


@attr.s
class SpanDecorationLineProbe(Probe, LineLocationMixin, SpanDecorationMixin):
    pass


@attr.s
class SpanDecorationFunctionProbe(Probe, FunctionLocationMixin, SpanDecorationMixin):
    pass


LineProbe = Union[LogLineProbe, MetricLineProbe, SpanDecorationLineProbe]
FunctionProbe = Union[LogFunctionProbe, MetricFunctionProbe, SpanFunctionProbe, SpanDecorationFunctionProbe]


class ProbeType(object):
    LOG_PROBE = "LOG_PROBE"
    METRIC_PROBE = "METRIC_PROBE"
    SPAN_PROBE = "SPAN_PROBE"
    SPAN_DECORATION_PROBE = "SPAN_DECORATION_PROBE"
