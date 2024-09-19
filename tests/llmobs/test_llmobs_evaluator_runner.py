import os
import time

import mock
import pytest

from ddtrace import Span
from ddtrace.llmobs._evaluators.ragas.faithfulness import RagasFaithfulnessEvaluator
from ddtrace.llmobs._evaluators.runner import EvaluatorRunner
from ddtrace.llmobs._writer import LLMObsEvaluationMetricEvent


INTAKE_ENDPOINT = "https://api.datad0g.com/api/intake/llm-obs/v1/eval-metric"
DD_SITE = "datad0g.com"
dd_api_key = os.getenv("DD_API_KEY", default="<not-a-real-api-key>")
DUMMY_SPAN = Span("dummy_span")


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
        label="ragas_faithfulness",
    )


def test_evaluator_runner_start(mock_evaluator_logs):
    evaluator_runner = EvaluatorRunner(interval=0.01, _evaluation_metric_writer=mock.MagicMock())
    evaluator_runner.start()
    mock_evaluator_logs.debug.assert_has_calls([mock.call("started %r to %r", "EvaluatorRunner")])


def test_evaluator_runner_buffer_limit(mock_evaluator_logs):
    evaluator_runner = EvaluatorRunner(interval=0.01, _evaluation_metric_writer=mock.MagicMock())
    for _ in range(1001):
        evaluator_runner.enqueue({}, DUMMY_SPAN)
    mock_evaluator_logs.warning.assert_called_with(
        "%r event buffer full (limit is %d), dropping event", "EvaluatorRunner", 1000
    )


def test_evaluator_runner_periodic_enqueues_eval_metric(monkeypatch, LLMObs, mock_llmobs_eval_metric_writer):
    monkeypatch.setenv("_DD_LLMOBS_EVALUATOR_DEFAULT_SAMPLE_RATE", 1.0)
    evaluator_runner = EvaluatorRunner(interval=0.01, _evaluation_metric_writer=mock_llmobs_eval_metric_writer)
    evaluator_runner.evaluators.append(RagasFaithfulnessEvaluator)
    evaluator_runner.enqueue({"span_id": "123", "trace_id": "1234"}, DUMMY_SPAN)
    evaluator_runner.periodic()
    mock_llmobs_eval_metric_writer.enqueue.assert_called_once_with(
        _dummy_ragas_eval_metric_event(span_id="123", trace_id="1234")
    )


@pytest.mark.vcr_logs
def test_ragas_faithfulness_evaluator_timed_enqueues_eval_metric(monkeypatch, LLMObs, mock_llmobs_eval_metric_writer):
    monkeypatch.setenv("_DD_LLMOBS_EVALUATOR_DEFAULT_SAMPLE_RATE", 1.0)
    evaluator_runner = EvaluatorRunner(interval=0.01, _evaluation_metric_writer=mock_llmobs_eval_metric_writer)
    evaluator_runner.evaluators.append(RagasFaithfulnessEvaluator)
    evaluator_runner.start()

    evaluator_runner.enqueue({"span_id": "123", "trace_id": "1234"}, DUMMY_SPAN)

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
from ddtrace import Span
from ddtrace.internal.utils.http import Response
from ddtrace.llmobs._writer import LLMObsEvalMetricWriter
from ddtrace.llmobs._evaluators.runner import EvaluatorRunner
from ddtrace.llmobs._evaluators.ragas.faithfulness import RagasFaithfulnessEvaluator

os.environ["_DD_LLMOBS_EVALUATOR_DEFAULT_SAMPLE_RATE"] = "1.0"

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
    evaluator_runner = EvaluatorRunner(
        interval=0.01, _evaluation_metric_writer=llmobs_eval_metric_writer
    )
    evaluator_runner.evaluators.append(RagasFaithfulnessEvaluator)
    evaluator_runner.start()
    evaluator_runner.enqueue({"span_id": "123", "trace_id": "1234"}, Span("dummy_span"))
""",
    )
    assert status == 0, err
    assert out == b""
    assert err == b""


def test_evaluator_runner_sampler_init():
    # sampler = EvaluatorSampler()
    # assert sampler.rules == []
    # assert (
    #     sampler.limiter.rate_limit == DatadogSampler.DEFAULT_RATE_LIMIT
    # ), "EvaluatorSampler initialized with no arguments should hold a RateLimiter with the default limit"
    pass


def test_evaluator_runner_sampler_parse_rules_from_env():
    pass


def test_evaluator_runner_sampler_no_rules():
    pass


def test_evaluator_sampling_rule_matches():
    pass
