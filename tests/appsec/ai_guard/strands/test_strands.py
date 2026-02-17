from unittest.mock import patch

import pytest

from ddtrace.appsec.ai_guard import AIGuardAbortError
from ddtrace.appsec.ai_guard import Function
from ddtrace.appsec.ai_guard import Message
from ddtrace.appsec.ai_guard import ToolCall
from ddtrace.appsec.ai_guard.strands import AIGuardStrandsHookProvider
from ddtrace.appsec.ai_guard.strands import _convert_strands_messages
from ddtrace.appsec.ai_guard.strands import _extract_text_from_content
from ddtrace.appsec.ai_guard.strands import _extract_tool_result_text
from ddtrace.appsec.ai_guard.strands import _extract_tool_results
from ddtrace.appsec.ai_guard.strands import _extract_tool_uses
from tests.appsec.ai_guard.utils import mock_evaluate_response


class TestExtractTextFromContent:
    def test_string_content(self):
        assert _extract_text_from_content("hello") == "hello"

    def test_text_blocks(self):
        content = [{"text": "hello"}, {"text": "world"}]
        assert _extract_text_from_content(content) == "hello world"

    def test_mixed_blocks(self):
        content = [{"text": "hello"}, {"toolUse": {"name": "test"}}, {"text": "world"}]
        assert _extract_text_from_content(content) == "hello world"

    def test_empty_list(self):
        assert _extract_text_from_content([]) == ""

    def test_none(self):
        assert _extract_text_from_content(None) == ""

    def test_string_blocks(self):
        content = ["hello", "world"]
        assert _extract_text_from_content(content) == "hello world"


class TestExtractToolUses:
    def test_single_tool_use(self):
        content = [{"toolUse": {"toolUseId": "1", "name": "calc", "input": {"x": 1}}}]
        result = _extract_tool_uses(content)
        assert len(result) == 1
        assert result[0]["name"] == "calc"

    def test_multiple_tool_uses(self):
        content = [
            {"toolUse": {"toolUseId": "1", "name": "calc", "input": {}}},
            {"text": "thinking..."},
            {"toolUse": {"toolUseId": "2", "name": "search", "input": {}}},
        ]
        result = _extract_tool_uses(content)
        assert len(result) == 2

    def test_no_tool_uses(self):
        content = [{"text": "hello"}]
        assert _extract_tool_uses(content) == []

    def test_non_list(self):
        assert _extract_tool_uses("not a list") == []


class TestExtractToolResults:
    def test_single_tool_result(self):
        content = [{"toolResult": {"toolUseId": "1", "content": [{"text": "42"}]}}]
        result = _extract_tool_results(content)
        assert len(result) == 1
        assert result[0]["toolUseId"] == "1"

    def test_no_tool_results(self):
        assert _extract_tool_results([{"text": "hello"}]) == []


class TestExtractToolResultText:
    def test_text_content(self):
        tr = {"content": [{"text": "result 1"}, {"text": "result 2"}]}
        assert _extract_tool_result_text(tr) == "result 1 result 2"

    def test_json_content(self):
        tr = {"content": [{"json": {"key": "value"}}]}
        assert _extract_tool_result_text(tr) == '{"key": "value"}'

    def test_mixed_content(self):
        tr = {"content": [{"text": "note"}, {"json": {"x": 1}}]}
        assert _extract_tool_result_text(tr) == 'note {"x": 1}'

    def test_empty_content(self):
        assert _extract_tool_result_text({"content": []}) == ""

    def test_missing_content(self):
        assert _extract_tool_result_text({}) == ""


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

    def test_system_prompt_as_content_blocks(self):
        result = _convert_strands_messages([], system_prompt=[{"text": "Be helpful"}])
        assert result == [Message(role="system", content="Be helpful")]

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


