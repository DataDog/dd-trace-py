from typing import Type

from ddtrace.appsec._ddwaf.waf_stubs import WAF
from ddtrace.appsec._ddwaf.waf_stubs import DDWaf_info
from ddtrace.appsec._ddwaf.waf_stubs import DDWaf_result
from ddtrace.appsec._ddwaf.waf_stubs import DDWafRulesType
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


__all__ = ["DDWaf", "DDWaf_info", "DDWaf_result", "version", "DDWafRulesType"]

LOGGER = get_logger(__name__)

_DDWAF_LOADED: bool = False

if asm_config._asm_libddwaf_available:
    try:
        import ddtrace.appsec._ddwaf.waf as waf_module

        _DDWAF_LOADED = True
    except Exception:
        import ddtrace.appsec._ddwaf.waf_mock as waf_module  # type: ignore[no-redef]

        LOGGER.warning("DDWaf features disabled. WARNING: Dynamic Library not loaded", exc_info=True)
else:
    import ddtrace.appsec._ddwaf.waf_mock as waf_module  # type: ignore[no-redef]

DDWaf: Type[WAF] = waf_module.DDWaf
version = waf_module.version
