import mock
import pytest

from ddtrace.llmobs._evaluators.ragas.faithfulness import RagasFaithfulnessEvaluator
from ddtrace.span import Span

from ._utils import _expected_llmobs_llm_span_event
from ._utils import _expected_ragas_spans
from ._utils import _llm_span_with_expected_ragas_inputs


def _llm_span_without_io():
    return _expected_llmobs_llm_span_event(Span("dummy"))


def test_ragas_evaluator_init(ragas, LLMObs):
    rf_evaluator = RagasFaithfulnessEvaluator(LLMObs)
    assert rf_evaluator.enabled
    assert rf_evaluator.llmobs == LLMObs
    assert rf_evaluator.faithfulness == ragas.metrics.faithfulness
    assert rf_evaluator.faithfulness.llm == ragas.llms.llm_factory()


def test_ragas_faithfulness_disabled_if_dependencies_not_present(LLMObs, ragas, mock_ragas_dependencies_not_present):
    rf_evaluator = RagasFaithfulnessEvaluator(LLMObs)
    assert not rf_evaluator.enabled
    assert rf_evaluator.evaluate(_llm_span_with_expected_ragas_inputs()) is None


def test_ragas_faithfulness_returns_none_if_inputs_extraction_fails(ragas, mock_llmobs_submit_evaluation, LLMObs):
    rf_evaluator = RagasFaithfulnessEvaluator(LLMObs)
    assert rf_evaluator.evaluate(_llm_span_without_io()) is None
    assert rf_evaluator.llmobs.submit_evaluation.call_count == 0


@pytest.mark.vcr_logs
def test_ragas_faithfulness_enqueues_score_evaluation_metric(ragas, LLMObs, mock_llmobs_submit_evaluation):
    rf_evaluator = RagasFaithfulnessEvaluator(LLMObs)
    rf_evaluator.evaluate(_llm_span_with_expected_ragas_inputs())
    rf_evaluator.llmobs.submit_evaluation.assert_has_calls(
        [
            mock.call(
                span_context={"trace_id": mock.ANY, "span_id": mock.ANY},
                value=mock.ANY,
                metric_type=RagasFaithfulnessEvaluator.METRIC_TYPE,
                label=RagasFaithfulnessEvaluator.LABEL,
            )
        ]
    )


@pytest.mark.vcr_logs
def test_ragas_faithfulness_emits_traces(ragas, LLMObs):
    rf_evaluator = RagasFaithfulnessEvaluator(LLMObs)
    rf_evaluator.evaluate(_llm_span_with_expected_ragas_inputs())
    assert rf_evaluator.llmobs._instance._llmobs_span_writer.enqueue.call_count == 5
    calls = rf_evaluator.llmobs._instance._llmobs_span_writer.enqueue.call_args_list
    spans = [call[0][0] for call in calls]
    assert spans == _expected_ragas_spans()
    root_span = spans[0]
    root_span_id = root_span["span_id"]
    assert root_span["parent_id"] == "undefined"
    root_span_trace_id = root_span["trace_id"]
    for child_span in spans[1:]:
        assert child_span["trace_id"] == root_span_trace_id
        assert child_span["parent_id"] == root_span_id
