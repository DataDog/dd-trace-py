from typing import Optional

from ddtrace.appsec._ddwaf.waf_stubs import WAF
from ddtrace.appsec._ddwaf.waf_stubs import DDWafRulesType
from ddtrace.appsec._utils import DDWaf_info
from ddtrace.appsec._utils import DDWaf_result
from ddtrace.internal.logger import get_logger


__all__ = ["WAF", "DDWaf_info", "DDWaf_result", "version", "DDWafRulesType"]

LOGGER = get_logger(__name__)

_DDWAF_LOADED: bool = False
version: str = "unloaded"


def waf_module() -> Optional[type[WAF]]:
    try:
        import ddtrace.appsec._ddwaf.waf as waf_module

        _DDWAF_LOADED = True
        global version
        version = waf_module.version()
        return waf_module.DDWaf
    except Exception:
        LOGGER.warning("DDWaf features disabled. WARNING: Dynamic Library not loaded", exc_info=True)
        return None
