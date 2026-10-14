"""Legacy re-export shim for a moved module.

``ddtrace.internal.utils.retry`` moved to:

``ddtrace_base.utils.retry``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.utils.retry import RetryError as RetryError  # noqa: F401
from ddtrace_base.utils.retry import fibonacci_backoff_with_jitter as fibonacci_backoff_with_jitter  # noqa: F401
from ddtrace_base.utils.retry import retry as retry  # noqa: F401
