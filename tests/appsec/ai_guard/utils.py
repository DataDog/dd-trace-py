import json
import random
import string
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Union
from unittest.mock import Mock

from openai.types.chat import ChatCompletion
from openai.types.chat import ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message import FunctionCall

from ddtrace._trace.span import Span
from ddtrace.appsec._constants import AI_GUARD
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import Prompt
from ddtrace.appsec.ai_guard import ToolCall
from tests.utils import DummyTracer


Evaluation = Union[Prompt, ToolCall]


def random_string(length: int) -> str:
    return "".join(random.choice(string.ascii_letters) for _ in range(length))


def find_ai_guard_span(tracer: DummyTracer) -> Span:
    spans = tracer.get_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == AI_GUARD.RESOURCE_TYPE
    return span


def assert_ai_guard_span(
    tracer: DummyTracer, history: List[Evaluation], current: Evaluation, tags: Dict[str, Any]
) -> None:
    span = find_ai_guard_span(tracer)
    for key, value in tags.items():
        assert span.get_tag(key) == value
    struct = span.get_struct_tag(AI_GUARD.TAG)
    assert struct["history"] == history
    assert struct["current"] == current


def mock_evaluate_response(action: str, reason: str = "", block: bool = True) -> Mock:
    mock_response = Mock()
    mock_response.status = 200
    mock_response.get_json.return_value = {
        "data": {"attributes": {"action": action, "reason": reason, "is_blocking_enabled": block}}
    }
    return mock_response


def assert_mock_execute_request_call(
    mock_execute_request, ai_guard_client: AIGuardClient, history: List[Evaluation], current: Evaluation
):
    expected_payload = {
        "data": {
            "attributes": {
                "history": history,
                "current": current,
            }
        }
    }
    mock_execute_request.assert_called_once_with(
        f"{ai_guard_client._endpoint}/evaluate",
        expected_payload,
    )


def mock_openai_tool_response(tool: str, args: Any) -> ChatCompletion:
    return ChatCompletion(
        id="'chatcmpl-C99UmjEgLlpSj8oX6NMnnmwCdsf90'",
        object="chat.completion",
        created=int(time.time()),
        model="gpt-3.5-turbo",
        choices=[
            Choice(
                index=0,
                message=ChatCompletionMessage(
                    role="assistant", function_call=FunctionCall(name=tool, arguments=json.dumps(args))
                ),
                finish_reason="function_call",
            )
        ],
    )
