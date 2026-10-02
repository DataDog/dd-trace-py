import sys

from ddtrace.internal import core


def _listen_anthropic() -> None:
    from ddtrace.llmobs._contrib.anthropic import listen

    listen()


def _unlisten_anthropic() -> None:
    from ddtrace.llmobs._contrib.anthropic import unlisten

    unlisten()


def listen_integrations() -> None:
    """Attach LLMObs subscribers to LLM integrations as they get patched.

    The subscribers stay registered while LLMObs is disabled because they also set
    the APM shadow tags. Loading them lazily on the integration's patch event keeps
    the LLMObs import chain out of applications that never patch an LLM library.
    """
    core.on("anthropic.patch", _listen_anthropic, "llmobs.anthropic")
    core.on("anthropic.unpatch", _unlisten_anthropic, "llmobs.anthropic")
    if getattr(sys.modules.get("anthropic"), "_datadog_patch", False):
        _listen_anthropic()
