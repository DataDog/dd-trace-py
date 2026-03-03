from unittest.mock import Mock
from unittest.mock import patch

import pytest
from strands.hooks import AfterModelCallEvent
from strands.hooks import BeforeModelCallEvent
from strands.hooks import BeforeToolCallEvent
from strands.hooks import HookRegistry

from ddtrace.appsec.ai_guard import AIGuardAbortError
from ddtrace.appsec.ai_guard import Function
from ddtrace.appsec.ai_guard import Message
from ddtrace.appsec.ai_guard import ToolCall
from ddtrace.appsec.ai_guard.integrations.strands import AIGuardStrandsHookProvider
from ddtrace.appsec.ai_guard.integrations.strands import _convert_strands_messages
from ddtrace.appsec.ai_guard.integrations.strands import _tool_result_text
from tests.appsec.ai_guard.utils import mock_evaluate_response


# ---------------------------------------------------------------------------
# Helpers to build mock Strands event objects
# ---------------------------------------------------------------------------


def _mock_agent(messages=None, system_prompt=None):
    """Build a mock Agent with .messages and .system_prompt."""
    agent = Mock()
    agent.messages = messages if messages is not None else []
    agent.system_prompt = system_prompt
    return agent


def _before_model_event(messages=None, system_prompt=None):
    """Build a BeforeModelCallEvent with a mock agent."""
    agent = _mock_agent(messages, system_prompt)
    return BeforeModelCallEvent(agent=agent, invocation_state={})


def _after_model_event(response_message=None):
    """Build an AfterModelCallEvent with a mock agent and optional response."""
    agent = _mock_agent()
    stop_response = None
    if response_message is not None:
        stop_response = AfterModelCallEvent.ModelStopResponse(
            message=response_message,
            stop_reason="end_turn",
        )
    return AfterModelCallEvent(agent=agent, stop_response=stop_response)


def _before_tool_event(tool_use, messages=None):
    """Build a BeforeToolCallEvent with a mock agent."""
    agent = _mock_agent(messages)
    return BeforeToolCallEvent(
        agent=agent,
        selected_tool=None,
        tool_use=tool_use,
        invocation_state={},
    )


# ---------------------------------------------------------------------------
# _tool_result_text
# ---------------------------------------------------------------------------


class TestToolResultText:
    def test_text_content(self):
        tr = {"content": [{"text": "result 1"}, {"text": "result 2"}]}
        assert _tool_result_text(tr) == "result 1 result 2"

    def test_json_content(self):
        tr = {"content": [{"json": {"key": "value"}}]}
        assert _tool_result_text(tr) == '{"key": "value"}'

    def test_mixed_content(self):
        tr = {"content": [{"text": "note"}, {"json": {"x": 1}}]}
        assert _tool_result_text(tr) == 'note {"x": 1}'

    def test_empty_content(self):
        assert _tool_result_text({"content": []}) == ""

    def test_missing_content(self):
        assert _tool_result_text({}) == ""


# ---------------------------------------------------------------------------
# _convert_strands_messages
# ---------------------------------------------------------------------------


