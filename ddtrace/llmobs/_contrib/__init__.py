import sys

from ddtrace.internal import core


def _listen_anthropic() -> None:
    from ddtrace.llmobs._contrib.anthropic import listen

    listen()


def _unlisten_anthropic() -> None:
    from ddtrace.llmobs._contrib.anthropic import unlisten

    unlisten()


def _listen_llama_index() -> None:
    from ddtrace.llmobs._contrib.llama_index import listen

    listen()


def _unlisten_llama_index() -> None:
    from ddtrace.llmobs._contrib.llama_index import unlisten

    unlisten()


# Component -> module carrying the contrib's `_datadog_patch` flag. Used to catch
# integrations that were already patched before this ran; llama_index flags
# llama_index.core rather than the top-level package.
_PATCH_FLAG_MODULES = {
    "anthropic": "anthropic",
    "llama_index": "llama_index.core",
}


def listen_integrations() -> None:
    """Attach LLMObs subscribers to LLM integrations as they get patched.

    The subscribers stay registered while LLMObs is disabled because they also set
    the APM shadow tags. Loading them lazily on the integration's patch event keeps
    the LLMObs import chain out of applications that never patch an LLM library.
    """
    core.on("anthropic.patch", _listen_anthropic, "llmobs.anthropic")
    core.on("anthropic.unpatch", _unlisten_anthropic, "llmobs.anthropic")
    core.on("llama_index.patch", _listen_llama_index, "llmobs.llama_index")
    core.on("llama_index.unpatch", _unlisten_llama_index, "llmobs.llama_index")

    if _is_patched("anthropic"):
        _listen_anthropic()
    if _is_patched("llama_index"):
        _listen_llama_index()


def _is_patched(component: str) -> bool:
    module = sys.modules.get(_PATCH_FLAG_MODULES[component])
    return bool(getattr(module, "_datadog_patch", False))
