"""Legacy re-export shim for a moved module.

``ddtrace.internal.settings._core`` moved to:

``ddtrace_base.settings._core``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.settings._core import ENV_CONFIG as ENV_CONFIG  # type: ignore[attr-defined]  # noqa: F401
from ddtrace_base.settings._core import FLEET_CONFIG as FLEET_CONFIG  # noqa: F401
from ddtrace_base.settings._core import FLEET_CONFIG_IDS as FLEET_CONFIG_IDS  # noqa: F401
from ddtrace_base.settings._core import LOCAL_CONFIG as LOCAL_CONFIG  # noqa: F401
from ddtrace_base.settings._core import DDConfig as DDConfig  # noqa: F401
from ddtrace_base.settings._core import ValueSource as ValueSource  # noqa: F401
