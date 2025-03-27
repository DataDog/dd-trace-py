from typing import Any
from typing import Dict
from typing import Sequence
from typing import Tuple

from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._ddwaf.waf_stubs import WAF
from ddtrace.appsec._ddwaf.waf_stubs import DDWaf_info
from ddtrace.appsec._ddwaf.waf_stubs import DDWaf_result
from ddtrace.appsec._ddwaf.waf_stubs import DDWafRulesType
from ddtrace.appsec._ddwaf.waf_stubs import PayloadType
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_context_capsule
from ddtrace.appsec._utils import _observator
from ddtrace.internal.logger import get_logger


DDWAF_ERR_INTERNAL = -3
DDWAF_ERR_INVALID_OBJECT = -2
DDWAF_ERR_INVALID_ARGUMENT = -1
DDWAF_OK = 0
DDWAF_MATCH = 1


LOGGER = get_logger(__name__)


# Mockup of the DDWaf class doing nothing
class DDWaf(WAF):
    empty_observator = _observator()

    def __init__(
        self,
        rules: Dict[str, Any],
        obfuscation_parameter_key_regexp: bytes,
        obfuscation_parameter_value_regexp: bytes,
    ):
        self._handle = None

    def run(
        self,
        ctx: ddwaf_context_capsule,
        data: DDWafRulesType,
        ephemeral_data: DDWafRulesType = None,
        timeout_ms: float = DEFAULT.WAF_TIMEOUT,
    ) -> DDWaf_result:
        LOGGER.debug("DDWaf features disabled. dry run")
        return DDWaf_result(0, [], {}, 0.0, 0.0, False, self.empty_observator, {})

    def update_rules(
        self, removals: Sequence[Tuple[str, str]], updates: Sequence[Tuple[str, str, PayloadType]]
    ) -> bool:
        LOGGER.debug("DDWaf features disabled. dry update")
        return False

    def _at_request_start(self) -> None:
        return None

    def _at_request_end(self) -> None:
        pass

    @property
    def required_data(self):
        return []

    @property
    def info(self):
        return DDWaf_info(0, 0, "", "")

    @property
    def initialized(self) -> bool:
        return False


def version() -> str:
    LOGGER.debug("DDWaf features disabled. null version")
    return "0.0.0"
