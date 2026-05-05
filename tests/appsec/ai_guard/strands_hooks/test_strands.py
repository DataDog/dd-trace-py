from unittest.mock import Mock
from unittest.mock import patch

import pytest
from strands.hooks import AfterInvocationEvent
from strands.hooks import AfterModelCallEvent
from strands.hooks import AfterToolCallEvent
from strands.hooks import BeforeInvocationEvent
from strands.hooks import BeforeModelCallEvent
from strands.hooks import BeforeToolCallEvent
from strands.hooks import HookRegistry
from strands.interrupt import _InterruptState

from ddtrace.appsec._ai_guard._context import is_aiguard_context_active
from ddtrace.appsec.ai_guard import AIGuardAbortError
from ddtrace.appsec.ai_guard import Function
from ddtrace.appsec.ai_guard import Message
from ddtrace.appsec.ai_guard import ToolCall
from ddtrace.appsec.ai_guard.integrations.strands import _INVOCATION_CTX_KEY
from ddtrace.appsec.ai_guard.integrations.strands import AIGuardStrandsHookProvider
from ddtrace.appsec.ai_guard.integrations.strands import AIGuardStrandsPlugin
from ddtrace.appsec.ai_guard.integrations.strands import _convert_strands_messages
from ddtrace.appsec.ai_guard.integrations.strands import _tool_result_text
from tests.appsec.ai_guard.strands_hooks.conftest import after_invocation_event
from tests.appsec.ai_guard.strands_hooks.conftest import after_model_event
from tests.appsec.ai_guard.strands_hooks.conftest import after_tool_event
from tests.appsec.ai_guard.strands_hooks.conftest import before_invocation_event
from tests.appsec.ai_guard.strands_hooks.conftest import before_model_event
from tests.appsec.ai_guard.strands_hooks.conftest import before_tool_event
from tests.appsec.ai_guard.strands_hooks.conftest import make_hook
from tests.appsec.ai_guard.strands_hooks.conftest import make_plugin
from tests.appsec.ai_guard.utils import mock_evaluate_response


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

    def test_user_tool_result_excluded(self):
        """When exclude_tool_results=True, tool results are skipped."""
        messages = [
            {
                "role": "user",
                "content": [{"toolResult": {"toolUseId": "tc1", "content": [{"text": "4"}]}}],
            }
        ]
        result = _convert_strands_messages(messages, exclude_tool_results=True)
        assert result == []

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

    def test_full_conversation_exclude_tool_results(self):
        """With exclude_tool_results=True, tool results are omitted."""
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
        result = _convert_strands_messages(messages, exclude_tool_results=True)
        assert len(result) == 3
        assert result[0] == Message(role="user", content="What is 2+2?")
        assert result[1]["role"] == "assistant"
        assert len(result[1]["tool_calls"]) == 1
        assert result[2] == Message(role="assistant", content="The answer is 4.")

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
# _on_before_model_call_base (via BeforeModelCallEvent)
# ---------------------------------------------------------------------------


