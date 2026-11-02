from ddtrace.constants import USER_KEEP
from ddtrace.internal.native._native import SpanData


def _asm_manual_keep(span: SpanData) -> None:
    from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
    from ddtrace.internal.constants import TRACE_SOURCE_PROPAGATION_KEY
    from ddtrace.internal.constants import TraceSource
    from ddtrace.internal.sampling import SamplingMechanism
    from ddtrace.internal.sampling import add_trace_source

    span._override_sampling_decision(USER_KEEP, sampling_mechanism=SamplingMechanism.APPSEC)
    # set decision maker to ASM = -5
    span._set_attribute(SAMPLING_DECISION_TRACE_TAG_KEY, f"-{SamplingMechanism.APPSEC}")

    # set trace source propagation tag (_dd.p.ts) with the ASM bit
    add_trace_source(span, TraceSource.ASM)

    root = span._local_root
    if root is not span:
        # Export only fills missing context tags, so replace any earlier retention tags on the root.
        meta = span.context._meta
        for key in (SAMPLING_DECISION_TRACE_TAG_KEY, TRACE_SOURCE_PROPAGATION_KEY):
            root._set_attribute(key, meta[key])
