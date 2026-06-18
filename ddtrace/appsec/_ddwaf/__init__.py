"""Python bindings for AppSec's security library: libddwaf.

libddwaf is statically linked into the native extension (``ddtrace.internal.native._native.ddwaf``);
this package keeps the orchestration, serialization and deserialization logic in Python on top of
that one-for-one native mirror of the libddwaf C ABI.
"""

from ddtrace.appsec._ddwaf.waf import DDWaf
from ddtrace.appsec._ddwaf.waf_stubs import DDWafRulesType
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_context_capsule


__all__ = ["DDWaf", "DDWafRulesType", "ddwaf_context_capsule"]
