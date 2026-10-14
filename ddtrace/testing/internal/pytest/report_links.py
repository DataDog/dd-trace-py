"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.pytest.report_links`` moved to:

``ddtrace_testing.internal.pytest.report_links``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.pytest.report_links import DEFAULT_DATADOG_SITE as DEFAULT_DATADOG_SITE  # noqa: F401
from ddtrace_testing.internal.pytest.report_links import (
    DEFAULT_DATADOG_SUBDOMAIN as DEFAULT_DATADOG_SUBDOMAIN,  # noqa: F401
)
from ddtrace_testing.internal.pytest.report_links import SAFE_FOR_QUERY as SAFE_FOR_QUERY  # noqa: F401
from ddtrace_testing.internal.pytest.report_links import (
    print_test_report_links as print_test_report_links,  # noqa: F401
)
