"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.utils`` moved to:

``ddtrace_testing.internal.utils``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.utils import DDTESTOPT_ROOT_SPAN_RESOURCE as DDTESTOPT_ROOT_SPAN_RESOURCE  # noqa: F401
from ddtrace_testing.internal.utils import DDTraceTestContext as DDTraceTestContext  # noqa: F401
from ddtrace_testing.internal.utils import PlainTestContext as PlainTestContext  # noqa: F401
from ddtrace_testing.internal.utils import TestContext as TestContext  # noqa: F401
from ddtrace_testing.internal.utils import _filter_sensitive_info as _filter_sensitive_info  # noqa: F401
from ddtrace_testing.internal.utils import _gen_item_id as _gen_item_id  # noqa: F401
from ddtrace_testing.internal.utils import asbool as asbool  # noqa: F401
from ddtrace_testing.internal.utils import ensure_text as ensure_text  # noqa: F401
