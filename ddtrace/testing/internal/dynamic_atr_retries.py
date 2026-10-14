"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.dynamic_atr_retries`` moved to:

``ddtrace_testing.internal.dynamic_atr_retries``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.dynamic_atr_retries import (
    DYNAMIC_ATR_BUCKETS_ENV as DYNAMIC_ATR_BUCKETS_ENV,  # noqa: F401
)
from ddtrace_testing.internal.dynamic_atr_retries import (
    DYNAMIC_ATR_ENABLED_ENV as DYNAMIC_ATR_ENABLED_ENV,  # noqa: F401
)
from ddtrace_testing.internal.dynamic_atr_retries import (
    DynamicATRRetriesHandler as DynamicATRRetriesHandler,  # noqa: F401
)
from ddtrace_testing.internal.dynamic_atr_retries import (
    dynamic_retries_for_duration as dynamic_retries_for_duration,  # noqa: F401
)
from ddtrace_testing.internal.dynamic_atr_retries import get_retries_buckets as get_retries_buckets  # noqa: F401
from ddtrace_testing.internal.dynamic_atr_retries import (
    is_dynamic_retries_enabled as is_dynamic_retries_enabled,  # noqa: F401
)
from ddtrace_testing.internal.dynamic_atr_retries import log as log  # noqa: F401
