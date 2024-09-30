import time

import mock
import pytest

import ddtrace
from ddtrace.llmobs._evaluators.runner import EvaluatorRunner
from ddtrace.llmobs._writer import LLMObsEvaluationMetricEvent


def _dummy_ragas_eval_metric_event(span_id, trace_id):
    return LLMObsEvaluationMetricEvent(
        span_id=span_id,
        trace_id=trace_id,
        score_value=1.0,
        ml_app="unnamed-ml-app",
        timestamp_ms=mock.ANY,
        metric_type="score",
        label="ragas_faithfulness",
        tags=["ddtrace.version:{}".format(ddtrace.__version__), "ml_app:unnamed-ml-app"],
    )


def test_evaluator_runner_start(mock_evaluator_logs, mock_ragas_evaluator):
    evaluator_runner = EvaluatorRunner(interval=0.01, llmobs_service=mock.MagicMock())
    evaluator_runner.evaluators.append(mock_ragas_evaluator)
    evaluator_runner.start()
    mock_evaluator_logs.debug.assert_has_calls([mock.call("started %r to %r", "EvaluatorRunner")])


def test_evaluator_runner_buffer_limit(mock_evaluator_logs):
    evaluator_runner = EvaluatorRunner(interval=0.01, llmobs_service=mock.MagicMock())
    for _ in range(1001):
        evaluator_runner.enqueue({})
    mock_evaluator_logs.warning.assert_called_with(
        "%r event buffer full (limit is %d), dropping event", "EvaluatorRunner", 1000
    )


def test_evaluator_runner_periodic_enqueues_eval_metric(LLMObs, mock_llmobs_eval_metric_writer, mock_ragas_evaluator):
    evaluator_runner = EvaluatorRunner(interval=0.01, llmobs_service=LLMObs)
    evaluator_runner.evaluators.append(mock_ragas_evaluator(llmobs_service=LLMObs))
    evaluator_runner.enqueue({"span_id": "123", "trace_id": "1234"})
    evaluator_runner.periodic()
    mock_llmobs_eval_metric_writer.enqueue.assert_called_once_with(
        _dummy_ragas_eval_metric_event(span_id="123", trace_id="1234")
    )


@pytest.mark.vcr_logs
def test_evaluator_runner_timed_enqueues_eval_metric(LLMObs, mock_llmobs_eval_metric_writer, mock_ragas_evaluator):
    evaluator_runner = EvaluatorRunner(interval=0.01, llmobs_service=LLMObs)
    evaluator_runner.evaluators.append(mock_ragas_evaluator(llmobs_service=LLMObs))
    evaluator_runner.start()

    evaluator_runner.enqueue({"span_id": "123", "trace_id": "1234"})

    time.sleep(0.1)

    mock_llmobs_eval_metric_writer.enqueue.assert_called_once_with(
        _dummy_ragas_eval_metric_event(span_id="123", trace_id="1234")
    )


def test_evaluator_runner_on_exit(mock_writer_logs, run_python_code_in_subprocess):
    out, err, status, pid = run_python_code_in_subprocess(
        """
import os
import time
import mock

from ddtrace.internal.utils.http import Response
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._evaluators.runner import EvaluatorRunner
from ddtrace.llmobs._evaluators.ragas.faithfulness import RagasFaithfulnessEvaluator

with mock.patch(
    "ddtrace.llmobs._evaluators.runner.EvaluatorRunner.periodic",
    return_value=Response(
        status=200,
        body="{}",
    ),
):
    LLMObs.enable(
        site="datad0g.com",
        api_key=os.getenv("DD_API_KEY"),
        ml_app="unnamed-ml-app",
    )
    evaluator_runner = EvaluatorRunner(
        interval=0.01, llmobs_service=LLMObs
    )
    evaluator_runner.evaluators.append(RagasFaithfulnessEvaluator(llmobs_service=LLMObs))
    evaluator_runner.start()
    evaluator_runner.enqueue({"span_id": "123", "trace_id": "1234"})
""",
    )
    assert status == 0, err
    assert out == b""
    assert err == b""
