import os
import time

import mock
import pytest

from ddtrace.llmobs._evaluations.ragas.faithfulness.evaluator import RagasFaithfulnessEvaluator
from ddtrace.llmobs._writer import LLMObsEvaluationMetricEvent


INTAKE_ENDPOINT = "https://api.datad0g.com/api/intake/llm-obs/v1/eval-metric"
DD_SITE = "datad0g.com"
dd_api_key = os.getenv("DD_API_KEY", default="<not-a-real-api-key>")


def _categorical_metric_event():
    return {
        "span_id": "12345678901",
        "trace_id": "98765432101",
        "metric_type": "categorical",
        "categorical_value": "very",
        "label": "toxicity",
        "ml_app": "dummy-ml-app",
        "timestamp_ms": round(time.time() * 1000),
    }


def _score_metric_event():
    return {
        "span_id": "12345678902",
        "trace_id": "98765432102",
        "metric_type": "score",
        "label": "sentiment",
        "score_value": 0.9,
        "ml_app": "dummy-ml-app",
        "timestamp_ms": round(time.time() * 1000),
    }


def _dummy_ragas_eval_metric_event(span_id, trace_id):
    return LLMObsEvaluationMetricEvent(
        span_id=span_id,
        trace_id=trace_id,
        score_value=1,
        ml_app="unnamed-ml-app",
        timestamp_ms=mock.ANY,
        metric_type="score",
        label="dummy.ragas.faithfulness",
    )


def test_ragas_faithfulness_evaluator_start(mock_evaluator_logs):
    ragas_faithfulness_evaluator = RagasFaithfulnessEvaluator(
        interval=0.01, _evaluation_metric_writer=mock.MagicMock(), _llmobs_instance=mock.MagicMock()
    )
    ragas_faithfulness_evaluator.start()
    mock_evaluator_logs.debug.assert_has_calls([mock.call("started %r to %r", "RagasFaithfulnessEvaluator")])


def test_ragas_faithfulness_evaluator_buffer_limit(mock_evaluator_logs):
    ragas_faithfulness_evaluator = RagasFaithfulnessEvaluator(
        interval=0.01, _evaluation_metric_writer=mock.MagicMock(), _llmobs_instance=mock.MagicMock()
    )
    for _ in range(1001):
        ragas_faithfulness_evaluator.enqueue({})
    mock_evaluator_logs.warning.assert_called_with(
        "%r event buffer full (limit is %d), dropping event", "RagasFaithfulnessEvaluator", 1000
    )


def test_ragas_faithfulness_evaluator_periodic_enqueues_eval_metric(LLMObs, mock_llmobs_eval_metric_writer):
    ragas_faithfulness_evaluator = RagasFaithfulnessEvaluator(
        interval=0.01, _evaluation_metric_writer=mock_llmobs_eval_metric_writer, _llmobs_instance=mock.MagicMock()
    )
    ragas_faithfulness_evaluator.enqueue({"span_id": "123", "trace_id": "1234"})
    ragas_faithfulness_evaluator.periodic()
    mock_llmobs_eval_metric_writer.enqueue.assert_called_once_with(
        _dummy_ragas_eval_metric_event(span_id="123", trace_id="1234")
    )


@pytest.mark.vcr_logs
def test_ragas_faithfulness_evaluator_timed_enqueues_eval_metric(LLMObs, mock_llmobs_eval_metric_writer):
    ragas_faithfulness_evaluator = RagasFaithfulnessEvaluator(
        interval=0.01, _evaluation_metric_writer=mock_llmobs_eval_metric_writer, _llmobs_instance=mock.MagicMock()
    )
    ragas_faithfulness_evaluator.start()

    ragas_faithfulness_evaluator.enqueue({"span_id": "123", "trace_id": "1234"})

    time.sleep(0.1)

    mock_llmobs_eval_metric_writer.enqueue.assert_called_once_with(
        _dummy_ragas_eval_metric_event(span_id="123", trace_id="1234")
    )


def test_ragas_evaluator_on_exit(mock_writer_logs, run_python_code_in_subprocess):
    out, err, status, pid = run_python_code_in_subprocess(
        """
import os
import time
import mock

from ddtrace.internal.utils.http import Response
from ddtrace.llmobs._writer import LLMObsEvalMetricWriter
from ddtrace.llmobs._evaluations.ragas.faithfulness.evaluator import RagasFaithfulnessEvaluator

with mock.patch(
    "ddtrace.internal.writer.HTTPWriter._send_payload",
    return_value=Response(
        status=200,
        body="{}",
    ),
):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(
    site="datad0g.com", api_key=os.getenv("DD_API_KEY_STAGING"), interval=0.01, timeout=1
    )
    llmobs_eval_metric_writer.start()
    ragas_faithfulness_evaluator = RagasFaithfulnessEvaluator(
        interval=0.01, _evaluation_metric_writer=llmobs_eval_metric_writer, _llmobs_instance=mock.MagicMock()
    )
    ragas_faithfulness_evaluator.start()
    ragas_faithfulness_evaluator.enqueue({"span_id": "123", "trace_id": "1234"})
""",
    )
    assert status == 0, err
    assert out == b""
    assert err == b""
