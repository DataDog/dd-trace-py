import json
import time

import mock
import pytest

import ddtrace
from ddtrace._trace.span import Span
from ddtrace.llmobs._evaluators.runner import EvaluatorRunner
from ddtrace.llmobs._evaluators.sampler import EvaluatorSampler
from ddtrace.llmobs._evaluators.sampler import EvaluatorSamplingRule
from ddtrace.llmobs._writer import LLMObsEvaluationMetricEvent


DUMMY_SPAN = Span("dummy_span")


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
        evaluator_runner.enqueue({}, DUMMY_SPAN)
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
from ddtrace._trace.span import Span
from ddtrace.internal.utils.http import Response
from ddtrace.llmobs import LLMObs
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
    evaluator_runner.enqueue({"span_id": "123", "trace_id": "1234"}, Span("dummy_span"))
""",
    )
    assert status == 0, err
    assert out == b""
    assert err == b""


def test_evaluator_runner_sampler_init(monkeypatch):
    sampler = EvaluatorSampler()
    assert sampler.rules == []
    assert sampler.default_sampling_rule.sample_rate == EvaluatorSampler.DEFAULT_SAMPLING_RATE


def test_evaluator_runner_sampler_single_rule(monkeypatch):
    monkeypatch.setenv(
        EvaluatorSampler.SAMPLING_RULES_ENV_VAR,
        json.dumps([{"sample_rate": 0.5, "evaluator_label": "ragas_faithfulness", "span_name": "dummy_span"}]),
    )
    sampling_rules = EvaluatorSampler().rules
    assert len(sampling_rules) == 1
    assert sampling_rules[0].sample_rate == 0.5
    assert sampling_rules[0].evaluator_label == "ragas_faithfulness"
    assert sampling_rules[0].span_name == "dummy_span"


def test_evaluator_runner_sampler_multiple_rules(monkeypatch):
    monkeypatch.setenv(
        EvaluatorSampler.SAMPLING_RULES_ENV_VAR,
        json.dumps(
            [
                {"sample_rate": 0.5, "evaluator_label": "ragas_faithfulness", "span_name": "dummy_span"},
                {"sample_rate": 0.2, "evaluator_label": "ragas_faithfulness", "span_name": "dummy_span_2"},
            ]
        ),
    )
    sampling_rules = EvaluatorSampler().rules
    assert len(sampling_rules) == 2
    assert sampling_rules[0].sample_rate == 0.5
    assert sampling_rules[0].evaluator_label == "ragas_faithfulness"
    assert sampling_rules[0].span_name == "dummy_span"

    assert sampling_rules[1].sample_rate == 0.2
    assert sampling_rules[1].evaluator_label == "ragas_faithfulness"
    assert sampling_rules[1].span_name == "dummy_span_2"


def test_evaluator_runner_sampler_no_rule_label_or_name(monkeypatch):
    monkeypatch.setenv(
        EvaluatorSampler.SAMPLING_RULES_ENV_VAR,
        json.dumps([{"sample_rate": 0.5}]),
    )
    sampling_rules = EvaluatorSampler().rules
    assert len(sampling_rules) == 1
    assert sampling_rules[0].sample_rate == 0.5
    assert sampling_rules[0].evaluator_label == EvaluatorSamplingRule.NO_RULE
    assert sampling_rules[0].span_name == EvaluatorSamplingRule.NO_RULE


def test_evaluator_runner_sampler_invalid_rule_not_a_list(monkeypatch):
    monkeypatch.setenv(
        EvaluatorSampler.SAMPLING_RULES_ENV_VAR,
        json.dumps({"sample_rate": 0.5, "evaluator_label": "ragas_faithfulness", "span_name": "dummy_span"}),
    )


def test_evaluator_runner_sampler_invalid_rule_sample_rate(monkeypatch):
    monkeypatch.setenv(
        EvaluatorSampler.SAMPLING_RULES_ENV_VAR,
        json.dumps([{"sample_rate": "invalid"}]),
    )


def test_evaluator_runner_sampler_invalid_json(monkeypatch):
    monkeypatch.setenv(
        EvaluatorSampler.SAMPLING_RULES_ENV_VAR,
        "invalid_json",
    )


def test_evaluator_runner_sampler_invalid_missing_sample_rate(monkeypatch):
    monkeypatch.setenv(EvaluatorSampler.SAMPLING_RULES_ENV_VAR, json.dumps([{"evaluator_label": "ragas_faithfulness"}]))


def test_evaluator_runner_sampler_no_rules(monkeypatch):
    monkeypatch.setenv(
        EvaluatorSampler.SAMPLING_RULES_ENV_VAR,
    )


def test_evaluator_sampling_rule_matches(monkeypatch):
    monkeypatch.setenv(EvaluatorSampler.SAMPLING_RULES_ENV_VAR, json.dumps([{"evaluator_label": "ragas_faithfulness"}]))
