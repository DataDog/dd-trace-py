from typing import Optional
from unittest.mock import Mock

from litellm.types.llms.openai import ChatCompletionAssistantMessage
from litellm.types.llms.openai import ChatCompletionFunctionMessage
from litellm.types.llms.openai import ChatCompletionSystemMessage
from litellm.types.llms.openai import ChatCompletionToolMessage
from litellm.types.llms.openai import ChatCompletionUserMessage
from litellm.types.utils import ModelResponse
import pytest

from ddtrace.appsec._ai_guard import init_ai_guard
from ddtrace.appsec.ai_guard.integrations.litellm import DatadogAIGuardGuardrail
from tests.appsec.ai_guard.utils import override_ai_guard_config


_AI_GUARD_CONFIG = dict(
    _ai_guard_enabled="True",
    _ai_guard_endpoint="https://api.example.com/ai-guard",
    _dd_api_key="test-api-key",
    _dd_app_key="test-application-key",
)


def pytest_configure():
    with override_ai_guard_config(_AI_GUARD_CONFIG):
        init_ai_guard()


@pytest.fixture
def guardrail():
    with override_ai_guard_config(_AI_GUARD_CONFIG):
        yield DatadogAIGuardGuardrail(block=True)


@pytest.fixture
def guardrail_monitor():
    with override_ai_guard_config(_AI_GUARD_CONFIG):
        yield DatadogAIGuardGuardrail(block=False)


@pytest.fixture
def guardrail_default():
    """Guardrail with block=None (default), delegating to env/config."""
    with override_ai_guard_config(_AI_GUARD_CONFIG):
        yield DatadogAIGuardGuardrail()


def make_choice(content=None, tool_calls=None, function_call=None):
    """Build a mock LiteLLM Choices object."""
    choice = Mock()
    msg = Mock()
    msg.content = content
    msg.tool_calls = tool_calls if tool_calls is not None else []
    msg.function_call = function_call
    choice.message = msg
    return choice


def make_tool_call_obj(tc_id, name, arguments="{}"):
    """Build a mock LiteLLM tool_call object as returned in a response."""
    tc = Mock()
    tc.id = tc_id
    tc.function = Mock()
    tc.function.name = name
    tc.function.arguments = arguments
    return tc


def make_model_response(choices=None):
    """Build a Mock that passes isinstance(response, ModelResponse) checks."""
    mock = Mock()
    mock.__class__ = ModelResponse
    mock.choices = choices if choices is not None else []
    return mock


# ---------------------------------------------------------------------------
# Typed AllMessageValues constructors
# ---------------------------------------------------------------------------


def user_msg(content) -> ChatCompletionUserMessage:
    return ChatCompletionUserMessage(role="user", content=content)


def system_msg(content) -> ChatCompletionSystemMessage:
    return ChatCompletionSystemMessage(role="system", content=content)


def assistant_msg(
    content=None,
    tool_calls: Optional[list] = None,
    function_call: Optional[dict] = None,
) -> ChatCompletionAssistantMessage:
    msg = ChatCompletionAssistantMessage(role="assistant", content=content)
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    if function_call is not None:
        msg["function_call"] = function_call
    return msg


def tool_msg(content, tool_call_id: str) -> ChatCompletionToolMessage:
    return ChatCompletionToolMessage(role="tool", content=content, tool_call_id=tool_call_id)


def function_msg(content, name: str = "") -> ChatCompletionFunctionMessage:
    return ChatCompletionFunctionMessage(role="function", content=content, name=name)


def make_request_data(messages=None, metadata=None):
    data = {"messages": messages or [], "model": "gpt-4o"}
    if metadata is not None:
        data["metadata"] = metadata
    else:
        data["metadata"] = {}
    return data
