from typing import Any

from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import in_asm_context
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


logger = get_logger(__name__)


def _on_openai_llm_call(kwargs: Any) -> None:
    if not in_asm_context():
        return
    # Azure OpenAI uses "engine" instead of "model".
    model = kwargs.get("model") or kwargs.get("engine") or ""
    try:
        call_waf_callback({"LLM_EVENT": {"provider": "openai", "model": model}})
    except Exception:
        logger.debug("Failed to emit LLM event to WAF", exc_info=True)


def listen() -> None:
    core.on("openai.chat.completions.create.before", _on_openai_llm_call)
    core.on("openai.responses.create.before", _on_openai_llm_call)
    core.on("openai.completions.create.before", _on_openai_llm_call)
