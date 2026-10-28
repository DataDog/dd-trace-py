"""Bindings for AppSec's security library: libddwaf

Importing this module loads libddwaf as a side-effect, from the package or from the system.
The native extension backend is the default; ``asm_config._asm_waf_backend = "ctypes"`` selects
the legacy ctypes bindings. The backend is fixed for the life of the process.
"""

from ddtrace.appsec._ddwaf._types import DDWafInputType
from ddtrace.appsec._ddwaf._types import DDWafOutputType
from ddtrace.appsec._ddwaf._types import DDWafSqlTokenizer
from ddtrace.internal.settings.asm import config as _asm_config


if _asm_config._asm_waf_backend == "ctypes":
    from ddtrace.appsec._ddwaf.waf_ctypes import DDWaf
    from ddtrace.appsec._ddwaf.waf_ctypes import DDWafContext
else:
    from ddtrace.appsec._ddwaf.waf import DDWaf  # type: ignore[assignment]
    from ddtrace.appsec._ddwaf.waf import DDWafContext  # type: ignore[assignment]


__all__ = ["DDWaf", "DDWafInputType", "DDWafOutputType", "DDWafSqlTokenizer", "DDWafContext"]