class TestBeforeModelCall:
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_allow(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = before_model_event(
            messages=[{"role": "user", "content": [{"text": "Hello"}]}],
        )

        ai_guard_strands_hook._on_before_model_call_base(event)

        mock_execute_request.assert_called_once()

    @pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_block_always_raises(self, mock_execute_request, ai_guard_strands_hook, decision):
        """BeforeModelCall always raises AIGuardAbortError on violation."""
        mock_execute_request.return_value = mock_evaluate_response(decision)
        event = before_model_event(
            messages=[{"role": "user", "content": [{"text": "malicious prompt"}]}],
        )

        with pytest.raises(AIGuardAbortError):
            ai_guard_strands_hook._on_before_model_call_base(event)

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_with_system_prompt(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = before_model_event(
            messages=[{"role": "user", "content": [{"text": "Hello"}]}],
            system_prompt="You are helpful",
        )

        ai_guard_strands_hook._on_before_model_call_base(event)

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_empty_messages(self, mock_execute_request, ai_guard_strands_hook):
        event = before_model_event(messages=[])

        ai_guard_strands_hook._on_before_model_call_base(event)

        mock_execute_request.assert_not_called()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_with_conversation_history(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = before_model_event(
            messages=[
                {"role": "user", "content": [{"text": "What is 2+2?"}]},
                {"role": "assistant", "content": [{"text": "4"}]},
                {"role": "user", "content": [{"text": "And 3+3?"}]},
            ],
        )

        ai_guard_strands_hook._on_before_model_call_base(event)

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_excludes_tool_results(self, mock_execute_request, ai_guard_strands_hook):
        """BeforeModel should not include tool results (already processed by AfterToolCall)."""
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = before_model_event(
            messages=[
                {"role": "user", "content": [{"text": "What is 2+2?"}]},
                {
                    "role": "assistant",
                    "content": [{"toolUse": {"toolUseId": "tc1", "name": "calc", "input": {"x": "2+2"}}}],
                },
                {
                    "role": "user",
                    "content": [{"toolResult": {"toolUseId": "tc1", "content": [{"text": "4"}]}}],
                },
            ],
        )

        ai_guard_strands_hook._on_before_model_call_base(event)

        payload = mock_execute_request.call_args[0][1]
        sent_messages = payload["data"]["attributes"]["messages"]
        # Tool results should be excluded
        tool_msgs = [m for m in sent_messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 0


# ---------------------------------------------------------------------------
# _on_after_model_call_base (via AfterModelCallEvent)
# ---------------------------------------------------------------------------


class TestAfterModelCall:
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_allow(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = after_model_event(
            response_message={"role": "assistant", "content": [{"text": "Here is the answer."}]},
        )

        ai_guard_strands_hook._on_after_model_call_base(event)

        mock_execute_request.assert_called_once()

    @pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_block_always_raises(self, mock_execute_request, ai_guard_strands_hook, decision):
        """AfterModelCall always raises AIGuardAbortError on violation."""
        mock_execute_request.return_value = mock_evaluate_response(decision)
        event = after_model_event(
            response_message={"role": "assistant", "content": [{"text": "sensitive data"}]},
        )

        with pytest.raises(AIGuardAbortError):
            ai_guard_strands_hook._on_after_model_call_base(event)

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_no_stop_response(self, mock_execute_request, ai_guard_strands_hook):
        event = after_model_event(response_message=None)

        ai_guard_strands_hook._on_after_model_call_base(event)

        mock_execute_request.assert_not_called()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_skips_tool_use_only_response(self, mock_execute_request, ai_guard_strands_hook):
        """AfterModel should skip responses that only contain tool calls (no text)."""
        event = after_model_event(
            response_message={
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": "tc1", "name": "calc", "input": {}}}],
            },
        )

        ai_guard_strands_hook._on_after_model_call_base(event)

        mock_execute_request.assert_not_called()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_evaluates_only_text_in_mixed_response(self, mock_execute_request, ai_guard_strands_hook):
        """AfterModel should only evaluate text content, not tool calls."""
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = after_model_event(
            response_message={
                "role": "assistant",
                "content": [
                    {"text": "Let me calculate."},
                    {"toolUse": {"toolUseId": "tc1", "name": "calc", "input": {}}},
                ],
            },
        )

        ai_guard_strands_hook._on_after_model_call_base(event)

        mock_execute_request.assert_called_once()
        payload = mock_execute_request.call_args[0][1]
        sent_messages = payload["data"]["attributes"]["messages"]
        # Should only contain the text message, not a tool call message
        assert len(sent_messages) == 1
        assert sent_messages[0]["role"] == "assistant"
        assert sent_messages[0]["content"] == "Let me calculate."


# ---------------------------------------------------------------------------
# _on_before_tool_call_base (via BeforeToolCallEvent)
# ---------------------------------------------------------------------------


class TestBeforeToolCall:
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_allow(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = before_tool_event(
            tool_use={"toolUseId": "tc1", "name": "calculator", "input": {"expr": "2+2"}},
            messages=[{"role": "user", "content": [{"text": "Calculate 2+2"}]}],
        )

        ai_guard_strands_hook._on_before_tool_call_base(event)

        mock_execute_request.assert_called_once()

    @pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_block_cancels_with_message(self, mock_execute_request, ai_guard_strands_hook, decision):
        """Default behavior: cancel tool with a descriptive message."""
        mock_execute_request.return_value = mock_evaluate_response(decision)
        event = before_tool_event(
            tool_use={"toolUseId": "tc1", "name": "shell_exec", "input": {"cmd": "rm -rf /"}},
            messages=[{"role": "user", "content": [{"text": "Delete everything"}]}],
        )

        ai_guard_strands_hook._on_before_tool_call_base(event)

        assert event.cancel_tool == "[DATADOG AI GUARD] 'shell_exec' has been canceled for security reasons"
        mock_execute_request.assert_called_once()

    @pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_block_raise_error(self, mock_execute_request, decision):
        """With raise_error_on_tool_calls=True, AIGuardAbortError is raised."""
        mock_execute_request.return_value = mock_evaluate_response(decision)
        hook = make_hook(raise_error_on_tool_calls=True)
        event = before_tool_event(
            tool_use={"toolUseId": "tc1", "name": "shell_exec", "input": {"cmd": "rm -rf /"}},
            messages=[{"role": "user", "content": [{"text": "Delete everything"}]}],
        )

        with pytest.raises(AIGuardAbortError):
            hook._on_before_tool_call_base(event)

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_tool_call_appended_to_history(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = before_tool_event(
            tool_use={"toolUseId": "tc1", "name": "search", "input": {"query": "foo"}},
            messages=[{"role": "user", "content": [{"text": "Search for foo"}]}],
        )

        ai_guard_strands_hook._on_before_tool_call_base(event)

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
# _on_after_tool_call_base (via AfterToolCallEvent)
# ---------------------------------------------------------------------------


class TestAfterToolCall:
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_allow(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = after_tool_event(
            tool_use={"toolUseId": "tc1", "name": "calculator", "input": {"expr": "2+2"}},
            tool_result={"toolUseId": "tc1", "content": [{"text": "4"}], "status": "success"},
            messages=[{"role": "user", "content": [{"text": "Calculate 2+2"}]}],
        )

        ai_guard_strands_hook._on_after_tool_call_base(event)

        mock_execute_request.assert_called_once()

    @pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_block_replaces_tool_result(self, mock_execute_request, ai_guard_strands_hook, decision):
        """Default behavior: replace tool result content with blocked message."""
        mock_execute_request.return_value = mock_evaluate_response(decision)
        tool_result = {"toolUseId": "tc1", "content": [{"text": "sensitive data"}], "status": "success"}
        event = after_tool_event(
            tool_use={"toolUseId": "tc1", "name": "search", "input": {"q": "foo"}},
            tool_result=tool_result,
            messages=[{"role": "user", "content": [{"text": "Search"}]}],
        )

        ai_guard_strands_hook._on_after_tool_call_base(event)

        mock_execute_request.assert_called_once()
        assert event.result["content"][0]["text"] == (
            "[DATADOG AI GUARD] 'search' has been canceled for security reasons"
        )

    @pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_block_raise_error(self, mock_execute_request, decision):
        """With raise_error_on_tool_calls=True, AIGuardAbortError is raised."""
        mock_execute_request.return_value = mock_evaluate_response(decision)
        hook = make_hook(raise_error_on_tool_calls=True)
        event = after_tool_event(
            tool_use={"toolUseId": "tc1", "name": "search", "input": {"q": "foo"}},
            tool_result={"toolUseId": "tc1", "content": [{"text": "data"}], "status": "success"},
            messages=[],
        )

        with pytest.raises(AIGuardAbortError):
            hook._on_after_tool_call_base(event)

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_tool_result_included_in_payload(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = after_tool_event(
            tool_use={"toolUseId": "tc1", "name": "search", "input": {"query": "foo"}},
            tool_result={"toolUseId": "tc1", "content": [{"text": "result data"}], "status": "success"},
            messages=[{"role": "user", "content": [{"text": "Search for foo"}]}],
        )

        ai_guard_strands_hook._on_after_tool_call_base(event)

        payload = mock_execute_request.call_args[0][1]
        sent_messages = payload["data"]["attributes"]["messages"]
        # user msg + tool call (assistant) + tool result
        assert len(sent_messages) == 3
        assert sent_messages[0] == Message(role="user", content="Search for foo")
        assert sent_messages[1]["role"] == "assistant"
        assert sent_messages[1]["tool_calls"][0]["id"] == "tc1"
        assert sent_messages[1]["tool_calls"][0]["function"]["name"] == "search"
        assert sent_messages[2]["role"] == "tool"
        assert sent_messages[2]["tool_call_id"] == "tc1"
        assert sent_messages[2]["content"] == "result data"

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_error_status_result(self, mock_execute_request, ai_guard_strands_hook):
        """Tool results with error status are still evaluated."""
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = after_tool_event(
            tool_use={"toolUseId": "tc1", "name": "api_call", "input": {}},
            tool_result={
                "toolUseId": "tc1",
                "content": [{"text": "Error: connection refused"}],
                "status": "error",
            },
            messages=[],
        )

        ai_guard_strands_hook._on_after_tool_call_base(event)

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_block_replaces_empty_content(self, mock_execute_request, ai_guard_strands_hook):
        """When tool result has empty content list, blocked message is added."""
        mock_execute_request.return_value = mock_evaluate_response("DENY")
        tool_result = {"toolUseId": "tc1", "content": [], "status": "success"}
        event = after_tool_event(
            tool_use={"toolUseId": "tc1", "name": "search", "input": {}},
            tool_result=tool_result,
            messages=[],
        )

        ai_guard_strands_hook._on_after_tool_call_base(event)

        assert event.result["content"] == [
            {"text": "[DATADOG AI GUARD] 'search' has been canceled for security reasons"}
        ]


# ---------------------------------------------------------------------------
# HookProvider: register_hooks (legacy API)
# ---------------------------------------------------------------------------


class TestLazyImport:
    """Regression test: importing siblings of ``AIGuardStrandsPlugin`` from
    ``ddtrace.appsec.ai_guard`` must not eagerly load the strands integration.

    Eager loading binds ``BeforeModelCallEvent`` (and friends) to whatever
    class object exists at that moment. Under ``ddtrace.auto`` the user's
    later ``import strands`` re-instantiates the event dataclasses with new
    class identities, leaving the plugin's @hook callbacks registered against
    the wrong (stale) classes — so they never fire when the agent dispatches.
    The integration must be loaded lazily, after the user has imported
    ``strands`` themselves.
    """

    def test_ai_guard_abort_error_import_does_not_load_strands_integration(self):
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys;"
                    "from ddtrace.appsec.ai_guard import AIGuardAbortError;"
                    "loaded = 'ddtrace.appsec.ai_guard.integrations.strands' in sys.modules;"
                    "print('LOADED' if loaded else 'NOT_LOADED')"
                ),
            ],
            capture_output=True,
            text=True,
        )
        assert "NOT_LOADED" in result.stdout, (
            "Importing AIGuardAbortError must not eagerly load the strands integration. "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}"
        )

    def test_strands_plugin_access_loads_integration(self):
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys;"
                    "from ddtrace.appsec.ai_guard import AIGuardStrandsPlugin;"
                    "assert AIGuardStrandsPlugin is not None;"
                    "loaded = 'ddtrace.appsec.ai_guard.integrations.strands' in sys.modules;"
                    "print('LOADED' if loaded else 'NOT_LOADED')"
                ),
            ],
            capture_output=True,
            text=True,
        )
        assert "LOADED" in result.stdout, (
            "Accessing AIGuardStrandsPlugin must trigger the lazy import. "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}"
        )


class TestInvocationLifecycle:
    """The contextvar that gates provider-level integrations (OpenAI) is owned
    by the invocation-level hooks, not the per-model hooks. This guarantees
    OpenAI consistently skips while Strands owns the agent invocation.
    """

    def test_before_invocation_marks_context_active(self, ai_guard_strands_hook):
        invocation_state = {}
        event = before_invocation_event(invocation_state=invocation_state)

        assert is_aiguard_context_active() is False
        ai_guard_strands_hook._on_before_invocation_base(event)
        try:
            assert is_aiguard_context_active() is True
            assert _INVOCATION_CTX_KEY in invocation_state
        finally:
            ai_guard_strands_hook._on_after_invocation_base(after_invocation_event(invocation_state=invocation_state))

    def test_after_invocation_resets_context(self, ai_guard_strands_hook):
        invocation_state = {}
        ai_guard_strands_hook._on_before_invocation_base(before_invocation_event(invocation_state=invocation_state))
        assert is_aiguard_context_active() is True

        ai_guard_strands_hook._on_after_invocation_base(after_invocation_event(invocation_state=invocation_state))

        assert is_aiguard_context_active() is False
        assert _INVOCATION_CTX_KEY not in invocation_state

    def test_after_invocation_without_before_is_noop(self, ai_guard_strands_hook):
        # Should not raise even if BeforeInvocation never ran.
        ai_guard_strands_hook._on_after_invocation_base(after_invocation_event(invocation_state={}))
        assert is_aiguard_context_active() is False

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_model_call_hooks_do_not_touch_context(self, mock_execute_request, ai_guard_strands_hook):
        """The per-model hooks must not set or reset the AI Guard context."""
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")

        before_event = before_model_event(messages=[{"role": "user", "content": [{"text": "Hi"}]}])
        ai_guard_strands_hook._on_before_model_call_base(before_event)
        assert is_aiguard_context_active() is False

        after_event = after_model_event(response_message={"role": "assistant", "content": [{"text": "Hello"}]})
        ai_guard_strands_hook._on_after_model_call_base(after_event)
        assert is_aiguard_context_active() is False

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_context_stays_active_when_model_call_blocks(self, mock_execute_request, ai_guard_strands_hook):
        """If BeforeModelCall raises an abort, the context remains active until
        AfterInvocation resets it (Strands fires AfterInvocation in `finally`).
        """
        invocation_state = {}
        ai_guard_strands_hook._on_before_invocation_base(before_invocation_event(invocation_state=invocation_state))

        mock_execute_request.return_value = mock_evaluate_response("DENY")
        with pytest.raises(AIGuardAbortError):
            ai_guard_strands_hook._on_before_model_call_base(
                before_model_event(messages=[{"role": "user", "content": [{"text": "bad"}]}])
            )

        assert is_aiguard_context_active() is True

        ai_guard_strands_hook._on_after_invocation_base(after_invocation_event(invocation_state=invocation_state))
        assert is_aiguard_context_active() is False


class TestRegisterHooks:
    def test_registers_all_callbacks(self, ai_guard_strands_hook):
        registry = HookRegistry()

        ai_guard_strands_hook.register_hooks(registry)

        assert registry.has_callbacks()
        mock_agent = Mock()
        mock_agent._interrupt_state = _InterruptState()
        tool_use = {"toolUseId": "x", "name": "x", "input": {}}
        tool_result = {"toolUseId": "x", "content": [], "status": "success"}
        # Verify each event type has a registered callback
        assert list(registry.get_callbacks_for(BeforeInvocationEvent(agent=mock_agent, invocation_state={})))
        assert list(registry.get_callbacks_for(AfterInvocationEvent(agent=mock_agent, invocation_state={})))
        assert list(registry.get_callbacks_for(BeforeModelCallEvent(agent=mock_agent, invocation_state={})))
        assert list(registry.get_callbacks_for(AfterModelCallEvent(agent=mock_agent)))
        assert list(
            registry.get_callbacks_for(
                BeforeToolCallEvent(
                    agent=mock_agent,
                    selected_tool=None,
                    tool_use=tool_use,
                    invocation_state={},
                )
            )
        )
        assert list(
            registry.get_callbacks_for(
                AfterToolCallEvent(
                    agent=mock_agent,
                    selected_tool=None,
                    tool_use=tool_use,
                    invocation_state={},
                    result=tool_result,
                )
            )
        )

    def test_has_register_hooks_method(self):
        assert hasattr(AIGuardStrandsHookProvider, "register_hooks")


# ---------------------------------------------------------------------------
# Plugin: @hook decorator discovery
# ---------------------------------------------------------------------------


class TestPluginHookDiscovery:
    """Verify that the Plugin subclass exposes hooks via @hook decorators."""

    def test_plugin_has_name(self):
        assert AIGuardStrandsPlugin.name == "ai-guard"

    def test_plugin_has_hook_methods(self):
        assert hasattr(AIGuardStrandsPlugin, "on_before_invocation")
        assert hasattr(AIGuardStrandsPlugin, "on_after_invocation")
        assert hasattr(AIGuardStrandsPlugin, "on_before_model_call")
        assert hasattr(AIGuardStrandsPlugin, "on_after_model_call")
        assert hasattr(AIGuardStrandsPlugin, "on_before_tool_call")
        assert hasattr(AIGuardStrandsPlugin, "on_after_tool_call")

    def test_plugin_hook_methods_are_decorated(self):
        """Each hook method should be marked by the @hook decorator."""
        for method_name in (
            "on_before_invocation",
            "on_after_invocation",
            "on_before_model_call",
            "on_after_model_call",
            "on_before_tool_call",
            "on_after_tool_call",
        ):
            method = getattr(AIGuardStrandsPlugin, method_name)
            assert getattr(method, "_hook_event_types", None) is not None, (
                f"{method_name} should be decorated with @hook"
            )

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_plugin_on_before_model_call(self, mock_execute_request, ai_guard_strands_plugin):
        """Plugin @hook method delegates to the shared base logic."""
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = before_model_event(
            messages=[{"role": "user", "content": [{"text": "Hello"}]}],
        )

        ai_guard_strands_plugin.on_before_model_call(event)

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_plugin_on_after_model_call(self, mock_execute_request, ai_guard_strands_plugin):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = after_model_event(
            response_message={"role": "assistant", "content": [{"text": "Answer"}]},
        )

        ai_guard_strands_plugin.on_after_model_call(event)

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_plugin_on_before_tool_call(self, mock_execute_request, ai_guard_strands_plugin):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = before_tool_event(
            tool_use={"toolUseId": "tc1", "name": "calc", "input": {}},
            messages=[],
        )

        ai_guard_strands_plugin.on_before_tool_call(event)

        mock_execute_request.assert_called_once()

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_plugin_on_after_tool_call(self, mock_execute_request, ai_guard_strands_plugin):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        event = after_tool_event(
            tool_use={"toolUseId": "tc1", "name": "calc", "input": {}},
            tool_result={"toolUseId": "tc1", "content": [{"text": "4"}], "status": "success"},
            messages=[],
        )

        ai_guard_strands_plugin.on_after_tool_call(event)

        mock_execute_request.assert_called_once()

    @pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_plugin_on_before_model_call_block_raises(self, mock_execute_request, ai_guard_strands_plugin, decision):
        mock_execute_request.return_value = mock_evaluate_response(decision)
        event = before_model_event(
            messages=[{"role": "user", "content": [{"text": "malicious"}]}],
        )

        with pytest.raises(AIGuardAbortError):
            ai_guard_strands_plugin.on_before_model_call(event)

    @pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_plugin_on_before_tool_call_block_cancels(self, mock_execute_request, ai_guard_strands_plugin, decision):
        mock_execute_request.return_value = mock_evaluate_response(decision)
        event = before_tool_event(
            tool_use={"toolUseId": "tc1", "name": "shell_exec", "input": {"cmd": "rm -rf /"}},
            messages=[],
        )

        ai_guard_strands_plugin.on_before_tool_call(event)

        assert event.cancel_tool == "[DATADOG AI GUARD] 'shell_exec' has been canceled for security reasons"

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_plugin_raise_error_on_tool_calls(self, mock_execute_request):
        mock_execute_request.return_value = mock_evaluate_response("DENY")
        plugin = make_plugin(raise_error_on_tool_calls=True)
        event = before_tool_event(
            tool_use={"toolUseId": "tc1", "name": "shell_exec", "input": {}},
            messages=[],
        )

        with pytest.raises(AIGuardAbortError):
            plugin.on_before_tool_call(event)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_before_model_call_swallows_non_abort_errors(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.side_effect = ConnectionError("network error")
        event = before_model_event(
            messages=[{"role": "user", "content": [{"text": "Hello"}]}],
        )

        # Should not raise
        ai_guard_strands_hook._on_before_model_call_base(event)

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_after_model_call_raises_on_block(self, mock_execute_request, ai_guard_strands_hook):
        """AfterModelCall always raises AIGuardAbortError on violation."""
        mock_execute_request.return_value = mock_evaluate_response("DENY")
        response_message = {"role": "assistant", "content": [{"text": "bad response"}]}
        event = after_model_event(response_message=response_message)

        with pytest.raises(AIGuardAbortError):
            ai_guard_strands_hook._on_after_model_call_base(event)

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_before_tool_call_swallows_non_abort_errors(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.side_effect = ConnectionError("network error")
        event = before_tool_event(
            tool_use={"toolUseId": "tc1", "name": "calc", "input": {}},
            messages=[],
        )

        # Should not raise
        ai_guard_strands_hook._on_before_tool_call_base(event)

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_after_tool_call_swallows_non_abort_errors(self, mock_execute_request, ai_guard_strands_hook):
        mock_execute_request.side_effect = ConnectionError("network error")
        event = after_tool_event(
            tool_use={"toolUseId": "tc1", "name": "calc", "input": {}},
            tool_result={"toolUseId": "tc1", "content": [{"text": "4"}], "status": "success"},
            messages=[],
        )

        # Should not raise
        ai_guard_strands_hook._on_after_tool_call_base(event)


# ---------------------------------------------------------------------------
# Constructor parameters
# ---------------------------------------------------------------------------


class TestConstructorParameters:
    def test_default_parameters(self, ai_guard_strands_hook):
        assert ai_guard_strands_hook._detailed_error is False
        assert ai_guard_strands_hook._raise_error_on_tool_calls is False

    def test_custom_parameters(self):
        hook = make_hook(detailed_error=True, raise_error_on_tool_calls=True)
        assert hook._detailed_error is True
        assert hook._raise_error_on_tool_calls is True

    def test_plugin_default_parameters(self, ai_guard_strands_plugin):
        assert ai_guard_strands_plugin._detailed_error is False
        assert ai_guard_strands_plugin._raise_error_on_tool_calls is False

    def test_plugin_custom_parameters(self):
        plugin = make_plugin(detailed_error=True, raise_error_on_tool_calls=True)
        assert plugin._detailed_error is True
        assert plugin._raise_error_on_tool_calls is True
