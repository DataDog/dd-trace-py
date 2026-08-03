from __future__ import annotations

from types import TracebackType
from typing import TYPE_CHECKING
from typing import Iterable
from typing import Literal
from typing import NoReturn

from ddtrace.appsec._constants import WAF_ACTIONS
from ddtrace.internal.settings.asm import config as asm_config


if TYPE_CHECKING:
    from ddtrace.appsec._processor import AppSecSpanProcessor


RaspCapability = Literal["cmdi", "lfi", "shi", "sqli", "ssrf"]


def must_block(actions: Iterable[str]) -> bool:
    return any(action in (WAF_ACTIONS.BLOCK_ACTION, WAF_ACTIONS.REDIRECT_ACTION) for action in actions)


def build_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str | list[str]]:
    """Turn header pairs into a WAF address, folding repeated header names into a list of values."""
    result: dict[str, str | list[str]] = {}
    for key, value in headers:
        current = result.get(key)
        result[key] = value if current is None else [current, value] if isinstance(current, str) else [*current, value]
    return result


def _capability_enabled(processor: AppSecSpanProcessor, capability: RaspCapability) -> bool:
    # Explicit dispatch rather than getattr(processor, f"rasp_{capability}_enabled", False), which would
    # turn a renamed attribute into a silently disabled capability. Written with nothing after the last
    # branch so that adding a member to RaspCapability without a branch here fails type checking.
    if capability == "cmdi":
        return processor.rasp_cmdi_enabled
    if capability == "lfi":
        return processor.rasp_lfi_enabled
    if capability == "shi":
        return processor.rasp_shi_enabled
    if capability == "sqli":
        return processor.rasp_sqli_enabled
    if capability == "ssrf":
        return processor.rasp_ssrf_enabled


def get_rasp_capability(capability: RaspCapability) -> bool:
    """Return whether a RASP capability is enabled for the current ASM request."""
    if asm_config._asm_enabled and asm_config._ep_enabled:
        from ddtrace.appsec._asm_request_context import in_asm_context

        if not in_asm_context():
            return False

        try:
            from ddtrace.appsec._processor import AppSecSpanProcessor
        except Exception:
            # load_appsec owns fatal processor load failures; wrappers only need to
            # report the capability as unavailable while imports are in progress.
            return False

        processor = AppSecSpanProcessor._instance
        return processor is not None and _capability_enabled(processor, capability)
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
