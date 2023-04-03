import typing as t

import attr

import ddtrace
from ddtrace import Span
from ddtrace.debugging._capture.model import CaptureState
from ddtrace.debugging._capture.model import CapturedEvent
from ddtrace.debugging._probe.model import SpanFunctionProbe
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

SPAN_NAME = "dd.dynamic.span"
PROBE_ID_TAG_NAME = "debugger.probeid"


@attr.s
class DynamicSpan(CapturedEvent):
    """Dynamically created span"""

    _span_cm = attr.ib(type=t.Optional[t.ContextManager[Span]], init=False)

    def __attrs_post_init__(self):
        # type: () -> None
        self._span_cm = None

    def enter(self):
        # type: () -> None
        probe = self.probe
        if not isinstance(probe, SpanFunctionProbe):
            log.debug("Dynamic span entered with non-span probe: %s", self.probe)
            return

        if not self._eval_condition(dict(self.args) if self.args else {}):
            return

        self._span_cm = ddtrace.tracer.trace(
            SPAN_NAME,
            service=None,  # Currently unused
            resource=probe.func_qname,
            span_type=None,  # Currently unused
        )
        span = self._span_cm.__enter__()

        span.set_tags(probe.tags)
        span.set_tag(PROBE_ID_TAG_NAME, probe.probe_id)

        self.state = CaptureState.DONE

    def exit(self, retval, exc_info, duration):
        # type: (t.Any, ExcInfoType, float) -> None
        if not isinstance(self.probe, SpanFunctionProbe):
            log.debug("Dynamic span exited with non-span probe: %s", self.probe)
            return

        if self._span_cm is not None:
            # Condition evaluated to true so we created a span. Finish it.
            self._span_cm.__exit__(*exc_info)

    def line(self):
        raise NotImplementedError("Dynamic line spans are not supported in Python")
