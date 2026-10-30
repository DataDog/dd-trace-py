from ddtrace.constants import USER_KEEP
from ddtrace.internal.native._native import SpanData


def _asm_manual_keep(span: SpanData) -> None:
    from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
    from ddtrace.internal.constants import TraceSource
    from ddtrace.internal.sampling import SamplingMechanism
    from ddtrace.internal.sampling import add_trace_source

    span._override_sampling_decision(USER_KEEP)
    # set decision maker to ASM = -5
    span._set_attribute(SAMPLING_DECISION_TRACE_TAG_KEY, span._set_sampling_decision_maker(SamplingMechanism.APPSEC))

    # set trace source propagation tag (_dd.p.ts) with the ASM bit
    add_trace_source(span, TraceSource.ASM)