class TestBeforeModelInvocation:
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_allow(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")

        ai_guard_strands_hook.before_model_invocation(
            messages=[{"role": "user", "content": [{"text": "Hello"}]}],
        )

        mock_execute_request.assert_called_once()

    @pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_block(self, mock_execute_request, ai_guard_strands_hook, decision):
        mock_execute_request.return_value = mock_evaluate_response(decision)

        with pytest.raises(AIGuardAbortError):
            ai_guard_strands_hook.before_model_invocation(
                messages=[{"role": "user", "content": [{"text": "malicious prompt"}]}],
            )

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_with_system_prompt(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")

        ai_guard_strands_hook.before_model_invocation(
            messages=[{"role": "user", "content": [{"text": "Hello"}]}],
            system_prompt="You are helpful",
        )

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_empty_messages(self, mock_execute_request, ai_guard_strands_hook):
        ai_guard_strands_hook.before_model_invocation(messages=[])

        mock_execute_request.assert_not_called()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_with_conversation_history(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")

        ai_guard_strands_hook.before_model_invocation(
            messages=[
                {"role": "user", "content": [{"text": "What is 2+2?"}]},
                {"role": "assistant", "content": [{"text": "4"}]},
                {"role": "user", "content": [{"text": "And 3+3?"}]},
            ],
        )

        mock_execute_request.assert_called_once()


class TestAfterModelInvocation:
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_allow(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")

        ai_guard_strands_hook.after_model_invocation(
            message={"role": "assistant", "content": [{"text": "Here is the answer."}]},
        )

        mock_execute_request.assert_called_once()

    @pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_block(self, mock_execute_request, ai_guard_strands_hook, decision):
        mock_execute_request.return_value = mock_evaluate_response(decision)

        with pytest.raises(AIGuardAbortError):
            ai_guard_strands_hook.after_model_invocation(
                message={"role": "assistant", "content": [{"text": "sensitive data"}]},
            )

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_empty_message(self, mock_execute_request, ai_guard_strands_hook):
        ai_guard_strands_hook.after_model_invocation(message={})

        mock_execute_request.assert_not_called()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_no_message(self, mock_execute_request, ai_guard_strands_hook):
        ai_guard_strands_hook.after_model_invocation()

        mock_execute_request.assert_not_called()


class TestBeforeToolInvocation:
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_allow(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")

        ai_guard_strands_hook.before_tool_invocation(
            tool={"toolUseId": "tc1", "name": "calculator", "input": {"expr": "2+2"}},
            messages=[{"role": "user", "content": [{"text": "Calculate 2+2"}]}],
        )

        mock_execute_request.assert_called_once()

    @pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_block(self, mock_execute_request, ai_guard_strands_hook, decision):
        mock_execute_request.return_value = mock_evaluate_response(decision)

        with pytest.raises(AIGuardAbortError):
            ai_guard_strands_hook.before_tool_invocation(
                tool={"toolUseId": "tc1", "name": "shell_exec", "input": {"cmd": "rm -rf /"}},
                messages=[{"role": "user", "content": [{"text": "Delete everything"}]}],
            )

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_empty_tool(self, mock_execute_request, ai_guard_strands_hook):
        ai_guard_strands_hook.before_tool_invocation(tool={})

        mock_execute_request.assert_not_called()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_no_tool(self, mock_execute_request, ai_guard_strands_hook):
        ai_guard_strands_hook.before_tool_invocation()

        mock_execute_request.assert_not_called()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_tool_call_appended_to_history(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")

        ai_guard_strands_hook.before_tool_invocation(
            tool={"toolUseId": "tc1", "name": "search", "input": {"query": "foo"}},
            messages=[
                {"role": "user", "content": [{"text": "Search for foo"}]},
            ],
        )

        call_args = mock_execute_request.call_args
        payload = call_args[0][1]
        sent_messages = payload["data"]["attributes"]["messages"]
        assert len(sent_messages) == 2
        assert sent_messages[0] == Message(role="user", content="Search for foo")
        assert sent_messages[1]["role"] == "assistant"
        assert sent_messages[1]["tool_calls"][0]["id"] == "tc1"
        assert sent_messages[1]["tool_calls"][0]["function"]["name"] == "search"
        assert sent_messages[1]["tool_calls"][0]["function"]["arguments"] == '{"query": "foo"}'


class TestHookProviderInheritance:
    def test_can_be_instantiated(self):
        from ddtrace.appsec.ai_guard.strands import AIGuardStrandsHookProvider
        from tests.appsec.ai_guard.utils import override_ai_guard_config

        with override_ai_guard_config(
            dict(
                _ai_guard_enabled="True",
                _ai_guard_endpoint="https://api.example.com/ai-guard",
                _dd_api_key="test-api-key",
                _dd_app_key="test-application-key",
            )
        ):
            hook = AIGuardStrandsHookProvider()
            assert hook._client is not None

    def test_has_required_methods(self):
        assert hasattr(AIGuardStrandsHookProvider, "before_model_invocation")
        assert hasattr(AIGuardStrandsHookProvider, "after_model_invocation")
        assert hasattr(AIGuardStrandsHookProvider, "before_tool_invocation")


class TestErrorHandling:
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_before_model_invocation_swallows_non_abort_errors(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.side_effect = ConnectionError("network error")

        ai_guard_strands_hook.before_model_invocation(
            messages=[{"role": "user", "content": [{"text": "Hello"}]}],
        )

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_after_model_invocation_swallows_non_abort_errors(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.side_effect = ConnectionError("network error")

        ai_guard_strands_hook.after_model_invocation(
            message={"role": "assistant", "content": [{"text": "response"}]},
        )

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_before_tool_invocation_swallows_non_abort_errors(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.side_effect = ConnectionError("network error")

        ai_guard_strands_hook.before_tool_invocation(
            tool={"toolUseId": "tc1", "name": "calc", "input": {}},
            messages=[],
        )
