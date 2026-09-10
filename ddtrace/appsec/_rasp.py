from typing import Iterable

from ddtrace.appsec._constants import WAF_ACTIONS
from ddtrace.internal.settings.asm import config as asm_config


def _must_block(actions: Iterable[str]) -> bool:
    return any(action in (WAF_ACTIONS.BLOCK_ACTION, WAF_ACTIONS.REDIRECT_ACTION) for action in actions)


def get_rasp_capability(capability: str) -> bool:
    """Return whether a RASP capability is active for the current request."""
    if not asm_config._asm_enabled or not asm_config._ep_enabled:
        return False

    from ddtrace.appsec._asm_request_context import in_asm_context

    if not in_asm_context():
        return False

    try:
        from ddtrace.appsec._processor import AppSecSpanProcessor
    except Exception:
        # load_appsec owns fatal processor load failures; listeners only need to
        # report the capability as unavailable while imports are in progress.
        return False

    processor = AppSecSpanProcessor._instance
    return processor is not None and bool(getattr(processor, f"rasp_{capability}_enabled", False))
