"""Legacy re-export shim for a moved module.

``ddtrace.internal.settings.env`` moved to:

``ddtrace_base.settings.env``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.settings.env import EnvConfig as EnvConfig  # noqa: F401
from ddtrace_base.settings.env import dd_environ as dd_environ  # noqa: F401
from ddtrace_base.settings.env import logger as logger  # noqa: F401
