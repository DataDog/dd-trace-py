"""Legacy re-export shim for a moved module.

``ddtrace.internal._unpatched`` moved to:

``ddtrace_base._unpatched``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base._unpatched import _gc as _gc  # noqa: F401
from ddtrace_base._unpatched import _threading as _threading  # noqa: F401
from ddtrace_base._unpatched import loaded_modules as loaded_modules  # noqa: F401
from ddtrace_base._unpatched import previous_loaded_modules as previous_loaded_modules  # noqa: F401
from ddtrace_base._unpatched import threading_Event as threading_Event  # noqa: F401
from ddtrace_base._unpatched import threading_Lock as threading_Lock  # noqa: F401
from ddtrace_base._unpatched import threading_RLock as threading_RLock  # noqa: F401
from ddtrace_base._unpatched import unpatched_close as unpatched_close  # noqa: F401
from ddtrace_base._unpatched import unpatched_json_loads as unpatched_json_loads  # noqa: F401
from ddtrace_base._unpatched import unpatched_open as unpatched_open  # noqa: F401
from ddtrace_base._unpatched import unpatched_Popen as unpatched_Popen  # noqa: F401
