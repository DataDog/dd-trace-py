from ddtrace._trace.sampler import AllSampler  # noqa: F401
from ddtrace._trace.sampler import BasePrioritySampler  # noqa: F401
from ddtrace._trace.sampler import DatadogSampler  # noqa: F401
from ddtrace._trace.sampler import RateByServiceSampler  # noqa: F401
from ddtrace._trace.sampler import SamplingError  # noqa: F401
from ddtrace.internal.sampler import BaseSampler  # noqa: F401
from ddtrace.internal.sampler import RateSampler  # noqa: F401
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    "The sampler module is deprecated and will be moved.",
    message="The sampler interface will be provided by the trace sub-package.",
    category=DDTraceDeprecationWarning,
)
