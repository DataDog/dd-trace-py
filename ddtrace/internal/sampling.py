import re
from typing import TYPE_CHECKING

from ddtrace.internal.logger import get_logger
from ddtrace.internal.processor import SpanProcessor


log = get_logger(__name__)

if TYPE_CHECKING:
    from typing import Dict
    from typing import Optional
    from typing import Text
    from typing import List

    from ddtrace.context import Context


class SamplingMechanism(object):
    DEFAULT = 0
    AGENT_RATE = 1
    REMOTE_RATE = 2
    TRACE_SAMPLING_RULE = 3
    MANUAL = 4
    APPSEC = 5
    REMOTE_RATE_USER = 6
    REMOTE_RATE_DATADOG = 7


SAMPLING_DECISION_TRACE_TAG_KEY = "_dd.p.dm"

# Use regex to validate trace tag value
TRACE_TAG_RE = re.compile(r"^-([0-9])$")


def _set_trace_tag(
    context,  # type: Context
    sampling_mechanism,  # type: int
):
    # type: (...) -> Optional[Text]

    value = "-%d" % sampling_mechanism

    context._meta[SAMPLING_DECISION_TRACE_TAG_KEY] = value

    return value


def _unset_trace_tag(
    context,  # type: Context
):
    # type: (...) -> Optional[Text]
    if SAMPLING_DECISION_TRACE_TAG_KEY not in context._meta:
        return None

    value = context._meta[SAMPLING_DECISION_TRACE_TAG_KEY]
    del context._meta[SAMPLING_DECISION_TRACE_TAG_KEY]
    return value


def validate_sampling_decision(
    meta,  # type: Dict[str, str]
):
    # type: (...) -> Dict[str, str]
    value = meta.get(SAMPLING_DECISION_TRACE_TAG_KEY)
    if value:
        # Skip propagating invalid sampling mechanism trace tag
        if TRACE_TAG_RE.match(value) is None:
            del meta[SAMPLING_DECISION_TRACE_TAG_KEY]
            meta["_dd.propagation_error"] = "decoding_error"
            log.warning("failed to decode _dd.p.dm: %r", value, exc_info=True)
    return meta


def update_sampling_decision(
    context,  # type: Context
    sampling_mechanism,  # type: int
    sampled,  # type: bool
):
    # type: (...) -> Optional[Text]
    # When sampler keeps trace, we need to set sampling decision trace tag.
    # If sampler rejects trace, we need to remove sampling decision trace tag to avoid unnecessary propagation.
    if sampled:
        return _set_trace_tag(context, sampling_mechanism)
    else:
        return _unset_trace_tag(context)

class SingleSpanSamplingProcessor(SpanProcessor):
    """SpanProcessor for sampling single spans"""
    def __init__(self, rules):
        # type: (List[SpanSamplingRule]) -> None
        self.rules = rules

    def on_span_start(self, span):
        # type: (Span) -> None
        pass

    def on_span_finish(self, span):
        # type: (Span) -> None
        for rule in self.rules:
            rule.sample(span)
    
    def shutdown(self, timeout):
        # type: (Optional[float]) -> None
        # do I need to call periodic() and stop() like how the stats processor does?
        pass
        # self.periodic()
        # self.stop(timeout)