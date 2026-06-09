"""Test-only helpers for LLMObs integration tests."""

from unittest import mock

from ddtrace._trace.tracer import Tracer
from ddtrace.llmobs._llmobs import LLMObs
from ddtrace.llmobs._processor import LLMObsProcessor


def install_mock_llmobs_writer(tracer: Tracer, mock_writer=None):
    """Stop the real writer, attach a mock, and rebind the processor with keep_meta_struct=True.

    ``LLMObs.enable()`` wires ``LLMObsProcessor`` to the real writer;
    contrib fixtures must call this after swapping in a mock so meta_struct is not
    scrubbed when the SDK predicts the trace will be dropped.
    """
    if mock_writer is None:
        mock_writer = mock.MagicMock()
    LLMObs._instance._llmobs_span_writer.stop()
    LLMObs._instance._llmobs_span_writer = mock_writer
    tracer._span_aggregator.llmobs_processor = LLMObsProcessor(mock_writer, tracer, keep_meta_struct=True)
    return mock_writer
