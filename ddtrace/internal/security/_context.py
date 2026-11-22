from typing import Literal
from typing import Optional

from ddtrace.internal import core
from ddtrace.internal import span_bus
from ddtrace.internal.native._native import SpanData


# Core context key holding the AAP request environment. It lives here so IAST can find the entry span of an AAP
# request without importing _asm_request_context.
_ASM_CONTEXT: Literal["_asm_env"] = "_asm_env"


def get_entry_span() -> Optional[SpanData]:
    env = core.find_item(_ASM_CONTEXT)
    if env is None:
        span = span_bus.get_span()
        if span:
            return span._service_entry_span
        else:
            return span_bus.get_root_span()
    return env.entry_span  # type: ignore[no-any-return]
