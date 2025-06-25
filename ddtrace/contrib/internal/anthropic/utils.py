from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._utils import _get_attr


log = get_logger(__name__)


def handle_non_streamed_response(integration, chat_completions, args, kwargs, span):
    usage = _get_attr(chat_completions, "usage", {})
    integration.record_usage(span, usage)


def _extract_api_key(instance: Any) -> Optional[str]:
    """
    Extract and format LLM-provider API key from instance.
    """
    client = getattr(instance, "_client", "")
    if client:
        return getattr(client, "api_key", None)
    return None
