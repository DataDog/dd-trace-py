from ddtrace.internal.logger import log_filter
from ddtrace.vendor.dogstatsd.base import log


def test_dogstatsd_logger():
    """Ensure dogstatsd logger is initialized as a rate limited logger"""
    assert log_filter in log.filters
