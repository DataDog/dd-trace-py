import sys
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import cast

import attr

from ddtrace.debugging import safety
from ddtrace.debugging._capture import utils
from ddtrace.debugging._capture.model import CaptureState
from ddtrace.debugging._capture.model import CapturedEvent
from ddtrace.debugging._capture.model import EvaluationError
from ddtrace.debugging._capture.utils import serialize
from ddtrace.debugging._expressions import DDExpressionEvaluationError
from ddtrace.debugging._probe.model import CaptureLimits
from ddtrace.debugging._probe.model import LiteralTemplateSegment
from ddtrace.debugging._probe.model import LogFunctionProbe
from ddtrace.debugging._probe.model import LogLineProbe
from ddtrace.debugging._probe.model import LogProbeMixin
from ddtrace.debugging._probe.model import ProbeEvaluateTimingForMethod
from ddtrace.debugging._probe.model import TemplateSegment
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.rate_limiter import RateLimitExceeded
from ddtrace.internal.utils.time import HourGlass


CAPTURE_TIME_BUDGET = 0.2  # seconds


def _capture_context(
    arguments,  # type: List[Tuple[str, Any]]
    _locals,  # type: List[Tuple[str, Any]]
    throwable,  # type: ExcInfoType
    limits=CaptureLimits(),  # type: CaptureLimits
):
    # type: (...) -> Dict[str, Any]
    with HourGlass(duration=CAPTURE_TIME_BUDGET) as hg:

        def timeout(_):
            return not hg.trickling()

        return {
            "arguments": {
                n: utils.capture_value(v, limits.max_level, limits.max_len, limits.max_size, limits.max_fields, timeout)
                for n, v in arguments
            }
            if arguments is not None
            else {},
            "locals": {
                n: utils.capture_value(v, limits.max_level, limits.max_len, limits.max_size, limits.max_fields, timeout)
                for n, v in _locals
            }
            if _locals is not None
            else {},
            "throwable": utils.capture_exc_info(throwable),
        }


@attr.s
class Snapshot(CapturedEvent):
    """Raw snapshot.

    Used to collect the minimum amount of information from a firing probe.
    """

    entry_capture = attr.ib(type=Optional[dict], default=None)
    return_capture = attr.ib(type=Optional[dict], default=None)
    line_capture = attr.ib(type=Optional[dict], default=None)

    message = attr.ib(type=Optional[str], default=None)
    duration = attr.ib(type=Optional[int], default=None)  # nanoseconds

    def _eval_segment(self, segment, _locals):
        # type: (TemplateSegment, Dict[str, Any]) -> str
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
            return "ERROR"

    def _eval_message(self, _locals):
        # type: (Dict[str, Any]) -> None
        probe = cast(LogProbeMixin, self.probe)
        self.message = "".join([self._eval_segment(s, _locals) for s in probe.segments])

    def enter(self):
        if not isinstance(self.probe, LogFunctionProbe):
            return

        probe = self.probe
        frame = self.frame
        _args = list(self.args or safety.get_args(frame))

        if probe.evaluate_at == ProbeEvaluateTimingForMethod.EXIT:
            return

        if not self._eval_condition(dict(_args)):
            return

        if probe.limiter.limit() is RateLimitExceeded:
            self.state = CaptureState.SKIP_RATE
            return

        if probe.take_snapshot:
            self.entry_capture = _capture_context(
                _args,
                [],
                (None, None, None),
                limits=probe.limits,
            )

        if probe.evaluate_at == ProbeEvaluateTimingForMethod.ENTER:
            self._eval_message(dict(_args))
            self.state = CaptureState.DONE_AND_COMMIT

    def exit(self, retval, exc_info, duration):
        if not isinstance(self.probe, LogFunctionProbe):
            return

        probe = self.probe
        _args = self._enrich_args(retval, exc_info, duration)

        if probe.evaluate_at == ProbeEvaluateTimingForMethod.EXIT:
            if not self._eval_condition(_args):
                return
            if probe.limiter.limit() is RateLimitExceeded:
                self.state = CaptureState.SKIP_RATE
                return
        elif self.state != CaptureState.NONE and self.state != CaptureState.DONE_AND_COMMIT:
            return

        _locals = []
        if exc_info[1] is None:
            _locals.append(("@return", retval))

        if probe.take_snapshot:
            self.return_capture = _capture_context(
                self.args or safety.get_args(self.frame), _locals, exc_info, limits=probe.limits
            )
        self.duration = duration
        self.state = CaptureState.DONE_AND_COMMIT
        if probe.evaluate_at != ProbeEvaluateTimingForMethod.ENTER:
            self._eval_message(dict(_args))

    def line(self):
        if not isinstance(self.probe, LogLineProbe):
            return

        frame = self.frame
        probe = self.probe

        if not self._eval_condition(frame.f_locals):
            return

        if probe.take_snapshot:
            if probe.limiter.limit() is RateLimitExceeded:
                self.state = CaptureState.SKIP_RATE
                return

            self.line_capture = _capture_context(
                self.args or safety.get_args(frame),
                safety.get_locals(frame),
                sys.exc_info(),
                limits=probe.limits,
            )

        self._eval_message(frame.f_locals)
        self.state = CaptureState.DONE_AND_COMMIT
