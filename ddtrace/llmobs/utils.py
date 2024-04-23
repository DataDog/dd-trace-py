from typing import Dict
from typing import List
from typing import TypedDict
from typing import Union

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


Message = TypedDict("Message", {"content": str, "role": str}, total=False)


class Messages:
    def __init__(self, messages: Union[List[Dict[str, str]], Dict[str, str], str]):
        self.messages = []
        if not isinstance(messages, list):
            messages = [messages]  # type: ignore[list-item]
        try:
            for message in messages:
                if isinstance(message, str):
                    self.messages.append(Message(content=message))
                    continue
                elif not isinstance(message, dict):
                    log.warning("messages must be a string, dictionary, or list of dictionaries.")
                    continue
                if "role" not in message:
                    self.messages.append(Message(content=message.get("content", "")))
                    continue
                self.messages.append(Message(content=message.get("content", ""), role=message.get("role", "")))
        except (TypeError, ValueError, AttributeError):
            log.warning(
                "Cannot format provided messages. The messages argument must be a string, a dictionary, or a "
                "list of dictionaries, or construct messages directly using the ``ddtrace.llmobs.utils.Message`` class."
            )