class TestConvertStrandsMessages:
    def test_user_text_message(self):
        messages = [{"role": "user", "content": [{"text": "Hello"}]}]
        result = _convert_strands_messages(messages)
        assert result == [Message(role="user", content="Hello")]

    def test_assistant_text_message(self):
        messages = [{"role": "assistant", "content": [{"text": "Hi there"}]}]
        result = _convert_strands_messages(messages)
        assert result == [Message(role="assistant", content="Hi there")]

    def test_system_prompt(self):
        result = _convert_strands_messages([], system_prompt="You are helpful")
        assert result == [Message(role="system", content="You are helpful")]

    def test_assistant_tool_use(self):
        messages = [
            {
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": "tc1", "name": "calculator", "input": {"expr": "2+2"}}}],
            }
        ]
        result = _convert_strands_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["tool_calls"] == [
            ToolCall(id="tc1", function=Function(name="calculator", arguments='{"expr": "2+2"}'))
        ]

    def test_user_tool_result(self):
        messages = [
            {
                "role": "user",
                "content": [{"toolResult": {"toolUseId": "tc1", "content": [{"text": "4"}]}}],
            }
        ]
        result = _convert_strands_messages(messages)
        assert result == [Message(role="tool", tool_call_id="tc1", content="4")]

    def test_full_conversation(self):
        messages = [
            {"role": "user", "content": [{"text": "What is 2+2?"}]},
            {
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": "tc1", "name": "calculator", "input": {"expr": "2+2"}}}],
            },
            {
                "role": "user",
                "content": [{"toolResult": {"toolUseId": "tc1", "content": [{"text": "4"}]}}],
            },
            {"role": "assistant", "content": [{"text": "The answer is 4."}]},
        ]
        result = _convert_strands_messages(messages, system_prompt="You are a math tutor")
        assert len(result) == 5
        assert result[0] == Message(role="system", content="You are a math tutor")
        assert result[1] == Message(role="user", content="What is 2+2?")
        assert result[2]["role"] == "assistant"
        assert len(result[2]["tool_calls"]) == 1
        assert result[3] == Message(role="tool", tool_call_id="tc1", content="4")
        assert result[4] == Message(role="assistant", content="The answer is 4.")

    def test_assistant_text_and_tool_use(self):
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"text": "Let me calculate that."},
                    {"toolUse": {"toolUseId": "tc1", "name": "calc", "input": {}}},
                ],
            }
        ]
        result = _convert_strands_messages(messages)
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert "tool_calls" in result[0]
        assert result[1] == Message(role="assistant", content="Let me calculate that.")

    def test_user_text_and_tool_result(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"text": "Here is the result"},
                    {"toolResult": {"toolUseId": "tc1", "content": [{"text": "42"}]}},
                ],
            }
        ]
        result = _convert_strands_messages(messages)
        assert len(result) == 2
        assert result[0] == Message(role="user", content="Here is the result")
        assert result[1] == Message(role="tool", tool_call_id="tc1", content="42")

    def test_empty_messages(self):
        assert _convert_strands_messages([]) == []

    def test_non_dict_messages_skipped(self):
        result = _convert_strands_messages(["not a dict", 42, None])
        assert result == []

    def test_tool_result_json_content(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"toolResult": {"toolUseId": "tc1", "content": [{"json": {"result": 42}}]}},
                ],
            }
        ]
        result = _convert_strands_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "tool"
        assert result[0]["content"] == '{"result": 42}'

    def test_multiple_tool_uses_in_single_message(self):
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": "tc1", "name": "search", "input": {"q": "foo"}}},
                    {"toolUse": {"toolUseId": "tc2", "name": "calc", "input": {"x": 1}}},
                ],
            }
        ]
        result = _convert_strands_messages(messages)
        assert len(result) == 1
        assert len(result[0]["tool_calls"]) == 2
        assert result[0]["tool_calls"][0]["function"]["name"] == "search"
        assert result[0]["tool_calls"][1]["function"]["name"] == "calc"

    def test_non_list_content_skipped(self):
        messages = [{"role": "user", "content": "plain string"}]
        result = _convert_strands_messages(messages)
        assert result == []


# ---------------------------------------------------------------------------
# _on_before_model_call (via BeforeModelCallEvent)
# ---------------------------------------------------------------------------


