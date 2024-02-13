import abc
from enum import Enum
from threading import Thread
import time
from types import FrameType
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union
from typing import cast
from uuid import uuid4

import attr

from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.debugging import _safety
from ddtrace.debugging._expressions import DDExpressionEvaluationError
from ddtrace.debugging._probe.model import FunctionLocationMixin
from ddtrace.debugging._probe.model import LineLocationMixin
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._probe.model import ProbeConditionMixin
from ddtrace.internal.rate_limiter import RateLimitExceeded


@attr.s
class EvaluationError(object):
    expr = attr.ib(type=str)
    message = attr.ib(type=str)


class SignalState(str, Enum):
    NONE = "NONE"
    SKIP_COND = "SKIP_COND"
    SKIP_COND_ERROR = "SKIP_COND_ERROR"
    SKIP_RATE = "SKIP_RATE"
    COND_ERROR = "COND_ERROR"
    DONE = "DONE"


@attr.s
class Signal(abc.ABC):
    """Debugger signal base class.

    Used to model the data carried by the signal emitted by a probe when it is
    triggered.
    """

    probe = attr.ib(type=Probe)
    frame = attr.ib(type=FrameType)
    thread = attr.ib(type=Thread)

    trace_context = attr.ib(type=Optional[Union[Span, Context]], default=None)
    args = attr.ib(type=Optional[List[Tuple[str, Any]]], default=None)
    state = attr.ib(type=str, default=SignalState.NONE)
    errors = attr.ib(type=List[EvaluationError], factory=lambda: list())
    timestamp = attr.ib(type=float, factory=time.time)
    uuid = attr.ib(type=str, init=False, factory=lambda: str(uuid4()))

    def _eval_condition(self, _locals: Optional[Dict[str, Any]] = None) -> bool:
        """Evaluate the probe condition against the collected frame."""
        probe = cast(ProbeConditionMixin, self.probe)
        condition = probe.condition
        if condition is None:
            return True

        try:
            if bool(condition.eval(_locals or self.frame.f_locals)):
                return True
        except DDExpressionEvaluationError as e:
            self.errors.append(EvaluationError(expr=e.dsl, message=e.error))
            self.state = (
                SignalState.SKIP_COND_ERROR
                if probe.condition_error_limiter.limit() is RateLimitExceeded
                else SignalState.COND_ERROR
            )
        else:
            self.state = SignalState.SKIP_COND

        return False

    def _enrich_args(self, retval, exc_info, duration):
        _locals = list(self.args or _safety.get_args(self.frame))
        _locals.append(("@duration", duration / 1e6))  # milliseconds

        exc = exc_info[1]
        _locals.append(("@return", retval) if exc is None else ("@exception", exc))

        return dict(_locals)

    @abc.abstractmethod
    def enter(self):
        pass

    @abc.abstractmethod
    def exit(self, retval, exc_info, duration):
        pass

    @abc.abstractmethod
    def line(self):
        pass


@attr.s
class LogSignal(Signal):
    """A signal that also emits a log message.

    Some signals might require sending a log message along with the base signal
    data. For example, all the collected errors from expression evaluations
    (e.g. conditions) might need to be reported.
    """

    @property
    @abc.abstractmethod
    def message(self):
        # type () -> Optional[str]
        """The log message to emit."""
        pass

    @abc.abstractmethod
    def has_message(self):
        # type () -> bool
        """Whether the signal has a log message to emit."""
        pass

    @property
    def data(self):
        # type () -> Dict[str, Any]
        """Extra data to include in the snapshot portion of the log message."""
        return {}

    def _probe_details(self):
        # type () -> Dict[str, Any]
        probe = self.probe
        if isinstance(probe, LineLocationMixin):
            location = {
                "file": str(probe.source_file),
                "lines": [probe.line],
            }
        elif isinstance(probe, FunctionLocationMixin):
            location = {
                "type": probe.module,
                "method": probe.func_qname,
            }
        else:
            return {}

        return {
            "id": probe.probe_id,
            "version": probe.version,
            "location": location,
        }

    @property
    def snapshot(self):
        # type () -> Dict[str, Any]
        full_data = {
            "id": self.uuid,
            "timestamp": int(self.timestamp * 1e3),  # milliseconds
            "evaluationErrors": [{"expr": e.expr, "message": e.message} for e in self.errors],
            "probe": self._probe_details(),
            "language": "python",
        }
        full_data.update(self.data)

        return full_data
