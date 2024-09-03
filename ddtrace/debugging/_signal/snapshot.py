from collections import ChainMap
from dataclasses import dataclass
from dataclasses import field
from itertools import chain
import sys
from types import FrameType
from types import FunctionType
from types import ModuleType
from typing import Any
from typing import Dict
from typing import Optional
from typing import cast

from ddtrace.debugging._expressions import DDExpressionEvaluationError
from ddtrace.debugging._probe.model import DEFAULT_CAPTURE_LIMITS
from ddtrace.debugging._probe.model import CaptureLimits
from ddtrace.debugging._probe.model import FunctionLocationMixin
from ddtrace.debugging._probe.model import LineLocationMixin
from ddtrace.debugging._probe.model import LiteralTemplateSegment
from ddtrace.debugging._probe.model import LogFunctionProbe
from ddtrace.debugging._probe.model import LogLineProbe
from ddtrace.debugging._probe.model import LogProbeMixin
from ddtrace.debugging._probe.model import ProbeEvaluateTimingForMethod
from ddtrace.debugging._probe.model import TemplateSegment
from ddtrace.debugging._redaction import REDACTED_PLACEHOLDER
from ddtrace.debugging._redaction import DDRedactedExpressionError
from ddtrace.debugging._safety import get_args
from ddtrace.debugging._safety import get_globals
from ddtrace.debugging._safety import get_locals
from ddtrace.debugging._signal import utils
from ddtrace.debugging._signal.model import EvaluationError
from ddtrace.debugging._signal.model import LogSignal
from ddtrace.debugging._signal.model import SignalState
from ddtrace.debugging._signal.utils import serialize
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.rate_limiter import RateLimitExceeded
from ddtrace.internal.utils.time import HourGlass


CAPTURE_TIME_BUDGET = 0.2  # seconds


_NOTSET = object()


EXCLUDE_GLOBAL_TYPES = (ModuleType, type, FunctionType)


def _capture_context(
    frame: FrameType,
    throwable: ExcInfoType,
    retval: Any = _NOTSET,
    limits: CaptureLimits = DEFAULT_CAPTURE_LIMITS,
) -> Dict[str, Any]:
    with HourGlass(duration=CAPTURE_TIME_BUDGET) as hg:

        def timeout(_):
            return not hg.trickling()

        arguments = get_args(frame)
        _locals = get_locals(frame)
        _globals = ((n, v) for n, v in get_globals(frame) if not isinstance(v, EXCLUDE_GLOBAL_TYPES))

        _, exc, _ = throwable
        if exc is not None:
            _locals = chain(_locals, [("@exception", exc)])
        elif retval is not _NOTSET:
            _locals = chain(_locals, [("@return", retval)])

        return {
            "arguments": utils.capture_pairs(
                arguments, limits.max_level, limits.max_len, limits.max_size, limits.max_fields, timeout
            )
            if arguments
            else {},
            "locals": utils.capture_pairs(
                _locals, limits.max_level, limits.max_len, limits.max_size, limits.max_fields, timeout
            )
            if _locals
            else {},
            "staticFields": utils.capture_pairs(
                _globals, limits.max_level, limits.max_len, limits.max_size, limits.max_fields, timeout
            )
            if _globals
            else {},
            "throwable": utils.capture_exc_info(throwable),
        }


_EMPTY_CAPTURED_CONTEXT: Dict[str, Any] = {"arguments": {}, "locals": {}, "staticFields": {}, "throwable": None}


