from typing import Dict
from typing import List
from typing import Union


# TypedDict was added to typing in python 3.8
try:
    from typing import TypedDict  # noqa:F401
except ImportError:
    from typing_extensions import TypedDict

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


ExportedLLMObsSpan = TypedDict("ExportedLLMObsSpan", {"span_id": str, "trace_id": str})
Message = TypedDict("Message", {"content": str, "role": str}, total=False)


class Messages:
    def __init__(self, messages: Union[List[Dict[str, str]], Dict[str, str], str]):
        self.messages = []
        if not isinstance(messages, list):
            messages = [messages]  # type: ignore[list-item]
        for message in messages:
            if isinstance(message, str):
                self.messages.append(Message(content=message))
                continue
            elif not isinstance(message, dict):
                raise TypeError("messages must be a string, dictionary, or list of dictionaries.")
            content = message.get("content", "")
            role = message.get("role")
            if not isinstance(content, str):
                raise TypeError("Message content must be a string.")
            if not role:
                self.messages.append(Message(content=content))
                continue
            if not isinstance(role, str):
                raise TypeError("Message role must be a string, and one of .")
            self.messages.append(Message(content=content, role=role))
