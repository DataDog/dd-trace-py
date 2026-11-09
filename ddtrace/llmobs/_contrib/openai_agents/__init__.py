from typing import Any
from typing import Optional

from agents.tracing import add_trace_processor

from ddtrace import config
from ddtrace.contrib.internal.openai_agents._constants import RUN_SINGLE_TURN_EVENT
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._contrib.openai_agents._processor import LLMObsTraceProcessor
from ddtrace.llmobs._integrations.openai_agents import OpenAIAgentsIntegration


log = get_logger(__name__)

# The integration of the trace processor added by the latest patch.
_integration: Optional[OpenAIAgentsIntegration] = None


def _on_run_single_turn(span: Any, args: list[Any], kwargs: dict[str, Any]) -> None:
    if _integration is None:
        return
    # MLOB-7584 — the SDK doesn't guard this wrap site, so an unguarded raise here
    # would surface in the user's Runner.run.
    try:
        _integration.tag_agent_manifest(span, args, kwargs)
    except Exception:
        log.debug("openai_agents tag_agent_manifest failed", exc_info=True)


def listen() -> None:
    global _integration
    # The SDK cannot remove a trace processor, so each patch adds one; a processor only
    # acts while the integration is patched.
    _integration = OpenAIAgentsIntegration(integration_config=config.openai_agents)
    add_trace_processor(LLMObsTraceProcessor(_integration))
    core.on(RUN_SINGLE_TURN_EVENT, _on_run_single_turn)


def unlisten() -> None:
    core.reset_listeners(RUN_SINGLE_TURN_EVENT, _on_run_single_turn)
