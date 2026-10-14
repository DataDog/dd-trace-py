"""Legacy re-export shim for a moved module.

``ddtrace.internal.utils.attrdict`` moved to:

``ddtrace_base.utils.attrdict``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.utils.attrdict import AttrDict as AttrDict  # noqa: F401