@dataclass
class Snapshot(LogSignal):
    """Raw snapshot.

    Used to collect the minimum amount of information from a firing probe.
    """

    entry_capture: Optional[dict] = field(default=None)
    return_capture: Optional[dict] = field(default=None)
    line_capture: Optional[dict] = field(default=None)
    _stack: Optional[list] = field(default=None)
    _message: Optional[str] = field(default=None)
    duration: Optional[int] = field(default=None)  # nanoseconds

    def _eval_segment(self, segment: TemplateSegment, _locals: Dict[str, Any]) -> str:
        probe = cast(LogProbeMixin, self.probe)
        capture = probe.limits
        try:
            if isinstance(segment, LiteralTemplateSegment):
                return segment.eval(_locals)
            return serialize(
                segment.eval(_locals),
                level=capture.max_level,
                maxsize=capture.max_size,
                maxlen=capture.max_len,
                maxfields=capture.max_fields,
            )
        except DDExpressionEvaluationError as e:
            self.errors.append(EvaluationError(expr=e.dsl, message=e.error))
            return REDACTED_PLACEHOLDER if isinstance(e.__cause__, DDRedactedExpressionError) else "ERROR"

    def _eval_message(self, _locals: Dict[str, Any]) -> None:
        probe = cast(LogProbeMixin, self.probe)
        self._message = "".join([self._eval_segment(s, _locals) for s in probe.segments])

    def enter(self):
        if not isinstance(self.probe, LogFunctionProbe):
            return

        probe = self.probe
        frame = self.frame

        if probe.evaluate_at == ProbeEvaluateTimingForMethod.EXIT:
            return

        scope = ChainMap(self.args, frame.f_globals)

        if not self._eval_condition(scope):
            return

        if probe.limiter.limit() is RateLimitExceeded:
            self.state = SignalState.SKIP_RATE
            return

        if probe.take_snapshot:
            self.entry_capture = _capture_context(frame, (None, None, None), limits=probe.limits)

        if probe.evaluate_at == ProbeEvaluateTimingForMethod.ENTER:
            self._eval_message(scope)
            self.state = SignalState.DONE

    def exit(self, retval, exc_info, duration):
        if not isinstance(self.probe, LogFunctionProbe):
            return

        probe = self.probe
        full_scope = self.get_full_scope(retval, exc_info, duration)

        if probe.evaluate_at == ProbeEvaluateTimingForMethod.EXIT:
            if not self._eval_condition(full_scope):
                return
            if probe.limiter.limit() is RateLimitExceeded:
                self.state = SignalState.SKIP_RATE
                return
        elif self.state not in {SignalState.NONE, SignalState.DONE}:
            return

        if probe.take_snapshot:
            self.return_capture = _capture_context(self.frame, exc_info, retval=retval, limits=probe.limits)

        self.duration = duration
        self.state = SignalState.DONE
        if probe.evaluate_at != ProbeEvaluateTimingForMethod.ENTER:
            self._eval_message(full_scope)

        stack = utils.capture_stack(self.frame)

        # Fix the line number of the top frame. This might have been mangled by
        # the instrumented exception handling of function probes.
        tb = exc_info[2]
        while tb is not None:
            frame = tb.tb_frame
            if frame == self.frame:
                stack[0]["lineNumber"] = tb.tb_lineno
                break
            tb = tb.tb_next

        self._stack = stack

    def line(self):
        if not isinstance(self.probe, LogLineProbe):
            return

        frame = self.frame
        probe = self.probe

        if not self._eval_condition(frame.f_locals):
            return

        if probe.take_snapshot:
            if probe.limiter.limit() is RateLimitExceeded:
                self.state = SignalState.SKIP_RATE
                return

            self.line_capture = _capture_context(frame, sys.exc_info(), limits=probe.limits)

        self._eval_message(ChainMap(frame.f_locals, frame.f_globals))

        self._stack = utils.capture_stack(frame)

        self.state = SignalState.DONE

    @property
    def message(self) -> Optional[str]:
        return self._message

    def has_message(self) -> bool:
        return self._message is not None or bool(self.errors)

    @property
    def data(self):
        probe = self.probe

        captures = {}
        if isinstance(probe, LogProbeMixin) and probe.take_snapshot:
            if isinstance(probe, LineLocationMixin):
                captures = {"lines": {str(probe.line): self.line_capture or _EMPTY_CAPTURED_CONTEXT}}
            elif isinstance(probe, FunctionLocationMixin):
                captures = {
                    "entry": self.entry_capture or _EMPTY_CAPTURED_CONTEXT,
                    "return": self.return_capture or _EMPTY_CAPTURED_CONTEXT,
                }

        return {
            "stack": self._stack,
            "captures": captures,
            "duration": self.duration,
        }
