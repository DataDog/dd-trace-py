"""Bridge to the extracted ``ddtrace-internal`` package: this module *is* ``_ddtrace_internal.logger``.

At runtime the module object is replaced by the package module, so there is a single copy of its
state and tests may patch either path. The ``TYPE_CHECKING`` block only tells static tools what it exports.
"""

import sys
from typing import TYPE_CHECKING

import _ddtrace_internal.logger as _impl


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any

    from _ddtrace_internal.logger import _MINF as _MINF  # noqa: F401
    from _ddtrace_internal.logger import _RATE_LIMITS as _RATE_LIMITS  # noqa: F401
    from _ddtrace_internal.logger import DAY as DAY  # noqa: F401
    from _ddtrace_internal.logger import HOUR as HOUR  # noqa: F401
    from _ddtrace_internal.logger import LOG_LEVEL_TRIE as LOG_LEVEL_TRIE  # noqa: F401
    from _ddtrace_internal.logger import MINUTE as MINUTE  # noqa: F401
    from _ddtrace_internal.logger import SECOND as SECOND  # noqa: F401
    from _ddtrace_internal.logger import LoggerPrefix as LoggerPrefix  # noqa: F401
    from _ddtrace_internal.logger import LoggingBucket as LoggingBucket  # noqa: F401
    from _ddtrace_internal.logger import LogInjectionState as LogInjectionState  # noqa: F401
    from _ddtrace_internal.logger import _buckets as _buckets  # noqa: F401
    from _ddtrace_internal.logger import _rate_limit as _rate_limit  # noqa: F401
    from _ddtrace_internal.logger import format_stack as format_stack  # noqa: F401
    from _ddtrace_internal.logger import get_log_injection_state as get_log_injection_state  # noqa: F401
    from _ddtrace_internal.logger import get_logger as get_logger  # noqa: F401
    from _ddtrace_internal.logger import key_type as key_type  # noqa: F401
    from _ddtrace_internal.logger import log_filter as log_filter  # noqa: F401
    from _ddtrace_internal.logger import set_tag_rate_limit as set_tag_rate_limit  # noqa: F401

    def __getattr__(name: str) -> Any: ...

else:
    sys.modules[__name__] = _impl
