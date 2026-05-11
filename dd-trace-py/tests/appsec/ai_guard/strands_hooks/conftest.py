from unittest.mock import Mock

import pytest
from strands.hooks import AfterInvocationEvent
from strands.hooks import AfterModelCallEvent
from strands.hooks import AfterToolCallEvent
from strands.hooks import BeforeInvocationEvent
from strands.hooks import BeforeModelCallEvent
from strands.hooks import BeforeToolCallEvent
from strands.interrupt import _InterruptState

from ddtrace.appsec._ai_guard import init_ai_guard
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
def ai_guard_strands_hook():
    with override_ai_guard_config(_AI_GUARD_CONFIG):
        from ddtrace.appsec.ai_guard import AIGuardStrandsHookProvider

        yield AIGuardStrandsHookProvider()


@pytest.fixture
def ai_guard_strands_plugin():
    with override_ai_guard_config(_AI_GUARD_CONFIG):
        from ddtrace.appsec.ai_guard import AIGuardStrandsPlugin

        yield AIGuardStrandsPlugin()


# ---------------------------------------------------------------------------
# Shared helpers to build mock Strands event objects
# ---------------------------------------------------------------------------


def mock_agent(messages=None, system_prompt=None):
    """Build a mock Agent with .messages and .system_prompt."""
    agent = Mock()
    agent.messages = messages if messages is not None else []
    agent.system_prompt = system_prompt
    # Required by BeforeToolCallEvent which inherits from _Interruptible
    agent._interrupt_state = _InterruptState()
    return agent


def before_invocation_event(invocation_state=None, messages=None):
    """Build a BeforeInvocationEvent with a mock agent."""
    agent = mock_agent()
    return BeforeInvocationEvent(
        agent=agent,
        invocation_state=invocation_state if invocation_state is not None else {},
        messages=messages,
    )


def after_invocation_event(invocation_state=None):
    """Build an AfterInvocationEvent with a mock agent."""
    agent = mock_agent()
    return AfterInvocationEvent(
        agent=agent,
        invocation_state=invocation_state if invocation_state is not None else {},
    )


def before_model_event(messages=None, system_prompt=None):
    """Build a BeforeModelCallEvent with a mock agent."""
    agent = mock_agent(messages, system_prompt)
    return BeforeModelCallEvent(agent=agent, invocation_state={})


def after_model_event(response_message=None):
    """Build an AfterModelCallEvent with a mock agent and optional response."""
    agent = mock_agent()
    stop_response = None
    if response_message is not None:
        stop_response = AfterModelCallEvent.ModelStopResponse(
            message=response_message,
            stop_reason="end_turn",
        )
    return AfterModelCallEvent(agent=agent, stop_response=stop_response)


def before_tool_event(tool_use, messages=None):
    """Build a BeforeToolCallEvent with a mock agent."""
    agent = mock_agent(messages)
    return BeforeToolCallEvent(
        agent=agent,
        selected_tool=None,
        tool_use=tool_use,
        invocation_state={},
    )


def after_tool_event(tool_use, tool_result, messages=None):
    """Build an AfterToolCallEvent with a mock agent."""
    agent = mock_agent(messages)
    return AfterToolCallEvent(
        agent=agent,
        selected_tool=None,
        tool_use=tool_use,
        invocation_state={},
        result=tool_result,
    )


def make_hook(**kwargs):
    """Create an AIGuardStrandsHookProvider with the given parameters."""
    from ddtrace.appsec.ai_guard import AIGuardStrandsHookProvider

    with override_ai_guard_config(_AI_GUARD_CONFIG):
        return AIGuardStrandsHookProvider(**kwargs)


def make_plugin(**kwargs):
    """Create an AIGuardStrandsPlugin with the given parameters."""
    from ddtrace.appsec.ai_guard import AIGuardStrandsPlugin

    with override_ai_guard_config(_AI_GUARD_CONFIG):
        return AIGuardStrandsPlugin(**kwargs)
