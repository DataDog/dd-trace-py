"""Legacy re-export shim for a moved module.

``ddtrace.internal.core.events`` moved to:

``ddtrace_base.core.events``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.core.events import Event as Event  # noqa: F401
from ddtrace_base.core.events import EventType as EventType  # noqa: F401
from ddtrace_base.core.events import event_field as event_field  # noqa: F401
