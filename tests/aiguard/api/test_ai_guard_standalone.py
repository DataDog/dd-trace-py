from unittest.mock import patch

import pytest

import ddtrace
from ddtrace._trace.constants import SAMPLING_DECISION_TRACE_TAG_KEY
from ddtrace.constants import USER_KEEP
from ddtrace.contrib.internal.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from ddtrace.internal.settings.asm import ai_guard_config
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils.constants import SamplingMechanism
from tests.aiguard.utils import mock_evaluate_response
from tests.aiguard.utils import override_ai_guard_config


MESSAGES = [
    {"role": "user", "content": "What is the meaning of life?"},
]


_STANDALONE_AI_GUARD_CONFIG = dict(
    _ai_guard_enabled="True",
    _ai_guard_endpoint="https://api.example.com/ai-guard",
    _dd_api_key="test-api-key",
    _dd_app_key="test-app-key",
)


@pytest.fixture
def ai_guard_standalone_tracer(tracer):
    """Enable AI Guard with APM tracing disabled (standalone mode) and restore afterwards."""
    with override_ai_guard_config(_STANDALONE_AI_GUARD_CONFIG):
        # compute_stats_enabled is passed to force tracer._recreate so the sampling processor
        # picks up apm_opt_out (which is now True because AI Guard is enabled + APM disabled).
        tracer.configure(apm_tracing_disabled=True, compute_stats_enabled=False)
        try:
            yield tracer
        finally:
            tracer.configure(apm_tracing_disabled=False, compute_stats_enabled=False)
            ddtrace.config._reset()


class TestAIGuardStandalone:
    """AI Guard standalone mode: DD_AI_GUARD_ENABLED=true + DD_APM_TRACING_ENABLED=false.

    AI Guard traces must still reach the backend (kept with USER_KEEP and the AI_GUARD decision
    maker) while APM host billing is opted out (``_dd.apm.enabled=0``).
    """

    def test_apm_opt_out_enabled_when_ai_guard_enabled_and_apm_disabled(self):
        """AI Guard alone (no AppSec/IAST/SCA) with APM disabled triggers _apm_opt_out."""
        with override_ai_guard_config(_STANDALONE_AI_GUARD_CONFIG):
            original = asm_config._apm_tracing_enabled
            try:
                asm_config._apm_tracing_enabled = False
                assert ai_guard_config._ai_guard_enabled
                assert asm_config._apm_opt_out is True
            finally:
                asm_config._apm_tracing_enabled = original

    def test_apm_opt_out_disabled_when_apm_tracing_enabled(self):
        """AI Guard enabled but APM tracing enabled must NOT opt out of APM billing."""
        with override_ai_guard_config(_STANDALONE_AI_GUARD_CONFIG):
            original = asm_config._apm_tracing_enabled
            try:
                asm_config._apm_tracing_enabled = True
                assert ai_guard_config._ai_guard_enabled
                assert asm_config._apm_opt_out is False
            finally:
                asm_config._apm_tracing_enabled = original

    def test_standalone_sets_apm_enabled_metric(self, ai_guard_standalone_tracer):
        """In standalone mode every span gets the _dd.apm.enabled=0 metric (no APM billing)."""
        tracer = ai_guard_standalone_tracer
        assert asm_config._apm_opt_out is True

        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(span, {}, raw_uri="http://example.com/", status_code="200")

        assert span.get_metric("_dd.apm.enabled") == 0.0

    @patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
    def test_standalone_keeps_ai_guard_trace(self, mock_execute_request, ai_guard_standalone_tracer):
        """After evaluate(), the service-entry span is kept (USER_KEEP) with the AI_GUARD decision maker."""
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        tracer = ai_guard_standalone_tracer
        assert asm_config._apm_opt_out is True

        from ddtrace.aiguard import new_ai_guard_client

        client = new_ai_guard_client()
        with tracer.trace("root_span", span_type=SpanTypes.WEB) as root_span:
            client.evaluate(MESSAGES)

        assert root_span.context.sampling_priority == USER_KEEP
        assert root_span.get_tag(SAMPLING_DECISION_TRACE_TAG_KEY) == "-%d" % SamplingMechanism.AI_GUARD
        assert root_span.get_metric("_dd.apm.enabled") == 0.0
