from typing import Any
from typing import List

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._utils import _get_attr


log = get_logger(__name__)


async def _retrieve_context(instance):
    if instance is None:
        return
    try:
        # set flag to skip tracing during internal context retrieval
        instance._dd_internal_context_query = True
        await instance.query("/context")
        context_messages = []
        async for msg in instance.receive_response():
            context_messages.append(msg)
        return context_messages
    except Exception:
        log.warning("Error retrieving after context from claude_agent_sdk", exc_info=True)
    finally:
        if instance is not None:
            instance._dd_internal_context_query = False


def _extract_model_from_response(response: List[Any]) -> str:
    if not response or not isinstance(response, list):
        return ""

    for msg in response:
        msg_type = type(msg).__name__

        # check AssistantMessage.model
        if msg_type == "AssistantMessage":
            return str(_get_attr(msg, "model", None) or "")

        # check SystemMessage.data.model
        if msg_type == "SystemMessage":
            data = _get_attr(msg, "data", None)
            if data and isinstance(data, dict):
                return data.get("model") or ""

    return ""
