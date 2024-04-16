from typing import Dict
from typing import List
from typing import TypedDict
from typing import Union

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class Message(TypedDict):
    """
    Utility class to format LLM messages for annotation.

    example:
    ```
    Message(content="this is a message", role="user")
    ```
    """

    content: str
    role: str


class Messages:
    def __init__(self, messages: Union[List[Dict[str, str]], Dict[str, str], str]):
        if not isinstance(messages, list):
            messages = [messages]
        self.messages = []
        try:
            for message in messages:
                if isinstance(message, str):
                    self.messages.append(Message(content=message))
                elif isinstance(message, dict):
                    self.messages.append(Message(**message))
                else:
                    log.warning("messages must be a string, dictionary, or list of dictionaries.")
        except:
            log.warning(
                "Cannot format provided messages. The messages argument must be a string, a dictionary, or a "
                "list of dictionaries, or construct messages directly using the ``ddtrace.llmobs.utils.Message`` "
                "helper class."
            )
