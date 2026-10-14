"""Legacy re-export shim for a moved module.

``ddtrace.internal.logger`` moved to:

``ddtrace_base.logger``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.logger import DAY as DAY  # noqa: F401
from ddtrace_base.logger import HOUR as HOUR  # noqa: F401
from ddtrace_base.logger import LOG_LEVEL_TRIE as LOG_LEVEL_TRIE  # noqa: F401
from ddtrace_base.logger import MINUTE as MINUTE  # noqa: F401
from ddtrace_base.logger import SECOND as SECOND  # noqa: F401
from ddtrace_base.logger import LoggerPrefix as LoggerPrefix  # noqa: F401
from ddtrace_base.logger import LoggingBucket as LoggingBucket  # noqa: F401
from ddtrace_base.logger import LogInjectionState as LogInjectionState  # noqa: F401
from ddtrace_base.logger import format_stack as format_stack  # noqa: F401
from ddtrace_base.logger import get_log_injection_state as get_log_injection_state  # noqa: F401
from ddtrace_base.logger import get_logger as get_logger  # noqa: F401
from ddtrace_base.logger import key_type as key_type  # noqa: F401
from ddtrace_base.logger import log_filter as log_filter  # noqa: F401
from ddtrace_base.logger import set_tag_rate_limit as set_tag_rate_limit  # noqa: F401
