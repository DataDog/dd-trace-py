from ddtrace.vendor.dogstatsd.base import log
from ddtrace_base.logger import log_filter


def test_dogstatsd_logger():
    """Ensure dogstatsd logger is initialized as a rate limited logger"""
    assert log_filter in log.filters