class TestBeforeModelCall:
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_allow(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = _before_model_event(
            messages=[{"role": "user", "content": [{"text": "Hello"}]}],
        )

        ai_guard_strands_hook._on_before_model_call(event)

        mock_execute_request.assert_called_once()

    @pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_block(self, mock_execute_request, ai_guard_strands_hook, decision):
        mock_execute_request.return_value = mock_evaluate_response(decision)
        event = _before_model_event(
            messages=[{"role": "user", "content": [{"text": "malicious prompt"}]}],
        )

        with pytest.raises(AIGuardAbortError):
            ai_guard_strands_hook._on_before_model_call(event)

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_with_system_prompt(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = _before_model_event(
            messages=[{"role": "user", "content": [{"text": "Hello"}]}],
            system_prompt="You are helpful",
        )

        ai_guard_strands_hook._on_before_model_call(event)

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_empty_messages(self, mock_execute_request, ai_guard_strands_hook):
        event = _before_model_event(messages=[])

        ai_guard_strands_hook._on_before_model_call(event)

        mock_execute_request.assert_not_called()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_with_conversation_history(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = _before_model_event(
            messages=[
                {"role": "user", "content": [{"text": "What is 2+2?"}]},
                {"role": "assistant", "content": [{"text": "4"}]},
                {"role": "user", "content": [{"text": "And 3+3?"}]},
            ],
        )

        ai_guard_strands_hook._on_before_model_call(event)

        mock_execute_request.assert_called_once()


# ---------------------------------------------------------------------------
# _on_after_model_call (via AfterModelCallEvent)
# ---------------------------------------------------------------------------


class TestAfterModelCall:
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_allow(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = _after_model_event(
            response_message={"role": "assistant", "content": [{"text": "Here is the answer."}]},
        )

        ai_guard_strands_hook._on_after_model_call(event)

        mock_execute_request.assert_called_once()

    @pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_block(self, mock_execute_request, ai_guard_strands_hook, decision):
        mock_execute_request.return_value = mock_evaluate_response(decision)
        event = _after_model_event(
            response_message={"role": "assistant", "content": [{"text": "sensitive data"}]},
        )

        with pytest.raises(AIGuardAbortError):
            ai_guard_strands_hook._on_after_model_call(event)

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_no_stop_response(self, mock_execute_request, ai_guard_strands_hook):
        event = _after_model_event(response_message=None)

        ai_guard_strands_hook._on_after_model_call(event)

        mock_execute_request.assert_not_called()


# ---------------------------------------------------------------------------
# _on_before_tool_call (via BeforeToolCallEvent)
# ---------------------------------------------------------------------------


class TestBeforeToolCall:
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_allow(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = _before_tool_event(
            tool_use={"toolUseId": "tc1", "name": "calculator", "input": {"expr": "2+2"}},
            messages=[{"role": "user", "content": [{"text": "Calculate 2+2"}]}],
        )

        ai_guard_strands_hook._on_before_tool_call(event)

        mock_execute_request.assert_called_once()

    @pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_block(self, mock_execute_request, ai_guard_strands_hook, decision):
        mock_execute_request.return_value = mock_evaluate_response(decision)
        event = _before_tool_event(
            tool_use={"toolUseId": "tc1", "name": "shell_exec", "input": {"cmd": "rm -rf /"}},
            messages=[{"role": "user", "content": [{"text": "Delete everything"}]}],
        )

        with pytest.raises(AIGuardAbortError):
            ai_guard_strands_hook._on_before_tool_call(event)

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_tool_call_appended_to_history(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = _before_tool_event(
            tool_use={"toolUseId": "tc1", "name": "search", "input": {"query": "foo"}},
            messages=[{"role": "user", "content": [{"text": "Search for foo"}]}],
        )

        ai_guard_strands_hook._on_before_tool_call(event)

        call_args = mock_execute_request.call_args
        payload = call_args[0][1]
        sent_messages = payload["data"]["attributes"]["messages"]
        assert len(sent_messages) == 2
        assert sent_messages[0] == Message(role="user", content="Search for foo")
        assert sent_messages[1]["role"] == "assistant"
        assert sent_messages[1]["tool_calls"][0]["id"] == "tc1"
        assert sent_messages[1]["tool_calls"][0]["function"]["name"] == "search"
        assert sent_messages[1]["tool_calls"][0]["function"]["arguments"] == '{"query": "foo"}'


# ---------------------------------------------------------------------------
# register_hooks
# ---------------------------------------------------------------------------


class TestRegisterHooks:
    def test_registers_all_callbacks(self, ai_guard_strands_hook):
        registry = HookRegistry()

        ai_guard_strands_hook.register_hooks(registry)

        assert registry.has_callbacks()
        # Verify each event type has a registered callback
        assert list(registry.get_callbacks_for(BeforeModelCallEvent(agent=Mock(), invocation_state={})))
        assert list(registry.get_callbacks_for(AfterModelCallEvent(agent=Mock())))
        assert list(
            registry.get_callbacks_for(
                BeforeToolCallEvent(
                    agent=Mock(),
                    selected_tool=None,
                    tool_use={"toolUseId": "x", "name": "x", "input": {}},
                    invocation_state={},
                )
            )
        )

    def test_has_register_hooks_method(self):
        assert hasattr(AIGuardStrandsHookProvider, "register_hooks")


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_before_model_call_swallows_non_abort_errors(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.side_effect = ConnectionError("network error")
        event = _before_model_event(
            messages=[{"role": "user", "content": [{"text": "Hello"}]}],
        )

        # Should not raise
        ai_guard_strands_hook._on_before_model_call(event)

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_after_model_call_propagates_abort_error(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("DENY")
        event = _after_model_event(
            response_message={"role": "assistant", "content": [{"text": "bad response"}]},
        )

        with pytest.raises(AIGuardAbortError):
            ai_guard_strands_hook._on_after_model_call(event)

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_before_tool_call_swallows_non_abort_errors(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.side_effect = ConnectionError("network error")
        event = _before_tool_event(
            tool_use={"toolUseId": "tc1", "name": "calc", "input": {}},
            messages=[],
        )

        # Should not raise
        ai_guard_strands_hook._on_before_tool_call(event)
