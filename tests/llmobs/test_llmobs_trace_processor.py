import mock

from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._trace_processor import LLMObsTraceProcessor
from tests.utils import override_global_config


def test_processor_returns_all_traces_by_default():
    """Test that the LLMObsTraceProcessor returns all traces by default."""
    trace_filter = LLMObsTraceProcessor(llmobs_span_writer=mock.MagicMock())
    root_llm_span = Span(name="span1", span_type=SpanTypes.LLM)
    root_llm_span._set_ctx_item(SPAN_KIND, "llm")
    trace1 = [root_llm_span]
    assert trace_filter.process_trace(trace1) == trace1


def test_processor_returns_all_traces_if_not_agentless():
    """Test that the LLMObsTraceProcessor returns all traces if DD_LLMOBS_AGENTLESS_ENABLED is not set to true."""
    with override_global_config(dict(_llmobs_agentless_enabled=False)):
        trace_filter = LLMObsTraceProcessor(llmobs_span_writer=mock.MagicMock())
        root_llm_span = Span(name="span1", span_type=SpanTypes.LLM)
        root_llm_span._set_ctx_item(SPAN_KIND, "llm")
        trace1 = [root_llm_span]
        assert trace_filter.process_trace(trace1) == trace1


def test_processor_returns_none_in_agentless_mode():
    """Test that the LLMObsTraceProcessor returns None if DD_LLMOBS_AGENTLESS_ENABLED is set to true."""
    with override_global_config(dict(_llmobs_agentless_enabled=True)):
        trace_filter = LLMObsTraceProcessor(llmobs_span_writer=mock.MagicMock())
        root_llm_span = Span(name="span1", span_type=SpanTypes.LLM)
        root_llm_span._set_ctx_item(SPAN_KIND, "llm")
        trace1 = [root_llm_span]
        assert trace_filter.process_trace(trace1) is None
