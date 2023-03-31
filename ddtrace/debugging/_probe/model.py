import abc
from os.path import abspath
from os.path import isfile
from os.path import normcase
from os.path import normpath
from os.path import sep
from os.path import splitdrive
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

import attr
import six

from ddtrace.debugging._expressions import DDExpression
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import _resolve
from ddtrace.internal.rate_limiter import BudgetRateLimiterWithJitter as RateLimiter
from ddtrace.internal.utils.cache import cached


log = get_logger(__name__)

DEFAULT_PROBE_RATE = 5000.0
DEFAULT_SNAPSHOT_PROBE_RATE = 1.0
DEFAULT_PROBE_CONDITION_ERROR_RATE = 1.0 / 60 / 5


@cached()
def _resolve_source_file(path):
    # type: (str) -> Optional[str]
    """Resolve the source path for the given path.

    This recursively strips parent directories until it finds a file that
    exists according to sys.path.
    """
    npath = abspath(normpath(normcase(path)))
    if isfile(npath):
        return npath

    _, relpath = splitdrive(npath)
    while relpath:
        resolved_path = _resolve(relpath)
        if resolved_path is not None:
            return abspath(resolved_path)
        _, _, relpath = relpath.partition(sep)

    return None


MAXLEVEL = 2
MAXSIZE = 100
MAXLEN = 255
MAXFIELDS = 20


@attr.s
class CaptureLimits(object):
    max_level = attr.ib(type=int, default=MAXLEVEL)  # type: int
    max_size = attr.ib(type=int, default=MAXSIZE)  # type: int
    max_len = attr.ib(type=int, default=MAXLEN)  # type: int
    max_fields = attr.ib(type=int, default=MAXFIELDS)  # type: int


@attr.s
class Probe(six.with_metaclass(abc.ABCMeta)):
    probe_id = attr.ib(type=str)
    version = attr.ib(type=int)
    tags = attr.ib(type=dict, eq=False)
    rate = attr.ib(type=float, eq=False)
    limiter = attr.ib(type=RateLimiter, init=False, repr=False, eq=False)  # type: RateLimiter

    @limiter.default
    def _(self):
        return RateLimiter(
            limit_rate=self.rate,
            tau=1.0 / self.rate if self.rate else 1.0,
            on_exceed=lambda: log.warning("Rate limit exceeeded for %r", self),
            call_once=True,
            raise_on_exceed=False,
        )

    def update(self, other):
        # type: (Probe) -> None
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
class ProbeConditionMixin(object):
    """Conditional probe.

    If the condition is ``None``, then this is equivalent to a non-conditional
    probe.
    """

    condition = attr.ib(type=Optional[DDExpression])  # type: Optional[DDExpression]
    condition_error_rate = attr.ib(type=float, eq=False)
    condition_error_limiter = attr.ib(type=RateLimiter, init=False, repr=False, eq=False)  # type: RateLimiter

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
    def location(self):
        # type: () -> Tuple[str,str]
        """return a turple of (location,sublocation) for the probe.
        For example, line probe returns the (file,line) and method probe return (module,method)
        """
        return ("", "")


@attr.s
class LineLocationMixin(ProbeLocationMixin):
    source_file = attr.ib(type=str, converter=_resolve_source_file, eq=False)  # type: ignore[misc]
    line = attr.ib(type=int, eq=False)

    def location(self):
        return (self.source_file, self.line)


# TODO: make this an Enum once Python 2 support is dropped.
class ProbeEvaluateTimingForMethod(object):
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


# TODO: make this an Enum once Python 2 support is dropped.
class MetricProbeKind(object):
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
class TemplateSegment(six.with_metaclass(abc.ABCMeta)):
    @abc.abstractmethod
    def eval(self, _locals):
        # type: (Dict[str,Any]) -> str
        pass


@attr.s
class LiteralTemplateSegment(TemplateSegment):
    str_value = attr.ib(type=str, default=None)

    def eval(self, _locals):
        # type: (Dict[str,Any]) -> Any
        return self.str_value


@attr.s
class ExpressionTemplateSegment(TemplateSegment):
    expr = attr.ib(type=DDExpression, default=None)  # type: DDExpression

    def eval(self, _locals):
        # type: (Dict[str,Any]) -> Any
        return self.expr.eval(_locals)


@attr.s
class LogProbeMixin(object):
    template = attr.ib(type=str)
    segments = attr.ib(type=List[TemplateSegment])
    take_snapshot = attr.ib(type=bool)
    limits = attr.ib(type=CaptureLimits, eq=False)  # type: CaptureLimits


@attr.s
class LogLineProbe(Probe, LineLocationMixin, LogProbeMixin, ProbeConditionMixin):
    pass


@attr.s
class LogFunctionProbe(Probe, FunctionLocationMixin, LogProbeMixin, ProbeConditionMixin):
    pass


@attr.s
class SpanProbeMixin(object):
    pass


@attr.s
class SpanFunctionProbe(Probe, FunctionLocationMixin, SpanProbeMixin, ProbeConditionMixin):
    pass


LineProbe = Union[LogLineProbe, MetricLineProbe]
FunctionProbe = Union[LogFunctionProbe, MetricFunctionProbe, SpanFunctionProbe]


class ProbeType(object):
    LOG_PROBE = "LOG_PROBE"
    METRIC_PROBE = "METRIC_PROBE"
    SPAN_PROBE = "SPAN_PROBE"
