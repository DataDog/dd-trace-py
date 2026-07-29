from typing import Iterable

from ddtrace.appsec._constants import WAF_ACTIONS
from ddtrace.internal.settings.asm import config as asm_config


def must_block(actions: Iterable[str]) -> bool:
    return any(action in (WAF_ACTIONS.BLOCK_ACTION, WAF_ACTIONS.REDIRECT_ACTION) for action in actions)


def get_rasp_capability(capability: str) -> bool:
    """Return whether a RASP capability is enabled for the current ASM request."""
    if asm_config._asm_enabled and asm_config._ep_enabled:
        from ddtrace.appsec._asm_request_context import in_asm_context

        if not in_asm_context():
            return False

        try:
            from ddtrace.appsec._processor import AppSecSpanProcessor
        except Exception as e:
            from ddtrace.appsec._listeners import _abort_appsec

            _abort_appsec(str(e))
            return False

        return AppSecSpanProcessor._instance is not None and getattr(
            AppSecSpanProcessor._instance, f"rasp_{capability}_enabled", False
        )
    return False
