from dataclasses import dataclass


@dataclass
class DeferredSpan:
    """
    A class to hold a span that is deferred for later processing.
    This is used to store spans that are created before the AppSec processor is initialized.
    """

    name: str
    start_time_ns: int
    end_time_ns: int


@dataclass
class MetricsDict:
    json_deser: DeferredSpan | None = None
    waf_init: DeferredSpan | None = None
    object_creation: DeferredSpan | None = None
    appsec_init: DeferredSpan | None = None


APPSEC_TEMP_METRICS: MetricsDict = MetricsDict()
