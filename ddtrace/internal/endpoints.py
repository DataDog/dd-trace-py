"""Legacy re-export shim for a moved module.

``ddtrace.internal.endpoints`` moved to:

``ddtrace_base.endpoints``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.endpoints import HttpEndPoint as HttpEndPoint  # noqa: F401
from ddtrace_base.endpoints import HttpEndPointsCollection as HttpEndPointsCollection  # noqa: F401
from ddtrace_base.endpoints import Singleton as Singleton  # noqa: F401
from ddtrace_base.endpoints import endpoint_collection as endpoint_collection  # noqa: F401
