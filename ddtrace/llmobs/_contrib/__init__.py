from collections.abc import Callable
import importlib
import sys

from ddtrace.internal import core


# Component -> module carrying the contrib's `_datadog_patch` flag. Doubles as the list
# of integrations LLMObs subscribes to. Used to catch integrations that were already
# patched before this ran; llama_index flags llama_index.core and mistralai flags
# mistralai.client rather than the top-level package.
_PATCH_FLAG_MODULES = {
    "anthropic": "anthropic",
    "llama_index": "llama_index.core",
    "mistralai": "mistralai.client",
}


def _subscriber_hook(component: str, action: str) -> Callable[[], None]:
    """Build the listener that imports ddtrace.llmobs._contrib.<component> on demand.

    The import is deferred to call time so that enabling LLMObs does not pull in every
    integration's subscriber module, only those whose library actually gets patched.
    """

    def hook() -> None:
        getattr(importlib.import_module("ddtrace.llmobs._contrib.%s" % component), action)()

    return hook


def listen_integrations() -> None:
    """Attach LLMObs subscribers to LLM integrations as they get patched.

    The subscribers stay registered while LLMObs is disabled because they also set
    the APM shadow tags. Loading them lazily on the integration's patch event keeps
    the LLMObs import chain out of applications that never patch an LLM library.

    core.on() de-duplicates by listener name, so calling this more than once (the
    LLMObs product's post_preload and LLMObs.enable() both do) is a no-op after the
    first call.
    """
    for component in _PATCH_FLAG_MODULES:
        listen = _subscriber_hook(component, "listen")
        core.on("%s.patch" % component, listen, "llmobs.%s.listen" % component)
        core.on("%s.unpatch" % component, _subscriber_hook(component, "unlisten"), "llmobs.%s.unlisten" % component)
        if _is_patched(component):
            listen()


def _is_patched(component: str) -> bool:
    module = sys.modules.get(_PATCH_FLAG_MODULES[component])
    return bool(getattr(module, "_datadog_patch", False))
