"""ctypes bindings for AppSec's security library: libddwaf

Importing this module will load `libddwaf.so` as a side-effect.
"""

from ddtrace.appsec._ddwaf.waf_stubs import DDWafRulesType
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_context_capsule


is_available = False
failure_msg = ""


try:
    from ddtrace.appsec._ddwaf.waf import DDWaf

    is_available = True
except Exception as e:
    failure_msg = str(e)


__all__ = ["DDWaf", "DDWafRulesType", "ddwaf_context_capsule", "failure_msg", "is_available"]
