"""Legacy re-export shim for a moved module.

``ddtrace.internal.forksafe`` moved to:

``ddtrace_base.forksafe``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.forksafe import Event as Event  # noqa: F401
from ddtrace_base.forksafe import Lock as Lock  # noqa: F401
from ddtrace_base.forksafe import ResetObject as ResetObject  # noqa: F401
from ddtrace_base.forksafe import RLock as RLock  # noqa: F401
from ddtrace_base.forksafe import ddtrace_after_in_child as ddtrace_after_in_child  # noqa: F401
from ddtrace_base.forksafe import ddtrace_after_in_parent as ddtrace_after_in_parent  # noqa: F401
from ddtrace_base.forksafe import ddtrace_before_fork as ddtrace_before_fork  # noqa: F401
from ddtrace_base.forksafe import get_generation as get_generation  # noqa: F401
from ddtrace_base.forksafe import has_forked as has_forked  # noqa: F401
from ddtrace_base.forksafe import in_child_hook as in_child_hook  # noqa: F401
from ddtrace_base.forksafe import is_fork_child as is_fork_child  # noqa: F401
from ddtrace_base.forksafe import log as log  # noqa: F401
from ddtrace_base.forksafe import register as register  # noqa: F401
from ddtrace_base.forksafe import register_after_child_hooks as register_after_child_hooks  # noqa: F401
from ddtrace_base.forksafe import register_after_parent as register_after_parent  # noqa: F401
from ddtrace_base.forksafe import register_before_child_hooks as register_before_child_hooks  # noqa: F401
from ddtrace_base.forksafe import register_before_fork as register_before_fork  # noqa: F401
from ddtrace_base.forksafe import register_hook as register_hook  # noqa: F401
from ddtrace_base.forksafe import run_child_hooks as run_child_hooks  # noqa: F401
from ddtrace_base.forksafe import run_hooks as run_hooks  # noqa: F401
from ddtrace_base.forksafe import set_fork_child as set_fork_child  # noqa: F401
from ddtrace_base.forksafe import set_forked as set_forked  # noqa: F401
from ddtrace_base.forksafe import unregister as unregister  # noqa: F401
from ddtrace_base.forksafe import unregister_after_child_hooks as unregister_after_child_hooks  # noqa: F401
from ddtrace_base.forksafe import unregister_before_child_hooks as unregister_before_child_hooks  # noqa: F401
from ddtrace_base.forksafe import unregister_before_fork as unregister_before_fork  # noqa: F401
from ddtrace_base.forksafe import unregister_parent as unregister_parent  # noqa: F401
