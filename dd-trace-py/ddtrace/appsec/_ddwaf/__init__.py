"""ctypes bindings for AppSec's security library: libddwaf

Importing this module will load `libddwaf.so` as a side-effect and update `_asm_libddwaf_available` accordingly.
"""

from ddtrace.appsec._ddwaf.waf import DDWaf
from ddtrace.appsec._ddwaf.waf_stubs import DDWafRulesType
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_context_capsule


__all__ = ["DDWaf", "DDWafRulesType", "ddwaf_context_capsule"]
