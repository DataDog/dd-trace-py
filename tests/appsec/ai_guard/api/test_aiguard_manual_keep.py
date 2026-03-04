from unittest.mock import patch

import pytest

from ddtrace.appsec._trace_utils import _aiguard_manual_keep
from ddtrace.appsec.ai_guard import AIGuardAbortError
from ddtrace.appsec.ai_guard import Message
from ddtrace.appsec.ai_guard import Options
from ddtrace.constants import USER_KEEP
from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
from ddtrace.internal.constants import SamplingMechanism
from tests.appsec.ai_guard.utils import mock_evaluate_response
from tests.appsec.ai_guard.utils import override_ai_guard_config


MESSAGES = [
    Message(role="user", content="What is the meaning of life?"),
]


class TestAiguardManualKeep:
    """Tests for _aiguard_manual_keep and its integration with AI Guard evaluate."""

    def test_aiguard_manual_keep_sets_user_keep_priority(self, tracer):
        """_aiguard_manual_keep sets the sampling priority to USER_KEEP."""
        with tracer.trace("root_span") as span:
            _aiguard_manual_keep(span)
            assert span.context.sampling_priority == USER_KEEP

    def test_aiguard_manual_keep_sets_decision_maker_tag(self, tracer):
        """_aiguard_manual_keep sets _dd.p.dm to -13 (AI_GUARD)."""
        with tracer.trace("root_span") as span:
            _aiguard_manual_keep(span)
            assert span.get_tag(SAMPLING_DECISION_TRACE_TAG_KEY) == "-%d" % SamplingMechanism.AI_GUARD

    def test_aiguard_manual_keep_does_not_set_propagation_header(self, tracer):
        """_aiguard_manual_keep does NOT set the ASM security propagation header (_dd.p.ts)."""
        with tracer.trace("root_span") as span:
            _aiguard_manual_keep(span)
            assert "_dd.p.ts" not in span.context._meta

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_evaluate_sets_manual_keep_on_root_span(self, mock_execute_request, tracer, test_spans):
        """After a successful evaluate, the root span should have USER_KEEP priority and AI_GUARD decision maker."""
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")

        with override_ai_guard_config(
            dict(
                _ai_guard_enabled="True",
                _ai_guard_endpoint="https://api.example.com/ai-guard",
                _dd_api_key="test-api-key",
                _dd_app_key="test-app-key",
            )
        ):
            from ddtrace.appsec.ai_guard import new_ai_guard_client

            client = new_ai_guard_client()
            with tracer.trace("root_span") as root_span:
                client.evaluate(MESSAGES)

        assert root_span.context.sampling_priority == USER_KEEP
        assert root_span.get_tag(SAMPLING_DECISION_TRACE_TAG_KEY) == "-%d" % SamplingMechanism.AI_GUARD

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_evaluate_sets_manual_keep_on_root_span_blocking(self, mock_execute_request, tracer, test_spans):
        """After a blocking evaluate (ABORT), the root span should still have USER_KEEP and AI_GUARD decision maker."""
        mock_execute_request.return_value = mock_evaluate_response("ABORT", reason="blocked", tags=["test_tag"])

        with override_ai_guard_config(
            dict(
                _ai_guard_enabled="True",
                _ai_guard_endpoint="https://api.example.com/ai-guard",
                _dd_api_key="test-api-key",
                _dd_app_key="test-app-key",
            )
        ):
            from ddtrace.appsec.ai_guard import new_ai_guard_client

            client = new_ai_guard_client()
            with tracer.trace("root_span") as root_span:
                with pytest.raises(AIGuardAbortError):
                    client.evaluate(MESSAGES, Options(block=True))

        assert root_span.context.sampling_priority == USER_KEEP
        assert root_span.get_tag(SAMPLING_DECISION_TRACE_TAG_KEY) == "-%d" % SamplingMechanism.AI_GUARD

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_evaluate_sets_manual_keep_on_root_span_deny_non_blocking(self, mock_execute_request, tracer, test_spans):
        """After a non-blocking DENY evaluate, the root span should have USER_KEEP and AI_GUARD decision maker."""
        mock_execute_request.return_value = mock_evaluate_response("DENY", reason="denied", tags=["deny_tag"])

        with override_ai_guard_config(
            dict(
                _ai_guard_enabled="True",
                _ai_guard_endpoint="https://api.example.com/ai-guard",
                _dd_api_key="test-api-key",
                _dd_app_key="test-app-key",
            )
        ):
            from ddtrace.appsec.ai_guard import new_ai_guard_client

            client = new_ai_guard_client()
            with tracer.trace("root_span") as root_span:
                result = client.evaluate(MESSAGES, Options(block=False))

        assert result["action"] == "DENY"
        assert root_span.context.sampling_priority == USER_KEEP
        assert root_span.get_tag(SAMPLING_DECISION_TRACE_TAG_KEY) == "-%d" % SamplingMechanism.AI_GUARD

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_evaluate_no_root_span_does_not_crash(self, mock_execute_request, ai_guard_client):
        """When there is no root span, evaluate should complete without errors."""
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        result = ai_guard_client.evaluate(MESSAGES)
        assert result["action"] == "ALLOW"

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_evaluate_manual_keep_applied_to_root_not_ai_guard_span(self, mock_execute_request, tracer, test_spans):
        """The manual keep tags should be on the root span, not the AI Guard child span."""
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")

        with override_ai_guard_config(
            dict(
                _ai_guard_enabled="True",
                _ai_guard_endpoint="https://api.example.com/ai-guard",
                _dd_api_key="test-api-key",
                _dd_app_key="test-app-key",
            )
        ):
            from ddtrace.appsec.ai_guard import new_ai_guard_client

            client = new_ai_guard_client()
            with tracer.trace("root_span") as root_span:
                client.evaluate(MESSAGES)

        # Root span should have the manual keep tags
        assert root_span.get_tag(SAMPLING_DECISION_TRACE_TAG_KEY) == "-%d" % SamplingMechanism.AI_GUARD

        # Find the AI Guard child span and verify it does NOT have the decision maker tag
        ai_guard_span = None
        for span in test_spans.spans:
            if span.name == "ai_guard":
                ai_guard_span = span
                break
        assert ai_guard_span is not None
        assert ai_guard_span.get_tag(SAMPLING_DECISION_TRACE_TAG_KEY) is None
