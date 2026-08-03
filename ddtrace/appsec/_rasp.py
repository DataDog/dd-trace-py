from types import TracebackType
from typing import Iterable
from typing import Literal
from typing import NoReturn

from ddtrace.appsec._constants import WAF_ACTIONS
from ddtrace.internal.settings.asm import config as asm_config


# AIDEV-NOTE: keep in sync with the rasp_<name>_enabled attributes of AppSecSpanProcessor.
RaspCapability = Literal["cmdi", "lfi", "shi", "sqli", "ssrf"]


def must_block(actions: Iterable[str]) -> bool:
    return any(action in (WAF_ACTIONS.BLOCK_ACTION, WAF_ACTIONS.REDIRECT_ACTION) for action in actions)


def get_rasp_capability(capability: RaspCapability) -> bool:
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

        return AppSecSpanProcessor._instance is not None and bool(
            getattr(AppSecSpanProcessor._instance, f"rasp_{capability}_enabled", False)
        )
    return False


def raise_without_wrapper_frame(error: BaseException) -> NoReturn:
    """Re-raise ``error`` with the current wrapper frame stripped from its traceback.

    RASP wrappers around ``open``-like builtins must not leak their own frame into the
    traceback the application sees, otherwise ``traceback.format_exc`` output changes.
    """
    traceback = error.__traceback__
    previous_frame = traceback.tb_frame.f_back if traceback is not None else None
    if previous_frame is None:
        raise error
    raise error.with_traceback(TracebackType(None, previous_frame, previous_frame.f_lasti, previous_frame.f_lineno))
