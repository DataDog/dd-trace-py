from typing import TYPE_CHECKING

from ddtrace.constants import USER_KEEP


if TYPE_CHECKING:
    from ddtrace._trace.span import Span


def _aiguard_manual_keep(span: "Span") -> None:
    from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
    from ddtrace.internal.constants import TraceSource
    from ddtrace.internal.sampling import SamplingMechanism
    from ddtrace.internal.sampling import add_trace_source

    span._override_sampling_decision(USER_KEEP)
    # set decision maker to AI_GUARD = -13
    span._set_attribute(SAMPLING_DECISION_TRACE_TAG_KEY, f"-{SamplingMechanism.AI_GUARD}")

    # set trace source propagation tag (_dd.p.ts) with the AI Guard bit
    add_trace_source(span, TraceSource.AI_GUARD)
