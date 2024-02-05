import abc
from typing import TYPE_CHECKING  # noqa:F401

from ddtrace.constants import SAMPLE_RATE_METRIC_KEY
from ddtrace.internal.constants import MAX_UINT_64BITS as _MAX_UINT_64BITS
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:  # pragma: no cover
    from ddtrace._trace.span import Span  # noqa:F401


log = get_logger(__name__)


# All references to MAX_TRACE_ID were replaced with _MAX_UINT_64BITS.
# Now that ddtrace supports generating 128bit trace_ids,
# the max trace id should be 2**128 - 1 (not 2**64 -1)
# MAX_TRACE_ID is no longer used and should be removed.
MAX_TRACE_ID = _MAX_UINT_64BITS
# Has to be the same factor and key as the Agent to allow chained sampling
KNUTH_FACTOR = 1111111111111111111


class BaseSampler(metaclass=abc.ABCMeta):
    __slots__ = ()

    @abc.abstractmethod
    def sample(self, span, allow_false=True):
        pass


class RateSampler(BaseSampler):
    """Sampler based on a rate

    Keep (100 * `sample_rate`)% of the traces.
    It samples randomly, its main purpose is to reduce the instrumentation footprint.
    """

    def __init__(self, sample_rate=1.0):
        # type: (float) -> None
        if sample_rate < 0.0:
            raise ValueError("sample_rate of {} is negative".format(sample_rate))
        elif sample_rate > 1.0:
            sample_rate = 1.0

        self.set_sample_rate(sample_rate)

        log.debug("initialized RateSampler, sample %s%% of traces", 100 * sample_rate)

    def set_sample_rate(self, sample_rate):
        # type: (float) -> None
        self.sample_rate = float(sample_rate)
        self.sampling_id_threshold = self.sample_rate * _MAX_UINT_64BITS

    def sample(self, span, allow_false=True):
        # type: (Span, bool) -> bool
        sampled = ((span._trace_id_64bits * KNUTH_FACTOR) % _MAX_UINT_64BITS) <= self.sampling_id_threshold
        # NB allow_false has weird functionality here, doing something other than "allowing false" to be returned
        # this is an artifact of this library's sampler abstractions having fallen out of alignment
        # with the functional specification over time.
        if sampled and allow_false:
            span.set_metric(SAMPLE_RATE_METRIC_KEY, self.sample_rate)
        return sampled
