from this import s
from typing import TYPE_CHECKING
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MECHANISM, _SINGLE_SPAN_SAMPLING_MAX_PER_SEC, _SINGLE_SPAN_SAMPLING_RATE

if TYPE_CHECKING:
    from .span import Span

SAMPLING_MECHANISM = 8

KNUTH_FACTOR = 1111111111111111111
MAX_SPAN_ID = 2 ** 64


class SpanSamplingRule():
    """A span sampling rule to evaluate and potentially tag each span upon finish."""

    __slots__ = ("service", "name", "sample_rate", "max_per_second", "sampling_id_threshold")

    def __init__(
        self, 
        service=None, # type: Optional[str]
        name=None, # type: Optional[str]
        sample_rate=1.0, # type: Optional[float] 
        max_per_second=None # type: Optional[int]
    ):
        self.service = service
        self.name = name
        self.set_sample_rate(sample_rate)
        self.max_per_second = max_per_second

    def sample(self, span):
        # type: (Span) -> bool
        if self.match(span) and self._sample(span):
            #DEV check rate limiter, if all good, add tags
            self.apply_span_sampling_tags(span)
    
    def _sample(self, span):
        if self.sample_rate == 1:
            return True
        elif self.sample_rate == 0:
            return False
            
        return((span.span_id * KNUTH_FACTOR) % MAX_SPAN_ID) <= self.sampling_id_threshold



    def match(self, span):
        """Determines if the span's service and name match the configured patterns"""
        if span.service == self.service and span.name == self.name:
            #Dev add rules to match glob rather than check direct
            return True

    def set_sample_rate(self, sample_rate):
        # type: (float) -> None
        self.sample_rate = float(sample_rate)
        self.sampling_id_threshold = self.sample_rate * MAX_SPAN_ID
    
    def apply_span_sampling_tags(self, span):
        span.set_metric(_SINGLE_SPAN_SAMPLING_MECHANISM, SAMPLING_MECHANISM)
        span.set_metric(_SINGLE_SPAN_SAMPLING_RATE, self.sample_rate)
        if self.max_per_second:
            span.set_metric(_SINGLE_SPAN_SAMPLING_MAX_PER_SEC, self.max_per_second)
