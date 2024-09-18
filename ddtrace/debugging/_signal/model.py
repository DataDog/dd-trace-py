import abc
from collections import ChainMap
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from threading import Thread
import time
from types import FrameType
from typing import Any
from typing import Dict
from typing import List
from typing import Mapping
from typing import Optional
from typing import Union
from typing import cast
from uuid import uuid4

from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.debugging._expressions import DDExpressionEvaluationError
from ddtrace.debugging._probe.model import FunctionLocationMixin
from ddtrace.debugging._probe.model import LineLocationMixin
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._probe.model import ProbeConditionMixin
from ddtrace.debugging._safety import get_args
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.rate_limiter import RateLimitExceeded


@dataclass
class EvaluationError:
    expr: str
    message: str


class SignalState(str, Enum):
    NONE = "NONE"
    SKIP_COND = "SKIP_COND"
    SKIP_COND_ERROR = "SKIP_COND_ERROR"
    SKIP_RATE = "SKIP_RATE"
    COND_ERROR = "COND_ERROR"
    DONE = "DONE"


@dataclass
class Signal(abc.ABC):
    """Debugger signal base class.

    Used to model the data carried by the signal emitted by a probe when it is
    triggered.
    """

    probe: Probe
    frame: FrameType
    thread: Thread
    trace_context: Optional[Union[Span, Context]] = None
    state: str = SignalState.NONE
    errors: List[EvaluationError] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    uuid: str = field(default_factory=lambda: str(uuid4()), init=False)

    def _eval_condition(self, scope: Optional[Mapping[str, Any]] = None) -> bool:
        """Evaluate the probe condition against the collected frame."""
        probe = cast(ProbeConditionMixin, self.probe)
        condition = probe.condition
        if condition is None:
            return True

        try:
            if bool(condition.eval(scope)):
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

    def get_full_scope(self, retval: Any, exc_info: ExcInfoType, duration: float) -> Mapping[str, Any]:
        frame = self.frame
        extra: Dict[str, Any] = {"@duration": duration / 1e6}  # milliseconds

        exc = exc_info[1]
        if exc is not None:
            extra["@exception"] = exc
        else:
            extra["@return"] = retval

        # Include the frame locals and globals.
        return ChainMap(extra, frame.f_locals, frame.f_globals)

    @property
    def args(self):
        return dict(get_args(self.frame))

    @abc.abstractmethod
    def enter(self):
        pass

    @abc.abstractmethod
    def exit(self, retval, exc_info, duration):
        pass

    @abc.abstractmethod
    def line(self):
        pass


@dataclass
class LogSignal(Signal):
    """A signal that also emits a log message.

    Some signals might require sending a log message along with the base signal
    data. For example, all the collected errors from expression evaluations
    (e.g. conditions) might need to be reported.
    """

    @property
    @abc.abstractmethod
    def message(self) -> Optional[str]:
        """The log message to emit."""
        pass

    @abc.abstractmethod
    def has_message(self) -> bool:
        """Whether the signal has a log message to emit."""
        pass

    @property
    def data(self) -> Dict[str, Any]:
        """Extra data to include in the snapshot portion of the log message."""
        return {}

    def _probe_details(self) -> Dict[str, Any]:
        probe = self.probe
        if isinstance(probe, LineLocationMixin):
            location = {
                "file": str(probe.resolved_source_file),
                "lines": [str(probe.line)],
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
    def snapshot(self) -> Dict[str, Any]:
        full_data = {
            "id": self.uuid,
            "timestamp": int(self.timestamp * 1e3),  # milliseconds
            "evaluationErrors": [{"expr": e.expr, "message": e.message} for e in self.errors],
            "probe": self._probe_details(),
            "language": "python",
        }
        full_data.update(self.data)

        return full_data
